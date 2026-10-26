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
#include <random>
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

}  // namespace

TEST_CASE("feed tools: loopback multicast with 10% random loss matches iex_digest") {
    using md_fixtures::TempFile;
    TempFile cap(".pcap.gz"), ref(".digests"), hout(".hout"), herr(".herr"), pout(".pout"), perr(".perr");
    feed_fixtures::write_feed_capture(cap.path, feed_fixtures::synthetic_feed());

    const std::string dig = std::string(IEX_DIGEST_BIN) + " " + cap.path + " --out " + ref.path;
    REQUIRE(std::system(dig.c_str()) == 0);

    std::random_device rd;
    const int port = 30000 + static_cast<int>(rd() % 30000);
    const std::string p = std::to_string(port), cp = std::to_string(port + 1);

    Child h = spawn({FEED_HANDLER_BIN, "--expect", ref.path, "--port", p, "--ctrl-port", cp, "--timeout-s", "60"},
                    hout.path, herr.path);
    REQUIRE(h.pid > 0);
    Child pub = spawn({FEED_PUBLISHER_BIN, cap.path, "--port", p, "--ctrl-port", cp, "--drop", "random:0.1", "--seed",
                       "3", "--rate", "200000"},
                      pout.path, perr.path);
    REQUIRE(pub.pid > 0);
    wait_all({&h, &pub}, clk::now() + std::chrono::seconds(90));

    const std::string ho = slurp(hout.path), po = slurp(pout.path);
    INFO("handler stdout:\n" << ho << "\nhandler stderr:\n" << slurp(herr.path));
    INFO("publisher stdout:\n" << po << "\npublisher stderr:\n" << slurp(perr.path));
    CHECK(exit_code(h) == 0);
    CHECK(ho.find("\"digest_match\": true") != std::string::npos);
    CHECK(json_int(ho, "gaps") > 0);
    CHECK(json_int(ho, "unrecoverable") == 0);
    CHECK(exit_code(pub) == 0);
    CHECK(json_int(po, "dropped") > 0);
    CHECK(json_int(po, "unserved") == 0);
    CHECK(json_int(po, "retransmit_packets") > 0);
}

#else

TEST_CASE("feed tools: loopback multicast with 10% random loss matches iex_digest") {
    SKIP("loopback multicast e2e runs in the Linux VM");
}

#endif
