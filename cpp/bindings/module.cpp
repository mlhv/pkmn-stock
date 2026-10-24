#include <nanobind/nanobind.h>
#include <nanobind/ndarray.h>
#include <nanobind/stl/string.h>
#include <nanobind/stl/vector.h>

#include <cerrno>
#include <cmath>
#include <cstring>
#include <span>
#include <cstdint>
#include <cstdio>
#include <fstream>
#include <limits>
#include <memory>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

#include "pkmn_engine/backtest.hpp"
#include "pkmn_engine/execution_book.hpp"
#include "pkmn_engine/strategies/callback.hpp"
#include "pkmn_engine/strategies/factory.hpp"
#include "pkmn_engine/version.hpp"
#include "pkmn_wire/decoder.hpp"
#include "pkmn_wire/encoder.hpp"
#include "pkmn_wire/replay.hpp"

namespace nb = nanobind;
using namespace pkmn;

namespace {

template <typename T>
using Arr = nb::ndarray<const T, nb::ndim<1>, nb::c_contig, nb::device::cpu>;

// std::invalid_argument -> ValueError.
void check_tiers(const Arr<double>& thresholds, const Arr<std::int64_t>& qtys) {
    if (thresholds.size() != qtys.size())
        throw std::invalid_argument("tier_thresholds and tier_qtys differ in length (" +
                                    std::to_string(thresholds.size()) + " vs " +
                                    std::to_string(qtys.size()) + ")");
}

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
    double initial_cash,
    Arr<std::int32_t> holding_asset, Arr<std::int64_t> holding_qty,
    Arr<double> holding_cost, Arr<std::int32_t> holding_opened,
    const std::string& execution,
    nb::object callback) {
    // Validated before any copying. std::invalid_argument -> ValueError.
    ExecutionMode mode;
    if (execution == "formula") mode = ExecutionMode::Formula;
    else if (execution == "book") mode = ExecutionMode::Book;
    else if (execution == "book-exact-test") mode = ExecutionMode::BookExactTest;
    else
        throw std::invalid_argument("unknown execution '" + execution +
                                    "'; choose formula, book or book-exact-test");
    check_tiers(tier_thresholds, tier_qtys);
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
    cm.execution = mode;

    std::vector<SeedPosition> holdings(holding_asset.size());
    for (std::size_t i = 0; i < holdings.size(); ++i) {
        holdings[i] = SeedPosition{holding_asset(i), holding_qty(i),
                                   holding_cost(i), holding_opened(i)};
    }

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

    // Native path: the engine touches no Python objects (all inputs were
    // copied to std::vectors above), so drop the GIL and let other fold
    // workers run concurrently. The bridge path re-enters Python every bar
    // and must keep the GIL.
    BacktestResult res;
    if (callback.is_none()) {
        nb::gil_scoped_release release;
        res = run_backtest(market, products, *strategy, cm, initial_cash, holdings);
    } else {
        res = run_backtest(market, products, *strategy, cm, initial_cash, holdings);
    }

    nb::list out_days, out_equity, out_fills;
    for (Day d : res.days) out_days.append(d);
    for (double e : res.equity) out_equity.append(e);
    for (const Fill& f : res.fills) {
        out_fills.append(
            nb::make_tuple(f.day, f.asset, f.quantity, f.price, f.fees, f.impact));
    }
    return nb::make_tuple(out_days, out_equity, out_fills);
}

// Hypothetical book-priced exit (pkmn horizon's still-open lots). `low` NaN =
// missing. Always Cent: the exact ladder is test-only and never a user surface.
double book_sell_impact_py(double market, double low, std::int64_t qty,
                           Arr<double> tier_thresholds, Arr<std::int64_t> tier_qtys,
                           std::int64_t fallback_max_qty) {
    check_tiers(tier_thresholds, tier_qtys);
    CostModel cm;
    cm.liquidity_tiers.clear();
    for (std::size_t i = 0; i < tier_thresholds.size(); ++i) {
        cm.liquidity_tiers.emplace_back(tier_thresholds(i), tier_qtys(i));
    }
    cm.fallback_max_qty = fallback_max_qty;
    cm.impact_enabled = true;  // the ladder builder requires it
    cm.execution = ExecutionMode::Book;
    DayQuote q{market, std::numeric_limits<double>::quiet_NaN(), low};
    return hypothetical_sell_impact(q, cm, LadderPricing::Cent, qty);
}

