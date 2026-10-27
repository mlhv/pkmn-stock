// feed_publisher + feed_handler end to end over real loopback multicast with
// the TCP retransmit channel: the handler's per-epoch digests under 10%
// seeded random loss must equal iex_digest's direct file replay of the same
// capture. Linux only (the VM); elsewhere the test is skipped.
//
// Both tools run as child processes with stdout captured to temp files. Every
// wait is bounded: a child still running at the deadline is killed and the
// test fails, so a hang can never stall ctest.

#include <catch2/catch_test_macros.hpp>

#if defined(__linux__)
#include <signal.h>
#include <spawn.h>
#include <sys/wait.h>
#include <unistd.h>

#include <chrono>
#include <cstdlib>
#include <fcntl.h>
#include <fstream>
#include <sstream>
#include <string>
#include <thread>
#include <vector>

#include "feed_fixtures.hpp"
#include "md_fixtures.hpp"

extern char** environ;

namespace {

using clk = std::chrono::steady_clock;

struct Child {
    pid_t pid = -1;
    int status = -1;
    bool exited = false;
};

// Starts argv[0] with stdout to out_path and stderr to err_path.
Child spawn(const std::vector<std::string>& args, const std::string& out_path, const std::string& err_path) {
    posix_spawn_file_actions_t fa;
    posix_spawn_file_actions_init(&fa);
    posix_spawn_file_actions_addopen(&fa, 1, out_path.c_str(), O_WRONLY | O_CREAT | O_TRUNC, 0644);
    posix_spawn_file_actions_addopen(&fa, 2, err_path.c_str(), O_WRONLY | O_CREAT | O_TRUNC, 0644);
    std::vector<char*> argv;
    for (const auto& a : args) argv.push_back(const_cast<char*>(a.c_str()));
    argv.push_back(nullptr);
    Child c;
    const int rc = posix_spawn(&c.pid, argv[0], &fa, nullptr, argv.data(), environ);
    posix_spawn_file_actions_destroy(&fa);
    if (rc != 0) c.pid = -1;
    return c;
}

// Waits for every child until the deadline; kills any still running.
void wait_all(std::vector<Child*> cs, clk::time_point deadline) {
    while (true) {
        bool all = true;
        for (Child* c : cs) {
            if (c->exited || c->pid < 0) continue;
            if (waitpid(c->pid, &c->status, WNOHANG) == c->pid) {
                c->exited = true;
            } else {
                all = false;
            }
        }
        if (all) return;
        if (clk::now() >= deadline) break;
        std::this_thread::sleep_for(std::chrono::milliseconds(20));
    }
    for (Child* c : cs) {
        if (c->exited || c->pid < 0) continue;
        kill(c->pid, SIGKILL);
        waitpid(c->pid, &c->status, 0);
    }
}

int exit_code(const Child& c) { return c.exited && WIFEXITED(c.status) ? WEXITSTATUS(c.status) : -1; }

std::string slurp(const std::string& path) {
    std::ifstream f(path);
    std::stringstream ss;
    ss << f.rdbuf();
    return ss.str();
}

// The integer after `"key": ` in a flat JSON object, or -1.
long long json_int(const std::string& s, const std::string& key) {
    const std::string k = "\"" + key + "\": ";
    const auto p = s.find(k);
    if (p == std::string::npos) return -1;
    return std::strtoll(s.c_str() + p + k.size(), nullptr, 10);
}

struct Pair {
    int handler_exit, publisher_exit;
    std::string hout, pout;
};

// Runs feed_handler (started first, as the brief orders) and feed_publisher on
// `cap` with a port pair and a multicast group of its own. Under ctest -j the
// test cases run as concurrent processes, so both come from a slot derived
// from this process's pid plus a per-process call counter: concurrent pids are
// close together, so their slots differ (a random pick could collide). Ports
// stay in 20000-32767, below Linux's ephemeral range (32768-60999); the group
// is 239.255.(1 + slot / 256).(slot % 256), so even a port clash would not mix
// two runs' datagrams. A child that fails to spawn, or outlives the 90 s
// deadline, is reaped so no process is orphaned; a failed spawn shows as exit -1.
Pair run_pair(const std::string& cap, std::vector<std::string> hargs, std::vector<std::string> pargs) {
    using md_fixtures::TempFile;
    TempFile hout(".hout"), herr(".herr"), pout(".pout"), perr(".perr");
    static unsigned calls = 0;
    const unsigned slot = (static_cast<unsigned>(getpid()) * 8u + calls++) % 6383u;
    const int port = 20000 + 2 * static_cast<int>(slot);
    const std::string p = std::to_string(port), cp = std::to_string(port + 1);
    const std::string group = "239.255." + std::to_string(1 + slot / 256) + "." + std::to_string(slot % 256);
    std::vector<std::string> h = {FEED_HANDLER_BIN, "--group", group, "--port", p, "--ctrl-port", cp,
                                  "--timeout-s", "60"};
    h.insert(h.end(), hargs.begin(), hargs.end());
    std::vector<std::string> pub = {FEED_PUBLISHER_BIN, cap, "--group", group, "--port", p, "--ctrl-port", cp};
    pub.insert(pub.end(), pargs.begin(), pargs.end());
    Child hc = spawn(h, hout.path, herr.path);
    Child pc = hc.pid > 0 ? spawn(pub, pout.path, perr.path) : Child{};
    if (pc.pid < 0 && hc.pid > 0) {
        kill(hc.pid, SIGKILL);
        waitpid(hc.pid, &hc.status, 0);
        hc.exited = true;
    }
    wait_all({&hc, &pc}, clk::now() + std::chrono::seconds(90));
    Pair r{pc.pid < 0 ? -1 : exit_code(hc), exit_code(pc), slurp(hout.path), slurp(pout.path)};
    r.hout += "\nhandler stderr:\n" + slurp(herr.path);
    r.pout += "\npublisher stderr:\n" + slurp(perr.path);
    return r;
}

// Reference digests of `cap` from the direct file replay (iex_digest).
void write_reference(const std::string& cap, const std::string& ref, const std::string& extra = "") {
    const std::string dig = std::string(IEX_DIGEST_BIN) + " " + cap + " --out " + ref + extra;
    REQUIRE(std::system(dig.c_str()) == 0);
}

}  // namespace

