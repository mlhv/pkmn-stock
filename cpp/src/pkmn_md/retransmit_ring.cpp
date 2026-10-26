#include "pkmn_md/retransmit_ring.hpp"

#include <algorithm>

namespace pkmn::md {

void RetransmitRing::store(std::uint32_t epoch, std::int64_t first_seq, std::uint16_t count,
                           std::span<const std::uint8_t> payload) {
    if (count == 0) return;
    const Key k{epoch, first_seq};
    if (payload.size() > cap_) {  // can never fit: not stored, counted as evicted
        ++evicted_;
        return;
    }
    if (auto old = index_.find(k); old != index_.end()) {
        if (old->second.count >= count) return;  // keep the longer packet
        // Replace in place: the FIFO slot (and so eviction age) is kept.
        bytes_ = bytes_ - old->second.payload.size() + payload.size();
        old->second.count = count;
        old->second.payload.assign(payload.begin(), payload.end());
    } else {
        index_.emplace(k, Entry{count, std::vector<std::uint8_t>(payload.begin(), payload.end())});
        fifo_.push_back(k);
        bytes_ += payload.size();
    }
    max_count_ = std::max<std::int64_t>(max_count_, count);
    // payload <= cap_, so the entry just stored is only reached last.
    while (bytes_ > cap_ && !fifo_.empty()) {
        auto it = index_.find(fifo_.front());
        bytes_ -= it->second.payload.size();
        index_.erase(it);
        fifo_.pop_front();
        ++evicted_;
    }
}

bool RetransmitRing::collect(std::uint32_t epoch, std::int64_t from, std::int64_t to,
                             std::vector<std::span<const std::uint8_t>>& out) const {
    if (from >= to) return true;
    auto it = index_.lower_bound(Key{epoch, from});
    // A packet starting before `from` may reach into the range, and it need
    // not be the immediate predecessor (a long packet can enclose shorter
    // ones). No stored count exceeds max_count_, so no key at or below
    // from - max_count_ can reach `from`; walk back only over same-epoch
    // keys above that bound. max_count_ never shrinks (conservative after
    // eviction) and is MTU-bound (tens) for real packets, so the walk is short.
    while (it != index_.begin()) {
        auto prev = std::prev(it);
        if (prev->first.first != epoch || prev->first.second + max_count_ <= from) break;
        it = prev;
    }
    std::vector<std::span<const std::uint8_t>> found;
    std::int64_t cursor = from;
    for (; it != index_.end() && it->first.first == epoch && it->first.second < to; ++it) {
        if (it->first.second + it->second.count <= from) continue;  // does not reach the range
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
