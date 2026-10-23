#pragma once

// Applying one accepted DEEP+ segment to a BookSet: the book half of the
// driver loop (FeedReader / SegmentFilter supply the segments).
//
//   * A segment that began a new sequence epoch (Segment::restart) first
//     clears every book: the old epoch's order ids cannot be trusted and the
//     restarted stream may reuse them (iex_oracle does the same).
//   * Each applied message (index >= Segment::skip) is decoded, and applied
//     if it is book-affecting.
//
// `per_message(step)` is called once per applied message with a callable
// that does the decode and apply; it must call step() exactly once. The
// default just calls it; iex_bench wraps it in a timer.

#include <span>

#include "pkmn_md/book_set.hpp"
#include "pkmn_md/deep_plus.hpp"
#include "pkmn_md/events.hpp"
#include "pkmn_md/feed_reader.hpp"

namespace pkmn::md {

struct CallStep {
    template <class Step>
    void operator()(Step&& step) const {
        step();
    }
};

template <class PerMessage = CallStep>
void apply_deep_plus_segment(const Segment& seg, BookSet& books, BookEvent& ev, DecodeStats& st,
                             PerMessage&& per_message = {}) {
    if (seg.restart) books.clear_all();
    for_each_applied(seg, [&](std::span<const std::uint8_t> m) {
        per_message([&] {
            if (decode_deep_plus(m, ev, st)) books.apply(ev);
        });
    });
}

}  // namespace pkmn::md
