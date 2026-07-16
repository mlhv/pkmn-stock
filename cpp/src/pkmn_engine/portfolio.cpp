#include "pkmn_engine/portfolio.hpp"

#include <stdexcept>
#include <string>

namespace pkmn {

void Portfolio::apply(const Fill& f) {
    // Fill.__post_init__ (portfolio.py:34-40)
    if (f.price <= 0.0) throw std::invalid_argument("Fill.price must be positive");
    if (f.fees < 0.0) throw std::invalid_argument("Fill.fees must be non-negative");
    if (f.impact < 0.0) throw std::invalid_argument("Fill.impact must be non-negative");
    // portfolio.py:64-71 (ledger list is not kept: the engine returns its
    // own fills vector; Python's Portfolio.ledger is never read by the loop)
    if (f.quantity == 0) throw std::invalid_argument("zero-quantity fill");
    if (f.quantity > 0) {
        buy_(f);
    } else {
        sell_(f);
    }
}

void Portfolio::buy_(const Fill& f) {
    // portfolio.py:73-85 — same expression grouping.
    double cost = static_cast<double>(f.quantity) * f.price;
    cash -= cost + f.fees + f.impact;
    realized_pnl -= f.fees + f.impact;
    Position* pos = positions.find(f.asset);
    if (pos == nullptr) {
        positions.set(f.asset, Position{f.quantity, f.price, f.day});
    } else {
        double total_cost = pos->avg_cost * static_cast<double>(pos->quantity) + cost;
        pos->quantity += f.quantity;
        pos->avg_cost = total_cost / static_cast<double>(pos->quantity);
    }
}

void Portfolio::sell_(const Fill& f) {
    // portfolio.py:87-98 — same expression grouping.
    std::int64_t qty = -f.quantity;
    Position* pos = positions.find(f.asset);
    if (pos == nullptr || pos->quantity < qty) {
        std::int64_t held = pos ? pos->quantity : 0;
        throw std::invalid_argument("cannot sell " + std::to_string(qty) + ": hold " +
                                    std::to_string(held));
    }
    double proceeds = static_cast<double>(qty) * f.price;
    cash += proceeds - f.fees - f.impact;
    realized_pnl += proceeds - static_cast<double>(qty) * pos->avg_cost - f.fees - f.impact;
    pos->quantity -= qty;
    if (pos->quantity == 0) positions.erase(f.asset);
}

double Portfolio::equity(const InsertionMap<double>& marks) const {
    // portfolio.py:100-108: sum() starts at 0 and adds in dict order.
    double value = 0.0;
    for (const auto& e : positions.entries()) {
        const double* m = marks.find(e.key);
        if (m == nullptr) throw std::out_of_range("no mark for held asset");
        value += static_cast<double>(e.value.quantity) * *m;
    }
    return cash + value;
}

}  // namespace pkmn
