#pragma once

// RecoveringHandler: the socket-free core of the DEEP+ feed handler. It takes
// multicast datagrams (on_datagram), the publisher's retransmit replies and
// epoch frames from the TCP side channel (on_retransmit, on_epoch_start,
// on_end, on_unserved), holds packets that arrive after a gap, asks the
// RequestSink for every missing range, and applies messages to its BookSet
// in strict sequence order. Its per-epoch digests must equal
// reference_digests() of the same capture.
//
// Rules:
//   1. Stream filter. The first datagram (or retransmit) with a parseable
//      header, a valid_first_seq, protocol kDeepPlus and sound framing fixes
//      (channel, session). Anything else is `rejected` and treated as never
//      received, so the hole it leaves is requested like any loss.
//   2. Epoch of a multicast datagram: the highest announced epoch whose
//      first_send_time <= send_time (epoch 0 starts at minus infinity). An
//      earlier epoch than the current one means a duplicate. A datagram of
//      the current epoch whose first_seq is below the highest sequence end
//      seen in it, and whose send time is newer than every send time seen,
//      may be a new epoch whose EpochStart frame has not arrived: it is
//      parked. Each on_epoch_start re-classifies parked datagrams (a later
//      epoch: held there; an earlier one: duplicate; still the current one:
//      stays parked). At on_end anything still parked is a duplicate. An
//      old send time never parks, so a merge-seam copy is a duplicate.
//      Retransmits are never classified; they carry their epoch.
//   3. Ordering, per epoch (SequenceTracker + held map first_seq -> bytes).
//      In the current epoch a datagram whose first_seq <= expected is
//      applied from index expected - first_seq (the skipped head counts as
//      duplicate_messages; a whole duplicate applies nothing), then held
//      entries are drained while their first_seq <= expected. A datagram
//      with first_seq > expected is held; the range below it that is neither
//      held nor already requested is requested. `requested_upto` marks the
//      sequence below which every message is applied, held or requested, so
//      a run of held packets behind one hole issues one request. Datagrams
//      of a later epoch are held until it becomes current.
//   4. Tail and finalize. on_epoch_start(e, prev_end, t) records end[e-1];
//      on_end(e, end) records end[e] and makes e the final epoch. When the
//      current epoch's end is known, any unrequested tail [requested_upto,
//      end) is requested. When expected == end[current] and either the next
//      epoch is announced or current is final, the epoch is finalized: its
//      EpochDigest is pushed; unless final, every book is cleared, the next
//      epoch becomes current (tracker at 1), its held entries are drained,
//      and every hole in front of and between them is requested (a dropped
//      restart packet gives request(e, 1, first held seq)). done() turns
//      true when the final epoch is finalized.
//   5. Failure. on_unserved counts `unrecoverable`; failed() is then true
//      and nothing more is applied. There is no resync.
//
// Known limitation (rule 2): a new-epoch datagram that arrives before its
// EpochStart frame is only recognized when its first_seq is below the
// current epoch's highest sequence end; a restart into an epoch whose
// sequence numbers already exceed the old epoch's would be read as a gap.

#include <cstddef>
#include <cstdint>
#include <deque>
#include <limits>
#include <map>
#include <optional>
#include <span>
#include <vector>

#include "pkmn_md/book_digest.hpp"
#include "pkmn_md/book_set.hpp"
#include "pkmn_md/deep_plus.hpp"
#include "pkmn_md/events.hpp"
#include "pkmn_md/iextp.hpp"
#include "pkmn_md/sequence.hpp"

namespace pkmn::md {

struct RecoveryStats {
    std::uint64_t datagrams = 0, rejected = 0;  // rejected: bad header/framing, wrong protocol or stream
    std::uint64_t segments_applied = 0, messages_applied = 0;
    std::uint64_t gaps = 0, requests = 0, requested_messages = 0;
    std::uint64_t retransmit_segments = 0, duplicate_messages = 0;
    std::uint64_t epochs = 0, parked = 0, unrecoverable = 0;  // epochs: finalized
    std::size_t held_max = 0;                                 // peak held datagrams
};

class RequestSink {
  public:
    virtual ~RequestSink() = default;
    virtual void request(std::uint32_t epoch, std::int64_t from, std::int64_t to) = 0;  // [from, to)
};

class RecoveringHandler {
  public:
    explicit RecoveringHandler(RequestSink& sink);

    void on_datagram(std::span<const std::uint8_t> udp_payload);  // multicast
    void on_retransmit(std::uint32_t epoch, std::span<const std::uint8_t> payload);
    void on_epoch_start(std::uint32_t epoch, std::int64_t prev_end_seq, std::int64_t first_send_time);
    void on_end(std::uint32_t epoch, std::int64_t end_seq);
    void on_unserved(std::uint32_t epoch, std::int64_t from, std::int64_t to);  // sets failed()

    bool done() const { return done_ && !failed(); }  // End seen and every epoch finalized
    bool failed() const { return stats_.unrecoverable > 0; }
    const std::vector<EpochDigest>& digests() const { return digests_; }
    const RecoveryStats& stats() const { return stats_; }

  private:
    struct Packet {
        TpHeader hdr;
        std::vector<std::uint8_t> bytes;
    };
    struct Epoch {
        SequenceTracker tracker;               // starts at 1
        std::map<std::int64_t, Packet> held;  // first_seq -> packet, all > expected once current
        std::int64_t requested_upto = 0;      // below: applied, held or requested
        std::int64_t max_end = 0;             // highest first_seq + count seen
        std::optional<std::int64_t> end;      // from EpochStart / End
    };

    bool validate(std::span<const std::uint8_t> payload, TpHeader& hdr);
    std::uint32_t classify(std::int64_t send_time) const;
    void route_current(const TpHeader& hdr, std::span<const std::uint8_t> bytes);
    void apply_or_drop(Epoch& ep, const TpHeader& hdr, std::span<const std::uint8_t> bytes);
    void hold(std::uint32_t e, const TpHeader& hdr, std::span<const std::uint8_t> bytes);
    void drain(Epoch& ep);
    void request_holes(Epoch& ep);
    void request(std::uint32_t e, std::int64_t from, std::int64_t to);
    void duplicate(const TpHeader& hdr) { stats_.duplicate_messages += hdr.msg_count; }
    void settle();

    RequestSink& sink_;
    BookSet books_;
    BookEvent ev_{};
    DecodeStats decode_stats_;
    std::deque<Epoch> epochs_;           // one per announced epoch; deque keeps references stable
    std::vector<std::int64_t> starts_;  // announced first_send_time per epoch
    std::vector<Packet> parked_;
    std::uint32_t current_ = 0;
    std::optional<std::uint32_t> final_epoch_;
    std::uint64_t epoch_messages_ = 0;
    std::size_t held_count_ = 0;
    bool have_stream_ = false, done_ = false;
    std::uint32_t channel_ = 0, session_ = 0;
    std::int64_t max_send_time_ = std::numeric_limits<std::int64_t>::min();
    std::vector<EpochDigest> digests_;
    RecoveryStats stats_;
};

}  // namespace pkmn::md
