#include "pkmn_book/order_book.hpp"

#include <algorithm>
#include <bit>
#include <stdexcept>

namespace pkmn::book {

namespace {

// Levels are stored worst first, best last: bids ascending, asks descending.
inline bool is_worse(Side s, Price a, Price b) { return s == Side::Buy ? a < b : a > b; }

// Linear probe this many levels from the touch before falling back to a
// binary search over the rest.
constexpr std::size_t kLinearLevels = 8;
constexpr std::size_t kMinTable = 16;

}  // namespace

std::size_t OrderBook::level_pos(const std::vector<LevelRec>& v, Side s, Price price) {
    std::size_t i = v.size();
    const std::size_t stop = i > kLinearLevels ? i - kLinearLevels : 0;
    while (i > stop && !is_worse(s, v[i - 1].price, price)) --i;
    if (i == stop && stop > 0 && !is_worse(s, v[i - 1].price, price)) {
        const auto it = std::lower_bound(v.begin(), v.begin() + static_cast<std::ptrdiff_t>(i), price,
                                         [s](const LevelRec& l, Price p) { return is_worse(s, l.price, p); });
        i = static_cast<std::size_t>(it - v.begin());
    }
    return i;
}

std::size_t OrderBook::level_of(Side s, Price price) const {
    return level_pos(levels(s), s, price);
}

std::size_t OrderBook::find_entry(OrderId id) const {
    if (table_.empty()) return npos;
    for (std::size_t i = home(id);; i = (i + 1) & mask_) {
        const Entry& e = table_[i];
        if (e.slot == kNil) return npos;
        if (e.id == id) return i;
    }
}

void OrderBook::erase_entry(std::size_t pos) {
    // Backward-shift deletion: pull later members of the probe run into the
    // hole when their home position does not lie cyclically in (hole, j].
    std::size_t hole = pos;
    for (std::size_t j = (pos + 1) & mask_;; j = (j + 1) & mask_) {
        Entry& e = table_[j];
        if (e.slot == kNil) break;
        const std::size_t h = home(e.id);
        const bool stays = hole <= j ? (hole < h && h <= j) : (hole < h || h <= j);
        if (!stays) {
            table_[hole] = e;
            hole = j;
        }
    }
    table_[hole].slot = kNil;
    --count_;
}

void OrderBook::grow_table() {
    const std::size_t cap = table_.empty() ? kMinTable : table_.size() * 2;
    std::vector<Entry> old;
    old.swap(table_);
    table_.assign(cap, Entry{0, kNil});
    mask_ = cap - 1;
    shift_ = 64u - static_cast<unsigned>(std::countr_zero(cap));
    for (const Entry& e : old) {
        if (e.slot == kNil) continue;
        std::size_t i = home(e.id);
        while (table_[i].slot != kNil) i = (i + 1) & mask_;
        table_[i] = e;
    }
}

OrderBook::Slot OrderBook::alloc_node() {
    if (free_head_ != kNil) {
        const Slot s = free_head_;
        free_head_ = nodes_[s].next;
        return s;
    }
    if (nodes_.size() >= kNil) throw std::length_error("OrderBook: order arena full");
    nodes_.emplace_back();
    return static_cast<Slot>(nodes_.size() - 1);
}

void OrderBook::free_node(Slot s) {
    nodes_[s].next = free_head_;
    free_head_ = s;
}

void OrderBook::link_back(Slot slot) {
    Node& n = nodes_[slot];
    std::vector<LevelRec>& v = levels(n.side);
    const std::size_t i = level_pos(v, n.side, n.price);
    n.next = kNil;
    if (i < v.size() && v[i].price == n.price) {
        LevelRec& lv = v[i];
        n.prev = lv.tail;
        nodes_[lv.tail].next = slot;
        lv.tail = slot;
        lv.qty += n.qty;
        ++lv.orders;
    } else {
        n.prev = kNil;
        v.insert(v.begin() + static_cast<std::ptrdiff_t>(i), LevelRec{n.price, n.qty, slot, slot, 1});
    }
}

void OrderBook::unlink_free(Slot slot) {
    const Node& n = nodes_[slot];
    std::vector<LevelRec>& v = levels(n.side);
    const std::size_t i = level_pos(v, n.side, n.price);
    LevelRec& lv = v[i];
    if (--lv.orders == 0) {
        v.erase(v.begin() + static_cast<std::ptrdiff_t>(i));  // never leave a zero-qty level
    } else {
        lv.qty -= n.qty;
        if (n.prev == kNil) lv.head = n.next; else nodes_[n.prev].next = n.next;
        if (n.next == kNil) lv.tail = n.prev; else nodes_[n.next].prev = n.prev;
    }
    free_node(slot);
}

void OrderBook::erase_order(std::size_t entry_pos) {
    const Slot slot = table_[entry_pos].slot;
    erase_entry(entry_pos);
    unlink_free(slot);
}

bool OrderBook::add(OrderId id, Side side, Price price, Qty qty) {
    if (qty <= 0) return false;
    // Keep the load factor at or under 1/2.
    if ((count_ + 1) * 2 > table_.size()) grow_table();
    std::size_t i = home(id);
    for (;; i = (i + 1) & mask_) {
        const Entry& e = table_[i];
        if (e.slot == kNil) break;
        if (e.id == id) {
            ++anomalies_.duplicate_add;
            return false;
        }
    }
    const Slot slot = alloc_node();
    Node& n = nodes_[slot];
    n.id = id;
    n.qty = qty;
    n.price = price;
    n.side = side;
    link_back(slot);
    table_[i] = Entry{id, slot};
    ++count_;
    return true;
}

bool OrderBook::remove(OrderId id) {
    const std::size_t e = find_entry(id);
    if (e == npos) {
        ++anomalies_.unknown_id;
        return false;
    }
    erase_order(e);
    return true;
}

bool OrderBook::modify(OrderId id, Price price, Qty new_total, bool keep_priority) {
    const std::size_t e = find_entry(id);
    if (e == npos) {
        ++anomalies_.unknown_id;
        return false;
    }
    if (new_total <= 0) {
        erase_order(e);
        return true;
    }
    const Slot slot = table_[e].slot;
    Node& n = nodes_[slot];
    if (keep_priority && price == n.price) {
        LevelRec& lv = levels(n.side)[level_of(n.side, n.price)];
        lv.qty += new_total - n.qty;
        n.qty = new_total;
        return true;
    }
    // Back of the queue at `price`: the id keeps its table entry and its slot
    // is re-used in place.
    const Side side = n.side;
    unlink_free(slot);
    const Slot fresh = alloc_node();  // pops the slot just freed
    Node& m = nodes_[fresh];
    m.id = id;
    m.qty = new_total;
    m.price = price;
    m.side = side;
    link_back(fresh);
    table_[e].slot = fresh;
    return true;
}

Qty OrderBook::execute(OrderId id, Qty qty) {
    const std::size_t e = find_entry(id);
    if (e == npos) {
        ++anomalies_.unknown_id;
        return 0;
    }
    if (qty <= 0) return 0;
    Node& n = nodes_[table_[e].slot];
    Qty applied = qty;
    if (applied > n.qty) {
        applied = n.qty;
        ++anomalies_.exec_clamped;
    }
    if (applied == n.qty) {
        erase_order(e);
    } else {
        n.qty -= applied;
        levels(n.side)[level_of(n.side, n.price)].qty -= applied;
    }
    return applied;
}

Qty OrderBook::match(Side aggressor, Qty qty, Price limit, std::vector<Execution>& out) {
    const Side resting = aggressor == Side::Buy ? Side::Sell : Side::Buy;
    std::vector<LevelRec>& v = levels(resting);
    Qty filled = 0;
    while (filled < qty && !v.empty()) {
        LevelRec& lv = v.back();  // best on either side
        const Price px = lv.price;
        if (aggressor == Side::Buy ? px > limit : px < limit) break;
        while (filled < qty && lv.head != kNil) {
            const Slot s = lv.head;
            Node& o = nodes_[s];
            const Qty take = std::min(qty - filled, o.qty);
            out.push_back(Execution{o.id, px, take});
            filled += take;
            lv.qty -= take;
            if (take == o.qty) {
                erase_entry(find_entry(o.id));
                lv.head = o.next;
                if (lv.head == kNil) lv.tail = kNil; else nodes_[lv.head].prev = kNil;
                --lv.orders;
                free_node(s);
            } else {
                o.qty -= take;
            }
        }
        if (lv.head == kNil) v.pop_back();
    }
    return filled;
}

std::optional<Level> OrderBook::best(Side side) const {
    const std::vector<LevelRec>& v = levels(side);
    if (v.empty()) return std::nullopt;
    const LevelRec& lv = v.back();
    return Level{lv.price, lv.qty, lv.orders};
}

void OrderBook::depth(Side side, std::size_t n, std::vector<Level>& out) const {
    out.clear();
    const std::vector<LevelRec>& v = levels(side);
    for (auto it = v.rbegin(); it != v.rend() && out.size() < n; ++it)
        out.push_back(Level{it->price, it->qty, it->orders});
}

Qty OrderBook::qty_at(Side side, Price price) const {
    const std::vector<LevelRec>& v = levels(side);
    const std::size_t i = level_pos(v, side, price);
    return i < v.size() && v[i].price == price ? v[i].qty : 0;
}

void OrderBook::clear() {
    nodes_.clear();
    free_head_ = kNil;
    bids_.clear();
    asks_.clear();
    std::fill(table_.begin(), table_.end(), Entry{0, kNil});
    count_ = 0;
}

}  // namespace pkmn::book
