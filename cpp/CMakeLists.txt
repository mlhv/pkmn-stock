cmake_minimum_required(VERSION 3.26)
project(pkmn_engine LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

# The engine core: pure C++, no Python. Parity rule: FMA contraction changes
# last-bit float results vs CPython, so it is off for the core and everything
# that links it (PUBLIC).
add_library(pkmn_engine_core STATIC
  src/pkmn_engine/version.cpp
  src/pkmn_engine/portfolio.cpp
  src/pkmn_engine/market.cpp
  src/pkmn_engine/execution.cpp
  src/pkmn_engine/execution_book.cpp
  src/pkmn_engine/ladder.cpp
  src/pkmn_engine/backtest.cpp
  src/pkmn_engine/strategies/buy_and_hold.cpp
  src/pkmn_engine/strategies/sealed_accumulation.cpp
  src/pkmn_engine/strategies/dip_buyer.cpp
  src/pkmn_engine/strategies/momentum.cpp
  src/pkmn_engine/strategies/cost_aware_reversion.cpp
  src/pkmn_engine/strategies/factory.cpp
)
target_include_directories(pkmn_engine_core PUBLIC src)
target_compile_options(pkmn_engine_core PUBLIC -ffp-contract=off)
# The core links into the _engine shared module; Linux g++ does not emit PIC
# for static-library objects by default (macOS does), so CI's .so link fails
# without this.
set_target_properties(pkmn_engine_core PROPERTIES POSITION_INDEPENDENT_CODE ON)
target_link_libraries(pkmn_engine_core PUBLIC pkmn_book)

# The order book: pure C++, no deps, not part of the Python module. Same FP
# flags as the core so anything that later links both stays consistent.
add_library(pkmn_book STATIC src/pkmn_book/order_book.cpp)
target_include_directories(pkmn_book PUBLIC src)
target_compile_options(pkmn_book PUBLIC -ffp-contract=off)
set_target_properties(pkmn_book PROPERTIES POSITION_INDEPENDENT_CODE ON)

# Pokemon quote wire protocol: encoder and decoder. No zlib, so it builds in
# the wheel build too. Links the core because the replay path (Task 6) uses
# the ladder builder; reuses the header-only SequenceTracker from pkmn_md.
add_library(pkmn_wire STATIC src/pkmn_wire/encoder.cpp src/pkmn_wire/decoder.cpp)
target_include_directories(pkmn_wire PUBLIC src)
target_compile_options(pkmn_wire PUBLIC -ffp-contract=off)
set_target_properties(pkmn_wire PROPERTIES POSITION_INDEPENDENT_CODE ON)
target_link_libraries(pkmn_wire PUBLIC pkmn_engine_core)

# Market data (IEX HIST pcapng over zlib) and its tools. Kept out of the
# Python wheel build entirely so that build never needs zlib.
if(NOT SKBUILD)
  find_package(ZLIB REQUIRED)
  # On macOS FindZLIB returns the SDK's usr/include, which is already the
  # compiler's sysroot include dir; passing it again as -isystem puts it
  # ahead of libc++'s headers and breaks <cstddef>. Drop it there.
  if(APPLE AND ZLIB_INCLUDE_DIRS MATCHES "\\.sdk/usr/include$")
    set_property(TARGET ZLIB::ZLIB PROPERTY INTERFACE_INCLUDE_DIRECTORIES "")
  endif()
  add_library(pkmn_md STATIC
    src/pkmn_md/pcapng.cpp
    src/pkmn_md/iextp.cpp
    src/pkmn_md/deep_plus.cpp
    src/pkmn_md/deep.cpp
    src/pkmn_md/book_set.cpp
    src/pkmn_md/feed_reader.cpp
    src/pkmn_md/book_digest.cpp
  )
  target_include_directories(pkmn_md PUBLIC src)
  target_compile_options(pkmn_md PUBLIC -ffp-contract=off)
  target_link_libraries(pkmn_md PUBLIC pkmn_book PRIVATE ZLIB::ZLIB)
  set_target_properties(pkmn_md PROPERTIES POSITION_INDEPENDENT_CODE ON)

  add_executable(pcap_count tools/pcap_count.cpp)
  target_link_libraries(pcap_count PRIVATE pkmn_md)
  add_executable(iex_decode tools/iex_decode.cpp)
  target_link_libraries(iex_decode PRIVATE pkmn_md)
  add_executable(iex_digest tools/iex_digest.cpp)
  target_link_libraries(iex_digest PRIVATE pkmn_md)
  add_executable(iex_oracle tools/iex_oracle.cpp)
  target_link_libraries(iex_oracle PRIVATE pkmn_md)
  # pkmn_md/timing.hpp is header-only (reached through pkmn_wire's src
  # include dir), so the replay tool needs no zlib or pkmn_md link.
  add_executable(pokemon_replay tools/pokemon_replay.cpp)
  target_link_libraries(pokemon_replay PRIVATE pkmn_wire)

  # iex_bench reports its own build in its JSON. The flags are fixed per
  # configure (they can only change by re-configuring). The source stamp
  # (git SHA, dirty flag incl. untracked files, working-tree hash) is
  # regenerated on every build by an always-run target; see
  # cmake/bench_build_info.cmake.
  add_executable(iex_bench tools/iex_bench.cpp)
  target_link_libraries(iex_bench PRIVATE pkmn_md)
  string(TOUPPER "${CMAKE_BUILD_TYPE}" _pkmn_bt)
  string(STRIP "${CMAKE_CXX_FLAGS} ${CMAKE_CXX_FLAGS_${_pkmn_bt}} -ffp-contract=off" _pkmn_flags)
  set(_pkmn_flags "CMAKE_BUILD_TYPE=${CMAKE_BUILD_TYPE}: ${_pkmn_flags}")
  target_compile_definitions(iex_bench PRIVATE "PKMN_BENCH_FLAGS=\"${_pkmn_flags}\"")
  set(_pkmn_info "${CMAKE_CURRENT_BINARY_DIR}/generated/pkmn_bench_build_info.hpp")
  add_custom_target(iex_bench_build_info
    COMMAND ${CMAKE_COMMAND} -DREPO=${CMAKE_CURRENT_SOURCE_DIR}/.. -DOUT=${_pkmn_info}
            -P ${CMAKE_CURRENT_SOURCE_DIR}/cmake/bench_build_info.cmake
    BYPRODUCTS ${_pkmn_info}
    COMMENT "iex_bench source stamp")
  add_dependencies(iex_bench iex_bench_build_info)
  target_include_directories(iex_bench PRIVATE ${CMAKE_CURRENT_BINARY_DIR}/generated)
endif()

if(SKBUILD)
  find_package(Python 3.12 COMPONENTS Interpreter Development.Module REQUIRED)
  find_package(nanobind CONFIG REQUIRED)
  nanobind_add_module(_engine bindings/module.cpp)
  target_link_libraries(_engine PRIVATE pkmn_engine_core pkmn_wire)
  install(TARGETS _engine LIBRARY DESTINATION pkmn_quant)
endif()

option(PKMN_BUILD_TESTS "Build Catch2 unit tests" OFF)
if(PKMN_BUILD_TESTS)
  include(FetchContent)
  FetchContent_Declare(
    catch2
    GIT_REPOSITORY https://github.com/catchorg/Catch2.git
    GIT_TAG v3.7.1
  )
  FetchContent_MakeAvailable(catch2)
  add_executable(engine_tests
    tests/test_version.cpp
    tests/test_insertion_map.cpp
    tests/test_costs.cpp
    tests/test_portfolio.cpp
    tests/test_market.cpp
    tests/test_backtest_golden.cpp
    tests/test_strategies.cpp
    tests/test_concurrency.cpp
    tests/test_order_book.cpp
    tests/test_order_book_paths.cpp
    tests/test_ladder.cpp
    tests/test_execution_book.cpp
    tests/test_wire.cpp
    tests/test_replay.cpp
  )
  find_package(Threads REQUIRED)
  target_link_libraries(engine_tests PRIVATE pkmn_engine_core pkmn_book pkmn_wire Catch2::Catch2WithMain Threads::Threads)
  include(CTest)
  list(APPEND CMAKE_MODULE_PATH ${catch2_SOURCE_DIR}/extras)
  include(Catch)
  catch_discover_tests(engine_tests)

  # Separate binary so engine_tests stays zlib-free (and so its replaced
  # global operator new, used to count allocations, stays contained).
  if(NOT SKBUILD)
    add_executable(md_tests tests/test_pcapng.cpp tests/test_iextp.cpp tests/test_deep_plus.cpp
      tests/test_book_set.cpp tests/test_deep.cpp tests/test_timing.cpp tests/test_iex_oracle.cpp
      tests/test_book_digest.cpp)
    target_link_libraries(md_tests PRIVATE pkmn_md ZLIB::ZLIB Catch2::Catch2WithMain)
    # test_iex_oracle.cpp runs the built tool end to end (exit codes, counts).
    add_dependencies(md_tests iex_oracle)
    target_compile_definitions(md_tests PRIVATE "IEX_ORACLE_BIN=\"$<TARGET_FILE:iex_oracle>\"")
    catch_discover_tests(md_tests)
  endif()
endif()
