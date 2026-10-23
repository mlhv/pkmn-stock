#include "pkmn_md/book_set.hpp"

namespace pkmn::md {

void BookSet::apply(const BookEvent& ev) {
    if (ev.kind == BookEvent::Kind::Clear) {
        // Nothing to forget for a symbol never seen; do not create a book.
        if (auto it = books_.find(ev.symbol); it != books_.end()) it->second.clear();
        return;
    }
    book::OrderBook& b = books_[ev.symbol];
    switch (ev.kind) {
        case BookEvent::Kind::Add: b.add(ev.order_id, ev.side, ev.price, ev.qty); break;
        case BookEvent::Kind::Modify: b.modify(ev.order_id, ev.price, ev.qty, ev.keep_priority); break;
        case BookEvent::Kind::Delete: b.remove(ev.order_id); break;
        case BookEvent::Kind::Execute: b.execute(ev.order_id, ev.qty); break;
        case BookEvent::Kind::Clear: break;  // handled above
    }
}

const book::OrderBook* BookSet::find(std::uint64_t symbol) const {
    const auto it = books_.find(symbol);
    return it == books_.end() ? nullptr : &it->second;
}

book::Anomalies BookSet::total_anomalies() const {
    book::Anomalies sum;
    for (const auto& [sym, b] : books_) {
        const book::Anomalies& a = b.anomalies();
        sum.unknown_id += a.unknown_id;
        sum.duplicate_add += a.duplicate_add;
        sum.exec_clamped += a.exec_clamped;
    }
    return sum;
}

void BookSet::clear_all() {
    for (auto& [sym, b] : books_) b.clear();
    ++clear_alls_;
}

std::size_t BookSet::order_count() const {
    std::size_t n = 0;
    for (const auto& [sym, b] : books_) n += b.order_count();
    return n;
}

}  // namespace pkmn::md
