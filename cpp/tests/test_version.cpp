#include <catch2/catch_test_macros.hpp>

#include <string>

#include "pkmn_engine/version.hpp"

TEST_CASE("engine reports its version") {
    REQUIRE(std::string(pkmn::engine_version()) == "0.1.0");
}
