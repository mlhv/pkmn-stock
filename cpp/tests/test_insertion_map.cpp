#include <catch2/catch_test_macros.hpp>

#include "pkmn_engine/types.hpp"

using pkmn::InsertionMap;

TEST_CASE("InsertionMap preserves insertion order like a Python dict") {
    InsertionMap<int> m(10);
    m.set(7, 70);
    m.set(2, 20);
    m.set(5, 50);
    REQUIRE(m.size() == 3);
    // iteration order is insertion order, not key order
    REQUIRE(m.entries()[0].key == 7);
    REQUIRE(m.entries()[1].key == 2);
    REQUIRE(m.entries()[2].key == 5);
    // re-assigning an existing key keeps its original position
    m.set(2, 99);
    REQUIRE(m.entries()[1].key == 2);
    REQUIRE(m.entries()[1].value == 99);
    REQUIRE(*m.find(2) == 99);
    REQUIRE(m.find(3) == nullptr);
    REQUIRE(m.contains(5));
}

TEST_CASE("InsertionMap erase preserves relative order of survivors") {
    InsertionMap<int> m(10);
    m.set(7, 70);
    m.set(2, 20);
    m.set(5, 50);
    m.erase(2);
    REQUIRE(m.size() == 2);
    REQUIRE(m.entries()[0].key == 7);
    REQUIRE(m.entries()[1].key == 5);
    REQUIRE(m.find(2) == nullptr);
    REQUIRE(*m.find(5) == 50);  // index remap after erase
    m.set(2, 21);  // re-insert goes to the back (Python dict semantics)
    REQUIRE(m.entries()[2].key == 2);
}
