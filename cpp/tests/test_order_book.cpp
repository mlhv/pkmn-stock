#include <catch2/catch_test_macros.hpp>

#include <vector>

#include "pkmn_book/order_book.hpp"

using namespace pkmn::book;

TEST_CASE("order book: add/best/depth aggregate per level") {
    OrderBook b;
    CHECK(b.add(1, Side::Buy, 100, 10));
    CHECK(b.add(2, Side::Buy, 100, 5));
    CHECK(b.add(3, Side::Buy, 99, 7));
    auto best = b.best(Side::Buy);
    REQUIRE(best.has_value());
    CHECK(best->price == 100);
    CHECK(best->qty == 15);
    CHECK(best->orders == 2);
    std::vector<Level> d;
    b.depth(Side::Buy, 2, d);
    REQUIRE(d.size() == 2);
    CHECK(d[0].price == 100);
    CHECK(d[0].qty == 15);
    CHECK(d[0].orders == 2);
    CHECK(d[1].price == 99);
    CHECK(d[1].qty == 7);
    CHECK(d[1].orders == 1);
    CHECK(b.order_count() == 3);
    CHECK_FALSE(b.best(Side::Sell).has_value());
}

TEST_CASE("order book: duplicate add is rejected and counted") {
    OrderBook b;
    CHECK(b.add(1, Side::Buy, 100, 10));
    CHECK_FALSE(b.add(1, Side::Sell, 105, 3));
    CHECK(b.anomalies().duplicate_add == 1);
    CHECK(b.order_count() == 1);
    CHECK(b.qty_at(Side::Buy, 100) == 10);
    CHECK_FALSE(b.best(Side::Sell).has_value());
}

TEST_CASE("order book: add with non-positive qty is rejected") {
    OrderBook b;
    CHECK_FALSE(b.add(1, Side::Buy, 100, 0));
    CHECK_FALSE(b.add(2, Side::Buy, 100, -3));
    CHECK(b.order_count() == 0);
    CHECK_FALSE(b.best(Side::Buy).has_value());
}

TEST_CASE("order book: remove unknown id is counted, not fatal") {
    OrderBook b;
    CHECK_FALSE(b.remove(42));
    CHECK(b.anomalies().unknown_id == 1);
    b.add(1, Side::Buy, 100, 10);
    CHECK(b.remove(1));
    CHECK(b.order_count() == 0);
    CHECK_FALSE(b.best(Side::Buy).has_value());  // empty level erased
}

TEST_CASE("order book: unknown id on modify/execute is counted, book unchanged") {
    OrderBook b;
    b.add(1, Side::Buy, 100, 10);
    CHECK_FALSE(b.modify(9, 100, 5, true));
    CHECK(b.execute(9, 5) == 0);
    CHECK(b.anomalies().unknown_id == 2);
    CHECK(b.qty_at(Side::Buy, 100) == 10);
}

TEST_CASE("order book: execute keeps residual priority") {
    OrderBook b;
    b.add(1, Side::Sell, 101, 10);
    b.add(2, Side::Sell, 101, 10);
    CHECK(b.execute(1, 4) == 4);
    std::vector<Execution> out;
    CHECK(b.match(Side::Buy, 8, 101, out) == 8);
    REQUIRE(out.size() == 2);
    CHECK(out[0].resting_id == 1);
    CHECK(out[0].qty == 6);
    CHECK(out[0].price == 101);
    CHECK(out[1].resting_id == 2);
    CHECK(out[1].qty == 2);
}

TEST_CASE("order book: execute clamps and ignores trade price") {
    OrderBook b;
    b.add(1, Side::Sell, 101, 10);
    CHECK(b.execute(1, 25) == 10);
    CHECK(b.anomalies().exec_clamped == 1);
    CHECK(b.order_count() == 0);
    CHECK_FALSE(b.best(Side::Sell).has_value());
    CHECK(b.add(1, Side::Sell, 101, 1));  // id was forgotten
}

namespace {
// Which resting sell is first in the queue at 101? Probe by matching 1 lot.
OrderId front_of_101(OrderBook& b) {
    std::vector<Execution> out;
    b.match(Side::Buy, 1, 101, out);
    REQUIRE(out.size() == 1);
    return out[0].resting_id;
}

OrderBook& two_asks(OrderBook& b) {
    b.add(1, Side::Sell, 101, 10);
    b.add(2, Side::Sell, 101, 10);
    return b;
}
}  // namespace

TEST_CASE("order book: modify keep_priority shrinks in place") {
    OrderBook b;
    two_asks(b);
    CHECK(b.modify(1, 101, 6, true));
    CHECK(b.qty_at(Side::Sell, 101) == 16);
    CHECK(front_of_101(b) == 1);
}

TEST_CASE("order book: modify keep_priority grows in place") {
    OrderBook b;
    two_asks(b);
    CHECK(b.modify(1, 101, 12, true));
    CHECK(b.qty_at(Side::Sell, 101) == 22);
    CHECK(front_of_101(b) == 1);
}

TEST_CASE("order book: modify without keep_priority goes to the back") {
    OrderBook b;
    two_asks(b);
    CHECK(b.modify(1, 101, 6, false));
    CHECK(b.qty_at(Side::Sell, 101) == 16);
    CHECK(front_of_101(b) == 2);
}

TEST_CASE("order book: modify price change moves level; zero removes") {
    OrderBook b;
    two_asks(b);
    CHECK(b.modify(2, 102, 10, true));  // price change always loses priority
    CHECK(b.qty_at(Side::Sell, 101) == 10);
    CHECK(b.qty_at(Side::Sell, 102) == 10);
    CHECK(b.modify(2, 102, 0, true));
    CHECK(b.qty_at(Side::Sell, 102) == 0);
    CHECK(b.order_count() == 1);
    CHECK_FALSE(b.modify(2, 102, 5, true));  // removed
}

TEST_CASE("order book: match respects limit and walks levels") {
    OrderBook b;
    b.add(1, Side::Sell, 100, 3);
    b.add(2, Side::Sell, 101, 3);
    b.add(3, Side::Sell, 103, 3);
    std::vector<Execution> out;
    CHECK(b.match(Side::Buy, 10, 101, out) == 6);
    REQUIRE(out.size() == 2);
    CHECK(out[0].price == 100);
    CHECK(out[1].price == 101);
    CHECK(b.qty_at(Side::Sell, 103) == 3);
    CHECK(b.order_count() == 1);
    CHECK(b.best(Side::Sell)->price == 103);
}

TEST_CASE("order book: sell aggressor takes bids >= limit, appends to out") {
    OrderBook b;
    b.add(1, Side::Buy, 100, 5);
    b.add(2, Side::Buy, 98, 5);
    std::vector<Execution> out{{0, 0, 0}};
    CHECK(b.match(Side::Sell, 7, 99, out) == 5);
    REQUIRE(out.size() == 2);  // appended, not cleared
    CHECK(out[1].resting_id == 1);
    CHECK(b.qty_at(Side::Buy, 98) == 5);
}

TEST_CASE("order book: clear empties and forgets ids") {
    OrderBook b;
    b.add(1, Side::Buy, 100, 10);
    b.clear();
    CHECK(b.order_count() == 0);
    CHECK_FALSE(b.best(Side::Buy).has_value());
    CHECK(b.add(1, Side::Buy, 100, 10));
    CHECK(b.order_count() == 1);
}