TEST_CASE("feed tools: loopback multicast with 10% random loss matches iex_digest") {
    md_fixtures::TempFile cap(".pcap.gz"), ref(".digests");
    feed_fixtures::write_feed_capture(cap.path, feed_fixtures::synthetic_feed());
    write_reference(cap.path, ref.path);
    const Pair r = run_pair(cap.path, {"--expect", ref.path}, {"--drop", "random:0.1", "--seed", "3", "--rate", "200000"});
    INFO("handler stdout:\n" << r.hout);
    INFO("publisher stdout:\n" << r.pout);
    CHECK(r.handler_exit == 0);
    CHECK(r.hout.find("\"digest_match\": true") != std::string::npos);
    CHECK(json_int(r.hout, "gaps") > 0);
    CHECK(json_int(r.hout, "unrecoverable") == 0);
    CHECK(r.publisher_exit == 0);
    CHECK(json_int(r.pout, "dropped") > 0);
    CHECK(json_int(r.pout, "unserved") == 0);
    CHECK(json_int(r.pout, "retransmit_packets") > 0);
}

// A first epoch far shorter than the second: every new-epoch datagram from
// seq ~50 on is past the old epoch's highest sequence end, so if one reached
// the core before its EpochStart it would be held as a gap of the old epoch
// and requested there (unservable). Unthrottled, so the whole stream is in
// flight at once.
TEST_CASE("feed tools: a short first epoch survives the restart unthrottled") {
    md_fixtures::TempFile cap(".pcap.gz"), ref(".digests");
    feed_fixtures::write_feed_capture(cap.path, feed_fixtures::synthetic_feed(2000, 20));
    write_reference(cap.path, ref.path);
    for (const char* seed : {"1", "2", "3"}) {
        const Pair r = run_pair(cap.path, {"--expect", ref.path}, {"--drop", "random:0.1", "--seed", seed});
        INFO("seed " << seed << "\nhandler stdout:\n" << r.hout);
        INFO("publisher stdout:\n" << r.pout);
        CHECK(r.handler_exit == 0);
        CHECK(r.hout.find("\"digest_match\": true") != std::string::npos);
        CHECK(r.publisher_exit == 0);
    }
}

