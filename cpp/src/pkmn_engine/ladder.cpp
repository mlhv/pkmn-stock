#include "pkmn_engine/ladder.hpp"

#include <algorithm>

namespace pkmn {

std::int64_t ticks_per_dollar(LadderPricing p) {
    return p == LadderPricing::Cent ? 100 : 1'000'000'000;
}

LadderShape ask_shape(const DayQuote& q, const CostModel& cm) {
    double base = cm.buy_base(q.market, q.low);
    double far = q.mid;
    bool flat = std::isnan(far) || !(far - base > 0.0);
    return {base, far, cm.max_daily_qty(base), flat};
}

LadderShape bid_shape(const DayQuote& q, const CostModel& cm) {
    double base = q.market;
    double far = q.low;
    bool flat = std::isnan(far) || !(base - far > 0.0);
    return {base, far, cm.max_daily_qty(base), flat};
}

book::Price unit_ticks(const LadderShape& s, book::Side resting, std::int64_t k,
                       LadderPricing p) {
    double tpd = static_cast<double>(ticks_per_dollar(p));
    double price = s.base;
    if (!s.flat) {
        if (resting == book::Side::Sell)
            price = s.base + (s.far - s.base) * (static_cast<double>(k) + 0.5) /
                                 static_cast<double>(s.depth);
        else
            price = s.base - (s.base - s.far) * (static_cast<double>(k) + 0.5) /
                                 static_cast<double>(s.depth);
    }
    book::Price t = static_cast<book::Price>(std::llround(price * tpd));
    return std::max<book::Price>(t, 0);
}

void build_day_ladder(const DayQuote& q, const CostModel& cm, LadderPricing p,
                      book::OrderBook& out) {
    out.clear();
    build_day_ladder(q, cm, p, 0, [&](const book::BookEvent& e) {
        out.add(e.order_id, e.side, e.price, e.qty);
    });
}

}  // namespace pkmn
