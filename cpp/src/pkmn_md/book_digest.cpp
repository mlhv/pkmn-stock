#include "pkmn_md/book_digest.hpp"

#include <algorithm>
#include <cinttypes>
#include <fstream>
#include <stdexcept>

#include "pkmn_md/deep_plus.hpp"
#include "pkmn_md/feed_reader.hpp"
#include "pkmn_md/iextp.hpp"
#include "pkmn_md/replay.hpp"

namespace pkmn::md {

namespace {

constexpr std::uint64_t kFnvBasis = 0xcbf29ce484222325ULL;
constexpr std::uint64_t kFnvPrime = 0x100000001b3ULL;

void mix(std::uint64_t& h, std::uint64_t v) {
    for (int i = 0; i < 8; ++i) {
        h ^= (v >> (8 * i)) & 0xFF;
        h *= kFnvPrime;
    }
}

struct DigestHooks : DeepPlusHooks {
    const BookSet* books = nullptr;
    std::vector<EpochDigest>* out = nullptr;
    CheckpointRecorder* ckpt = nullptr;
    std::uint64_t messages = 0;
    std::int64_t seq = 0;  // sequence number of the next applied message

    void close_epoch() {
        out->push_back({digest_books(*books), books->order_count(), messages});
        messages = 0;
    }
    void before_clear(const Segment&) { close_epoch(); }
    template <class Step>
    void per_message(Step&& step) {
        ++messages;
        step();
        ckpt->after(static_cast<std::uint32_t>(out->size()), seq++, *books);
    }
};

}  // namespace

std::uint64_t digest_books(const BookSet& books) {
    std::vector<std::pair<std::uint64_t, const book::OrderBook*>> live;
    books.for_each_book([&](std::uint64_t sym, const book::OrderBook& b) {
        if (b.order_count() > 0) live.emplace_back(sym, &b);
    });
    std::sort(live.begin(), live.end(), [](const auto& a, const auto& b) { return a.first < b.first; });
    std::uint64_t h = kFnvBasis;
    for (const auto& [sym, b] : live) {
        for (book::Side side : {book::Side::Buy, book::Side::Sell}) {
            b->for_each_order(side, [&](book::Price price, book::OrderId id, book::Qty qty) {
                mix(h, sym);
                mix(h, static_cast<std::uint64_t>(side));
                mix(h, static_cast<std::uint64_t>(price));
                mix(h, id);
                mix(h, static_cast<std::uint64_t>(qty));
            });
        }
    }
    return h;
}

DigestSet reference_digest_set(const std::string& pcap_path, std::uint64_t checkpoint_every) {
    FeedReader reader(pcap_path, kDeepPlus);
    BookSet books;
    BookEvent ev{};
    DecodeStats st;
    DigestSet out;
    out.checkpoint_every = checkpoint_every;
    CheckpointRecorder ckpt(checkpoint_every);
    DigestHooks hooks;
    hooks.books = &books;
    hooks.out = &out.epochs;
    hooks.ckpt = &ckpt;
    Segment seg;
    while (reader.next(seg)) {
        // for_each_applied applies messages skip.. of the segment, in order.
        hooks.seq = seg.hdr.first_seq + seg.skip;
        apply_deep_plus_segment_with(seg, books, ev, st, hooks);
    }
    hooks.close_epoch();
    out.checkpoints = ckpt.checkpoints();
    return out;
}

std::vector<EpochDigest> reference_digests(const std::string& pcap_path) {
    return reference_digest_set(pcap_path, 0).epochs;
}

void write_digests(std::FILE* out, const std::vector<EpochDigest>& d) {
    for (std::size_t i = 0; i < d.size(); ++i) {
        std::fprintf(out, "epoch %zu digest %016" PRIx64 " orders %" PRIu64 " messages %" PRIu64 "\n", i, d[i].digest,
                     d[i].orders, d[i].messages);
    }
}

void write_digest_set(std::FILE* out, const DigestSet& d) {
    if (d.checkpoint_every != 0) std::fprintf(out, "checkpoint-every %" PRIu64 "\n", d.checkpoint_every);
    write_digests(out, d.epochs);
    for (const Checkpoint& c : d.checkpoints) {
        std::fprintf(out, "checkpoint %" PRIu32 " %" PRId64 " digest %016" PRIx64 " orders %" PRIu64 "\n", c.epoch,
                     c.seq, c.digest, c.orders);
    }
}

DigestSet read_digest_set(const std::string& path) {
    std::ifstream f(path);
    if (!f) throw std::runtime_error("cannot open digest file: " + path);
    DigestSet out;
    std::string line;
    bool first = true;
    while (std::getline(f, line)) {
        const bool was_first = first;
        first = false;
        auto bad = [&] { return std::runtime_error("malformed digest line: " + line); };
        int consumed = 0;
        if (line.rfind("checkpoint-every ", 0) == 0) {
            unsigned long long every = 0;
            if (!was_first || std::sscanf(line.c_str(), "checkpoint-every %llu%n", &every, &consumed) != 1 ||
                line.size() != static_cast<std::size_t>(consumed) || every == 0)
                throw bad();
            out.checkpoint_every = every;
        } else if (line.rfind("checkpoint ", 0) == 0) {
            unsigned int epoch = 0;
            long long seq = 0;
            unsigned long long dg = 0, orders = 0;
            if (out.checkpoint_every == 0 ||
                std::sscanf(line.c_str(), "checkpoint %u %lld digest %16llx orders %llu%n", &epoch, &seq, &dg, &orders,
                            &consumed) != 4 ||
                line.size() != static_cast<std::size_t>(consumed) || seq <= 0 ||
                static_cast<unsigned long long>(seq) % out.checkpoint_every != 0)
                throw bad();
            if (!out.checkpoints.empty()) {
                const Checkpoint& prev = out.checkpoints.back();
                if (epoch < prev.epoch || (epoch == prev.epoch && seq <= prev.seq)) throw bad();
            }
            out.checkpoints.push_back({epoch, seq, dg, orders});
        } else {
            std::size_t idx = 0;
            unsigned long long dg = 0, orders = 0, msgs = 0;
            if (!out.checkpoints.empty() ||
                std::sscanf(line.c_str(), "epoch %zu digest %16llx orders %llu messages %llu%n", &idx, &dg, &orders,
                            &msgs, &consumed) != 4 ||
                idx != out.epochs.size() || line.size() != static_cast<std::size_t>(consumed)) {
                throw bad();
            }
            out.epochs.push_back({dg, orders, msgs});
        }
    }
    for (const Checkpoint& c : out.checkpoints) {
        if (c.epoch >= out.epochs.size()) throw std::runtime_error("checkpoint names a missing epoch in " + path);
    }
    return out;
}

std::vector<EpochDigest> read_digests(const std::string& path) { return read_digest_set(path).epochs; }

}  // namespace pkmn::md
