#include "pkmn_md/retransmit_ring.hpp"

#include <algorithm>

namespace pkmn::md {

void RetransmitRing::store(std::uint32_t epoch, std::int64_t first_seq, std::uint16_t count,
                           std::span<const std::uint8_t> payload) {
    if (count == 0) return;
    const Key k{epoch, first_seq};
    if (index_.count(k)) return;
    while (!fifo_.empty() && bytes_ + payload.size() > cap_) {
        auto it = index_.find(fifo_.front());
        bytes_ -= it->second.payload.size();
        index_.erase(it);
        fifo_.pop_front();
        ++evicted_;
    }
    index_.emplace(k, Entry{count, std::vector<std::uint8_t>(payload.begin(), payload.end())});
    fifo_.push_back(k);
    bytes_ += payload.size();
}

bool RetransmitRing::collect(std::uint32_t epoch, std::int64_t from, std::int64_t to,
                             std::vector<std::span<const std::uint8_t>>& out) const {
    if (from >= to) return true;
    auto it = index_.lower_bound(Key{epoch, from});
    // The packet starting before `from` may still reach into the range.
    if (it != index_.begin()) {
        auto prev = std::prev(it);
        if (prev->first.first == epoch && prev->first.second + prev->second.count > from) it = prev;
    }
    std::vector<std::span<const std::uint8_t>> found;
    std::int64_t cursor = from;
    for (; it != index_.end() && it->first.first == epoch && it->first.second < to; ++it) {
        if (it->first.second > cursor) return false;  // hole
        cursor = std::max(cursor, it->first.second + it->second.count);
        found.emplace_back(it->second.payload);
        if (cursor >= to) break;
    }
    if (cursor < to) return false;
    out.insert(out.end(), found.begin(), found.end());
    return true;
}

}  // namespace pkmn::md
