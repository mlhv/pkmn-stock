// Correctness oracle for the DEEP+ order book: replays IEX DEEP+ (order by
// order) into a BookSet and checks it against IEX's own aggregated DEEP
// price levels and TOPS best quotes for the same day.
//
//   iex_oracle --deep-plus <f> --deep <f> --tops <f> [--symbols N] [--symbol S]... [--round-lot S=N]... [--lookahead-ms M]
//
// Checkpoints. A DEEP Price Level Update with Event Flags ON closes one
// atomic book transition (DEEP v1.08 pp.17-19); every level that transition
// touched (its Event Flags OFF updates plus the closing one, the last update
// of a (side, price) winning; and when several transitions share a timestamp
// they are one atomic event, DEEP v1.08 p.4, so the last one per (side,
// price) at that timestamp wins) is checked at the closing update's timestamp
// t: once every DEEP+ event for that symbol with ts_ns <= t has been applied
// and none later, qty_at(side, price) must equal the DEEP size (0 = level
// gone). A TOPS Quote Update is checked the same way against the book's
// top-of-book quotation as IEX defines it for its Top of Book Data Feeds
// since SR-IEX-2021-01 (SEC Release 34-90933, pp.7-8): per side, the most
// aggressive price at which the aggregate displayed size at that price or
// better reaches one round lot, with that entire aggregate size; no such
// price is price 0 / size 0 (TOPS v1.66 p.16 "zero quote"). The round lot
// is the Security Directory's (DEEP+ v1.06 p.14) when one was sent, else an
// operator-supplied --round-lot SYM=N, else 100. DEEP+ sends Security
// Directory messages for IEX-listed securities only, so a non-IEX-listed
// security whose listing market sets a smaller round lot needs --round-lot.
// TOPS has no event-complete flag; quotes sharing a timestamp are one atomic
// event (TOPS v1.66 p.4), so only the last quote of a (symbol, timestamp) run
// is a checkpoint (the superseded ones are counted and printed).
//
// Excluded by name (a documented feed difference, not a tolerance): a TOPS
// zero quote with the Symbol Availability flag set (0x80, TOPS v1.66 p.26),
// i.e. the symbol is halted or unavailable while its orders still rest.
// Counted under "excluded", never as a mismatch, and not in "checked". The
// same shape against a book that is itself empty on both sides is not
// excluded: it is compared like any quote (and matches), so it is checked.
//
// Merge. The three files are streamed concurrently, one FeedReader each
// (sequence tracking + restart policy per feed), ordered by the feeds' own
// message timestamps, never pcap capture time. Checks wait in per-symbol
// FIFO queues and are resolved lazily: just before a DEEP+ event for symbol
// s at time T is applied, every queued check for s with t < T is resolved.
// DEEP and TOPS are read ahead of DEEP+ by --lookahead-ms (default 1000) of
// feed time, so a check is queued before DEEP+ passes it; one that arrives
// too late (DEEP+ already applied an event for s after t) cannot be verified
// and is reported as the mismatch category "late_checkpoint".
//
// A DEEP+ stream restart (is_stream_restart) first resolves every queued
// check with t before the restart segment's send time, then clears every
// book. The restart -> clear -> decode -> apply rule itself is
// apply_deep_plus_segment_with (replay.hpp), shared with iex_bench; this tool
// only supplies its hooks. --symbols N restricts checking (and book building) to the first N
// distinct symbols encountered on any of the three feeds; --symbol S
// (repeatable) restricts it to exactly the named symbols.
//
// Prints checkpoint counts, mismatches by category and examples, then the
// validity preconditions and the result. Exit status:
//   0  PASS: every validity precondition held and there were 0 mismatches.
//   1  MISMATCH: the preconditions held and at least one check mismatched.
//   3  INCONCLUSIVE: a validity precondition failed (named on stdout), so the
//      run does not establish what it set out to check, whatever the
//      mismatch count. Preconditions: on each of the three feeds, 0 gaps,
//      0 tp_parse_failures and 0 other_stream segments (a new session id
//      would otherwise leave the rest of a feed silently unchecked); 0 DEEP+
//      per-symbol timestamp regressions; 0 DEEP transitions left open;
//      book anomalies unknown_id, duplicate_add and exec_clamped all 0;
//      0 late_checkpoint; and both DEEP and TOPS produced at least 1 check.
//   2  usage or I/O error.

#include <sys/resource.h>

#include <algorithm>
#include <chrono>
#include <cinttypes>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <deque>
#include <iterator>
#include <exception>
#include <limits>
#include <map>
#include <string>
#include <unordered_map>
#include <unordered_set>
#include <vector>

