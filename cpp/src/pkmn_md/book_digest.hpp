#pragma once

// Canonical digest of a BookSet, and the reference digests of a DEEP+
// capture: one per sequence epoch, taken just before the restart clear (and
// at end of stream), plus optional mid-epoch checkpoints. A feed handler that
// recovers every gap must reproduce these exactly; iex_digest writes them,
// read_digest_set (or read_digests, epochs only) loads them.
//
// Checkpoints: with an interval N > 0, one is taken immediately after
// applying the message whose sequence number s (within its epoch) satisfies
// s % N == 0. Messages are applied in strict sequence order on both sides,
// so the books at sequence s do not depend on segment boundaries, straddles
// or retransmits. A message skipped as a duplicate is not applied and takes
// no checkpoint. The end-of-epoch digest alone is weak on a capture whose
// books end empty; checkpoints compare populated books mid-epoch.

#include <cstdint>
#include <cstdio>
#include <string>
#include <vector>

#include "pkmn_md/book_set.hpp"

namespace pkmn::md {

struct EpochDigest {
    std::uint64_t digest;
    std::uint64_t orders;    // resting orders at the end of the epoch
    std::uint64_t messages;  // messages applied in the epoch
};

struct Checkpoint {
    std::uint32_t epoch;
    std::int64_t seq;  // the last message applied before the digest
    std::uint64_t digest;
    std::uint64_t orders;
    bool operator==(const Checkpoint&) const = default;
};

struct DigestSet {
    std::uint64_t checkpoint_every = 0;  // 0: no checkpoints
    std::vector<EpochDigest> epochs;
    std::vector<Checkpoint> checkpoints;
};

// FNV-1a 64 over (symbol, side, price, id, qty) of every resting order, as
// little-endian bytes. Symbols ascend, then Buy before Sell, then best level
// first and FIFO within a level, so queue priority is part of the digest.
// Empty books contribute nothing.
std::uint64_t digest_books(const BookSet& books);

// The checkpoint rule above, shared by the reference and the handler. Call
// after(epoch, seq, books) once per applied message, after applying it.
class CheckpointRecorder {
  public:
    explicit CheckpointRecorder(std::uint64_t every = 0) : every_(every) {}
    void after(std::uint32_t epoch, std::int64_t seq, const BookSet& books) {
        if (every_ != 0 && seq > 0 && static_cast<std::uint64_t>(seq) % every_ == 0)
            out_.push_back({epoch, seq, digest_books(books), books.order_count()});
    }
    std::uint64_t every() const { return every_; }
    const std::vector<Checkpoint>& checkpoints() const { return out_; }

  private:
    std::uint64_t every_;
    std::vector<Checkpoint> out_;
};

// Throws std::runtime_error as FeedReader does.
std::vector<EpochDigest> reference_digests(const std::string& pcap_path);
DigestSet reference_digest_set(const std::string& pcap_path, std::uint64_t checkpoint_every);

// One line per epoch: "epoch <i> digest <16 hex> orders <n> messages <n>".
void write_digests(std::FILE* out, const std::vector<EpochDigest>& d);
// With checkpoints: "checkpoint-every <N>" first, then the epoch lines, then
// one "checkpoint <epoch> <seq> digest <16 hex> orders <n>" line each. With
// checkpoint_every == 0 the output is exactly write_digests(epochs).
void write_digest_set(std::FILE* out, const DigestSet& d);
// Throws std::runtime_error on an unreadable file or a malformed line.
// read_digests returns the epoch lines only (checkpoint lines are parsed
// and validated, then ignored).
std::vector<EpochDigest> read_digests(const std::string& path);
DigestSet read_digest_set(const std::string& path);

}  // namespace pkmn::md
