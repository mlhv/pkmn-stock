#include <nanobind/nanobind.h>
#include <nanobind/ndarray.h>
#include <nanobind/stl/string.h>

#include <cstdint>
#include <memory>
#include <utility>
#include <vector>

#include "pkmn_engine/backtest.hpp"
#include "pkmn_engine/strategies/callback.hpp"
#include "pkmn_engine/strategies/factory.hpp"
#include "pkmn_engine/version.hpp"

namespace nb = nanobind;
using namespace pkmn;

namespace {

template <typename T>
using Arr = nb::ndarray<const T, nb::ndim<1>, nb::c_contig, nb::device::cpu>;

// One flat entry point: arrays in, plain Python lists out. Crossed once per
// run — clarity over marshaling micro-optimizations.
nb::object run_backtest_py(
    Arr<std::int32_t> trading_days,
    Arr<std::int32_t> row_day, Arr<std::int32_t> row_asset, Arr<double> row_market,
    Arr<double> row_mid, Arr<double> row_low,
    Arr<std::int32_t> ev_day, Arr<std::int32_t> ev_asset, Arr<double> ev_price,
    Arr<std::int64_t> prod_id, Arr<std::int8_t> prod_kind, Arr<std::int32_t> prod_released,
    const std::string& strategy_name, nb::dict params, std::int8_t universe_kind,
    double fee_rate, double shipping_per_line,
    Arr<double> tier_thresholds, Arr<std::int64_t> tier_qtys,
    std::int64_t fallback_max_qty, bool impact_enabled,
    double initial_cash, nb::object callback) {
    std::size_t n_assets = prod_id.size();

    std::vector<Day> days(trading_days.data(), trading_days.data() + trading_days.size());
    std::vector<PriceRow> rows(row_day.size());
    for (std::size_t i = 0; i < rows.size(); ++i) {
        rows[i] = PriceRow{row_day(i), row_asset(i), row_market(i), row_mid(i), row_low(i)};
    }
    std::vector<MarkEvent> events(ev_day.size());
    for (std::size_t i = 0; i < events.size(); ++i) {
        events[i] = MarkEvent{ev_day(i), ev_asset(i), ev_price(i)};
    }
    MarketView market(n_assets, std::move(days), std::move(rows), std::move(events));

    ProductTable products;
    products.product_id.assign(prod_id.data(), prod_id.data() + n_assets);
    products.kind.assign(prod_kind.data(), prod_kind.data() + n_assets);
    products.released_on.assign(prod_released.data(), prod_released.data() + n_assets);

    CostModel cm;
    cm.fee_rate = fee_rate;
    cm.shipping_per_line = shipping_per_line;
    cm.liquidity_tiers.clear();
    for (std::size_t i = 0; i < tier_thresholds.size(); ++i) {
        cm.liquidity_tiers.emplace_back(tier_thresholds(i), tier_qtys(i));
    }
    cm.fallback_max_qty = fallback_max_qty;
    cm.impact_enabled = impact_enabled;

    std::unique_ptr<Strategy> strategy;
    if (!callback.is_none()) {
        nb::callable cb = nb::cast<nb::callable>(callback);
        strategy = std::make_unique<CallbackStrategy>([cb](const Context& ctx) {
            nb::list pos;
            for (const auto& e : ctx.positions.entries()) {
                pos.append(nb::make_tuple(e.key, e.value.quantity, e.value.avg_cost,
                                          e.value.opened_on));
            }
            nb::object ret = cb(ctx.today, pos, ctx.cash);
            std::vector<Order> orders;
            for (nb::handle h : nb::cast<nb::list>(ret)) {
                auto t = nb::cast<nb::tuple>(h);
                orders.push_back(
                    Order{nb::cast<AssetId>(t[0]), nb::cast<std::int64_t>(t[1])});
            }
            return orders;
        });
    } else {
        ParamMap pmap;
        for (auto item : params) {
            pmap[nb::cast<std::string>(item.first)] = nb::cast<double>(item.second);
        }
        strategy = make_strategy(strategy_name, pmap, universe_kind);
    }

    BacktestResult res = run_backtest(market, products, *strategy, cm, initial_cash);

    nb::list out_days, out_equity, out_fills;
    for (Day d : res.days) out_days.append(d);
    for (double e : res.equity) out_equity.append(e);
    for (const Fill& f : res.fills) {
        out_fills.append(
            nb::make_tuple(f.day, f.asset, f.quantity, f.price, f.fees, f.impact));
    }
    return nb::make_tuple(out_days, out_equity, out_fills);
}

}  // namespace

NB_MODULE(_engine, m) {
    m.doc() = "pkmn_quant native backtest engine";
    m.attr("__version__") = pkmn::engine_version();
    m.def("run_backtest", &run_backtest_py, nb::arg("trading_days"), nb::arg("row_day"),
          nb::arg("row_asset"), nb::arg("row_market"), nb::arg("row_mid"), nb::arg("row_low"),
          nb::arg("ev_day"), nb::arg("ev_asset"), nb::arg("ev_price"), nb::arg("prod_id"),
          nb::arg("prod_kind"), nb::arg("prod_released"), nb::arg("strategy_name"),
          nb::arg("params"), nb::arg("universe_kind"), nb::arg("fee_rate"),
          nb::arg("shipping_per_line"), nb::arg("tier_thresholds"), nb::arg("tier_qtys"),
          nb::arg("fallback_max_qty"), nb::arg("impact_enabled"), nb::arg("initial_cash"),
          nb::arg("callback").none());
}
