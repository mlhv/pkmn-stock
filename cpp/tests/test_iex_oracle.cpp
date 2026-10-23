// iex_oracle end to end on tiny synthetic DEEP+/DEEP/TOPS captures: the exit
// status (0 pass, 1 mismatch, 3 inconclusive) and the evaluated / excluded /
// checked split. Runs the built tool (IEX_ORACLE_BIN) and reads its stdout.

#include <sys/wait.h>

#include <catch2/catch_test_macros.hpp>
#include <catch2/matchers/catch_matchers_string.hpp>

#include <cstdint>
#include <cstdio>
#include <string>
#include <vector>

#include "md_fixtures.hpp"
#include "pkmn_md/iextp.hpp"

using namespace md_fixtures;
using Catch::Matchers::ContainsSubstring;
using pkmn::md::kDeep;
using pkmn::md::kDeepPlus;
using pkmn::md::kTops;

namespace {

constexpr std::int64_t kBase = 1'734'964'524'000'000'000LL;  // 2024-12-23, feed-time origin

void put_sym(Bytes& b, const char* s) {
    std::string v(s);
    v.resize(8, ' ');
    append(b, bytes_of(v));
}

// DEEP+ Add Order 'a' (38 B): side/1 ts/2 sym/10 id/18 size/26 price/30.
Bytes dp_add(const char* sym, char side, std::uint64_t id, std::uint32_t size, std::int64_t price, std::int64_t ts) {
    Bytes m = {'a', static_cast<std::uint8_t>(side)};
    put_le64(m, static_cast<std::uint64_t>(ts));
    put_sym(m, sym);
    put_le64(m, id);
    put_le32(m, size);
    put_le64(m, static_cast<std::uint64_t>(price));
    return m;
}

// DEEP Price Level Update (30 B), Event Flags ON: type/0 flags/1 ts/2 sym/10 size/18 price/22.
Bytes deep_level(const char* sym, char side, std::uint32_t size, std::int64_t price, std::int64_t ts) {
    Bytes m = {static_cast<std::uint8_t>(side == 'B' ? '8' : '5'), 0x01};
    put_le64(m, static_cast<std::uint64_t>(ts));
    put_sym(m, sym);
    put_le32(m, size);
    put_le64(m, static_cast<std::uint64_t>(price));
    return m;
}

// TOPS Quote Update 'Q' (42 B): flags/1 ts/2 sym/10 bid size/18 bid/22 ask/30 ask size/38.
Bytes quote(const char* sym, std::uint8_t flags, std::uint32_t bid_size, std::int64_t bid, std::int64_t ask,
            std::uint32_t ask_size, std::int64_t ts) {
    Bytes m = {'Q', flags};
    put_le64(m, static_cast<std::uint64_t>(ts));
    put_sym(m, sym);
    put_le32(m, bid_size);
    put_le64(m, static_cast<std::uint64_t>(bid));
    put_le64(m, static_cast<std::uint64_t>(ask));
    put_le32(m, ask_size);
    return m;
}

Bytes with_session(Bytes seg, std::uint32_t session) {
    for (int i = 0; i < 4; ++i) seg[8 + i] = static_cast<std::uint8_t>(session >> (8 * i));
    return seg;
}

std::string str(const Bytes& b) { return std::string(b.begin(), b.end()); }

void write_feed(const std::string& path, const std::vector<Bytes>& segments) {
    std::vector<std::string> payloads;
    std::vector<std::uint64_t> ts;
    for (const Bytes& s : segments) {
        payloads.push_back(str(s));
        ts.push_back(ts.size() + 1);
    }
    write_raw(path, capture(payloads, ts));
}

struct Run {
    int exit_code;
    std::string out;
};

Run run_oracle(const std::vector<Bytes>& dp, const std::vector<Bytes>& deep, const std::vector<Bytes>& tops) {
    TempFile fdp(".pcapng"), fdeep(".pcapng"), ftops(".pcapng");
    write_feed(fdp.path, dp);
    write_feed(fdeep.path, deep);
    write_feed(ftops.path, tops);
    const std::string cmd = std::string(IEX_ORACLE_BIN) + " --deep-plus " + fdp.path + " --deep " + fdeep.path +
                            " --tops " + ftops.path + " 2>&1";
    FILE* p = popen(cmd.c_str(), "r");
    REQUIRE(p != nullptr);
    std::string out;
    char buf[4096];
    while (std::size_t n = std::fread(buf, 1, sizeof buf, p)) out.append(buf, n);
    const int status = pclose(p);
    REQUIRE(WIFEXITED(status));
    return Run{WEXITSTATUS(status), out};
}

// One resting bid 100 @ 10.00 in ZTEST, confirmed by DEEP and TOPS.
const std::int64_t kPx = 100000;  // $10.00 in 1/10000
std::vector<Bytes> dp_ok() { return {tp_segment(kDeepPlus, 1, {dp_add("ZTEST", '8', 7, 100, kPx, kBase + 1000)})}; }
std::vector<Bytes> deep_ok(std::uint32_t size = 100) {
    return {tp_segment(kDeep, 1, {deep_level("ZTEST", 'B', size, kPx, kBase + 2000)})};
}
std::vector<Bytes> tops_ok() { return {tp_segment(kTops, 1, {quote("ZTEST", 0, 100, kPx, 0, 0, kBase + 2000)})}; }

}  // namespace