// Book-mode CostModel for the replay digests: the given tiers, impact on (the
// ladder builder requires it).
CostModel ladder_cost_model(const Arr<double>& thresholds, const Arr<std::int64_t>& qtys,
                            std::int64_t fallback_max_qty) {
    check_tiers(thresholds, qtys);
    CostModel cm;
    cm.liquidity_tiers.clear();
    for (std::size_t i = 0; i < thresholds.size(); ++i)
        cm.liquidity_tiers.emplace_back(thresholds(i), qtys(i));
    cm.fallback_max_qty = fallback_max_qty;
    cm.impact_enabled = true;
    cm.execution = ExecutionMode::Book;
    return cm;
}

template <typename T>
using OutArr = nb::ndarray<nb::numpy, T, nb::ndim<1>>;

// Hands a vector to numpy without copying; the capsule owns it.
template <typename T>
OutArr<T> to_numpy(std::vector<T>&& v) {
    auto* p = new std::vector<T>(std::move(v));
    nb::capsule owner(p, [](void* x) noexcept { delete static_cast<std::vector<T>*>(x); });
    return OutArr<T>(p->data(), {p->size()}, owner);
}

[[noreturn]] void raise_os_error(const std::string& msg) {
    PyErr_SetString(PyExc_OSError, msg.c_str());
    throw nb::python_error();
}

// Writes the wire file. Raises OSError if the file cannot be opened or a
// write fails (e.g. disk full); a failed write removes the partial file, so a
// returned count always describes a complete file. Validation errors
// (std::invalid_argument -> ValueError) are raised before the file is opened.
std::uint64_t encode_wire_py(Arr<std::int64_t> inst_product_id,
                             const std::vector<std::string>& inst_sub_type,
                             Arr<std::int32_t> row_day, Arr<std::uint32_t> row_instrument,
                             Arr<std::uint32_t> row_market, Arr<std::uint32_t> row_low,
                             Arr<std::uint32_t> row_mid, Arr<std::uint32_t> row_high,
                             const std::string& path) {
    auto span = [](const auto& a) { return std::span(a.data(), a.size()); };
    wire::EncodeInput in{span(inst_product_id), std::span<const std::string>(inst_sub_type),
                         span(row_day), span(row_instrument), span(row_market), span(row_low),
                         span(row_mid), span(row_high)};
    wire::validate(in);  // a bad input never truncates an existing file
    std::ofstream f(path, std::ios::binary | std::ios::trunc);
    if (!f) raise_os_error("cannot open " + path + " for writing: " + std::strerror(errno));
    std::uint64_t n = 0;
    try {
        n = wire::encode(in, f);
        f.close();
        if (!f) throw std::runtime_error("close failed");
    } catch (const std::runtime_error& e) {
        f.close();
        std::remove(path.c_str());
        raise_os_error("writing " + path + " failed (" + e.what() + "); partial file removed");
    }
    return n;
}

