#pragma once

// A Strategy that delegates on_bar to a std::function. The binding wraps a
// Python callable in one; the core stays Python-free.

#include <functional>
#include <utility>
#include <vector>

#include "pkmn_engine/strategy.hpp"

namespace pkmn {

class CallbackStrategy final : public Strategy {
  public:
    using Fn = std::function<std::vector<Order>(const Context&)>;
    explicit CallbackStrategy(Fn fn) : fn_(std::move(fn)) {}
    std::vector<Order> on_bar(const Context& ctx) override { return fn_(ctx); }

  private:
    Fn fn_;
};

}  // namespace pkmn
