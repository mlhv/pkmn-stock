// Replays a Pokemon quote wire file (PKW1, docs/pokemon-wire-protocol.md).
//
//   pokemon_replay <file.pkw> [--no-ladders] [--repeat N]
//
// Streams the file through the decoder. With ladders on (the default), every
// Quote with a non-null market is turned into its day's modelled resting
// orders (synthetic, built from the day's tcgcsv quote) through the same
// builder the backtester uses, into one reused OrderBook, with the default
// CostModel liquidity tiers and whole-cent ladder prices. --no-ladders times
// decode alone. --repeat N streams the file N times (default 1); "seconds"
// and "msgs_per_s" are the best pass, "pass_seconds" lists every pass.
//
// Timing is pkmn_md/timing.hpp's counter (cntvct_el0 on arm64) around each
// whole pass, including file reads. Output: one JSON object on stdout.

#include <algorithm>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <exception>
#include <string>
#include <vector>

#include "pkmn_md/timing.hpp"
#include "pkmn_wire/decoder.hpp"
#include "pkmn_wire/replay.hpp"

namespace {

int usage() {
    std::fprintf(stderr, "usage: pokemon_replay <file.pkw> [--no-ladders] [--repeat N]\n");
    return 2;
}

}  // namespace

int main(int argc, char** argv) {
    std::string path;
    bool ladders = true;
    long repeat = 1;
    for (int i = 1; i < argc; ++i) {
        const std::string a = argv[i];
        if (a == "--no-ladders") {
            ladders = false;
        } else if (a == "--repeat" && i + 1 < argc) {
            repeat = std::strtol(argv[++i], nullptr, 10);
            if (repeat < 1) return usage();
        } else if (!a.empty() && a[0] != '-' && path.empty()) {
            path = a;
        } else {
            return usage();
        }
    }
    if (path.empty()) return usage();

    try {
        pkmn::CostModel cm;
        cm.impact_enabled = true;  // the ladder builder requires it
        const double tpn = pkmn::md::ticks_per_ns();

        std::vector<double> pass_seconds;
        std::uint64_t sink_guard = 0;  // keeps the book work observable
        pkmn::wire::DecodeStats stats;
        std::uint64_t quotes = 0, ladders_built = 0, null_market = 0, days = 0, instruments = 0;
        for (long r = 0; r < repeat; ++r) {
            auto on = [&](std::int32_t, std::uint32_t, const pkmn::book::OrderBook& b) {
                sink_guard += b.order_count();
            };
            pkmn::wire::LadderReplay<decltype(on)> rep(cm, pkmn::LadderPricing::Cent, ladders, on);
            const std::uint64_t t0 = pkmn::md::ticks();
            stats = pkmn::wire::decode_file(path, rep);
            const std::uint64_t t1 = pkmn::md::ticks();
            pass_seconds.push_back(static_cast<double>(t1 - t0) / tpn / 1e9);
            quotes = rep.quotes();
            ladders_built = rep.ladders_built();
            null_market = rep.null_market();
            days = rep.days();
            instruments = rep.instruments().size();
        }
        const double best = *std::min_element(pass_seconds.begin(), pass_seconds.end());

        std::printf("{\n");
        std::printf("  \"file\": \"%s\",\n", path.c_str());
        std::printf("  \"ladders\": %s,\n", ladders ? "true" : "false");
        std::printf("  \"repeat\": %ld,\n", repeat);
        std::printf("  \"messages\": %llu,\n", static_cast<unsigned long long>(stats.messages));
        std::printf("  \"instruments\": %llu,\n", static_cast<unsigned long long>(instruments));
        std::printf("  \"days\": %llu,\n", static_cast<unsigned long long>(days));
        std::printf("  \"quotes\": %llu,\n", static_cast<unsigned long long>(quotes));
        std::printf("  \"null_market_quotes\": %llu,\n", static_cast<unsigned long long>(null_market));
        std::printf("  \"ladders_built\": %llu,\n", static_cast<unsigned long long>(ladders_built));
        std::printf("  \"resting_orders_booked\": %llu,\n", static_cast<unsigned long long>(sink_guard / static_cast<std::uint64_t>(repeat)));
        std::printf("  \"seconds\": %.6f,\n", best);
        std::printf("  \"msgs_per_s\": %.0f,\n", best > 0 ? static_cast<double>(stats.messages) / best : 0.0);
        std::printf("  \"pass_seconds\": [");
        for (std::size_t i = 0; i < pass_seconds.size(); ++i)
            std::printf("%s%.6f", i ? ", " : "", pass_seconds[i]);
        std::printf("],\n");
        std::printf("  \"timer_ticks_per_ns\": %.6f,\n", tpn);
        std::printf("  \"stats\": {\"packets\": %llu, \"messages\": %llu, \"unknown\": %llu, "
                    "\"short_messages\": %llu, \"short_packets\": %llu, \"gaps\": %llu, "
                    "\"gap_messages\": %llu, \"duplicates\": %llu, \"bad_magic\": %llu}\n",
                    static_cast<unsigned long long>(stats.packets),
                    static_cast<unsigned long long>(stats.messages),
                    static_cast<unsigned long long>(stats.unknown),
                    static_cast<unsigned long long>(stats.short_messages),
                    static_cast<unsigned long long>(stats.short_packets),
                    static_cast<unsigned long long>(stats.gaps),
                    static_cast<unsigned long long>(stats.gap_messages),
                    static_cast<unsigned long long>(stats.duplicates),
                    static_cast<unsigned long long>(stats.bad_magic));
        std::printf("}\n");
    } catch (const std::exception& e) {
        std::fprintf(stderr, "pokemon_replay: %s\n", e.what());
        return 1;
    }
    return 0;
}
