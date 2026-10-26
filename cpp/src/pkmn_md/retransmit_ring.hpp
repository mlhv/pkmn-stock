#pragma once

// Bounded store of recent IEX-TP payloads for answering retransmit requests.
// Capacity is counted in payload bytes. When a store would exceed it, the
// oldest-stored entries are evicted first (FIFO by store order). An ordered
// index keyed by (epoch, first_seq) gives O(log n) lookup. Not internally
// synchronized: the owner locks around it.

#include <cstddef>
#include <cstdint>
#include <deque>
#include <map>
#include <span>
#include <utility>
#include <vector>

namespace pkmn::md {

class RetransmitRing {
public:
    explicit RetransmitRing(std::size_t capacity_bytes) : cap_(capacity_bytes) {}

    // Copies the payload. count == 0 (heartbeat) is ignored; a key already
    // stored is left as is.
    void store(std::uint32_t epoch, std::int64_t first_seq, std::uint16_t count,
               std::span<const std::uint8_t> payload);

    // Appends every stored packet of `epoch` overlapping [from, to), ascending
    // by first_seq. Returns false, leaving out unchanged, unless the union of
    // those packets' [first_seq, first_seq + count) covers [from, to) with no
    // hole. An empty range is trivially covered. Spans stay valid until the
    // next store().
    bool collect(std::uint32_t epoch, std::int64_t from, std::int64_t to,
                 std::vector<std::span<const std::uint8_t>>& out) const;

    std::size_t bytes() const { return bytes_; }
    std::uint64_t evicted() const { return evicted_; }

private:
    using Key = std::pair<std::uint32_t, std::int64_t>;
    struct Entry {
        std::uint16_t count;
        std::vector<std::uint8_t> payload;
    };
    std::size_t cap_;
    std::size_t bytes_ = 0;
    std::uint64_t evicted_ = 0;
    std::map<Key, Entry> index_;
    std::deque<Key> fifo_;
};

}  // namespace pkmn::md
