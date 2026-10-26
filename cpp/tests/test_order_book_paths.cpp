// Regression tests for the Task 6 OrderBook internals (pooled arena,
// open-addressing id table, contiguous price levels), through the public API
// only and without any IEX data: table growth, backward-shift delete across the
// table's wrap-around, the binary-search branch of the level search, slot reuse
// after clear(), and a seeded differential run against a naive reference book.

#include <catch2/catch_test_macros.hpp>

#include <algorithm>
#include <cstdint>
#include <deque>
#include <map>
#include <random>
#include <vector>

#include "pkmn_book/order_book.hpp"

using namespace pkmn::book;

namespace {

// Mirrors OrderBook::home() for the initial 16-entry table (shift 60). Only
// used to pick ids that collide at the end of the table; if the hash changes,
// the wrap test below still passes but no longer exercises the wrap, so keep
// the two in step.
std::size_t home16(OrderId id) { return static_cast<std::size_t>((id * 0x9E3779B97F4A7C15ull) >> 60); }

std::vector<OrderId> ids_with_home16(std::size_t home, std::size_t n) {
    std::vector<OrderId> out;
    for (OrderId id = 1; out.size() < n; ++id)
        if (home16(id) == home) out.push_back(id);
    return out;
}

// Naive reference: std::map levels, std::deque FIFO, linear id search.
class RefBook {
  public:
    struct O {
        OrderId id;
        Qty qty;
    };
    std::map<Price, std::deque<O>> bids, asks;
    Anomalies an;

    std::map<Price, std::deque<O>>& lad(Side s) { return s == Side::Buy ? bids : asks; }

    bool locate(OrderId id, Side& side, Price& px, std::size_t& pos) {
        for (Side s : {Side::Buy, Side::Sell})
            for (auto& [p, q] : lad(s))
                for (std::size_t i = 0; i < q.size(); ++i)
                    if (q[i].id == id) {
                        side = s;
                        px = p;
                        pos = i;
                        return true;
                    }
        return false;
    }
    void drop(Side s, Price p, std::size_t pos) {
        auto& q = lad(s)[p];
        q.erase(q.begin() + static_cast<std::ptrdiff_t>(pos));
        if (q.empty()) lad(s).erase(p);
    }
    bool add(OrderId id, Side s, Price p, Qty q) {
        if (q <= 0) return false;
        Side s2;
        Price p2;
        std::size_t i;
        if (locate(id, s2, p2, i)) {
            ++an.duplicate_add;
            return false;
        }
        lad(s)[p].push_back(O{id, q});
        return true;
    }
    bool remove(OrderId id) {
        Side s;
        Price p;
        std::size_t i;
        if (!locate(id, s, p, i)) {
            ++an.unknown_id;
            return false;
        }
        drop(s, p, i);
        return true;
    }
    bool modify(OrderId id, Price p, Qty q, bool keep) {
        Side s;
        Price p0;
        std::size_t i;
        if (!locate(id, s, p0, i)) {
            ++an.unknown_id;
            return false;
        }
        if (q <= 0) {
            drop(s, p0, i);
        } else if (keep && p == p0) {
            lad(s)[p0][i].qty = q;
        } else {
            drop(s, p0, i);
            lad(s)[p].push_back(O{id, q});
        }
        return true;
    }
    Qty execute(OrderId id, Qty q) {
        Side s;
        Price p;
        std::size_t i;
        if (!locate(id, s, p, i)) {
            ++an.unknown_id;
            return 0;
        }
        if (q <= 0) return 0;
        O& o = lad(s)[p][i];
        if (q > o.qty) {
            q = o.qty;
            ++an.exec_clamped;
        }
        o.qty -= q;
        if (o.qty == 0) drop(s, p, i);
        return q;
    }
    Qty match(Side aggr, Qty qty, Price limit, std::vector<Execution>& out) {
        auto& l = lad(aggr == Side::Buy ? Side::Sell : Side::Buy);
        Qty filled = 0;
        while (filled < qty && !l.empty()) {
            auto it = aggr == Side::Buy ? l.begin() : std::prev(l.end());
            if (aggr == Side::Buy ? it->first > limit : it->first < limit) break;
            auto& q = it->second;
            while (filled < qty && !q.empty()) {
                const Qty take = std::min(qty - filled, q.front().qty);
                out.push_back(Execution{q.front().id, it->first, take});
                filled += take;
                q.front().qty -= take;
                if (q.front().qty == 0) q.pop_front();
            }
            if (q.empty()) l.erase(it);
        }
        return filled;
    }
    std::vector<Level> depth(Side s) {
        std::vector<Level> out;
        auto emit = [&](const auto& kv) {
            Qty t = 0;
            for (const O& o : kv.second) t += o.qty;
            out.push_back(Level{kv.first, t, static_cast<std::uint32_t>(kv.second.size())});
        };
        if (s == Side::Sell)
            for (const auto& kv : asks) emit(kv);
        else
            for (auto it = bids.rbegin(); it != bids.rend(); ++it) emit(*it);
        return out;
    }
    std::size_t count() const {
        std::size_t n = 0;
        for (const auto& kv : bids) n += kv.second.size();
        for (const auto& kv : asks) n += kv.second.size();
        return n;
    }
    void clear() {
        bids.clear();
        asks.clear();
    }
};

void require_same_levels(const std::vector<Level>& a, const std::vector<Level>& b) {
    REQUIRE(a.size() == b.size());
    for (std::size_t i = 0; i < a.size(); ++i) {
        REQUIRE(a[i].price == b[i].price);
        REQUIRE(a[i].qty == b[i].qty);
        REQUIRE(a[i].orders == b[i].orders);
    }
}

}  // namespace

