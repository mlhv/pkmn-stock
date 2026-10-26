// DEEP+ decoder tests. The byte strings are the worked examples printed in
// the IEX DEEP+ Specification v1.06 (October 8, 2026), pages 21-27, typed in
// field by field; the sell-side Add is the p.21 example with only the side
// byte changed, since the spec prints no sell example.

#include <catch2/catch_test_macros.hpp>

#include <atomic>
#include <cstdint>
#include <span>
#include <vector>

#include "md_fixtures.hpp"
#include "pkmn_md/deep_plus.hpp"
#include "pkmn_md/events.hpp"
#include "pkmn_md/iextp.hpp"

// Global operator new counter, defined in test_pcapng.cpp (same binary).
extern std::atomic<std::uint64_t> g_allocs;

using namespace pkmn::md;
using pkmn::book::Side;
using Bytes = std::vector<std::uint8_t>;

namespace {

const Bytes kTs = {0xb2, 0x8f, 0xa5, 0xa0, 0xab, 0x86, 0x6d, 0x14};  // 2016-08-23 15:32:04.912754610 ET
const Bytes kSym = {0x5a, 0x49, 0x45, 0x58, 0x54, 0x20, 0x20, 0x20};  // "ZIEXT   "
const Bytes kId = {0x96, 0x8f, 0x06, 0x00, 0x00, 0x00, 0x00, 0x00};   // 429974
const Bytes kSize = {0x64, 0x00, 0x00, 0x00};                          // 100
const Bytes kPrice = {0x24, 0x1d, 0x0f, 0x00, 0x00, 0x00, 0x00, 0x00};  // 990500 = $99.05
const Bytes kTradeId = {0x96, 0x8f, 0x02, 0x00, 0x00, 0x00, 0x00, 0x00};  // 167830

constexpr std::int64_t kTsNs = 1'471'980'724'912'754'610LL;
constexpr std::uint64_t kZiext = 0x202020545845495aULL;  // "ZIEXT   " loaded little-endian

Bytes cat(std::initializer_list<Bytes> parts) {
    Bytes out;
    for (const Bytes& p : parts) out.insert(out.end(), p.begin(), p.end());
    return out;
}

// p.21 Add Order: a, side '8' (Buy).
Bytes add_example() { return cat({{0x61, 0x38}, kTs, kSym, kId, kSize, kPrice}); }
// p.22 Order Modify: M, flags 00 (reset priority).
Bytes modify_example() { return cat({{0x4D, 0x00}, kTs, kSym, kId, kSize, kPrice}); }
// p.23 Order Delete: R, reserved 00.
Bytes delete_example() { return cat({{0x52, 0x00}, kTs, kSym, kId}); }
// p.24 Order Executed: L, sale condition flags 00, plus trade id.
Bytes executed_example() { return cat({{0x4C, 0x00}, kTs, kSym, kId, kSize, kPrice, kTradeId}); }
// p.25 Trade: T, flags 00, ts, sym, size, price, trade id.
Bytes trade_example() { return cat({{0x54, 0x00}, kTs, kSym, kSize, kPrice, kTradeId}); }
// p.27 Clear Book: C, reserved 00.
Bytes clear_example() { return cat({{0x43, 0x00}, kTs, kSym}); }

bool decode(const Bytes& m, BookEvent& ev, DecodeStats& st) {
    return decode_deep_plus(std::span<const std::uint8_t>(m), ev, st);
}

}  // namespace

TEST_CASE("deep_plus: spec examples have the spec lengths") {
    CHECK(add_example().size() == 38);
    CHECK(modify_example().size() == 38);
    CHECK(delete_example().size() == 26);
    CHECK(executed_example().size() == 46);
    CHECK(trade_example().size() == 38);
    CHECK(clear_example().size() == 18);
}

TEST_CASE("deep_plus: add order (spec p.21)") {
    BookEvent ev{};
    DecodeStats st{};
    REQUIRE(decode(add_example(), ev, st));
    CHECK(ev.kind == BookEvent::Kind::Add);
    CHECK(ev.side == Side::Buy);
    CHECK(ev.order_id == 429974);
    CHECK(ev.qty == 100);
    CHECK(ev.price == 990500);
    CHECK(ev.ts_ns == kTsNs);
    CHECK(ev.symbol == kZiext);
    CHECK(st.messages == 1);
    CHECK(st.by_type[0x61] == 1);
    CHECK(st.unknown_type == 0);
    CHECK(st.short_message == 0);
}

TEST_CASE("deep_plus: add order sell side") {
    Bytes m = add_example();
    m[1] = 0x35;  // '5' = Sell
    BookEvent ev{};
    DecodeStats st{};
    REQUIRE(decode(m, ev, st));
    CHECK(ev.kind == BookEvent::Kind::Add);
    CHECK(ev.side == Side::Sell);
}

TEST_CASE("deep_plus: add order with an invalid side is rejected and counted") {
    Bytes m = add_example();
    m[1] = 0x31;
    BookEvent ev{};
    DecodeStats st{};
    CHECK_FALSE(decode(m, ev, st));
    CHECK(st.bad_side == 1);
    CHECK(st.by_type[0x61] == 1);
}

