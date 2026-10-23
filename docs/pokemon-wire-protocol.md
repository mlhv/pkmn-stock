# Pokemon quote wire protocol (PKW1)

The wire carries one daily quote per instrument, not ladder levels. The
modelled resting orders (synthetic, built from the day's tcgcsv quote) would
come to on the order of a billion one-unit levels over the warehouse, and
baking them into the file would freeze the cost settings (tick size, depth
tiers, ramp) into the data. The receiver builds the ladder from the quote
with the same builder the backtester uses, so the cost settings stay a
replay-time choice.

Everything is little-endian and packed. Source of truth for the code:
`cpp/src/pkmn_wire/` (`wire.hpp` constants, `encoder.cpp`, `decoder.cpp`).

## Packet

A 16-byte header, then `msg_count` messages. A packet is at most 1400 bytes
(header plus payload), so it fits a UDP datagram (Plan C).

| offset | field | type | value |
|---|---|---|---|
| 0 | `magic` | u32 | the bytes `"PKW1"` |
| 4 | `msg_count` | u16 | messages in this packet |
| 6 | `payload_len` | u16 | bytes after the header |
| 8 | `first_seq` | i64 | sequence number of the first message; starts at 1 per stream |

Message `i` of a packet has sequence `first_seq + i`.

## Messages

Each message is `u16 len` (the bytes after this field, type byte included),
`u8 type`, then the body. Offsets below are from the start of the type byte.

| type | name | body (offset) | len |
|---|---|---|---|
| `'D'` | Instrument | instrument u32 (1), product_id i64 (5), sub_type_len u8 (13), sub_type char[24] zero-padded (14) | 38 |
| `'S'` | DayStart | day i32, days since 1970-01-01 (1) | 5 |
| `'Q'` | Quote | instrument u32 (1), market u32 (5), low u32 (9), mid u32 (13), high u32 (17) | 21 |
| `'E'` | DayEnd | day i32 (1), quote_count u32 (5) | 9 |
| `'T'` | Trade | instrument u32 (1), price u32 (5), qty u32 (9), ts_ns i64 (13) | 21 |

Prices are whole cents. `0xFFFFFFFF` means null (no such price that day), so
the largest representable price is $42,949,672.94.

## Ordering rules

- Instrument ids are dense from 0, ordered by `(product_id, sub_type)`.
- Every `D` precedes the first `S`.
- Per day: `S`, then that day's `Q` messages in ascending instrument order,
  then `E` carrying the day and the number of `Q` sent. Days ascend.
- `T` is reserved for the spec's future trade feed. The encoder never emits
  it; the decoder decodes it.

## Decoder rules

- Unknown types are skipped (counted in `unknown`).
- A `len` longer than the known body is accepted: the known prefix is read,
  the rest skipped.
- A message cut off by the end of the packet, or a `len` below its type's
  size, counts in `short_messages` and ends that packet. A packet shorter
  than its header, or with `payload_len` beyond the bytes present, counts in
  `short_packets` (messages that fit are still delivered). Bad magic counts
  in `bad_magic` and delivers nothing. A `first_seq` outside the sequence
  tracker's range counts as a short packet.
- Sequencing uses `pkmn::md::SequenceTracker`: `gaps`, `gap_messages` and
  `duplicates` are exposed; duplicate messages are not redelivered.

## Annotated packet

One packet holding a D (instrument 0, product 12345, sub_type "Normal"), an S
(day 20000) and a Q (market 12.50, low 12.00, mid 13.00, high null). 86 bytes.

```
0000  50 4b 57 31 03 00 46 00 01 00 00 00 00 00 00 00
0010  26 00 44 00 00 00 00 39 30 00 00 00 00 00 00 06
0020  4e 6f 72 6d 61 6c 00 00 00 00 00 00 00 00 00 00
0030  00 00 00 00 00 00 00 00 05 00 53 20 4e 00 00 15
0040  00 51 00 00 00 00 e2 04 00 00 b0 04 00 00 14 05
0050  00 00 ff ff ff ff
```

- `0000-000f` header: `PKW1`, msg_count 3, payload_len 0x46 = 70, first_seq 1.
- `0010` D: len 0x26 = 38, type `D` (0x44), instrument 0, product_id 0x3039 =
  12345, sub_type_len 6, then `Normal` and 18 zero bytes (ends at `0x37`).
- `0038` S: len 5, type `S` (0x53), day 0x4e20 = 20000.
- `003f` Q: len 0x15 = 21, type `Q` (0x51), instrument 0, market 0x4e2 = 1250,
  low 0x4b0 = 1200, mid 0x514 = 1300, high `ffffffff` (null).
