#pragma once

// Seeded, deterministic packet-loss injection for tests and the publisher.

#include <charconv>
#include <cstdint>
#include <optional>
#include <random>
#include <string>
#include <string_view>

namespace pkmn::md {

struct DropSpec {
    enum class Mode : std::uint8_t { None, Random, Burst } mode = Mode::None;
    double p = 0;
    std::uint32_t burst_len = 0;
    std::uint64_t seed = 0;
};

namespace detail {
inline bool parse_double(std::string_view s, double& v) {
    if (s.empty()) return false;
    try {
        std::size_t n = 0;
        v = std::stod(std::string(s), &n);
        return n == s.size();
    } catch (...) {
        return false;
    }
}
}  // namespace detail

// "none", "random:<p>", "burst:<p>:<len>"; nullopt on p outside [0,1], len 0
// or anything unparseable.
inline std::optional<DropSpec> parse_drop_spec(std::string_view s, std::uint64_t seed) {
    DropSpec d;
    d.seed = seed;
    if (s == "none") return d;
    auto colon = s.find(':');
    if (colon == std::string_view::npos) return std::nullopt;
    const auto head = s.substr(0, colon);
    auto rest = s.substr(colon + 1);
    if (head == "random") {
        if (!detail::parse_double(rest, d.p) || !(d.p >= 0.0 && d.p <= 1.0)) return std::nullopt;
        d.mode = DropSpec::Mode::Random;
        return d;
    }
    if (head == "burst") {
        auto c2 = rest.find(':');
        if (c2 == std::string_view::npos) return std::nullopt;
        const auto ls = rest.substr(c2 + 1);
        if (!detail::parse_double(rest.substr(0, c2), d.p) || !(d.p >= 0.0 && d.p <= 1.0)) return std::nullopt;
        std::uint32_t len = 0;
        auto r = std::from_chars(ls.data(), ls.data() + ls.size(), len);
        if (ls.empty() || r.ec != std::errc() || r.ptr != ls.data() + ls.size() || len == 0) return std::nullopt;
        d.burst_len = len;
        d.mode = DropSpec::Mode::Burst;
        return d;
    }
    return std::nullopt;
}

class DropInjector {
public:
    explicit DropInjector(const DropSpec& s) : spec_(s), rng_(s.seed) {}

    // Call once per packet; true means drop it.
    bool drop() {
        switch (spec_.mode) {
            case DropSpec::Mode::None:
                return false;
            case DropSpec::Mode::Random:
                if (draw() < spec_.p) {
                    ++dropped_;
                    return true;
                }
                return false;
            case DropSpec::Mode::Burst:
                if (remaining_ == 0) {
                    if (!(draw() < spec_.p)) return false;
                    remaining_ = spec_.burst_len;
                    ++bursts_;
                }
                --remaining_;
                ++dropped_;
                return true;
        }
        return false;
    }

    std::uint64_t dropped() const { return dropped_; }
    std::uint64_t bursts() const { return bursts_; }

private:
    double draw() { return std::uniform_real_distribution<double>(0, 1)(rng_); }
    DropSpec spec_;
    std::mt19937_64 rng_;
    std::uint32_t remaining_ = 0;
    std::uint64_t dropped_ = 0;
    std::uint64_t bursts_ = 0;
};

}  // namespace pkmn::md
