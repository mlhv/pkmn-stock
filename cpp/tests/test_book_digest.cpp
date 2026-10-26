// Book digest: canonical, order-sensitive, symbol-order-independent; and the
// per-epoch reference digests over a synthetic DEEP+ capture.

#include <catch2/catch_test_macros.hpp>

#include <cstdint>
#include <cstdio>
#include <string>
#include <vector>

#include "md_fixtures.hpp"
#include "pkmn_md/book_digest.hpp"
#include "pkmn_md/iextp.hpp"

using namespace md_fixtures;
using namespace pkmn::md;
using pkmn::book::Side;

namespace {

constexpr std::int64_t kBase = 1'734'964'524'000'000'000LL;

std::uint64_t sym64(const char* s) {
    std::string v(s);
    v.resize(8, ' ');
    std::uint64_t r = 0;
    for (int i = 0; i < 8; ++i) r |= static_cast<std::uint64_t>(static_cast<unsigned char>(v[i])) << (8 * i);
    return r;
}

BookEvent add(std::uint64_t sym, std::uint64_t id, Side side, std::int64_t price, std::int64_t qty) {
    return BookEvent{BookEvent::Kind::Add, side, false, sym, id, price, qty, 1};
}

std::string str(const Bytes& b) { return std::string(b.begin(), b.end()); }

}  // namespace

TEST_CASE("digest: empty and cleared books digest equal") {
    BookSet empty, cleared;
    cleared.apply(add(sym64("AAA"), 1, Side::Buy, 1000, 10));
    cleared.clear_all();
    CHECK(digest_books(empty) == digest_books(cleared));
}

TEST_CASE("digest: sensitive to price, qty, id, side and priority") {
    const auto A = sym64("AAA");
    auto build = [&](std::uint64_t id1, Side s1, std::int64_t px1, std::int64_t q1, bool swap_order) {
        BookSet bs;
        const BookEvent first = add(A, id1, s1, px1, q1);
        const BookEvent second = add(A, 2, Side::Buy, 1000, 20);
        if (swap_order) {
            bs.apply(second);
            bs.apply(first);
        } else {
            bs.apply(first);
            bs.apply(second);
        }
        return digest_books(bs);
    };
    const auto base = build(1, Side::Buy, 1000, 10, false);
    CHECK(build(1, Side::Buy, 1000, 10, true) != base);   // same level, opposite priority
    CHECK(build(1, Side::Buy, 1001, 10, false) != base);  // price
    CHECK(build(1, Side::Buy, 1000, 11, false) != base);  // qty
    CHECK(build(3, Side::Buy, 1000, 10, false) != base);  // id
    CHECK(build(1, Side::Sell, 1000, 10, false) != base); // side
}

TEST_CASE("digest: symbol iteration order does not matter") {
    BookSet x, y;
    for (const char* s : {"AAA", "BBB", "CCC"}) x.apply(add(sym64(s), 1, Side::Buy, 1000, 10));
    for (const char* s : {"CCC", "AAA", "BBB"}) y.apply(add(sym64(s), 1, Side::Buy, 1000, 10));
    CHECK(digest_books(x) == digest_books(y));
}

TEST_CASE("reference: one entry per epoch, restart digested before clear") {
    const std::int64_t t0 = kBase, t1 = kBase + 1'000, t2 = kBase + 2'000;
    const Bytes a1 = dp_add("AAA", '8', 1, 10, 1000, t0);
    const Bytes a2 = dp_add("AAA", '5', 2, 20, 1010, t0);
    const Bytes a3 = dp_add("BBB", '8', 3, 30, 500, t1);
    const Bytes b1 = dp_add("AAA", '8', 1, 5, 900, t2);
    const Bytes s1 = tp_segment(kDeepPlus, 1, {a1, a2}, -1, -1, t0);
    const Bytes s2 = tp_segment(kDeepPlus, 3, {a3}, -1, -1, t1);
    const Bytes restart = tp_segment(kDeepPlus, 1, {b1}, -1, -1, t2);

    TempFile f(".pcapng.gz");
    // s2 appears twice before the restart: the second copy is a merge-seam duplicate.
    write_gzip(f.path, capture({str(s1), str(s2), str(s2), str(restart)}, {1, 2, 3, 4}));

    const auto d = reference_digests(f.path);
    REQUIRE(d.size() == 2);
    CHECK(d[0].orders == 3);
    CHECK(d[1].orders == 1);
    CHECK(d[0].messages == 3);
    CHECK(d[1].messages == 1);

    BookSet direct;
    direct.apply(add(sym64("AAA"), 1, Side::Buy, 1000, 10));
    direct.apply(add(sym64("AAA"), 2, Side::Sell, 1010, 20));
    direct.apply(add(sym64("BBB"), 3, Side::Buy, 500, 30));
    CHECK(d[0].digest == digest_books(direct));
    CHECK(d[1].digest != d[0].digest);
}

TEST_CASE("digests: write/read round trip") {
    const std::vector<EpochDigest> in = {{0x0123456789abcdefULL, 3, 7}, {0xffffffffffffffffULL, 0, 0}};
    TempFile f(".txt");
    std::FILE* out = std::fopen(f.path.c_str(), "w");
    REQUIRE(out != nullptr);
    write_digests(out, in);
    std::fclose(out);
    const auto back = read_digests(f.path);
    REQUIRE(back.size() == in.size());
    for (std::size_t i = 0; i < in.size(); ++i) {
        CHECK(back[i].digest == in[i].digest);
        CHECK(back[i].orders == in[i].orders);
        CHECK(back[i].messages == in[i].messages);
    }

    write_raw(f.path, bytes_of("epoch 0 digest zz orders 1 messages 1\n"));
    CHECK_THROWS_AS(read_digests(f.path), std::runtime_error);
}
