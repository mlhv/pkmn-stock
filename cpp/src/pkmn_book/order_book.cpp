#include "pkmn_book/order_book.hpp"

#include <algorithm>

namespace pkmn::book {

bool OrderBook::add(OrderId id, Side side, Price price, Qty qty) {
    if (qty <= 0) return false;
    if (index_.contains(id)) {
        ++anomalies_.duplicate_add;
        return false;
    }
    LevelData& lv = ladder(side)[price];
    lv.queue.push_back(Order{id, qty});
    lv.qty += qty;
    index_.emplace(id, Loc{side, price, std::prev(lv.queue.end())});
    return true;
}

void OrderBook::erase_order(std::unordered_map<OrderId, Loc>::iterator idx) {
    const Loc loc = idx->second;
    Ladder& lad = ladder(loc.side);
    auto lvl = lad.find(loc.price);
    lvl->second.qty -= loc.it->qty;
    lvl->second.queue.erase(loc.it);
    if (lvl->second.queue.empty()) lad.erase(lvl);  // never leave a zero-qty level
    index_.erase(idx);
}

bool OrderBook::remove(OrderId id) {
    auto idx = index_.find(id);
    if (idx == index_.end()) {
        ++anomalies_.unknown_id;
        return false;
    }
    erase_order(idx);
    return true;
}

bool OrderBook::modify(OrderId id, Price price, Qty new_total, bool keep_priority) {
    auto idx = index_.find(id);
    if (idx == index_.end()) {
        ++anomalies_.unknown_id;
        return false;
    }
    if (new_total <= 0) {
        erase_order(idx);
        return true;
    }
    Loc& loc = idx->second;
    if (keep_priority && price == loc.price) {
        LevelData& lv = ladder(loc.side).find(loc.price)->second;
        lv.qty += new_total - loc.it->qty;
        loc.it->qty = new_total;
        return true;
    }
    const Side side = loc.side;
    erase_order(idx);
    add(id, side, price, new_total);
    return true;
}

Qty OrderBook::execute(OrderId id, Qty qty) {
    auto idx = index_.find(id);
    if (idx == index_.end()) {
        ++anomalies_.unknown_id;
        return 0;
    }
    if (qty <= 0) return 0;
    Loc& loc = idx->second;
    Qty applied = qty;
    if (applied > loc.it->qty) {
        applied = loc.it->qty;
        ++anomalies_.exec_clamped;
    }
    if (applied == loc.it->qty) {
        erase_order(idx);
    } else {
        loc.it->qty -= applied;
        ladder(loc.side).find(loc.price)->second.qty -= applied;
    }
    return applied;
}

Qty OrderBook::match(Side aggressor, Qty qty, Price limit, std::vector<Execution>& out) {
    const Side resting = aggressor == Side::Buy ? Side::Sell : Side::Buy;
    Ladder& lad = ladder(resting);
    Qty filled = 0;
    while (filled < qty && !lad.empty()) {
        // Best asks are at begin(), best bids at the back.
        auto lvl = resting == Side::Sell ? lad.begin() : std::prev(lad.end());
        const Price px = lvl->first;
        if (aggressor == Side::Buy ? px > limit : px < limit) break;
        LevelData& lv = lvl->second;
        while (filled < qty && !lv.queue.empty()) {
            Order& o = lv.queue.front();
            const Qty take = std::min(qty - filled, o.qty);
            out.push_back(Execution{o.id, px, take});
            filled += take;
            lv.qty -= take;
            if (take == o.qty) {
                index_.erase(o.id);
                lv.queue.pop_front();
            } else {
                o.qty -= take;
            }
        }
        if (lv.queue.empty()) lad.erase(lvl);
    }
    return filled;
}

std::optional<Level> OrderBook::best(Side side) const {
    const Ladder& lad = ladder(side);
    if (lad.empty()) return std::nullopt;
    auto it = side == Side::Sell ? lad.begin() : std::prev(lad.end());
    return Level{it->first, it->second.qty, static_cast<std::uint32_t>(it->second.queue.size())};
}

void OrderBook::depth(Side side, std::size_t n, std::vector<Level>& out) const {
    out.clear();
    const Ladder& lad = ladder(side);
    auto emit = [&](auto it) {
        out.push_back(Level{it->first, it->second.qty,
                            static_cast<std::uint32_t>(it->second.queue.size())});
    };
    if (side == Side::Sell) {
        for (auto it = lad.begin(); it != lad.end() && out.size() < n; ++it) emit(it);
    } else {
        for (auto it = lad.rbegin(); it != lad.rend() && out.size() < n; ++it) emit(std::prev(it.base()));
    }
}

Qty OrderBook::qty_at(Side side, Price price) const {
    const Ladder& lad = ladder(side);
    auto it = lad.find(price);
    return it == lad.end() ? 0 : it->second.qty;
}

void OrderBook::clear() {
    bids_.clear();
    asks_.clear();
    index_.clear();
}

}  // namespace pkmn::book
