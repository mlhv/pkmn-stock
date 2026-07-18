#pragma once

#include <cstddef>
#include <cstdint>
#include <limits>
#include <vector>

namespace pkmn {

// Dense asset index assigned by the Python adapter: assets sorted by
// (product_id, sub_type), ids 0..n_assets-1. Comparing AssetId therefore
// orders by (product_id, sub_type).
using AssetId = std::int32_t;

// Days since 1970-01-01 (polars Date physical repr). Calendar arithmetic
// (Python timedelta / .days) is plain integer subtraction.
using Day = std::int32_t;

inline constexpr Day kNullDay = std::numeric_limits<Day>::min();

// Mirrors engine/execution.py Order.
struct Order {
    AssetId asset;
    std::int64_t quantity;  // > 0 buy, < 0 sell
};

// Mirrors engine/portfolio.py Fill.
struct Fill {
    Day day;
    AssetId asset;
    std::int64_t quantity;
    double price;
    double fees;
    double impact;
};

// Mirrors engine/portfolio.py Position. opened_on is always set by engine
// fills (Python's None case exists only for hand-built test portfolios,
// which cannot reach the C++ engine).
struct Position {
    std::int64_t quantity;
    double avg_cost;
    Day opened_on;
};

// Insertion-ordered map with dense int keys — Python dict semantics:
// iteration in first-insertion order, re-assignment keeps position,
// erase + re-insert moves to the back. O(1) find/set; erase is O(n)
// (n = live entries, small: positions/filled_today).
template <typename V>
class InsertionMap {
  public:
    struct Entry {
        AssetId key;
        V value;
    };

    explicit InsertionMap(std::size_t n_keys) : index_(n_keys, -1) {}

    V* find(AssetId k) {
        auto i = index_[static_cast<std::size_t>(k)];
        return i < 0 ? nullptr : &entries_[static_cast<std::size_t>(i)].value;
    }

    const V* find(AssetId k) const {
        auto i = index_[static_cast<std::size_t>(k)];
        return i < 0 ? nullptr : &entries_[static_cast<std::size_t>(i)].value;
    }

    bool contains(AssetId k) const { return index_[static_cast<std::size_t>(k)] >= 0; }

    void set(AssetId k, V v) {
        auto& slot = index_[static_cast<std::size_t>(k)];
        if (slot < 0) {
            slot = static_cast<std::int64_t>(entries_.size());
            entries_.push_back(Entry{k, std::move(v)});
        } else {
            entries_[static_cast<std::size_t>(slot)].value = std::move(v);
        }
    }

    void erase(AssetId k) {
        auto i = index_[static_cast<std::size_t>(k)];
        if (i < 0) return;
        entries_.erase(entries_.begin() + i);
        index_[static_cast<std::size_t>(k)] = -1;
        for (std::size_t j = static_cast<std::size_t>(i); j < entries_.size(); ++j) {
            index_[static_cast<std::size_t>(entries_[j].key)] = static_cast<std::int64_t>(j);
        }
    }

    const std::vector<Entry>& entries() const { return entries_; }
    std::size_t size() const { return entries_.size(); }

  private:
    std::vector<Entry> entries_;       // insertion order
    std::vector<std::int64_t> index_;  // AssetId -> position in entries_, or -1
};

}  // namespace pkmn
