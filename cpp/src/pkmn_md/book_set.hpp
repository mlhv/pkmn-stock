#pragma once

// One OrderBook per symbol, driven by feed-neutral BookEvents.
//
//   Add      book(symbol).add(id, side, price, qty)
//   Modify   book(symbol).modify(id, price, qty, keep_priority)
//   Delete   book(symbol).remove(id)
//   Execute  book(symbol).execute(id, qty)      (the trade price is not used)
//   Clear    book(symbol).clear(): that symbol only; its ids are forgotten
//
// A book is created on the first event naming its symbol, so a Modify,
// Delete or Execute for an id never added lands in an empty book and counts
// unknown_id there. Books are never destroyed (OrderBook is not movable once
// its id index is live, and anomaly counters must survive clears): clear()
// and clear_all() empty them in place.

#include <cstddef>
#include <cstdint>
#include <unordered_map>

#include "pkmn_book/order_book.hpp"
#include "pkmn_md/events.hpp"

namespace pkmn::md {

class BookSet {
  public:
    void apply(const BookEvent& ev);
    // Nullptr if no event has named `symbol` yet.
    const book::OrderBook* find(std::uint64_t symbol) const;
    // Sum of every book's anomaly counters (clears do not reset them).
    book::Anomalies total_anomalies() const;

    // Empty every book (feed restart: the old epoch's ids cannot be trusted).
    void clear_all();
    std::uint64_t clear_alls() const { return clear_alls_; }
    std::size_t symbols() const { return books_.size(); }
    // Resting orders across all books.
    std::size_t order_count() const;

  private:
    // Node-based: element addresses are stable across rehash, which the
    // non-movable OrderBook needs.
    std::unordered_map<std::uint64_t, book::OrderBook> books_;
    std::uint64_t clear_alls_ = 0;
};

}  // namespace pkmn::md