nb::tuple wire_ladder_digests_py(const std::string& path, Arr<double> tier_thresholds,
                                 Arr<std::int64_t> tier_qtys, std::int64_t fallback_max_qty) {
    const CostModel cm = ladder_cost_model(tier_thresholds, tier_qtys, fallback_max_qty);
    std::vector<std::int32_t> day;
    std::vector<std::uint32_t> inst;
    std::vector<std::uint64_t> digest;
    wire::DecodeStats st;
    std::vector<wire::InstrumentInfo> instruments;
    {
        nb::gil_scoped_release release;
        auto on = [&](std::int32_t d, std::uint32_t i, const book::OrderBook& b) {
            day.push_back(d);
            inst.push_back(i);
            digest.push_back(wire::ladder_digest(b));
        };
        wire::LadderReplay<decltype(on)> rep(cm, LadderPricing::Cent, true, on);
        st = wire::decode_file(path, rep);
        instruments = rep.instruments();
    }
    std::vector<std::int64_t> pid(inst.size());
    std::vector<std::string> sub(inst.size());
    for (std::size_t k = 0; k < inst.size(); ++k) {
        if (inst[k] >= instruments.size())
            throw std::invalid_argument("quote for undeclared instrument " + std::to_string(inst[k]));
        pid[k] = instruments[inst[k]].product_id;
        sub[k] = instruments[inst[k]].sub_type;
    }
    nb::dict stats;
    stats["packets"] = st.packets;
    stats["messages"] = st.messages;
    stats["unknown"] = st.unknown;
    stats["short_messages"] = st.short_messages;
    stats["short_packets"] = st.short_packets;
    stats["gaps"] = st.gaps;
    stats["gap_messages"] = st.gap_messages;
    stats["duplicates"] = st.duplicates;
    stats["bad_magic"] = st.bad_magic;
    return nb::make_tuple(to_numpy(std::move(day)), to_numpy(std::move(pid)), nb::cast(sub),
                          to_numpy(std::move(digest)), stats);
}

// One digest per row through the backtester's own DayQuote path (the doubles
// straight from PreparedMarket). A NaN market builds no ladder (the wire side
// skips those quotes too) and gets digest 0.
OutArr<std::uint64_t> ladder_digests_py(Arr<std::int32_t> row_day, Arr<std::int32_t> row_asset,
                                        Arr<double> row_market, Arr<double> row_mid,
                                        Arr<double> row_low, Arr<double> tier_thresholds,
                                        Arr<std::int64_t> tier_qtys,
                                        std::int64_t fallback_max_qty) {
    const std::size_t n = row_day.size();
    if (row_asset.size() != n || row_market.size() != n || row_mid.size() != n ||
        row_low.size() != n)
        throw std::invalid_argument("row arrays differ in length");
    const CostModel cm = ladder_cost_model(tier_thresholds, tier_qtys, fallback_max_qty);
    std::vector<std::uint64_t> out(n);
    {
        nb::gil_scoped_release release;
        book::OrderBook b;
        for (std::size_t i = 0; i < n; ++i) {
            if (std::isnan(row_market(i))) {
                out[i] = 0;
                continue;
            }
            build_day_ladder(DayQuote{row_market(i), row_mid(i), row_low(i)}, cm,
                             LadderPricing::Cent, b);
            out[i] = wire::ladder_digest(b);
        }
    }
    return to_numpy(std::move(out));
}

