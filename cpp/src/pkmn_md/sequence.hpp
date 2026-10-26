#pragma once

// Per-stream message sequence tracking for IEX-TP (HIST replay now, live UDP
// later). A segment covers sequence numbers [first_seq, first_seq + count).
//
//   Next       first_seq == expected: apply every message.
//   Duplicate  first_seq <  expected: some or all of the range was already
//              seen. skip() leading messages are old; apply the rest (a
//              "straddle" when 0 < skip() < count).
//   Gap        first_seq >  expected: first_seq - expected messages were
//              never seen. Recorded, `expected` jumps, apply every message.
//
// The one rule for callers: after observe(), apply the segment's messages
// with index >= skip() (skip() is 0 for Next and Gap, and equals count for a
// whole-duplicate segment). A heartbeat (count 0) with first_seq == expected
// is Next.
//
// Counters: gaps() counts gap events, gap_messages() the messages missing
// across them, and duplicates() counts duplicate MESSAGES, not segments -- a
// whole-duplicate segment adds its count, a straddle adds only the overlap,
// and a duplicate heartbeat adds 0.
//
// Restarts are not decided here. IEX can restart a feed mid-day at seq 1
// under the same session id (2024-12-23, 11:39 ET); observe() alone would
// call that whole restarted stream Duplicate. The caller detects a restart
// (is_stream_restart in iextp.hpp), calls reset(1), then observe(). The
// policy lives outside observe() so the tracker stays a pure sequence check
// that Plan C can reuse for live UDP.
//
// Known limitation: a packet reordered at an A/B merge seam (seq N+1
// arriving before N) is recorded as a Gap, and then the late N is dropped as
// Duplicate. This rule cannot tell a reorder from a loss. 2024-12-23 shows 0
// gaps; Plan C (retransmission) owns recovery.
//
// Precondition (arithmetic range): every first_seq passed to observe() is in
// [kMinSeq, kMaxFirstSeq], and every expected value (constructor, reset) is
// in [kMinSeq, kMaxFirstSeq + 65535]. Then first_seq + count, first_seq -
// expected and expected - first_seq all stay inside int64, for any u16 count.
// The values come off the wire, so a caller must screen them first with
// valid_first_seq() (SegmentFilter does, counting a failure as a
// tp_parse_failure); a debug build asserts the precondition.

#include <cassert>
#include <cstdint>
#include <limits>

namespace pkmn::md {

enum class SeqVerdict : std::uint8_t { Next, Duplicate, Gap };

// IEX sequence numbers start at 1; the upper bound leaves room for the
// largest u16 message count.
inline constexpr std::int64_t kMinSeq = 1;
inline constexpr std::int64_t kMaxFirstSeq = std::numeric_limits<std::int64_t>::max() - 65535;

// True iff `first_seq` may be passed to SequenceTracker::observe().
constexpr bool valid_first_seq(std::int64_t first_seq) { return first_seq >= kMinSeq && first_seq <= kMaxFirstSeq; }

class SequenceTracker {
  public:
    // IEX sequence numbers start at 1 per session, so a capture that starts
    // late reports its missing head as a gap. A live join mid-session passes
    // the sequence it intends to start from.
    explicit SequenceTracker(std::int64_t first_expected = 1) : expected_(first_expected) {
        assert(valid_expected(first_expected));
    }

    SeqVerdict observe(std::int64_t first_seq, std::uint16_t count) {
        assert(valid_first_seq(first_seq) && valid_expected(expected_));
        const std::int64_t end = first_seq + count;
        if (first_seq == expected_) {
            skip_ = 0;
            expected_ = end;
            return SeqVerdict::Next;
        }
        if (first_seq > expected_) {
            ++gaps_;
            gap_messages_ += static_cast<std::uint64_t>(first_seq - expected_);
            skip_ = 0;
            expected_ = end;
            return SeqVerdict::Gap;
        }
        if (end <= expected_) {
            skip_ = count;
        } else {
            skip_ = static_cast<std::uint16_t>(expected_ - first_seq);
            expected_ = end;
        }
        duplicates_ += skip_;
        return SeqVerdict::Duplicate;
    }

    // Start a new sequence epoch at `first_expected`. Changes only expected()
    // and counts resets(); gaps, gap_messages and duplicates are untouched.
    void reset(std::int64_t first_expected) {
        assert(valid_expected(first_expected));
        expected_ = first_expected;
        skip_ = 0;
        ++resets_;
    }

    std::int64_t expected() const { return expected_; }
    // Leading messages of the last observed segment that were already seen.
    std::uint16_t skip() const { return skip_; }
    std::uint64_t gaps() const { return gaps_; }
    std::uint64_t gap_messages() const { return gap_messages_; }
    std::uint64_t duplicates() const { return duplicates_; }
    std::uint64_t resets() const { return resets_; }

  private:
    static constexpr bool valid_expected(std::int64_t e) { return e >= kMinSeq; }  // <= INT64_MAX by type

    std::int64_t expected_;
    std::uint16_t skip_ = 0;
    std::uint64_t gaps_ = 0, gap_messages_ = 0, duplicates_ = 0, resets_ = 0;
};

}  // namespace pkmn::md
