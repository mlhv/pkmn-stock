#include "pkmn_wire/encoder.hpp"

#include <cstring>
#include <stdexcept>
#include <vector>

namespace pkmn::wire {
namespace {

class Writer {
  public:
    explicit Writer(std::ostream& out) : out_(out) { buf_.reserve(kMaxPacketBytes); }

    // Appends one message of `len` bytes after the u16 length; the caller
    // fills the type and body through the returned writer functions.
    void begin(char type, std::size_t len) {
        if (kHeaderBytes + payload_.size() + 2 + len > kMaxPacketBytes) flush();
        u16(static_cast<std::uint16_t>(len));
        payload_.push_back(static_cast<unsigned char>(type));
        ++count_;
        ++total_;
    }
    void u8(std::uint8_t v) { payload_.push_back(v); }
    void u16(std::uint16_t v) { put(v, 2); }
    void u32(std::uint32_t v) { put(v, 4); }
    void i32(std::int32_t v) { put(static_cast<std::uint32_t>(v), 4); }
    void i64(std::int64_t v) { put(static_cast<std::uint64_t>(v), 8); }
    void raw(const std::string& s, std::size_t pad_to) {
        payload_.insert(payload_.end(), s.begin(), s.end());
        payload_.insert(payload_.end(), pad_to - s.size(), 0);
    }

    void flush() {
        if (count_ == 0) return;
        buf_.clear();
        auto push = [&](std::uint64_t v, int n) {
            for (int i = 0; i < n; ++i) buf_.push_back(static_cast<unsigned char>(v >> (8 * i)));
        };
        for (char c : kMagic) buf_.push_back(static_cast<unsigned char>(c));
        push(count_, 2);
        push(payload_.size(), 2);
        push(static_cast<std::uint64_t>(next_seq_), 8);
        buf_.insert(buf_.end(), payload_.begin(), payload_.end());
        out_.write(reinterpret_cast<const char*>(buf_.data()), static_cast<std::streamsize>(buf_.size()));
        next_seq_ += count_;
        count_ = 0;
        payload_.clear();
    }
    std::uint64_t total() const { return total_; }

  private:
    void put(std::uint64_t v, int n) {
        for (int i = 0; i < n; ++i) payload_.push_back(static_cast<unsigned char>(v >> (8 * i)));
    }
    std::ostream& out_;
    std::vector<unsigned char> payload_, buf_;
    std::uint32_t count_ = 0;
    std::int64_t next_seq_ = 1;
    std::uint64_t total_ = 0;
};

}  // namespace

void validate(const EncodeInput& in) {
    const std::size_t n = in.inst_product_id.size();
    if (in.inst_sub_type.size() != n) throw std::invalid_argument("instrument arrays differ in size");
    const std::size_t r = in.row_day.size();
    if (in.row_instrument.size() != r || in.row_market.size() != r || in.row_low.size() != r ||
        in.row_mid.size() != r || in.row_high.size() != r)
        throw std::invalid_argument("row arrays differ in size");
    for (std::size_t i = 0; i < n; ++i) {
        if (in.inst_sub_type[i].size() > kSubTypeBytes)
            throw std::invalid_argument("sub_type longer than 24 bytes: product " +
                                        std::to_string(in.inst_product_id[i]));
        if (i > 0) {
            const bool ok = in.inst_product_id[i - 1] < in.inst_product_id[i] ||
                            (in.inst_product_id[i - 1] == in.inst_product_id[i] &&
                             in.inst_sub_type[i - 1] < in.inst_sub_type[i]);
            if (!ok) throw std::invalid_argument("instruments not strictly ordered by (product_id, sub_type)");
        }
    }
    for (std::size_t i = 0; i < r; ++i) {
        if (in.row_instrument[i] >= n) throw std::invalid_argument("row instrument id out of range");
        if (i > 0) {
            const bool ok = in.row_day[i - 1] < in.row_day[i] ||
                            (in.row_day[i - 1] == in.row_day[i] && in.row_instrument[i - 1] < in.row_instrument[i]);
            if (!ok) throw std::invalid_argument("rows not strictly ascending by (day, instrument)");
        }
    }
}

std::uint64_t encode(const EncodeInput& in, std::ostream& out) {
    validate(in);
    if (!out) throw std::runtime_error("wire output stream is not writable");
    Writer w(out);
    for (std::size_t i = 0; i < in.inst_product_id.size(); ++i) {
        w.begin(kTypeInstrument, kLenInstrument);
        w.u32(static_cast<std::uint32_t>(i));
        w.i64(in.inst_product_id[i]);
        w.u8(static_cast<std::uint8_t>(in.inst_sub_type[i].size()));
        w.raw(in.inst_sub_type[i], kSubTypeBytes);
    }
    const std::size_t r = in.row_day.size();
    for (std::size_t i = 0; i < r;) {
        const std::int32_t day = in.row_day[i];
        w.begin(kTypeDayStart, kLenDayStart);
        w.i32(day);
        std::uint32_t quotes = 0;
        for (; i < r && in.row_day[i] == day; ++i, ++quotes) {
            w.begin(kTypeQuote, kLenQuote);
            w.u32(in.row_instrument[i]);
            w.u32(in.row_market[i]);
            w.u32(in.row_low[i]);
            w.u32(in.row_mid[i]);
            w.u32(in.row_high[i]);
        }
        w.begin(kTypeDayEnd, kLenDayEnd);
        w.i32(day);
        w.u32(quotes);
    }
    w.flush();
    out.flush();
    // A failed write (disk full, closed pipe) sets failbit/badbit on the
    // stream and is otherwise silent; a count must never stand for a file
    // that is not all there.
    if (!out) throw std::runtime_error("wire output stream failed while writing");
    return w.total();
}

}  // namespace pkmn::wire
