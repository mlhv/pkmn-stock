#pragma once

// Port of engine/data.py MarketData, restructured for arrays: eager day
// partition (epoch-stamped dense lookups), incremental marks cursor, and
// per-asset CSR series for the strategy history queries.

#include <cstddef>
#include <cstdint>
#include <optional>
#include <unordered_map>
#include <utility>
#include <vector>

#include "pkmn_engine/types.hpp"

namespace pkmn {

struct PriceRow {
    Day day;
    AssetId asset;
    double market;
    double mid;  // NaN = source row had no value (Python None)
    double low;  // NaN = missing
};

struct MarkEvent {
    Day day;
    AssetId asset;
    double price;
};

// Per-asset product attributes, indexed by AssetId.
struct ProductTable {
    std::vector<std::int64_t> product_id;
    std::vector<std::int8_t> kind;  // 0 sealed, 1 single, -1 other
    std::vector<Day> released_on;   // kNullDay when null

    std::size_t n_assets() const { return product_id.size(); }
};

class MarketView {
  public:
    MarketView(std::size_t n_assets, std::vector<Day> trading_days,
               std::vector<PriceRow> rows, std::vector<MarkEvent> events);

    std::size_t n_assets() const { return n_assets_; }
    const std::vector<Day>& days() const { return trading_days_; }

    // Restart the marks cursor and current-day tables (run_backtest calls
    // this first so repeated runs on one view are independent).
    void reset();

    // Stamp the day's prints into the dense current-day tables. O(rows that
    // day), amortized O(1) queries after.
    void load_day(Day day);
    double price(AssetId a) const { return current_(cur_market_, a); }
    double mid(AssetId a) const { return current_(cur_mid_, a); }
    double low(AssetId a) const { return current_(cur_low_, a); }

    // data.py marks_on: carry-forward marks as of `day`. Monotone only —
    // the event loop never goes backwards; a backwards query is a bug.
    const InsertionMap<double>& marks_until(Day day);

    // Strategy history queries (anti-look-ahead: callers pass day <= today).
    std::optional<double> last_price_at_or_before(AssetId a, Day d) const;
    std::optional<double> peak_until(AssetId a, Day d) const;
    std::optional<double> max_in_window(AssetId a, Day from, Day to) const;

  private:
    double current_(const std::vector<double>& table, AssetId a) const;
    std::pair<std::size_t, std::size_t> range_(AssetId a) const;

    std::size_t n_assets_;
    std::vector<Day> trading_days_;
    std::vector<PriceRow> rows_;  // date-sorted
    std::vector<MarkEvent> events_;

    // day -> [begin, end) into rows_
    std::unordered_map<Day, std::pair<std::size_t, std::size_t>> day_ranges_;

    // current-day tables, epoch-stamped so load_day is O(day's rows)
    std::vector<double> cur_market_, cur_mid_, cur_low_;
    std::vector<std::uint32_t> stamp_;
    std::uint32_t epoch_ = 0;

    // per-asset CSR over rows_ (day-sorted within each asset)
    std::vector<std::size_t> h_off_;   // n_assets_+1
    std::vector<Day> h_day_;
    std::vector<double> h_price_;
    std::vector<double> h_prefmax_;    // running max within each asset slice

    // marks cursor
    InsertionMap<double> marks_;
    std::size_t ev_idx_ = 0;
    Day watermark_ = kNullDay;
};

}  // namespace pkmn
