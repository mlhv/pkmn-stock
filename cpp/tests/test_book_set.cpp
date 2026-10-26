// BookSet: one OrderBook per symbol, routed by BookEvent::symbol.

#include <catch2/catch_test_macros.hpp>

#include <cstdint>

#include "pkmn_md/book_set.hpp"
#include "pkmn_md/events.hpp"

using namespace pkmn::md;
using pkmn::book::Side;

namespace {

constexpr std::uint64_t kAaa = 0x2020202020414141ULL;  // "AAA     "
constexpr std::uint64_t kBbb = 0x2020202020424242ULL;  // "BBB     "

BookEvent add(std::uint64_t sym, std::uint64_t id, Side side, std::int64_t price, std::int64_t qty) {
    return BookEvent{BookEvent::Kind::Add, side, false, sym, id, price, qty, 1};
}
BookEvent modify(std::uint64_t sym, std::uint64_t id, std::int64_t price, std::int64_t qty, bool keep) {
    return BookEvent{BookEvent::Kind::Modify, Side::Buy, keep, sym, id, price, qty, 2};
}
BookEvent del(std::uint64_t sym, std::uint64_t id) {
    return BookEvent{BookEvent::Kind::Delete, Side::Buy, false, sym, id, 0, 0, 3};
}
BookEvent exec(std::uint64_t sym, std::uint64_t id, std::int64_t qty, std::int64_t price) {
    return BookEvent{BookEvent::Kind::Execute, Side::Buy, false, sym, id, price, qty, 4};
}
BookEvent clear(std::uint64_t sym) { return BookEvent{BookEvent::Kind::Clear, Side::Buy, false, sym, 0, 0, 0, 5}; }

}  // namespace

TEST_CASE("book_set: routes by symbol") {
    BookSet bs;
    CHECK(bs.find(kAaa) == nullptr);
    bs.apply(add(kAaa, 7, Side::Buy, 1000, 100));
    bs.apply(add(kBbb, 7, Side::Sell, 2000, 50));  // same id, other symbol: independent
    const auto* a = bs.find(kAaa);
    const auto* b = bs.find(kBbb);
    REQUIRE(a != nullptr);
    REQUIRE(b != nullptr);
    CHECK(a->qty_at(Side::Buy, 1000) == 100);
    CHECK(a->qty_at(Side::Sell, 2000) == 0);
    CHECK(b->qty_at(Side::Sell, 2000) == 50);
    CHECK(bs.total_anomalies().duplicate_add == 0);

    bs.apply(modify(kAaa, 7, 1000, 60, true));
    bs.apply(exec(kBbb, 7, 20, 2000));
    CHECK(a->qty_at(Side::Buy, 1000) == 60);
    CHECK(b->qty_at(Side::Sell, 2000) == 30);

    bs.apply(del(kAaa, 7));
    CHECK(a->order_count() == 0);
    CHECK(b->order_count() == 1);
    CHECK(bs.symbols() == 2);
    CHECK(bs.order_count() == 1);
}

TEST_CASE("book_set: unknown-id modify/delete/execute counted, book intact") {
    BookSet bs;
    bs.apply(add(kAaa, 1, Side::Buy, 1000, 100));
    bs.apply(add(kAaa, 2, Side::Sell, 1010, 200));
    bs.apply(modify(kAaa, 99, 1005, 10, false));
    bs.apply(del(kAaa, 98));
    bs.apply(exec(kAaa, 97, 5, 1000));
    // An unknown id on a symbol never seen creates an empty book and counts there.
    bs.apply(del(kBbb, 1));
    const auto an = bs.total_anomalies();
    CHECK(an.unknown_id == 4);
    CHECK(an.duplicate_add == 0);
    CHECK(an.exec_clamped == 0);
    const auto* a = bs.find(kAaa);
    REQUIRE(a != nullptr);
    CHECK(a->order_count() == 2);
    CHECK(a->qty_at(Side::Buy, 1000) == 100);
    CHECK(a->qty_at(Side::Sell, 1010) == 200);
    CHECK(a->qty_at(Side::Buy, 1005) == 0);
    REQUIRE(bs.find(kBbb) != nullptr);
    CHECK(bs.find(kBbb)->order_count() == 0);
}

TEST_CASE("book_set: clear empties one symbol only and forgets its ids") {
    BookSet bs;
    bs.apply(add(kAaa, 1, Side::Buy, 1000, 100));
    bs.apply(add(kAaa, 2, Side::Sell, 1010, 200));
    bs.apply(add(kBbb, 1, Side::Buy, 500, 10));
    bs.apply(clear(kAaa));
    const auto* a = bs.find(kAaa);
    const auto* b = bs.find(kBbb);
    REQUIRE(a != nullptr);
    CHECK(a->order_count() == 0);
    CHECK_FALSE(a->best(Side::Buy).has_value());
    CHECK_FALSE(a->best(Side::Sell).has_value());
    CHECK(b->order_count() == 1);
    CHECK(b->qty_at(Side::Buy, 500) == 10);

    // Forgotten: re-adding id 1 is not a duplicate; deleting id 2 is unknown.
    bs.apply(add(kAaa, 1, Side::Sell, 1020, 5));
    CHECK(bs.total_anomalies().duplicate_add == 0);
    bs.apply(del(kAaa, 2));
    CHECK(bs.total_anomalies().unknown_id == 1);
    CHECK(a->qty_at(Side::Sell, 1020) == 5);

    // Clear on a symbol never seen is harmless.
    bs.apply(clear(0x2020202020434343ULL));
    CHECK(bs.order_count() == 2);
}

TEST_CASE("book_set: clear_all empties every book, keeps anomaly counts") {
    BookSet bs;
    bs.apply(add(kAaa, 1, Side::Buy, 1000, 100));
    bs.apply(add(kBbb, 1, Side::Buy, 500, 10));
    bs.apply(add(kBbb, 1, Side::Buy, 500, 10));  // duplicate_add
    bs.clear_all();
    CHECK(bs.clear_alls() == 1);
    CHECK(bs.order_count() == 0);
    CHECK(bs.find(kAaa)->order_count() == 0);
    CHECK(bs.total_anomalies().duplicate_add == 1);
    bs.apply(add(kAaa, 1, Side::Buy, 1000, 100));  // the new epoch may reuse ids
    CHECK(bs.total_anomalies().duplicate_add == 1);
    CHECK(bs.order_count() == 1);
}