TEST_CASE("order book paths: id table grows well past its initial size") {
    OrderBook b;
    constexpr OrderId kN = 5000;  // initial table is 16 entries; this forces ~9 doublings
    for (OrderId id = 1; id <= kN; ++id) REQUIRE(b.add(id * 7919, Side::Buy, 100 + id % 3, 1));
    CHECK(b.order_count() == kN);
    CHECK(b.qty_at(Side::Buy, 100) + b.qty_at(Side::Buy, 101) + b.qty_at(Side::Buy, 102) == static_cast<Qty>(kN));
    for (OrderId id = 1; id <= kN; ++id) REQUIRE_FALSE(b.add(id * 7919, Side::Sell, 200, 1));  // all still found
    CHECK(b.anomalies().duplicate_add == kN);
    for (OrderId id = 1; id <= kN; id += 2) REQUIRE(b.remove(id * 7919));
    for (OrderId id = 1; id <= kN; ++id) REQUIRE(b.remove(id * 7919) == (id % 2 == 0));
    CHECK(b.order_count() == 0);
    CHECK_FALSE(b.best(Side::Buy).has_value());
}

TEST_CASE("order book paths: backward-shift delete across the table's wrap-around") {
    // Four ids homed at slot 15 occupy 15, 0, 1, 2; two ids homed at 0 land at
    // 3, 4. Six entries keep the 16-entry table from growing. Every removal
    // order must leave every other id findable.
    const std::vector<OrderId> at15 = ids_with_home16(15, 4);
    const std::vector<OrderId> at0 = ids_with_home16(0, 2);
    std::vector<OrderId> ids = at15;
    ids.insert(ids.end(), at0.begin(), at0.end());
    std::vector<std::size_t> order{0, 1, 2, 3, 4, 5};
    int perms = 0;
    do {
        OrderBook b;
        for (OrderId id : ids) REQUIRE(b.add(id, Side::Sell, 50, 1));
        std::vector<bool> live(ids.size(), true);
        for (std::size_t k : order) {
            REQUIRE(b.remove(ids[k]));
            live[k] = false;
            for (std::size_t j = 0; j < ids.size(); ++j) {
                // modify to the same size is a pure lookup: true iff still present.
                REQUIRE(b.modify(ids[j], 50, 1, true) == live[j]);
            }
        }
        REQUIRE(b.order_count() == 0);
        ++perms;
    } while (std::next_permutation(order.begin(), order.end()));
    CHECK(perms == 720);
}

TEST_CASE("order book paths: level search past the linear window (> 8 levels per side)") {
    OrderBook b;
    constexpr int kLevels = 40;
    // Insert in a scrambled order so new levels land in the middle as well as at the ends.
    std::vector<int> px(kLevels);
    for (int i = 0; i < kLevels; ++i) px[i] = (i * 17) % kLevels;
    OrderId id = 1;
    for (int p : px) {
        REQUIRE(b.add(id++, Side::Buy, 1000 + p, p + 1));
        REQUIRE(b.add(id++, Side::Sell, 2000 + p, p + 1));
    }
    for (int p = 0; p < kLevels; ++p) {
        REQUIRE(b.qty_at(Side::Buy, 1000 + p) == p + 1);
        REQUIRE(b.qty_at(Side::Sell, 2000 + p) == p + 1);
    }
    CHECK(b.qty_at(Side::Buy, 999) == 0);
    CHECK(b.qty_at(Side::Buy, 1000 + kLevels) == 0);
    CHECK(b.qty_at(Side::Sell, 1999) == 0);
    CHECK(b.qty_at(Side::Sell, 2000 + kLevels) == 0);
    // Deep levels (far from the touch): grow one in place, remove another.
    // Ids: buy at price p is 2*k+1 where px[k] == p.
    auto buy_id = [&](int p) { return static_cast<OrderId>(2 * (std::find(px.begin(), px.end(), p) - px.begin()) + 1); };
    auto sell_id = [&](int p) { return buy_id(p) + 1; };
    REQUIRE(b.modify(buy_id(3), 1003, 100, true));
    REQUIRE(b.remove(buy_id(5)));
    REQUIRE(b.execute(sell_id(30), 1) == 1);
    REQUIRE(b.add(id++, Side::Buy, 1003, 5));
    CHECK(b.qty_at(Side::Buy, 1003) == 105);
    CHECK(b.qty_at(Side::Buy, 1005) == 0);
    CHECK(b.qty_at(Side::Sell, 2030) == 30);
    std::vector<Level> d;
    b.depth(Side::Buy, 100, d);
    REQUIRE(d.size() == kLevels - 1);
    for (std::size_t i = 1; i < d.size(); ++i) REQUIRE(d[i - 1].price > d[i].price);
    b.depth(Side::Sell, 100, d);
    REQUIRE(d.size() == kLevels);
    for (std::size_t i = 1; i < d.size(); ++i) REQUIRE(d[i - 1].price < d[i].price);
    CHECK(b.best(Side::Buy)->price == 1000 + kLevels - 1);
    CHECK(b.best(Side::Sell)->price == 2000);
}

