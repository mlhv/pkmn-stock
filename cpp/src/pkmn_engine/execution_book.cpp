#include "pkmn_engine/execution_book.hpp"

#include <algorithm>
#include <cmath>
#include <limits>

namespace pkmn {

DayBooks::DayBooks(std::size_t n_assets) : books_(n_assets), built_(n_assets, 0) {}

void DayBooks::new_day() { ++epoch_; }

book::OrderBook& DayBooks::get(AssetId a, const DayQuote& q, const CostModel& cm,
                               LadderPricing p) {
    auto i = static_cast<std::size_t>(a);
    if (!books_[i]) books_[i] = std::make_unique<book::OrderBook>();
    if (built_[i] != epoch_) {
        build_day_ladder(q, cm, p, *books_[i]);  // clears, then adds
        built_[i] = epoch_;
    }
    return *books_[i];
}

LadderPricing ladder_pricing(ExecutionMode m) {
    return m == ExecutionMode::BookExactTest ? LadderPricing::ExactTest : LadderPricing::Cent;
}

namespace {

book::Qty side_units(const book::OrderBook& b, book::Side s, std::vector<book::Level>& lv) {
    b.depth(s, std::numeric_limits<std::size_t>::max(), lv);
    book::Qty n = 0;
    for (const auto& l : lv) n += l.qty;
    return n;
}

std::int64_t notional_ticks(const std::vector<book::Execution>& execs) {
    std::int64_t t = 0;
    for (const auto& e : execs) t += e.price * e.qty;
    return t;
}

}  // namespace

std::vector<Fill> execute_book(const std::vector<Order>& orders, const MarketView& market,
                               Portfolio& portfolio, Day day, const CostModel& cm,
                               DayBooks& books) {
    const LadderPricing pricing = ladder_pricing(cm.execution);
    const double tpd = static_cast<double>(ticks_per_dollar(pricing));
    std::vector<Fill> fills;
    std::vector<book::Level> levels;
    std::vector<book::Execution> execs;
    for (const auto& order : orders) {
        double px = market.price(order.asset);
        // Same skip rules as execute(): NaN = didn't print; <= 0 defensive.
        if (std::isnan(px) || px <= 0.0 || order.quantity == 0) continue;
        DayQuote q{px, market.mid(order.asset), market.low(order.asset)};
        book::OrderBook& b = books.get(order.asset, q, cm, pricing);
        execs.clear();
        if (order.quantity > 0) {
            std::int64_t qty = std::min<std::int64_t>(
                order.quantity, side_units(b, book::Side::Sell, levels));
            // Largest qty whose cost is affordable, walking the asks best
            // first (`levels` holds them from side_units). The check uses the
            // amount actually debited (qty*market + shipping + impact, the
            // formula path's expression), which equals ladder notional plus
            // shipping up to the non-negative clamp below, so cash never goes
            // negative from rounding.
            std::int64_t afford = 0, ticks = 0;
            for (const auto& l : levels) {
                bool stop = false;
                for (book::Qty u = 0; u < l.qty && afford < qty; ++u) {
                    std::int64_t t = ticks + l.price;
                    std::int64_t n = afford + 1;
                    double mq = static_cast<double>(n) * px;
                    double impact = std::max(static_cast<double>(t) / tpd - mq, 0.0);
                    double cost = mq + cm.shipping_per_line + impact;
                    if (cost > portfolio.cash) {
                        stop = true;
                        break;
                    }
                    ticks = t;
                    afford = n;
                }
                if (stop || afford >= qty) break;
            }
            qty = afford;
            if (qty <= 0) continue;
            b.match(book::Side::Buy, qty, std::numeric_limits<book::Price>::max(), execs);
            // Clamp: a cent-rounded flat ladder can land a hair under
            // qty*market (float or half-cent rounding); impact is never
            // negative (Fill invariant).
            double impact = std::max(
                static_cast<double>(notional_ticks(execs)) / tpd - static_cast<double>(qty) * px,
                0.0);
            Fill f{day, order.asset, qty, px, cm.shipping_per_line, impact};
            portfolio.apply(f);
            fills.push_back(f);
        } else {
            const Position* pos = portfolio.positions.find(order.asset);
            if (pos == nullptr) continue;
            std::int64_t qty = std::min({-order.quantity, pos->quantity,
                                         side_units(b, book::Side::Buy, levels)});
            if (qty <= 0) continue;
            b.match(book::Side::Sell, qty, 0, execs);
            // Same expression as fill_sell: qty * market * fee_rate + shipping.
            double fees = static_cast<double>(qty) * px * cm.fee_rate + cm.shipping_per_line;
            // Clamp: proceeds are never above qty * market.
            double impact = std::max(
                static_cast<double>(qty) * px - static_cast<double>(notional_ticks(execs)) / tpd,
                0.0);
            Fill f{day, order.asset, -qty, px, fees, impact};
            portfolio.apply(f);
            fills.push_back(f);
        }
    }
    return fills;
}

double hypothetical_sell_impact(const DayQuote& q, const CostModel& cm, LadderPricing p,
                                std::int64_t qty) {
    if (qty <= 0 || std::isnan(q.market) || q.market <= 0.0) return 0.0;
    const double tpd = static_cast<double>(ticks_per_dollar(p));
    book::OrderBook b;
    build_day_ladder(q, cm, p, b);
    std::vector<book::Level> levels;
    std::int64_t in_book = std::min<std::int64_t>(qty, side_units(b, book::Side::Buy, levels));
    std::vector<book::Execution> execs;
    if (in_book > 0) b.match(book::Side::Sell, in_book, 0, execs);
    std::int64_t ticks = notional_ticks(execs);
    // Past the book's depth: the same ramp, extended (hypothetical exit only).
    const LadderShape s = bid_shape(q, cm);
    for (std::int64_t k = in_book; k < qty; ++k) ticks += unit_ticks(s, book::Side::Buy, k, p);
    return std::max(static_cast<double>(qty) * q.market - static_cast<double>(ticks) / tpd, 0.0);
}

}  // namespace pkmn
