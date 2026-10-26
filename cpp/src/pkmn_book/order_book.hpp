#pragma once

// Price-time-priority limit order book. Pure C++, no dependencies; fed by the
// IEX DEEP+ replayer and (later) by the Pokemon backtester. Prices and
// quantities are integers (price in the feed's fixed-point ticks).
//
// Structure (Task 6, chosen from the baseline profile in
// docs/benchmarks/iex-book.md): resting orders live in a per-book pooled arena
// (std::vector<Node> plus a free list) and are FIFO-linked per level by slot
// index, so steady state allocates nothing per order. An open-addressing
// OrderId -> slot table (linear probing, backward-shift delete) replaces the
// node-based hash map. Each side's levels are a sorted contiguous vector with
// the best price at the back (bids ascending, asks descending), searched from
// the back first because most activity is near the touch. `best` and `depth`
// hide the ordering.

#include <cstddef>
#include <cstdint>
#include <optional>
#include <vector>

namespace pkmn::book {

using Price = std::int64_t;
using Qty = std::int64_t;
using OrderId = std::uint64_t;

enum class Side : std::uint8_t { Buy, Sell };

struct Level {
    Price price;
    Qty qty;
    std::uint32_t orders;
};

struct Execution {
    OrderId resting_id;
    Price price;
    Qty qty;
};

// Feed irregularities the book tolerates instead of throwing.
struct Anomalies {
    std::uint64_t unknown_id = 0, duplicate_add = 0, exec_clamped = 0;
};

class OrderBook {
  public:
    OrderBook() = default;
    // Kept non-copyable (the public API from Task 1). The arena is
    // index-linked, so a copy would no longer alias, but nothing needs one.
    OrderBook(const OrderBook&) = delete;
    OrderBook& operator=(const OrderBook&) = delete;

    // False for qty <= 0, or (counting duplicate_add) when the id is live.
    bool add(OrderId id, Side side, Price price, Qty qty);
    // False + unknown_id if absent.
    bool remove(OrderId id);
    // keep_priority with an unchanged price changes size in place (grow or
    // shrink); otherwise the order goes to the back of the queue at `price`.
    // new_total <= 0 removes it. False + unknown_id if absent.
    bool modify(OrderId id, Price price, Qty new_total, bool keep_priority);
    // Passive fill by id: reduces by min(qty, remaining), keeps the residual's
    // time priority, removes at zero. Clamping counts exec_clamped. Returns
    // the qty applied (0 + unknown_id if absent). Takes no price by design.
    Qty execute(OrderId id, Qty qty);
    // Aggressive order: takes the opposite side best price first, FIFO within
    // a level, never crossing `limit`. Appends one Execution per resting order
    // touched (price = that level's price) to `out`; never rests a remainder.
    Qty match(Side aggressor, Qty qty, Price limit, std::vector<Execution>& out);

    std::optional<Level> best(Side side) const;
    // Replaces `out` with up to n levels, best first.
    void depth(Side side, std::size_t n, std::vector<Level>& out) const;
    Qty qty_at(Side side, Price price) const;
    // Calls f(Price, OrderId, Qty) for every resting order on `side`: best
    // level first, FIFO within a level. Read-only; the book must not change
    // during the walk.
    template <class F>
    void for_each_order(Side side, F&& f) const {
        const std::vector<LevelRec>& v = levels(side);
        for (std::size_t i = v.size(); i-- > 0;) {
            for (Slot s = v[i].head; s != kNil; s = nodes_[s].next) f(v[i].price, nodes_[s].id, nodes_[s].qty);
        }
    }
    std::size_t order_count() const { return count_; }
    void clear();
    const Anomalies& anomalies() const { return anomalies_; }

  private:
    using Slot = std::uint32_t;
    static constexpr Slot kNil = 0xFFFFFFFFu;

    struct Node {
        OrderId id;
        Qty qty;
        Price price;
        Slot prev, next;  // FIFO links within the level; `next` doubles as the free-list link
        Side side;
    };
    struct LevelRec {
        Price price;
        Qty qty;
        Slot head, tail;
        std::uint32_t orders;
    };
    // Open-addressing OrderId -> slot. An entry with slot == kNil is empty.
    struct Entry {
        OrderId id;
        Slot slot;
    };

    std::vector<LevelRec>& levels(Side s) { return s == Side::Buy ? bids_ : asks_; }
    const std::vector<LevelRec>& levels(Side s) const { return s == Side::Buy ? bids_ : asks_; }
    // Index of the level at `price`, or the position it would be inserted at.
    static std::size_t level_pos(const std::vector<LevelRec>& v, Side s, Price price);
    // Index of the existing level at `price` (must exist).
    std::size_t level_of(Side s, Price price) const;

    std::size_t home(OrderId id) const {
        return static_cast<std::size_t>((id * 0x9E3779B97F4A7C15ull) >> shift_);
    }
    // Table index holding `id`, or npos.
    std::size_t find_entry(OrderId id) const;
    void erase_entry(std::size_t pos);
    void grow_table();

    Slot alloc_node();
    void free_node(Slot s);
    // Links node `slot` at the back of its (side, price) level, creating it.
    void link_back(Slot slot);
    // Unlinks node `slot` from its level (erasing an emptied level) and frees it.
    void unlink_free(Slot slot);
    void erase_order(std::size_t entry_pos);

    static constexpr std::size_t npos = static_cast<std::size_t>(-1);

    std::vector<Node> nodes_;
    Slot free_head_ = kNil;
    std::vector<LevelRec> bids_, asks_;
    std::vector<Entry> table_;
    std::size_t mask_ = 0;
    unsigned shift_ = 64;
    std::size_t count_ = 0;
    Anomalies anomalies_;
};

}  // namespace pkmn::book