#include "pkmn_md/book_set.hpp"
#include "pkmn_md/deep.hpp"
#include "pkmn_md/deep_plus.hpp"
#include "pkmn_md/events.hpp"
#include "pkmn_md/feed_reader.hpp"
#include "pkmn_md/iextp.hpp"
#include "pkmn_md/replay.hpp"

using namespace pkmn::md;
using pkmn::book::OrderBook;
using pkmn::book::Side;

namespace {

using u64 = unsigned long long;
using i64 = long long;
constexpr std::int64_t kNever = std::numeric_limits<std::int64_t>::min();
constexpr std::int64_t kForever = std::numeric_limits<std::int64_t>::max();
// Round lot when no Security Directory message names one (DEEP+ sends 'D' for
// IEX-listed securities only, DEEP+ v1.06 p.14); 100 shares for every NMS
// stock in December 2024 (IEX Rule 11.180(a), cited in SR-IEX-2021-01 n.14).
constexpr std::int64_t kDefaultRoundLot = 100;

// IEX's top-of-book quotation for one side, as TOPS disseminates it
// (SEC Release 34-90933, SR-IEX-2021-01, pp.7-8): the most aggressive price
// at which the aggregate displayed size at that price or better is at least
// one round lot, with the entire aggregate size. No such price: 0 / 0.
struct Top {
    std::int64_t price = 0, size = 0;
};
Top aggregated_top(const OrderBook& b, Side side, std::int64_t round_lot, std::vector<pkmn::book::Level>& scratch) {
    for (std::size_t n = 8;; n *= 4) {
        b.depth(side, n, scratch);
        std::int64_t cum = 0;
        for (const auto& l : scratch) {
            cum += l.qty;
            if (cum >= round_lot) return Top{l.price, cum};
        }
        if (scratch.size() < n) return Top{};
    }
}

std::string sym_str(std::uint64_t s) {
    char b[9];
    std::memcpy(b, &s, 8);
    b[8] = 0;
    std::string out(b);
    while (!out.empty() && out.back() == ' ') out.pop_back();
    return out;
}

// HH:MM:SS.nnnnnnnnn in US Eastern (2024-12-23 is EST, UTC-5).
std::string et(std::int64_t ns) {
    if (ns == kNever || ns == kForever) return "-";
    const std::int64_t s = ns / 1'000'000'000 - 5 * 3600;
    const std::int64_t day_s = ((s % 86400) + 86400) % 86400;
    char b[40];
    std::snprintf(b, sizeof b, "%02lld:%02lld:%02lld.%09lld ET", static_cast<i64>(day_s / 3600),
                  static_cast<i64>(day_s / 60 % 60), static_cast<i64>(day_s % 60),
                  static_cast<i64>(ns % 1'000'000'000));
    return b;
}

std::string px(std::int64_t p) {
    char b[32];
    std::snprintf(b, sizeof b, "%lld.%04lld", static_cast<i64>(p / 10000), static_cast<i64>(std::llabs(p % 10000)));
    return b;
}

// 8-byte space-padded symbol field, loaded little-endian (as the decoders do).
bool parse_symbol(const char* a, std::uint64_t& out) {
    const std::size_t n = std::strlen(a);
    if (n == 0 || n > 8) return false;
    char b[8];
    std::memset(b, ' ', 8);
    std::memcpy(b, a, n);
    std::memcpy(&out, b, 8);
    return true;
}

struct DeepCheck {
    std::int64_t t;
    Side side;
    std::int64_t price, size;
};
struct TopsCheck {
    std::int64_t t;
    std::int64_t bid, bid_size, ask, ask_size;
    std::uint8_t flags;
};

struct SymState {
    std::int64_t last_dp_ts = kNever;  // last DEEP+ book event applied for this symbol
    std::int64_t last_deep_t = kNever, last_tops_t = kNever;
    std::deque<DeepCheck> deep;
    std::deque<TopsCheck> tops;
    std::vector<LevelUpdate> open_txn;  // DEEP Event Flags OFF updates awaiting their closing update
    std::int64_t round_lot = kDefaultRoundLot;
};

struct Mismatch {
    std::string category;
    std::uint64_t symbol;
    std::int64_t t;
    std::string expected, actual;
};

struct FeedReport {
    const char* name;
    std::uint64_t messages = 0, decoded = 0;
    std::vector<std::pair<std::int64_t, std::uint64_t>> restarts;  // (send_time, packet)
    std::int64_t first_ts = kNever, last_ts = kNever;
};

class Oracle {
  public:
    Oracle(const std::string& dp, const std::string& deep, const std::string& tops, std::size_t max_symbols,
           std::int64_t lookahead_ns)
        : dp_(dp, kDeepPlus),
          deep_(deep, kDeep),
          tops_(tops, kTops),
          max_symbols_(max_symbols),
          lookahead_(lookahead_ns) {}

    int run();
    // Restrict checking to exactly these symbols (--symbol); overrides --symbols.
    void only(const std::vector<std::uint64_t>& syms) {
        selected_.insert(syms.begin(), syms.end());
        max_symbols_ = selected_.size();
    }
    // Operator-supplied round lot (--round-lot SYM=N) for a security whose
    // round lot is not 100 and that DEEP+ sends no Security Directory for
    // (non-IEX-listed). A Security Directory message still overrides it.
    void round_lot(std::uint64_t sym, std::int64_t lot) { lot_overrides_[sym] = lot; }

  private:
    bool selected(std::uint64_t sym) {
        if (max_symbols_ == 0) return true;
        if (selected_.count(sym) != 0) return true;
        if (selected_.size() >= max_symbols_) return false;
        selected_.insert(sym);
        return true;
    }

    void read_ahead(std::int64_t T);
    bool read_deep_segment();
    bool read_tops_segment();
    void on_level(const LevelUpdate& u);
    void on_quote(const TopQuote& q);
    void resolve(std::uint64_t sym, SymState& s, std::int64_t before);
    void resolve_all(std::int64_t before);
    void mismatch(const char* cat, std::uint64_t sym, std::int64_t t, std::string exp, std::string act);
    void report(double secs) const;
    std::uint64_t mismatches() const;
    std::vector<std::string> failed_preconditions() const;
    friend struct OracleHooks;

    FeedReader dp_, deep_, tops_;
    std::size_t max_symbols_;
    std::int64_t lookahead_;
    std::unordered_set<std::uint64_t> selected_;
    std::map<std::uint64_t, std::int64_t> lot_overrides_;
    SymState& sym_state(std::uint64_t sym) {
        auto [it, fresh] = syms_.try_emplace(sym);
        if (fresh)
            if (auto o = lot_overrides_.find(sym); o != lot_overrides_.end()) it->second.round_lot = o->second;
        return it->second;
    }
    std::unordered_map<std::uint64_t, SymState> syms_;
    BookSet books_;
    DecodeStats dp_stats_{};
    bool deep_eof_ = false, tops_eof_ = false;
    std::int64_t deep_max_ts_ = kNever, tops_max_ts_ = kNever;
    FeedReport rep_dp_{"DEEP+"}, rep_deep_{"DEEP"}, rep_tops_{"TOPS"};

    std::uint64_t dp_book_events_ = 0, dp_ts_regress_ = 0;
    // TOPS: evaluated = every quote checkpoint resolved; excluded = halt zero
    // quotes excluded by name; checked = evaluated - excluded (compared).
    std::uint64_t deep_checkpoints_ = 0, deep_levels_checked_ = 0, tops_evaluated_ = 0, tops_checked_ = 0;
    std::uint64_t deep_txn_ts_differs_ = 0, deep_open_txn_dropped_ = 0, deep_ooo_ = 0, tops_ooo_ = 0;
    std::uint64_t tops_same_ts_superseded_ = 0, deep_same_ts_superseded_ = 0;
    std::uint64_t tops_odd_lot_aggregated_ = 0, round_lots_seen_ = 0;
    std::vector<pkmn::book::Level> scratch_;
    std::map<std::string, std::uint64_t> by_category_, excluded_;
    std::map<std::string, std::unordered_map<std::uint64_t, std::uint64_t>> by_symbol_;
    std::vector<Mismatch> first_;
    std::map<std::string, std::vector<Mismatch>> per_category_;
};

void Oracle::mismatch(const char* cat, std::uint64_t sym, std::int64_t t, std::string exp, std::string act) {
    ++by_category_[cat];
    ++by_symbol_[cat][sym];
    Mismatch m{cat, sym, t, std::move(exp), std::move(act)};
    auto& pc = per_category_[cat];
    if (pc.size() < 5) pc.push_back(m);
    if (first_.size() < 20) first_.push_back(std::move(m));
}

void Oracle::on_level(const LevelUpdate& u) {
    ++rep_deep_.decoded;
    if (!selected(u.symbol)) return;
    SymState& s = sym_state(u.symbol);
    if (!u.event_complete) {
        s.open_txn.push_back(u);
        return;
    }
    ++deep_checkpoints_;
    // Collapse the transition: the last update of a (side, price) wins. Every
    // level is checked at the closing update's timestamp.
    s.open_txn.push_back(u);
    for (std::size_t i = 0; i < s.open_txn.size(); ++i) {
        const LevelUpdate& x = s.open_txn[i];
        if (x.ts_ns != u.ts_ns) ++deep_txn_ts_differs_;
        bool superseded = false;
        for (std::size_t j = i + 1; j < s.open_txn.size(); ++j)
            if (s.open_txn[j].side == x.side && s.open_txn[j].price == x.price) superseded = true;
        if (superseded) continue;
        // Several transitions can share one timestamp: they are then one
        // atomic event (DEEP v1.08 p.4), so an earlier queued check of the
        // same (side, price) at the same t is an in-transition state.
        for (auto it = s.deep.rbegin(); it != s.deep.rend() && it->t == u.ts_ns; ++it) {
            if (it->side == x.side && it->price == x.price) {
                s.deep.erase(std::next(it).base());
                ++deep_same_ts_superseded_;
                break;
            }
        }
        s.deep.push_back(DeepCheck{u.ts_ns, x.side, x.price, x.size});
    }
    s.open_txn.clear();
    if (u.ts_ns < s.last_deep_t) ++deep_ooo_;
    s.last_deep_t = u.ts_ns;
    if (s.last_dp_ts > u.ts_ns) {
        // DEEP+ already moved this symbol past t: the state at t is gone.
        while (!s.deep.empty() && s.deep.back().t == u.ts_ns) {
            const DeepCheck c = s.deep.back();
            s.deep.pop_back();
            mismatch("late_checkpoint", u.symbol, c.t, "queued before DEEP+ passed t",
                     "DEEP+ already at " + et(s.last_dp_ts));
        }
    }
}

void Oracle::on_quote(const TopQuote& q) {
    ++rep_tops_.decoded;
    if (!selected(q.symbol)) return;
    SymState& s = sym_state(q.symbol);
    if (q.ts_ns < s.last_tops_t) ++tops_ooo_;
    s.last_tops_t = q.ts_ns;
    if (s.last_dp_ts > q.ts_ns) {
        mismatch("late_checkpoint", q.symbol, q.ts_ns, "queued before DEEP+ passed t",
                 "DEEP+ already at " + et(s.last_dp_ts));
        return;
    }
    // Quotes with one timestamp are one atomic event (TOPS v1.66 p.4,
    // "Timestamp Relationships"); TOPS has no event-complete flag, so the
    // last quote of a same-timestamp run is the checkpoint and earlier ones
    // are in-transition states (e.g. both sides moving in one event).
    if (!s.tops.empty() && s.tops.back().t == q.ts_ns) {
        s.tops.back() = TopsCheck{q.ts_ns, q.bid, q.bid_size, q.ask, q.ask_size, q.flags};
        ++tops_same_ts_superseded_;
        return;
    }
    s.tops.push_back(TopsCheck{q.ts_ns, q.bid, q.bid_size, q.ask, q.ask_size, q.flags});
}

bool Oracle::read_deep_segment() {
    Segment seg{};
    if (!deep_.next(seg)) return false;
    if (seg.restart) {
        rep_deep_.restarts.emplace_back(seg.hdr.send_time, seg.packet_no);
        // A transition left open across a restart can never close.
        for (auto& [sym, s] : syms_) {
            deep_open_txn_dropped_ += s.open_txn.size();
            s.open_txn.clear();
        }
    }
    LevelUpdate u{};
    for_each_applied(seg, [&](std::span<const std::uint8_t> m) {
        ++rep_deep_.messages;
        if (!decode_deep_level(m, u)) return;
        if (rep_deep_.first_ts == kNever) rep_deep_.first_ts = u.ts_ns;
        rep_deep_.last_ts = u.ts_ns;
        if (u.ts_ns > deep_max_ts_) deep_max_ts_ = u.ts_ns;
        on_level(u);
    });
    return true;
}

bool Oracle::read_tops_segment() {
    Segment seg{};
    if (!tops_.next(seg)) return false;
    if (seg.restart) rep_tops_.restarts.emplace_back(seg.hdr.send_time, seg.packet_no);
    TopQuote q{};
    for_each_applied(seg, [&](std::span<const std::uint8_t> m) {
        ++rep_tops_.messages;
        if (!decode_tops_quote(m, q)) return;
        if (rep_tops_.first_ts == kNever) rep_tops_.first_ts = q.ts_ns;
        rep_tops_.last_ts = q.ts_ns;
        if (q.ts_ns > tops_max_ts_) tops_max_ts_ = q.ts_ns;
        on_quote(q);
    });
    return true;
}

void Oracle::read_ahead(std::int64_t T) {
    const std::int64_t until = T > kForever - lookahead_ ? kForever : T + lookahead_;
    while (!deep_eof_ && deep_max_ts_ <= until)
        if (!read_deep_segment()) deep_eof_ = true;
    while (!tops_eof_ && tops_max_ts_ <= until)
        if (!read_tops_segment()) tops_eof_ = true;
}

void Oracle::resolve(std::uint64_t sym, SymState& s, std::int64_t before) {
    const OrderBook* b = books_.find(sym);
    while (!s.deep.empty() && s.deep.front().t < before) {
        const DeepCheck c = s.deep.front();
        s.deep.pop_front();
        ++deep_levels_checked_;
        const std::int64_t actual = b != nullptr ? b->qty_at(c.side, c.price) : 0;
        if (actual != c.size) {
            const char* cat = c.side == Side::Buy ? "deep_level_bid" : "deep_level_ask";
            mismatch(cat, sym, c.t, px(c.price) + " size " + std::to_string(c.size),
                     px(c.price) + " size " + std::to_string(actual));
        }
    }
    while (!s.tops.empty() && s.tops.front().t < before) {
        const TopsCheck c = s.tops.front();
        s.tops.pop_front();
        ++tops_evaluated_;
        std::int64_t bid = 0, bid_size = 0, ask = 0, ask_size = 0;
        if (b != nullptr) {
            const Top tb = aggregated_top(*b, Side::Buy, s.round_lot, scratch_);
            const Top ta = aggregated_top(*b, Side::Sell, s.round_lot, scratch_);
            bid = tb.price, bid_size = tb.size, ask = ta.price, ask_size = ta.size;
            // Informational: how often the round-lot rule differs from the raw best level.
            const auto rb = b->best(Side::Buy), ra = b->best(Side::Sell);
            if ((rb ? rb->price : 0) != bid || (ra ? ra->price : 0) != ask) ++tops_odd_lot_aggregated_;
        }
        char fl[16];
        std::snprintf(fl, sizeof fl, " flags 0x%02x", c.flags);
        // Excluded by name: while a symbol is "halted, paused, or otherwise not
        // available for trading on IEX" (Quote Update flag A = 0x80, TOPS v1.66
        // p.26) TOPS publishes a zero quote, but the order book keeps its resting
        // orders (DEEP+ and DEEP send no deletes; e.g. the 10:22:29 ET operational
        // halt on 2024-12-23). Only that exact shape is excluded: flag A set AND
        // all four fields zero. Any other quote, flag A or not, is checked.
        // If the book is itself empty on both sides the quote is compared like
        // any other (it matches), so it is checked, not excluded.
        const bool zero_quote = c.bid == 0 && c.bid_size == 0 && c.ask == 0 && c.ask_size == 0;
        const bool book_zero = bid == 0 && bid_size == 0 && ask == 0 && ask_size == 0;
        if ((c.flags & 0x80) != 0 && zero_quote && !book_zero) {
            ++excluded_["tops_unavailable_zero_quote"];
            continue;
        }
        ++tops_checked_;
        if (bid != c.bid || bid_size != c.bid_size)
            mismatch("tops_bid", sym, c.t, std::to_string(c.bid_size) + " @ " + px(c.bid) + fl,
                     std::to_string(bid_size) + " @ " + px(bid));
        if (ask != c.ask || ask_size != c.ask_size)
            mismatch("tops_ask", sym, c.t, std::to_string(c.ask_size) + " @ " + px(c.ask) + fl,
                     std::to_string(ask_size) + " @ " + px(ask));
    }
}

void Oracle::resolve_all(std::int64_t before) {
    for (auto& [sym, s] : syms_) resolve(sym, s, before);
}

// The oracle's side of apply_deep_plus_segment_with (replay.hpp): the
// restart/clear/decode/apply rule stays in the library; these hooks resolve
// pending checks before the clear and before each event.
struct OracleHooks : DeepPlusHooks {
    Oracle& o;
    explicit OracleHooks(Oracle& oracle) : o(oracle) {}

    void before_clear(const Segment& seg) {
        o.rep_dp_.restarts.emplace_back(seg.hdr.send_time, seg.packet_no);
        o.read_ahead(seg.hdr.send_time);
        o.resolve_all(seg.hdr.send_time);
    }
    template <class Step>
    void per_message(Step&& step) {
        ++o.rep_dp_.messages;
        step();
    }
    bool before_apply(const BookEvent& ev) {
        ++o.rep_dp_.decoded;
        if (o.rep_dp_.first_ts == kNever) o.rep_dp_.first_ts = ev.ts_ns;
        o.rep_dp_.last_ts = ev.ts_ns;
        o.read_ahead(ev.ts_ns);
        if (!o.selected(ev.symbol)) return false;
        SymState& s = o.sym_state(ev.symbol);
        o.resolve(ev.symbol, s, ev.ts_ns);
        if (ev.ts_ns < s.last_dp_ts) ++o.dp_ts_regress_;
        s.last_dp_ts = ev.ts_ns;
        ++o.dp_book_events_;
        return true;
    }
    void non_book(std::span<const std::uint8_t> m) {
        // Security Directory: round lot size at offset 18 (DEEP+ v1.06 p.14).
        if (m.size() >= 31 && m[0] == 'D') {
            const auto sym = pkmn::md::detail::load<std::uint64_t>(m.data() + 10);
            if (o.selected(sym)) {
                o.sym_state(sym).round_lot = pkmn::md::detail::load<std::uint32_t>(m.data() + 18);
                ++o.round_lots_seen_;
            }
        }
    }
};

int Oracle::run() {
    const auto t0 = std::chrono::steady_clock::now();
    Segment seg{};
    BookEvent ev{};
    OracleHooks hooks(*this);
    while (dp_.next(seg)) apply_deep_plus_segment_with(seg, books_, ev, dp_stats_, hooks);
    read_ahead(kForever);
    resolve_all(kForever);
    for (auto& [sym, s] : syms_) deep_open_txn_dropped_ += s.open_txn.size();
    const double secs = std::chrono::duration<double>(std::chrono::steady_clock::now() - t0).count();
    report(secs);
    const std::vector<std::string> failed = failed_preconditions();
    const std::uint64_t total = mismatches();
    if (!failed.empty()) {
        std::printf("validity          INCONCLUSIVE: %zu precondition(s) failed:\n", failed.size());
        for (const auto& f : failed) std::printf("  %s\n", f.c_str());
        std::printf("result            INCONCLUSIVE (exit 3; %llu mismatches not a verdict)\n", static_cast<u64>(total));
        return 3;
    }
    std::printf("validity          all preconditions held\n");
    if (total != 0) {
        std::printf("result            MISMATCH (exit 1)\n");
        return 1;
    }
    std::printf("result            PASS (exit 0)\n");
    return 0;
}

std::uint64_t Oracle::mismatches() const {
    std::uint64_t total = 0;
    for (const auto& [cat, n] : by_category_) total += n;
    return total;
}

// Each entry names one failed validity precondition and its count.
std::vector<std::string> Oracle::failed_preconditions() const {
    std::vector<std::string> out;
    auto need_zero = [&](const std::string& name, std::uint64_t n) {
        if (n != 0) out.push_back(name + " " + std::to_string(n) + " (must be 0)");
    };
    const std::pair<const char*, const FeedReader*> feeds[] = {{"DEEP+", &dp_}, {"DEEP", &deep_}, {"TOPS", &tops_}};
    for (const auto& [name, fr] : feeds) {
        need_zero(std::string(name) + " gaps", fr->tracker().gaps());
        need_zero(std::string(name) + " tp_parse_failures", fr->tp_parse_failures());
        need_zero(std::string(name) + " other_stream", fr->other_stream());
    }
    need_zero("DEEP+ per-symbol ts regressions", dp_ts_regress_);
    need_zero("DEEP open txn dropped", deep_open_txn_dropped_);
    const auto an = books_.total_anomalies();
    need_zero("book unknown_id", an.unknown_id);
    need_zero("book duplicate_add", an.duplicate_add);
    need_zero("book exec_clamped", an.exec_clamped);
    if (auto it = by_category_.find("late_checkpoint"); it != by_category_.end()) need_zero("late_checkpoint", it->second);
    if (deep_levels_checked_ == 0) out.push_back("DEEP checks 0 (must be >= 1)");
    if (tops_checked_ == 0) out.push_back("TOPS checks 0 (must be >= 1)");
    return out;
}

void print_feed(const FeedReader& fr, const FeedReport& r) {
    std::printf("%-6s packets %llu segments %llu heartbeats %llu tp_parse_failures %llu other_stream %llu\n", r.name,
                static_cast<u64>(fr.packets()), static_cast<u64>(fr.segments()), static_cast<u64>(fr.heartbeats()),
                static_cast<u64>(fr.tp_parse_failures()), static_cast<u64>(fr.other_stream()));
    std::printf("       stream channel %u session %u; messages applied %llu, decoded %llu\n", fr.channel(),
                fr.session(), static_cast<u64>(r.messages), static_cast<u64>(r.decoded));
    std::printf("       gaps %llu (%llu messages) duplicates %llu resets %llu\n",
                static_cast<u64>(fr.tracker().gaps()), static_cast<u64>(fr.tracker().gap_messages()),
                static_cast<u64>(fr.tracker().duplicates()), static_cast<u64>(fr.tracker().resets()));
    for (const auto& [st, pkt] : r.restarts)
        std::printf("       restart at send_time %lld (%s), packet %llu\n", static_cast<i64>(st), et(st).c_str(),
                    static_cast<u64>(pkt));
    std::printf("       feed time %s .. %s\n", et(r.first_ts).c_str(), et(r.last_ts).c_str());
}

void Oracle::report(double secs) const {
    print_feed(dp_, rep_dp_);
    print_feed(deep_, rep_deep_);
    print_feed(tops_, rep_tops_);
    const auto an = books_.total_anomalies();
    std::printf("symbols           %zu%s\n", syms_.size(),
                max_symbols_ != 0 ? (" (limited to first " + std::to_string(max_symbols_) + ")").c_str() : "");
    std::printf("deep+ book events %llu applied; per-symbol ts regressions %llu\n", static_cast<u64>(dp_book_events_),
                static_cast<u64>(dp_ts_regress_));
    std::printf("book anomalies    unknown_id %llu duplicate_add %llu exec_clamped %llu; clear_all %llu; resting at end %zu\n",
                static_cast<u64>(an.unknown_id), static_cast<u64>(an.duplicate_add), static_cast<u64>(an.exec_clamped),
                static_cast<u64>(books_.clear_alls()), books_.order_count());
    std::uint64_t tops_excluded = 0;
    for (const auto& [cat, n] : excluded_) tops_excluded += n;
    std::printf("checkpoints       DEEP %llu (levels checked %llu); TOPS evaluated %llu, excluded %llu, checked %llu\n",
                static_cast<u64>(deep_checkpoints_), static_cast<u64>(deep_levels_checked_),
                static_cast<u64>(tops_evaluated_), static_cast<u64>(tops_excluded), static_cast<u64>(tops_checked_));
    std::printf("checked points    %llu evaluated, %llu excluded by name, %llu checked (DEEP levels + TOPS checked)\n",
                static_cast<u64>(deep_levels_checked_ + tops_evaluated_), static_cast<u64>(tops_excluded),
                static_cast<u64>(deep_levels_checked_ + tops_checked_));
    std::printf("deep checkpoints  final state per (symbol, timestamp); %llu earlier same-timestamp level updates superseded\n",
                static_cast<u64>(deep_same_ts_superseded_));
    std::printf("tops checkpoints  last quote per (symbol, timestamp); %llu earlier same-timestamp quotes superseded\n",
                static_cast<u64>(tops_same_ts_superseded_));
    std::printf("tops rule         odd-lot aggregation (SR-IEX-2021-01): differs from raw best level at %llu quotes; "
                "round lots from Security Directory %llu\n",
                static_cast<u64>(tops_odd_lot_aggregated_), static_cast<u64>(round_lots_seen_));
    std::printf("merge diagnostics DEEP txn ts differs %llu, DEEP open txn dropped %llu, out-of-order DEEP %llu TOPS %llu\n",
                static_cast<u64>(deep_txn_ts_differs_), static_cast<u64>(deep_open_txn_dropped_),
                static_cast<u64>(deep_ooo_), static_cast<u64>(tops_ooo_));
    std::printf("mismatches        %llu\n", static_cast<u64>(mismatches()));
    for (const auto& [cat, n] : by_category_) std::printf("  %-18s %llu\n", cat.c_str(), static_cast<u64>(n));
    for (const auto& [cat, m] : by_symbol_) {
        std::vector<std::pair<std::uint64_t, std::uint64_t>> v(m.begin(), m.end());
        std::sort(v.begin(), v.end(), [](const auto& a, const auto& b) { return a.second != b.second ? a.second > b.second : a.first < b.first; });
        std::printf("  %s: %zu symbols; top:", cat.c_str(), v.size());
        for (std::size_t i = 0; i < v.size() && i < 12; ++i)
            std::printf(" %s=%llu", sym_str(v[i].first).c_str(), static_cast<u64>(v[i].second));
        std::printf("\n");
    }
    std::printf("excluded by name  (documented feed differences; not mismatches)\n");
    for (const auto& [cat, n] : excluded_) std::printf("  %-28s %llu\n", cat.c_str(), static_cast<u64>(n));
    auto show = [](const Mismatch& m) {
        std::printf("  %-16s %-8s %s (%lld) expected %s actual %s\n", m.category.c_str(), sym_str(m.symbol).c_str(),
                    et(m.t).c_str(), static_cast<i64>(m.t), m.expected.c_str(), m.actual.c_str());
    };
    if (!first_.empty()) {
        std::printf("first %zu mismatches:\n", first_.size());
        for (const auto& m : first_) show(m);
        std::printf("first mismatches per category:\n");
        for (const auto& [cat, v] : per_category_)
            for (const auto& m : v) show(m);
    }
    std::printf("wall_seconds      %.1f\n", secs);
    rusage ru{};
    getrusage(RUSAGE_SELF, &ru);
#ifdef __APPLE__
    const long long rss_kib = static_cast<long long>(ru.ru_maxrss) / 1024;  // bytes on macOS
#else
    const long long rss_kib = static_cast<long long>(ru.ru_maxrss);  // KiB on Linux
#endif
    std::printf("peak_rss_mib      %.1f\n", static_cast<double>(rss_kib) / 1024.0);
}

}  // namespace

int main(int argc, char** argv) {
    std::string dp, deep, tops;
    std::size_t max_symbols = 0;
    std::vector<std::uint64_t> only;
    std::vector<std::pair<std::uint64_t, std::int64_t>> lots;
    std::string lot_args;
    std::int64_t lookahead_ms = 1000;
    bool bad = false;
    for (int i = 1; i < argc; ++i) {
        const bool has = i + 1 < argc;
        if (std::strcmp(argv[i], "--deep-plus") == 0 && has) {
            dp = argv[++i];
        } else if (std::strcmp(argv[i], "--deep") == 0 && has) {
            deep = argv[++i];
        } else if (std::strcmp(argv[i], "--tops") == 0 && has) {
            tops = argv[++i];
        } else if (std::strcmp(argv[i], "--symbols") == 0 && has) {
            char* end = nullptr;
            max_symbols = std::strtoull(argv[++i], &end, 10);
            bad |= *end != 0 || max_symbols == 0;
        } else if (std::strcmp(argv[i], "--symbol") == 0 && has) {
            std::uint64_t sym = 0;
            bad |= !parse_symbol(argv[++i], sym);
            only.push_back(sym);
        } else if (std::strcmp(argv[i], "--round-lot") == 0 && has) {
            std::string a = argv[++i];
            const auto eq = a.find('=');
            std::uint64_t sym = 0;
            char* end = nullptr;
            const long long lot = eq == std::string::npos ? 0 : std::strtoll(a.c_str() + eq + 1, &end, 10);
            if (eq == std::string::npos || lot <= 0 || *end != 0 || !parse_symbol(a.substr(0, eq).c_str(), sym)) {
                bad = true;
            } else {
                lots.emplace_back(sym, lot);
                lot_args += " " + a;
            }
        } else if (std::strcmp(argv[i], "--lookahead-ms") == 0 && has) {
            char* end = nullptr;
            lookahead_ms = std::strtoll(argv[++i], &end, 10);
            bad |= *end != 0 || lookahead_ms < 0;
        } else {
            bad = true;
        }
    }
    if (bad || dp.empty() || deep.empty() || tops.empty()) {
        std::fprintf(stderr,
                     "usage: %s --deep-plus <f> --deep <f> --tops <f> [--symbols N] [--symbol S]... [--round-lot S=N]... "
                     "[--lookahead-ms M]\n"
                     "exit: 0 pass (preconditions held, 0 mismatches); 1 mismatches; 3 inconclusive (a validity\n"
                     "      precondition failed: gaps, tp_parse_failures or other_stream on any feed, DEEP+ ts\n"
                     "      regressions, open DEEP transitions, book anomalies, late checkpoints, or 0 DEEP or TOPS\n"
                     "      checks); 2 usage or I/O error\n",
                     argv[0]);
        return 2;
    }
    try {
        Oracle o(dp, deep, tops, max_symbols, lookahead_ms * 1'000'000);
        if (!only.empty()) o.only(only);
        for (const auto& [sym, lot] : lots) o.round_lot(sym, lot);
        if (!lot_args.empty()) std::printf("round lot overrides (operator-supplied):%s\n", lot_args.c_str());
        return o.run();
    } catch (const std::exception& e) {
        std::fprintf(stderr, "error: %s\n", e.what());
        return 2;
    }
}