TEST_CASE("deep_plus: order modify, reset priority (spec p.22)") {
    BookEvent ev{};
    DecodeStats st{};
    REQUIRE(decode(modify_example(), ev, st));
    CHECK(ev.kind == BookEvent::Kind::Modify);
    CHECK_FALSE(ev.keep_priority);
    CHECK(ev.order_id == 429974);
    CHECK(ev.qty == 100);
    CHECK(ev.price == 990500);
    CHECK(ev.ts_ns == kTsNs);
    CHECK(ev.symbol == kZiext);
}

TEST_CASE("deep_plus: order modify, maintain priority is flag bit 0") {
    Bytes m = modify_example();
    m[1] = 0x01;
    BookEvent ev{};
    DecodeStats st{};
    REQUIRE(decode(m, ev, st));
    CHECK(ev.keep_priority);
    m[1] = 0xFE;  // bits 1-7 are reserved and must not read as priority
    REQUIRE(decode(m, ev, st));
    CHECK_FALSE(ev.keep_priority);
}

TEST_CASE("deep_plus: order delete (spec p.23)") {
    BookEvent ev{};
    DecodeStats st{};
    REQUIRE(decode(delete_example(), ev, st));
    CHECK(ev.kind == BookEvent::Kind::Delete);
    CHECK(ev.order_id == 429974);
    CHECK(ev.ts_ns == kTsNs);
    CHECK(ev.symbol == kZiext);
}

TEST_CASE("deep_plus: order executed (spec p.24)") {
    BookEvent ev{};
    DecodeStats st{};
    REQUIRE(decode(executed_example(), ev, st));
    CHECK(ev.kind == BookEvent::Kind::Execute);
    CHECK(ev.order_id == 429974);
    CHECK(ev.qty == 100);
    CHECK(ev.price == 990500);
    CHECK(ev.ts_ns == kTsNs);
}

TEST_CASE("deep_plus: clear book (spec p.27)") {
    BookEvent ev{};
    DecodeStats st{};
    REQUIRE(decode(clear_example(), ev, st));
    CHECK(ev.kind == BookEvent::Kind::Clear);
    CHECK(ev.symbol == kZiext);
    CHECK(ev.ts_ns == kTsNs);
}

TEST_CASE("deep_plus: trade (spec p.25) is counted but not book-affecting") {
    BookEvent ev{};
    DecodeStats st{};
    CHECK_FALSE(decode(trade_example(), ev, st));
    CHECK(st.messages == 1);
    CHECK(st.by_type[0x54] == 1);
    CHECK(st.unknown_type == 0);
    CHECK(st.short_message == 0);
}

TEST_CASE("deep_plus: unknown type counted and skipped") {
    Bytes m = add_example();
    m[0] = 0x7A;
    BookEvent ev{};
    DecodeStats st{};
    CHECK_FALSE(decode(m, ev, st));
    CHECK(st.messages == 1);
    CHECK(st.unknown_type == 1);
    CHECK(st.by_type[0x7A] == 1);
    CHECK(st.short_message == 0);
}

TEST_CASE("deep_plus: longer message accepted") {
    Bytes m = add_example();
    m.insert(m.end(), {1, 2, 3, 4, 5, 6});
    BookEvent ev{};
    DecodeStats st{};
    REQUIRE(decode(m, ev, st));
    CHECK(ev.kind == BookEvent::Kind::Add);
    CHECK(ev.order_id == 429974);
    CHECK(ev.price == 990500);
    CHECK(st.short_message == 0);
}

TEST_CASE("deep_plus: short message counted") {
    Bytes m = add_example();
    m.resize(30);
    BookEvent ev{};
    DecodeStats st{};
    CHECK_FALSE(decode(m, ev, st));
    CHECK(st.short_message == 1);
    CHECK(st.by_type[0x61] == 1);

    Bytes t = trade_example();  // non-book types are length-checked too
    t.resize(37);
    CHECK_FALSE(decode(t, ev, st));
    CHECK(st.short_message == 2);

    CHECK_FALSE(decode(Bytes{}, ev, st));  // an empty message has no type byte
    CHECK(st.short_message == 3);
    CHECK(st.messages == 3);
}

TEST_CASE("deep_plus: framing plus decoding does not allocate") {
    const md_fixtures::Bytes seg = md_fixtures::tp_segment(
        kDeepPlus, 1, {add_example(), modify_example(), executed_example(), delete_example(), clear_example(),
                       trade_example()});
    BookEvent ev{};
    DecodeStats st{};
    TpHeader hdr{};
    std::uint64_t applied = 0;
    const std::uint64_t before = g_allocs.load();
    for (int rep = 0; rep < 1000; ++rep) {
        for_each_message(std::span<const std::uint8_t>(seg), hdr, [&](std::span<const std::uint8_t> m) {
            if (decode_deep_plus(m, ev, st)) ++applied;
        });
    }
    const std::uint64_t after = g_allocs.load();
    CHECK(after == before);
    CHECK(applied == 5000);
    CHECK(st.messages == 6000);
}
