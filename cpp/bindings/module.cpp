#include <nanobind/nanobind.h>

#include "pkmn_engine/version.hpp"

namespace nb = nanobind;

NB_MODULE(_engine, m) {
    m.doc() = "pkmn_quant native backtest engine";
    m.attr("__version__") = pkmn::engine_version();
}