TEST_CASE("oracle: all preconditions held and 0 mismatches exits 0") {
    const Run r = run_oracle(dp_ok(), deep_ok(), tops_ok());
    INFO(r.out);
    CHECK(r.exit_code == 0);
    CHECK_THAT(r.out, ContainsSubstring("validity          all preconditions held"));
    CHECK_THAT(r.out, ContainsSubstring("result            PASS (exit 0)"));
    CHECK_THAT(r.out, ContainsSubstring("TOPS evaluated 1, excluded 0, checked 1"));
}

TEST_CASE("oracle: a mismatch with preconditions held exits 1") {
    const Run r = run_oracle(dp_ok(), deep_ok(200), tops_ok());
    INFO(r.out);
    CHECK(r.exit_code == 1);
    CHECK_THAT(r.out, ContainsSubstring("deep_level_bid     1"));
    CHECK_THAT(r.out, ContainsSubstring("result            MISMATCH (exit 1)"));
}

TEST_CASE("oracle: a DEEP+ gap makes the run inconclusive (exit 3), named") {
    std::vector<Bytes> dp = dp_ok();
    dp.push_back(tp_segment(kDeepPlus, 5, {dp_add("ZOTHER", '8', 9, 100, kPx, kBase + 1500)}));
    const Run r = run_oracle(dp, deep_ok(), tops_ok());
    INFO(r.out);
    CHECK(r.exit_code == 3);
    CHECK_THAT(r.out, ContainsSubstring("DEEP+ gaps 1 (must be 0)"));
    CHECK_THAT(r.out, ContainsSubstring("result            INCONCLUSIVE (exit 3"));
}

TEST_CASE("oracle: a new session id on one feed is inconclusive, not silently unchecked") {
    std::vector<Bytes> tops = tops_ok();
    // A restarted TOPS stream under a new session id: every later segment is
    // other_stream, so its quotes would never be checked.
    tops.push_back(with_session(tp_segment(kTops, 1, {quote("ZTEST", 0, 999, kPx, 0, 0, kBase + 3000)}), 7));
    const Run r = run_oracle(dp_ok(), deep_ok(), tops);
    INFO(r.out);
    CHECK(r.exit_code == 3);
    CHECK_THAT(r.out, ContainsSubstring("TOPS other_stream 1 (must be 0)"));
}

TEST_CASE("oracle: a feed that produces no checks is inconclusive") {
    const std::vector<Bytes> tops_heartbeat_only = {tp_segment(kTops, 1, {})};
    const Run r = run_oracle(dp_ok(), deep_ok(), tops_heartbeat_only);
    INFO(r.out);
    CHECK(r.exit_code == 3);
    CHECK_THAT(r.out, ContainsSubstring("TOPS checks 0 (must be >= 1)"));
}

TEST_CASE("oracle: inconclusive takes precedence over mismatches") {
    std::vector<Bytes> dp = dp_ok();
    dp.push_back(tp_segment(kDeepPlus, 5, {dp_add("ZOTHER", '8', 9, 100, kPx, kBase + 1500)}));
    const Run r = run_oracle(dp, deep_ok(200), tops_ok());
    INFO(r.out);
    CHECK(r.exit_code == 3);
    CHECK_THAT(r.out, ContainsSubstring("mismatches        1"));
}

TEST_CASE("oracle: halt zero quotes are excluded from checked unless the book is empty too") {
    std::vector<Bytes> tops = tops_ok();
    // ZTEST still rests 100 @ 10.00: a flag-0x80 zero quote is excluded by name.
    // ZEMPTY has no orders: the same shape matches the empty book, so it is checked.
    tops.push_back(tp_segment(kTops, 2,
                              {quote("ZTEST", 0x80, 0, 0, 0, 0, kBase + 3000),
                               quote("ZEMPTY", 0x80, 0, 0, 0, 0, kBase + 3000)}));
    const Run r = run_oracle(dp_ok(), deep_ok(), tops);
    INFO(r.out);
    CHECK(r.exit_code == 0);
    CHECK_THAT(r.out, ContainsSubstring("TOPS evaluated 3, excluded 1, checked 2"));
    CHECK_THAT(r.out, ContainsSubstring("checked points    4 evaluated, 1 excluded by name, 3 checked"));
    CHECK_THAT(r.out, ContainsSubstring("tops_unavailable_zero_quote  1"));
}
