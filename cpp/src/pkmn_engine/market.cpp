#include "pkmn_engine/market.hpp"

#include <algorithm>
#include <cmath>
#include <limits>
#include <stdexcept>

namespace pkmn {

namespace {
constexpr double kNaN = std::numeric_limits<double>::quiet_NaN();
}

MarketView::MarketView(std::size_t n_assets, std::vector<Day> trading_days,
                       std::vector<PriceRow> rows, std::vector<MarkEvent> events)
    : n_assets_(n_assets),
      trading_days_(std::move(trading_days)),
      rows_(std::move(rows)),
      events_(std::move(events)),
      cur_market_(n_assets, kNaN),
      cur_mid_(n_assets, kNaN),
      cur_low_(n_assets, kNaN),
      stamp_(n_assets, 0),
      marks_(n_assets) {
    for (std::size_t i = 1; i < trading_days_.size(); ++i) {
        if (trading_days_[i] <= trading_days_[i - 1])
            throw std::invalid_argument("trading_days must be strictly increasing");
    }
    for (std::size_t i = 0; i < rows_.size(); ++i) {
        const auto& r = rows_[i];
        if (r.asset < 0 || static_cast<std::size_t>(r.asset) >= n_assets_)
            throw std::invalid_argument("PriceRow.asset out of range");
        if (i > 0 && r.day < rows_[i - 1].day)
            throw std::invalid_argument("rows must be date-sorted");
    }
    for (std::size_t i = 0; i < events_.size(); ++i) {
        const auto& e = events_[i];
        if (e.asset < 0 || static_cast<std::size_t>(e.asset) >= n_assets_)
            throw std::invalid_argument("MarkEvent.asset out of range");
        if (i > 0 && e.day < events_[i - 1].day)
            throw std::invalid_argument("events must be date-sorted");
    }

    // day partition
    std::size_t begin = 0;
    for (std::size_t i = 0; i <= rows_.size(); ++i) {
        if (i == rows_.size() || (i > 0 && rows_[i].day != rows_[begin].day)) {
            if (i > begin) day_ranges_[rows_[begin].day] = {begin, i};
            begin = i;
        }
    }

    // per-asset CSR (stable: rows_ is date-sorted, so each slice is too)
    std::vector<std::size_t> counts(n_assets_, 0);
    for (const auto& r : rows_) ++counts[static_cast<std::size_t>(r.asset)];
    h_off_.assign(n_assets_ + 1, 0);
    for (std::size_t a = 0; a < n_assets_; ++a) h_off_[a + 1] = h_off_[a] + counts[a];
    h_day_.resize(rows_.size());
    h_price_.resize(rows_.size());
    std::vector<std::size_t> cursor(h_off_.begin(), h_off_.end() - 1);
    for (const auto& r : rows_) {
        auto& c = cursor[static_cast<std::size_t>(r.asset)];
        h_day_[c] = r.day;
        h_price_[c] = r.market;
        ++c;
    }
    h_prefmax_.resize(rows_.size());
    for (std::size_t a = 0; a < n_assets_; ++a) {
        double running = -std::numeric_limits<double>::infinity();
        for (std::size_t i = h_off_[a]; i < h_off_[a + 1]; ++i) {
            running = std::max(running, h_price_[i]);
            h_prefmax_[i] = running;
        }
    }
}

void MarketView::reset() {
    ++epoch_;  // invalidates all current-day stamps
    marks_ = InsertionMap<double>(n_assets_);
    ev_idx_ = 0;
    watermark_ = kNullDay;
}

void MarketView::load_day(Day day) {
    ++epoch_;
    auto it = day_ranges_.find(day);
    if (it == day_ranges_.end()) return;  // trading day with no prints
    for (std::size_t i = it->second.first; i < it->second.second; ++i) {
        const auto& r = rows_[i];
        auto a = static_cast<std::size_t>(r.asset);
        cur_market_[a] = r.market;
        cur_mid_[a] = r.mid;
        cur_low_[a] = r.low;
        stamp_[a] = epoch_;
    }
}

double MarketView::current_(const std::vector<double>& table, AssetId a) const {
    auto i = static_cast<std::size_t>(a);
    return stamp_[i] == epoch_ ? table[i] : kNaN;
}

const InsertionMap<double>& MarketView::marks_until(Day day) {
    if (watermark_ != kNullDay && day < watermark_)
        throw std::logic_error("marks_until must be queried in non-decreasing day order");
    while (ev_idx_ < events_.size() && events_[ev_idx_].day <= day) {
        marks_.set(events_[ev_idx_].asset, events_[ev_idx_].price);
        ++ev_idx_;
    }
    watermark_ = day;
    return marks_;
}

std::pair<std::size_t, std::size_t> MarketView::range_(AssetId a) const {
    auto i = static_cast<std::size_t>(a);
    return {h_off_[i], h_off_[i + 1]};
}

std::optional<double> MarketView::last_price_at_or_before(AssetId a, Day d) const {
    auto [b, e] = range_(a);
    auto it = std::upper_bound(h_day_.begin() + b, h_day_.begin() + e, d);
    if (it == h_day_.begin() + b) return std::nullopt;
    return h_price_[static_cast<std::size_t>(it - h_day_.begin()) - 1];
}

std::optional<double> MarketView::peak_until(AssetId a, Day d) const {
    auto [b, e] = range_(a);
    auto it = std::upper_bound(h_day_.begin() + b, h_day_.begin() + e, d);
    if (it == h_day_.begin() + b) return std::nullopt;
    return h_prefmax_[static_cast<std::size_t>(it - h_day_.begin()) - 1];
}

std::optional<double> MarketView::max_in_window(AssetId a, Day from, Day to) const {
    auto [b, e] = range_(a);
    auto lo = std::lower_bound(h_day_.begin() + b, h_day_.begin() + e, from) - h_day_.begin();
    auto hi = std::upper_bound(h_day_.begin() + b, h_day_.begin() + e, to) - h_day_.begin();
    if (lo >= hi) return std::nullopt;
    double m = h_price_[static_cast<std::size_t>(lo)];
    for (auto i = lo + 1; i < hi; ++i)
        m = std::max(m, h_price_[static_cast<std::size_t>(i)]);
    return m;
}

}  // namespace pkmn