TEST_CASE("order book paths: slots are reused after clear and after removal") {
    OrderBook b;
    for (int round = 0; round < 3; ++round) {
        for (OrderId id = 1; id <= 100; ++id) REQUIRE(b.add(id, Side::Buy, 100 + static_cast<Price>(id % 10), 2));
        REQUIRE(b.order_count() == 100);
        // Free and re-take half the slots: FIFO order must follow the new adds.
        for (OrderId id = 2; id <= 100; id += 2) REQUIRE(b.remove(id));
        for (OrderId id = 1001; id <= 1050; ++id) REQUIRE(b.add(id, Side::Buy, 100, 1));
        REQUIRE(b.qty_at(Side::Buy, 100) == 50);  // the 10 originals at 100 were all even ids
        std::vector<Execution> out;
        REQUIRE(b.match(Side::Sell, 3, 100, out) == 3);
        REQUIRE(out.size() == 2);
        CHECK(out[0].resting_id == 9);  // best bid 109 holds ids 9, 19, ..., 99 (2 each): takes 9 then 19
        CHECK(out[1].resting_id == 19);
        b.clear();
        REQUIRE(b.order_count() == 0);
        REQUIRE_FALSE(b.best(Side::Buy).has_value());
        REQUIRE_FALSE(b.remove(1));
    }
}

TEST_CASE("order book paths: seeded differential run against a naive reference book") {
    for (std::uint64_t seed : {1u, 2u, 3u}) {
        std::mt19937_64 r(seed);
        OrderBook b;
        RefBook ref;
        std::vector<Execution> e1, e2;
        std::vector<Level> d1;
        const OrderId max_id = seed == 2 ? 200 : 600;
        for (int step = 0; step < 60000; ++step) {
            const unsigned op = static_cast<unsigned>(r() % 100);
            const OrderId id = r() % max_id;
            const Side s = (r() & 1) ? Side::Buy : Side::Sell;
            const Price p = 1000 + static_cast<Price>(r() % 40) - 20;
            const Qty q = static_cast<Qty>(r() % 30) - 2;
            if (op < 45) {
                REQUIRE(b.add(id, s, p, q) == ref.add(id, s, p, q));
            } else if (op < 63) {
                REQUIRE(b.remove(id) == ref.remove(id));
            } else if (op < 78) {
                const bool keep = r() & 1;
                REQUIRE(b.modify(id, p, q, keep) == ref.modify(id, p, q, keep));
            } else if (op < 92) {
                REQUIRE(b.execute(id, q) == ref.execute(id, q));
            } else if (op < 99) {
                e1.clear();
                e2.clear();
                REQUIRE(b.match(s, q, p, e1) == ref.match(s, q, p, e2));
                REQUIRE(e1.size() == e2.size());
                for (std::size_t i = 0; i < e1.size(); ++i) {
                    REQUIRE(e1[i].resting_id == e2[i].resting_id);
                    REQUIRE(e1[i].price == e2[i].price);
                    REQUIRE(e1[i].qty == e2[i].qty);
                }
            } else {
                b.clear();
                ref.clear();
            }
            REQUIRE(b.order_count() == ref.count());
            if (step % 16 == 0) {
                for (Side side : {Side::Buy, Side::Sell}) {
                    b.depth(side, 1000, d1);
                    require_same_levels(d1, ref.depth(side));
                }
            }
        }
        REQUIRE(b.anomalies().unknown_id == ref.an.unknown_id);
        REQUIRE(b.anomalies().duplicate_add == ref.an.duplicate_add);
        REQUIRE(b.anomalies().exec_clamped == ref.an.exec_clamped);
    }
}
