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
    std::uint64_t messages = 0;

    void close_epoch() {
        out->push_back({digest_books(*books), books->order_count(), messages});
        messages = 0;
    }
    void before_clear(const Segment&) { close_epoch(); }
    template <class Step>
    void per_message(Step&& step) {
        ++messages;
        step();
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

std::vector<EpochDigest> reference_digests(const std::string& pcap_path) {
    FeedReader reader(pcap_path, kDeepPlus);
    BookSet books;
    BookEvent ev{};
    DecodeStats st;
    std::vector<EpochDigest> out;
    DigestHooks hooks;
    hooks.books = &books;
    hooks.out = &out;
    Segment seg;
    while (reader.next(seg)) apply_deep_plus_segment_with(seg, books, ev, st, hooks);
    hooks.close_epoch();
    return out;
}

void write_digests(std::FILE* out, const std::vector<EpochDigest>& d) {
    for (std::size_t i = 0; i < d.size(); ++i) {
        std::fprintf(out, "epoch %zu digest %016" PRIx64 " orders %" PRIu64 " messages %" PRIu64 "\n", i, d[i].digest,
                     d[i].orders, d[i].messages);
    }
}

std::vector<EpochDigest> read_digests(const std::string& path) {
    std::ifstream f(path);
    if (!f) throw std::runtime_error("cannot open digest file: " + path);
    std::vector<EpochDigest> out;
    std::string line;
    while (std::getline(f, line)) {
        std::size_t idx = 0;
        unsigned long long dg = 0, orders = 0, msgs = 0;
        int consumed = 0;
        if (std::sscanf(line.c_str(), "epoch %zu digest %16llx orders %llu messages %llu%n", &idx, &dg, &orders, &msgs,
                        &consumed) != 4 ||
            idx != out.size() || line.size() != static_cast<std::size_t>(consumed)) {
            throw std::runtime_error("malformed digest line: " + line);
        }
        out.push_back({dg, orders, msgs});
    }
    return out;
}

}  // namespace pkmn::md
