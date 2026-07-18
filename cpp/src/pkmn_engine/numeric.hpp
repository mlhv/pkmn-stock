#pragma once

// Shared Neumaier (improved Kahan-Babuska) compensated summation, matching
// CPython >= 3.12's builtin sum() over floats exactly. CPython's sum() does
// NOT add left-to-right for floats (naive `value += x`); it tracks a running
// compensation for lost low-order bits, which is more accurate than naive
// accumulation and NOT bit-identical to it for >= 3 terms. Both
// Portfolio::equity() (portfolio.py:100-108) and CrossSectionalMomentum's
// equity computation (momentum.py:106) sum position values this way — this
// is the ONE place the algorithm lives; do not duplicate it inline.

#include <cmath>
#include <stdexcept>

#include "pkmn_engine/types.hpp"

namespace pkmn {

class NeumaierSum {
  public:
    void add(double x) {
        double t = value_ + x;
        if (std::fabs(value_) >= std::fabs(x)) {
            c_ += (value_ - t) + x;
        } else {
            c_ += (x - t) + value_;
        }
        value_ = t;
    }

    double result() const { return value_ + c_; }

  private:
    double value_ = 0.0;
    double c_ = 0.0;
};

// Sums quantity * mark for every held position, in insertion order, via
// NeumaierSum. Throws std::out_of_range if a held asset has no mark
// (Python: KeyError from ctx.marks[a] / marks dict lookup).
inline double position_value_sum(const InsertionMap<Position>& positions,
                                  const InsertionMap<double>& marks) {
    NeumaierSum acc;
    for (const auto& e : positions.entries()) {
        const double* m = marks.find(e.key);
        if (m == nullptr) throw std::out_of_range("no mark for held asset");
        acc.add(static_cast<double>(e.value.quantity) * *m);
    }
    return acc.result();
}

}  // namespace pkmn
