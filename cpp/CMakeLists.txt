cmake_minimum_required(VERSION 3.26)
project(pkmn_engine LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

# The engine core: pure C++, no Python. Parity rule: FMA contraction changes
# last-bit float results vs CPython, so it is off for the core and everything
# that links it (PUBLIC).
add_library(pkmn_engine_core STATIC
  src/pkmn_engine/version.cpp
)
target_include_directories(pkmn_engine_core PUBLIC src)
target_compile_options(pkmn_engine_core PUBLIC -ffp-contract=off)

if(SKBUILD)
  find_package(Python 3.12 COMPONENTS Interpreter Development.Module REQUIRED)
  find_package(nanobind CONFIG REQUIRED)
  nanobind_add_module(_engine bindings/module.cpp)
  target_link_libraries(_engine PRIVATE pkmn_engine_core)
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
  )
  target_link_libraries(engine_tests PRIVATE pkmn_engine_core Catch2::Catch2WithMain)
  include(CTest)
  list(APPEND CMAKE_MODULE_PATH ${catch2_SOURCE_DIR}/extras)
  include(Catch)
  catch_discover_tests(engine_tests)
endif()
