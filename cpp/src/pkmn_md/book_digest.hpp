#pragma once

// Canonical digest of a BookSet, and the reference digests of a DEEP+
// capture: one per sequence epoch, taken just before the restart clear (and
// at end of stream). A feed handler that recovers every gap must reproduce
// these exactly; iex_digest writes them, read_digests loads them.

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

// FNV-1a 64 over (symbol, side, price, id, qty) of every resting order, as
// little-endian bytes. Symbols ascend, then Buy before Sell, then best level
// first and FIFO within a level, so queue priority is part of the digest.
// Empty books contribute nothing.
std::uint64_t digest_books(const BookSet& books);

// Throws std::runtime_error as FeedReader does.
std::vector<EpochDigest> reference_digests(const std::string& pcap_path);

// One line per epoch: "epoch <i> digest <16 hex> orders <n> messages <n>".
void write_digests(std::FILE* out, const std::vector<EpochDigest>& d);
// Throws std::runtime_error on an unreadable file or a malformed line.
std::vector<EpochDigest> read_digests(const std::string& path);

}  // namespace pkmn::md