// Real-data equivalence gate (Plan B Task 7, scripts/book_equivalence.py).
// For every row with a printed market (> 0, as execute_book), builds the
// test-only ExactTest ladder (1e9 ticks per dollar) of modelled resting orders
// (synthetic, built from the day's tcgcsv quote) into an OrderBook, takes the
// units out one at a time with match(..., 1, ...) so the book's own priority
// ordering decides the unit order, forms prefix sums, and compares every
// (used, qty) pair with used + qty <= depth against CostModel::buy_impact /
// sell_impact. The book value is NOT clamped at 0 here (stronger than the
// fill path, which clamps). Impact on, like the ladder builder requires.
nb::dict ladder_equivalence_py(Arr<double> row_market, Arr<double> row_mid, Arr<double> row_low,
                               Arr<double> tier_thresholds, Arr<std::int64_t> tier_qtys,
                               std::int64_t fallback_max_qty) {
    const std::size_t n = row_market.size();
    if (row_mid.size() != n || row_low.size() != n)
        throw std::invalid_argument("row arrays differ in length");
    const CostModel cm = ladder_cost_model(tier_thresholds, tier_qtys, fallback_max_qty);
    const double tpd = static_cast<double>(ticks_per_dollar(LadderPricing::ExactTest));
    std::uint64_t rows = 0, checks = 0;
    double max_err = 0.0;
    std::int64_t worst_row = -1, worst_used = -1, worst_qty = -1;
    const char* worst_side = "";
    {
        nb::gil_scoped_release release;
        book::OrderBook b;
        std::vector<book::Execution> execs;
        std::vector<std::int64_t> pre;
        for (std::size_t i = 0; i < n; ++i) {
            const double m = row_market(i);
            if (std::isnan(m) || m <= 0.0) continue;
            ++rows;
            const double mid = row_mid(i), low = row_low(i);
            build_day_ladder(DayQuote{m, mid, low}, cm, LadderPricing::ExactTest, b);
            for (int side = 0; side < 2; ++side) {
                const bool buy = side == 0;
                // A buy takes the asks; a sell hits the bids.
                pre.assign(1, 0);
                for (;;) {
                    execs.clear();
                    book::Qty got =
                        buy ? b.match(book::Side::Buy, 1,
                                      std::numeric_limits<book::Price>::max(), execs)
                            : b.match(book::Side::Sell, 1, 0, execs);
                    if (got == 0) break;
                    pre.push_back(pre.back() + execs.front().price * execs.front().qty);
                }
                const auto depth = static_cast<std::int64_t>(pre.size()) - 1;
                for (std::int64_t used = 0; used < depth; ++used) {
                    for (std::int64_t qty = 1; used + qty <= depth; ++qty) {
                        const double notional =
                            static_cast<double>(pre[static_cast<std::size_t>(used + qty)] -
                                                pre[static_cast<std::size_t>(used)]) /
                            tpd;
                        const double mq = static_cast<double>(qty) * m;
                        const double book_imp = buy ? notional - mq : mq - notional;
                        const double formula = buy ? cm.buy_impact(m, mid, low, qty, used)
                                                   : cm.sell_impact(m, low, qty, used);
                        const double err = std::fabs(book_imp - formula);
                        ++checks;
                        if (err > max_err || worst_row < 0) {
                            if (err > max_err) max_err = err;
                            worst_row = static_cast<std::int64_t>(i);
                            worst_side = buy ? "buy" : "sell";
                            worst_used = used;
                            worst_qty = qty;
                        }
                    }
                }
            }
        }
    }
    nb::dict out;
    out["rows"] = rows;
    out["checks"] = checks;
    out["max_abs_err"] = max_err;
    out["worst_row"] = worst_row;
    out["worst_side"] = std::string(worst_side);
    out["worst_used"] = worst_used;
    out["worst_qty"] = worst_qty;
    return out;
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
          nb::arg("fallback_max_qty"), nb::arg("impact_enabled"),
          nb::arg("initial_cash"), nb::arg("holding_asset"), nb::arg("holding_qty"),
          nb::arg("holding_cost"), nb::arg("holding_opened"), nb::arg("execution"),
          nb::arg("callback").none());
    m.def("book_sell_impact", &book_sell_impact_py, nb::arg("market"), nb::arg("low"),
          nb::arg("qty"), nb::arg("tier_thresholds"), nb::arg("tier_qtys"),
          nb::arg("fallback_max_qty"));
    m.def("encode_wire", &encode_wire_py, nb::arg("inst_product_id"), nb::arg("inst_sub_type"),
          nb::arg("row_day"), nb::arg("row_instrument"), nb::arg("row_market"),
          nb::arg("row_low"), nb::arg("row_mid"), nb::arg("row_high"), nb::arg("path"));
    m.def("wire_ladder_digests", &wire_ladder_digests_py, nb::arg("path"),
          nb::arg("tier_thresholds"), nb::arg("tier_qtys"), nb::arg("fallback_max_qty"));
    m.def("ladder_digests", &ladder_digests_py, nb::arg("row_day"), nb::arg("row_asset"),
          nb::arg("row_market"), nb::arg("row_mid"), nb::arg("row_low"),
          nb::arg("tier_thresholds"), nb::arg("tier_qtys"), nb::arg("fallback_max_qty"));
    m.def("ladder_equivalence", &ladder_equivalence_py, nb::arg("row_market"),
          nb::arg("row_mid"), nb::arg("row_low"), nb::arg("tier_thresholds"),
          nb::arg("tier_qtys"), nb::arg("fallback_max_qty"));
}
