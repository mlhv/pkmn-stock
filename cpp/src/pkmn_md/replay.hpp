#pragma once

// Applying one accepted DEEP+ segment to a BookSet: the book half of the
// driver loop (FeedReader / SegmentFilter supply the segments). This is the
// one place the rule lives; iex_bench, the tests and iex_oracle all call it.
//
//   * A segment that began a new sequence epoch (Segment::restart) first
//     clears every book: the old epoch's order ids cannot be trusted and the
//     restarted stream may reuse them.
//   * Each applied message (index >= Segment::skip) is decoded, and applied
//     if it is book-affecting.
//
// apply_deep_plus_segment_with(seg, books, ev, st, hooks) exposes the points
// a checker needs, without copying the rule (DeepPlusHooks has the no-op
// defaults; derive and shadow what you need):
//   before_clear(seg)   on a restart, before clear_all (iex_oracle resolves
//                       every pending check against the old epoch's books)
//   per_message(step)   once per applied message; must call step() exactly
//                       once (iex_bench wraps it in a timer)
//   before_apply(ev)    after a book event decodes, before books.apply(ev);
//                       false skips the apply (iex_oracle resolves checks
//                       pre-event and filters symbols here)
//   non_book(m)         an applied message that is not book-affecting
//                       (iex_oracle reads Security Directory round lots)
// apply_deep_plus_segment(seg, books, ev, st, per_message) is the same with
// only the per_message hook.

#include <span>
#include <type_traits>
#include <utility>

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

struct DeepPlusHooks {
    void before_clear(const Segment&) {}
    template <class Step>
    void per_message(Step&& step) {
        step();
    }
    bool before_apply(const BookEvent&) { return true; }
    void non_book(std::span<const std::uint8_t>) {}
};

template <class Hooks>
void apply_deep_plus_segment_with(const Segment& seg, BookSet& books, BookEvent& ev, DecodeStats& st, Hooks& hooks) {
    if (seg.restart) {
        hooks.before_clear(seg);
        books.clear_all();
    }
    for_each_applied(seg, [&](std::span<const std::uint8_t> m) {
        hooks.per_message([&] {
            if (decode_deep_plus(m, ev, st)) {
                if (hooks.before_apply(ev)) books.apply(ev);
            } else {
                hooks.non_book(m);
            }
        });
    });
}

namespace detail {
template <class PerMessage>
struct PerMessageHooks : DeepPlusHooks {
    PerMessage& pm;
    explicit PerMessageHooks(PerMessage& p) : pm(p) {}
    template <class Step>
    void per_message(Step&& step) {
        pm(std::forward<Step>(step));
    }
};
}  // namespace detail

template <class PerMessage = CallStep>
void apply_deep_plus_segment(const Segment& seg, BookSet& books, BookEvent& ev, DecodeStats& st,
                             PerMessage&& per_message = {}) {
    detail::PerMessageHooks<std::remove_reference_t<PerMessage>> hooks(per_message);
    apply_deep_plus_segment_with(seg, books, ev, st, hooks);
}

}  // namespace pkmn::md
