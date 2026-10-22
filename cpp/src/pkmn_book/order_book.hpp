#pragma once

// Price-time-priority limit order book. Pure C++, no dependencies; fed by the
// IEX DEEP+ replayer and (later) by the Pokemon backtester. Prices and
// quantities are integers (price in the feed's fixed-point ticks).
//
// Structure: one std::map of price levels per side, each level a FIFO
// std::list of resting orders, plus an id -> list-iterator index for O(log n)
// amend/cancel/execute. Bids are stored ascending too (best = last); `best`
// and `depth` hide that.

#include <cstddef>
#include <cstdint>
#include <list>
#include <map>
#include <optional>
#include <unordered_map>
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
    // The id index holds list iterators into this book's own levels; a
    // defaulted copy would alias them.
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
    std::size_t order_count() const { return index_.size(); }
    void clear();
    const Anomalies& anomalies() const { return anomalies_; }

  private:
    struct Order {
        OrderId id;
        Qty qty;
    };
    struct LevelData {
        std::list<Order> queue;
        Qty qty = 0;
    };
    using Ladder = std::map<Price, LevelData>;
    struct Loc {
        Side side;
        Price price;
        std::list<Order>::iterator it;
    };

    Ladder& ladder(Side s) { return s == Side::Buy ? bids_ : asks_; }
    const Ladder& ladder(Side s) const { return s == Side::Buy ? bids_ : asks_; }
    void erase_order(std::unordered_map<OrderId, Loc>::iterator idx);

    Ladder bids_, asks_;
    std::unordered_map<OrderId, Loc> index_;
    Anomalies anomalies_;
};

}  // namespace pkmn::book
