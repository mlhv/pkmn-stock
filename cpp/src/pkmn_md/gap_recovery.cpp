#include "pkmn_md/gap_recovery.hpp"

#include <algorithm>
#include <cassert>
#include <utility>

#include "pkmn_md/feed_reader.hpp"
#include "pkmn_md/replay.hpp"

namespace pkmn::md {

RecoveringHandler::RecoveringHandler(RequestSink& sink) : sink_(sink) {
    epochs_.emplace_back();
    starts_.push_back(std::numeric_limits<std::int64_t>::min());
}

bool RecoveringHandler::validate(std::span<const std::uint8_t> payload, TpHeader& hdr) {
    if (!parse_tp_header(payload, hdr) || !valid_first_seq(hdr.first_seq) || hdr.protocol_id != kDeepPlus) {
        return false;
    }
    if (!for_each_message(payload, hdr, [](std::span<const std::uint8_t>) {})) return false;
    if (!have_stream_) {
        have_stream_ = true;
        channel_ = hdr.channel_id;
        session_ = hdr.session_id;
    }
    return hdr.channel_id == channel_ && hdr.session_id == session_;
}

std::uint32_t RecoveringHandler::classify(std::int64_t send_time) const {
    std::size_t e = starts_.size() - 1;
    while (e > 0 && starts_[e] > send_time) --e;
    return static_cast<std::uint32_t>(e);
}

void RecoveringHandler::on_datagram(std::span<const std::uint8_t> udp_payload) {
    ++stats_.datagrams;
    if (failed()) return;
    TpHeader hdr{};
    if (!validate(udp_payload, hdr)) {
        ++stats_.rejected;
        return;
    }
    const bool newest = hdr.send_time > max_send_time_;
    if (newest) max_send_time_ = hdr.send_time;
    const std::uint32_t e = classify(hdr.send_time);
    if (e < current_) {
        duplicate(hdr);
    } else if (e > current_) {
        hold(e, hdr, udp_payload);
    } else if (newest && hdr.first_seq < epochs_[current_].max_end) {
        parked_.push_back({hdr, std::vector<std::uint8_t>(udp_payload.begin(), udp_payload.end())});
        ++stats_.parked;
        return;  // nothing changed for the epochs
    } else {
        route_current(hdr, udp_payload);
    }
    settle();
}

void RecoveringHandler::on_retransmit(std::uint32_t epoch, std::span<const std::uint8_t> payload) {
    if (failed()) return;
    TpHeader hdr{};
    if (!validate(payload, hdr) || epoch >= epochs_.size()) {
        ++stats_.rejected;
        return;
    }
    ++stats_.retransmit_segments;
    if (epoch < current_) {
        duplicate(hdr);
    } else if (epoch > current_) {
        hold(epoch, hdr, payload);
    } else {
        route_current(hdr, payload);
    }
    settle();
}

void RecoveringHandler::on_epoch_start(std::uint32_t epoch, std::int64_t prev_end_seq, std::int64_t first_send_time) {
    if (failed()) return;
    // Epochs are announced once each, in order, over the reliable channel.
    assert(epoch == starts_.size());
    if (epoch != starts_.size() || epoch == 0) return;
    starts_.push_back(first_send_time);
    epochs_.emplace_back();
    epochs_[epoch - 1].end = prev_end_seq;

    std::vector<Packet> still;
    for (Packet& p : parked_) {
        const std::uint32_t e = classify(p.hdr.send_time);
        if (e > current_) {
            hold(e, p.hdr, p.bytes);
        } else if (e < current_) {
            duplicate(p.hdr);
        } else {
            still.push_back(std::move(p));
        }
    }
    parked_ = std::move(still);
    settle();
}

void RecoveringHandler::on_end(std::uint32_t epoch, std::int64_t end_seq) {
    if (failed()) return;
    assert(epoch + 1 == starts_.size());
    if (epoch + 1 != starts_.size()) return;
    epochs_[epoch].end = end_seq;
    final_epoch_ = epoch;
    for (const Packet& p : parked_) duplicate(p.hdr);
    parked_.clear();
    settle();
}

void RecoveringHandler::on_unserved(std::uint32_t, std::int64_t, std::int64_t) { ++stats_.unrecoverable; }

void RecoveringHandler::route_current(const TpHeader& hdr, std::span<const std::uint8_t> bytes) {
    Epoch& ep = epochs_[current_];
    if (hdr.first_seq > ep.tracker.expected()) {
        hold(current_, hdr, bytes);
        return;
    }
    ep.max_end = std::max(ep.max_end, hdr.first_seq + hdr.msg_count);
    apply_or_drop(ep, hdr, bytes);
    drain(ep);
}

// Precondition: hdr.first_seq <= expected (Next, straddle or whole duplicate).
void RecoveringHandler::apply_or_drop(Epoch& ep, const TpHeader& hdr, std::span<const std::uint8_t> bytes) {
    Segment seg{};
    seg.hdr = hdr;
    seg.payload = bytes;
    seg.expected_before = ep.tracker.expected();
    seg.verdict = ep.tracker.observe(hdr.first_seq, hdr.msg_count);
    seg.skip = ep.tracker.skip();
    seg.restart = false;
    stats_.duplicate_messages += seg.skip;
    if (seg.skip >= hdr.msg_count) return;
    apply_deep_plus_segment(seg, books_, ev_, decode_stats_);
    const std::uint64_t n = hdr.msg_count - seg.skip;
    ++stats_.segments_applied;
    stats_.messages_applied += n;
    epoch_messages_ += n;
}

void RecoveringHandler::hold(std::uint32_t e, const TpHeader& hdr, std::span<const std::uint8_t> bytes) {
    Epoch& ep = epochs_[e];
    const std::int64_t end = hdr.first_seq + hdr.msg_count;
    ep.max_end = std::max(ep.max_end, end);
    if (hdr.msg_count > 0) {
        auto [it, inserted] = ep.held.try_emplace(hdr.first_seq);
        if (inserted || it->second.hdr.msg_count < hdr.msg_count) {
            it->second = Packet{hdr, std::vector<std::uint8_t>(bytes.begin(), bytes.end())};
        } else {
            duplicate(hdr);  // a copy of (or shorter than) a packet already held
        }
        if (inserted) stats_.held_max = std::max(stats_.held_max, ++held_count_);
    }
    // A later epoch's holes are requested when it becomes current.
    if (e != current_) return;
    const std::int64_t cursor = std::max(ep.tracker.expected(), ep.requested_upto);
    if (hdr.first_seq > cursor) request(e, cursor, hdr.first_seq);
    ep.requested_upto = std::max(cursor, end);
}

void RecoveringHandler::drain(Epoch& ep) {
    while (!ep.held.empty() && ep.held.begin()->first <= ep.tracker.expected()) {
        auto node = ep.held.extract(ep.held.begin());
        --held_count_;
        const Packet& p = node.mapped();
        apply_or_drop(ep, p.hdr, p.bytes);
    }
}

// The epoch just became current: request every hole in front of and between
// its held packets (none of them was requested while it was a later epoch).
void RecoveringHandler::request_holes(Epoch& ep) {
    std::int64_t cursor = std::max(ep.tracker.expected(), ep.requested_upto);
    for (const auto& [first, p] : ep.held) {
        if (first > cursor) request(current_, cursor, first);
        cursor = std::max(cursor, first + p.hdr.msg_count);
    }
    ep.requested_upto = cursor;
}

void RecoveringHandler::request(std::uint32_t e, std::int64_t from, std::int64_t to) {
    ++stats_.gaps;
    ++stats_.requests;
    stats_.requested_messages += static_cast<std::uint64_t>(to - from);
    sink_.request(e, from, to);
}

void RecoveringHandler::settle() {
    while (!failed() && !done_) {
        Epoch& ep = epochs_[current_];
        if (!ep.end) return;
        const std::int64_t cursor = std::max(ep.tracker.expected(), ep.requested_upto);
        if (cursor < *ep.end) {
            request(current_, cursor, *ep.end);  // tail loss: no later packet reveals it
            ep.requested_upto = *ep.end;
        }
        const bool final = final_epoch_ == current_;
        if (ep.tracker.expected() != *ep.end || !(final || starts_.size() > current_ + 1)) return;

        digests_.push_back({digest_books(books_), books_.order_count(), epoch_messages_});
        ++stats_.epochs;
        epoch_messages_ = 0;
        // Anything still held lies at or past the epoch's announced end.
        for (const auto& [first, p] : ep.held) duplicate(p.hdr);
        held_count_ -= ep.held.size();
        ep.held.clear();
        if (final) {
            done_ = true;
            return;
        }
        books_.clear_all();
        ++current_;
        Epoch& next = epochs_[current_];
        drain(next);
        request_holes(next);
    }
}

}  // namespace pkmn::md
