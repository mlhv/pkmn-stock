// DEEP Price Level Update and TOPS Quote Update decoder tests. The byte
// strings are the worked examples printed in the IEX DEEP Specification v1.08
// (October 19, 2021) p.18 and the IEX TOPS Specification v1.66 (October 19,
// 2021) p.17, typed in field by field. The sell-side and in-transition
// variants are the p.18 example with only the type or flags byte changed,
// since the spec prints no such example.

#include <catch2/catch_test_macros.hpp>

#include <cstdint>
#include <initializer_list>
#include <span>
#include <vector>

#include "pkmn_md/deep.hpp"

using namespace pkmn::md;
using pkmn::book::Side;
using Bytes = std::vector<std::uint8_t>;

namespace {

const Bytes kTs = {0xac, 0x63, 0xc0, 0x20, 0x96, 0x86, 0x6d, 0x14};  // 2016-08-23 15:30:32.572715948 ET
const Bytes kSym = {0x5a, 0x49, 0x45, 0x58, 0x54, 0x20, 0x20, 0x20};  // "ZIEXT   "
constexpr std::int64_t kTsNs = 1'471'980'632'572'715'948LL;
constexpr std::uint64_t kZiext = 0x202020545845495aULL;

Bytes cat(std::initializer_list<Bytes> parts) {
    Bytes out;
    for (const Bytes& p : parts) out.insert(out.end(), p.begin(), p.end());
    return out;
}

// DEEP v1.08 p.18: '8' buy side, event flags 01, size 9,700, price $99.05.
Bytes plu_example() {
    return cat({{0x38, 0x01}, kTs, kSym, {0xe4, 0x25, 0x00, 0x00}, {0x24, 0x1d, 0x0f, 0x00, 0x00, 0x00, 0x00, 0x00}});
}

// TOPS v1.66 p.17: flags 00, bid 9,700 @ $99.05, ask 1,000 @ $99.07.
Bytes quote_example() {
    return cat({{0x51, 0x00},
                kTs,
                {0x5A, 0x49, 0x45, 0x58, 0x54, 0x20, 0x20, 0x20},
                {0xe4, 0x25, 0x00, 0x00},
                {0x24, 0x1d, 0x0f, 0x00, 0x00, 0x00, 0x00, 0x00},
                {0xec, 0x1d, 0x0f, 0x00, 0x00, 0x00, 0x00, 0x00},
                {0xe8, 0x03, 0x00, 0x00}});
}

std::span<const std::uint8_t> sp(const Bytes& b) { return std::span<const std::uint8_t>(b); }

}  // namespace

TEST_CASE("deep: price level update (DEEP spec p.18)") {
    const Bytes m = plu_example();
    REQUIRE(m.size() == 30);
    LevelUpdate u{};
    REQUIRE(decode_deep_level(sp(m), u));
    CHECK(u.symbol == kZiext);
    CHECK(u.side == Side::Buy);
    CHECK(u.price == 990500);
    CHECK(u.size == 9700);
    CHECK(u.ts_ns == kTsNs);
    CHECK(u.event_complete);
}

TEST_CASE("deep: sell side and in-transition flag") {
    Bytes m = plu_example();
    m[0] = 0x35;  // '5' = sell side
    m[1] = 0x00;  // order book in transition
    LevelUpdate u{};
    REQUIRE(decode_deep_level(sp(m), u));
    CHECK(u.side == Side::Sell);
    CHECK_FALSE(u.event_complete);
}

TEST_CASE("deep: other types, short messages rejected; longer accepted") {
    LevelUpdate u{};
    Bytes m = plu_example();
    m[0] = 0x54;  // 'T' trade report
    CHECK_FALSE(decode_deep_level(sp(m), u));
    m = plu_example();
    m.pop_back();
    CHECK_FALSE(decode_deep_level(sp(m), u));
    CHECK_FALSE(decode_deep_level(std::span<const std::uint8_t>(), u));
    m = plu_example();
    m.push_back(0xff);
    REQUIRE(decode_deep_level(sp(m), u));
    CHECK(u.size == 9700);
}

TEST_CASE("tops: quote update (TOPS spec p.17)") {
    const Bytes m = quote_example();
    REQUIRE(m.size() == 42);
    TopQuote q{};
    REQUIRE(decode_tops_quote(sp(m), q));
    CHECK(q.symbol == kZiext);
    CHECK(q.bid_size == 9700);
    CHECK(q.bid == 990500);
    CHECK(q.ask == 990700);
    CHECK(q.ask_size == 1000);
    CHECK(q.ts_ns == kTsNs);
    CHECK(q.flags == 0);
}

TEST_CASE("tops: other types, short messages rejected; longer accepted") {
    TopQuote q{};
    Bytes m = quote_example();
    m[0] = 0x54;
    CHECK_FALSE(decode_tops_quote(sp(m), q));
    m = quote_example();
    m.pop_back();
    CHECK_FALSE(decode_tops_quote(sp(m), q));
    m = quote_example();
    m.insert(m.end(), {1, 2, 3});
    REQUIRE(decode_tops_quote(sp(m), q));
    CHECK(q.ask_size == 1000);
}