// The ring holds only the last few packets and the whole 50-segment stream is
// sent before the first request arrives, so the Unserved reply comes after
// End: the publisher must still exit nonzero, as must the handler.
TEST_CASE("feed tools: an unserved request fails both tools") {
    md_fixtures::TempFile cap(".pcap.gz");
    feed_fixtures::write_feed_capture(cap.path, feed_fixtures::synthetic_feed(50, 0));
    const Pair r = run_pair(cap.path, {}, {"--drop", "random:0.1", "--seed", "3", "--ring-bytes", "1000"});
    INFO("handler stdout:\n" << r.hout);
    INFO("publisher stdout:\n" << r.pout);
    CHECK(r.handler_exit == 2);
    CHECK(json_int(r.hout, "unrecoverable") > 0);
    CHECK(json_int(r.pout, "unserved") > 0);
    CHECK(r.publisher_exit == 1);
}

TEST_CASE("feed tools: checkpoints under 10% random loss match iex_digest --checkpoint-every") {
    md_fixtures::TempFile cap(".pcap.gz"), ref(".digests");
    feed_fixtures::write_feed_capture(cap.path, feed_fixtures::synthetic_feed());
    write_reference(cap.path, ref.path, " --checkpoint-every 50");
    const Pair r = run_pair(cap.path, {"--expect", ref.path, "--checkpoint-every", "50"},
                            {"--drop", "random:0.1", "--seed", "4", "--rate", "200000"});
    INFO("handler stdout:\n" << r.hout);
    INFO("publisher stdout:\n" << r.pout);
    CHECK(r.handler_exit == 0);
    CHECK(r.hout.find("\"digest_match\": true") != std::string::npos);
    CHECK(r.hout.find("\"checkpoint_match\": true") != std::string::npos);
    CHECK(json_int(r.hout, "checkpoints") == 99);  // 3,789 / 50 = 75 in epoch 0, 1,243 / 50 = 24 in epoch 1
    CHECK(json_int(r.hout, "checkpoints_matched") == json_int(r.hout, "checkpoints_expected"));
    CHECK(json_int(r.hout, "gaps") > 0);
    CHECK(r.publisher_exit == 0);
}

// The publisher sends a feed whose books end exactly as the reference's
// (empty, same message count) but differ mid-epoch: the handler must exit 1
// on the checkpoints although every epoch digest matches.
TEST_CASE("feed tools: a checkpoint mismatch fails the handler when the epoch digest matches") {
    md_fixtures::TempFile ref_cap(".pcap.gz"), cap(".pcap.gz"), ref(".digests");
    feed_fixtures::write_feed_capture(ref_cap.path, feed_fixtures::empty_ending_feed(100'000));
    feed_fixtures::write_feed_capture(cap.path, feed_fixtures::empty_ending_feed(100'100));
    write_reference(ref_cap.path, ref.path, " --checkpoint-every 1");
    const Pair r = run_pair(cap.path, {"--expect", ref.path, "--checkpoint-every", "1"}, {});
    INFO("handler stdout:\n" << r.hout);
    CHECK(r.handler_exit == 1);
    CHECK(r.hout.find("\"digest_match\": true") != std::string::npos);
    CHECK(r.hout.find("\"checkpoint_match\": false") != std::string::npos);
    CHECK(json_int(r.hout, "checkpoints_matched") == 2);  // seq 3 (only BBB left) and seq 4 (empty)
    CHECK(r.hout.find("\"first_checkpoint_mismatch\": {\"index\": 0,") != std::string::npos);
    CHECK(r.publisher_exit == 0);
}

#else

TEST_CASE("feed tools: loopback multicast with 10% random loss matches iex_digest") {
    SKIP("loopback multicast e2e runs in the Linux VM");
}

TEST_CASE("feed tools: a short first epoch survives the restart unthrottled") {
    SKIP("loopback multicast e2e runs in the Linux VM");
}

TEST_CASE("feed tools: an unserved request fails both tools") {
    SKIP("loopback multicast e2e runs in the Linux VM");
}

TEST_CASE("feed tools: checkpoints under 10% random loss match iex_digest --checkpoint-every") {
    SKIP("loopback multicast e2e runs in the Linux VM");
}

TEST_CASE("feed tools: a checkpoint mismatch fails the handler when the epoch digest matches") {
    SKIP("loopback multicast e2e runs in the Linux VM");
}

#endif
