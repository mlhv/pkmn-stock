# Plan C: IEX DEEP+ Multicast Feed Handler Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replay the real 2024-12-23 IEX DEEP+ capture as UDP multicast with seeded drop injection, and rebuild the order books in a feed handler that repairs every gap by TCP retransmit. The proof is that the handler's book digests equal a direct file replay's at every epoch boundary.

**Architecture:** All of the recovery logic lives in a socket-free core, `RecoveringHandler` in `pkmn_md`. It is fed datagrams and control frames and emits retransmit requests through a sink, so Catch2 drives it in-process on macOS. Two thin tools wrap it in real sockets, `feed_publisher` (UDP multicast sender plus TCP retransmit server) and `feed_handler`. A third tool, `iex_digest`, computes the reference digests from the existing `FeedReader` + `apply_deep_plus_segment_with` path, so the handler is never compared against itself.

**Tech Stack:** C++20, CMake, Catch2 v3.7.1, POSIX sockets (Linux VM for real runs; everything also builds on macOS), zlib (existing `PcapngReader`).

**Spec:** `docs/superpowers/specs/2026-10-26-feed-handler-design.md` (read it; its Decisions and Cut line sections bind this plan). Parent: `docs/superpowers/specs/2026-10-22-market-data-and-clob-design.md`.

## Global Constraints

- Branch `feat/feed-handler` (exists, cut from `main` `3ce6d42`; the spec is committed at `4b2a2c8`).
- All new code is in the non-SKBUILD part of `cpp/CMakeLists.txt`, next to `pkmn_md`. It never enters the Python wheel. No Python file changes.
- Never `-ffast-math`, `-ffp-contract=fast` or `/fp:fast`. `pkmn_md` already adds `-ffp-contract=off` publicly.
- The publisher sends each IEX-TP UDP payload **byte for byte unchanged**. Epoch and control information travel only on the TCP channel.
- Never decompress the capture to disk. Never delete anything under `data/`.
- Before every commit:
  - macOS: `cmake -S cpp -B cpp/build -DPKMN_BUILD_TESTS=ON && cmake --build cpp/build -j && ctest --test-dir cpp/build`.
  - The four Python gates: `uv sync --group api --group dashboard && uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run mypy`. They should be untouched by this plan, but they are project rules.
  - Once Task 4 lands, also the VM C++ suite: `orb -m ubuntu bash -lc 'cd /Users/ml3787/pkmn-stock && cmake -S cpp -B cpp/build-linux -DPKMN_BUILD_TESTS=ON && cmake --build cpp/build-linux -j && ctest --test-dir cpp/build-linux'`.
- Existing tools (`iex_oracle`, `iex_bench`, `iex_decode`, `pcap_count`) and `SegmentFilter`'s rules must not change behaviour.
- The IEX citation line is required wherever IEX results are published: "Data provided for free by IEX. By accessing or using IEX Historical Data, you agree to the IEX Historical Data Terms of Use."
- The headline hardware string is "Apple M1 Pro, 7-vCPU OrbStack Ubuntu 24.04 arm64 VM, loopback multicast". The VM has 7 GB RAM, `net.core.rmem_max = 7500000`, and `cmake` is installed.
- **Deadline ordering (owner cut line):** Tasks 1-5 are must-ship and must be committed before Task 6 starts. Task 6 (burst run) is cut first and Task 7 (throughput) second. Task 8 (docs) always runs and covers whatever shipped.
- No em dashes and no AI-sounding phrasing in README, benchmark prose or resume bullets.

## Review Focus

1. **Loss of the restart packet itself** (seq 1 of the new epoch). The handler must still recognize the new epoch, request `[1, first_seen_seq)` from it, and match the reference digests. It is pinned in Task 3: `"recovery: restart packet dropped"`.
2. **Tail loss at the end of an epoch or of the stream.** No later UDP packet reveals the hole, so the `EpochStart.prev_end_seq` / `End.end_seq` frames must trigger the request. It is pinned in Task 3: `"recovery: tail loss before restart and at end"`.
3. **A retransmit that overlaps already-applied messages** (straddle), or a held packet that a retransmit makes partly redundant. No message may be applied twice. It is pinned in Task 3: `"recovery: overlapping retransmit applies each message once"`.
4. **The publisher's ring buffer cannot serve a request.** The handler must fail loudly: `failed()`, a nonzero tool exit, and no silent resync. It is pinned in Task 3 (`"recovery: unserved request is fatal"`) and in Task 2 (`"ring: evicted range is not covered"`).
5. **The file's own merge-seam duplicates** (an old send time, a seq below expected) must be counted as duplicates, never mistaken for a new epoch. It is pinned in Task 3: `"recovery: merge-seam duplicate is not an epoch"`.

---

### Task 1: Book digest and the reference digest tool

**Files:**
- Modify: `cpp/src/pkmn_book/order_book.hpp`: add a const visitor.
- Modify: `cpp/src/pkmn_md/book_set.hpp`: add a const visitor.
- Create: `cpp/src/pkmn_md/book_digest.hpp`, `cpp/src/pkmn_md/book_digest.cpp`.
- Create: `cpp/tools/iex_digest.cpp`.
- Modify: `cpp/tests/md_fixtures.hpp`:
  - move `dp_add` and `put_sym` in from `test_iex_oracle.cpp`, and update that file to use them;
  - give `tp_segment` an optional trailing `std::int64_t send_time = 1'734'964'524'000'000'000` parameter.
- Create: `cpp/tests/test_book_digest.cpp`.
- Modify: `cpp/CMakeLists.txt`: add `book_digest.cpp` to `pkmn_md`, add the `iex_digest` executable, and add the test to `md_tests`.

**Interfaces:**
- Produces:
  - `template <class F> void OrderBook::for_each_order(Side side, F&& f) const`: calls `f(Price price, OrderId id, Qty qty)` for every resting order, best level first and FIFO within a level. It walks `levels(side)` from the back, following `head`/`next`.
  - `template <class F> void BookSet::for_each_book(F&& f) const`: calls `f(std::uint64_t symbol, const book::OrderBook&)` in unspecified order.
  - In `pkmn_md/book_digest.hpp`, namespace `pkmn::md`:
    - `struct EpochDigest { std::uint64_t digest; std::uint64_t orders; std::uint64_t messages; };`. `messages` is the count of messages applied in that epoch.
    - `std::uint64_t digest_books(const BookSet& books);`: skips empty books, visits symbols in ascending order, then Buy then Sell, then each order. It mixes `(symbol, side, price, id, qty)` with FNV-1a 64 over the little-endian bytes. Two books with the same levels but a different order priority **must** digest differently.
    - `std::vector<EpochDigest> reference_digests(const std::string& pcap_path);`: runs `FeedReader(path, kDeepPlus)` with `apply_deep_plus_segment_with`. One entry is pushed in `before_clear` (the digest before the restart clear) and one at end of stream, so 1 restart gives 2 entries.
    - `void write_digests(std::FILE* out, const std::vector<EpochDigest>& d);`: one line per epoch, `epoch <i> digest <16 hex> orders <n> messages <n>`.
    - `std::vector<EpochDigest> read_digests(const std::string& path);`: throws `std::runtime_error` on a malformed line.
  - Tool: `iex_digest <dplus.pcap.gz> [--out FILE]` writes `write_digests` output (stdout by default) and exits 0.

- [ ] **Step 1: Write the failing tests** in `test_book_digest.cpp`:
  - `"digest: empty and cleared books digest equal"`: a fresh `BookSet`, and a `BookSet` that had one add then `clear_all()`, give equal `digest_books`.
  - `"digest: sensitive to price, qty, id, side and priority"`: build two sets with the same two orders at one level, added in opposite order. The digests differ. Changing any one field of one order also changes the digest.
  - `"digest: symbol iteration order does not matter"`: the same orders, with symbols first touched in a different order, give equal digests.
  - `"reference: one entry per epoch, restart digested before clear"`: write a gzipped fixture with `capture()`. It holds:
    - seg(seq 1, 2 adds, t0) and seg(seq 3, 1 add, t1);
    - a restart seg(seq 1, 1 add, t2 > t1);
    - a merge-seam duplicate of the second segment (seq 3, t1).

    `reference_digests` returns 2 entries, with `orders` 3 then 1 and `messages` 3 then 1. Entry 0's digest equals `digest_books` of a `BookSet` built directly from those 3 adds.
  - `"digests: write/read round trip"`.
- [ ] **Step 2:** Configure and build `md_tests`. Expected: compile failure (`book_digest.hpp` not found).
- [ ] **Step 3:** Implement the visitors, `book_digest.*` and `iex_digest.cpp`, then wire up CMake.
- [ ] **Step 4:** Run `ctest --test-dir cpp/build -R "digest|reference"`. Expected: all pass. Then run the full ctest, which must stay green, including `test_iex_oracle` after the fixture move.
- [ ] **Step 5: Commit** `feat(md): canonical book digest and iex_digest reference tool`.

### Task 2: Retransmit protocol, ring buffer and drop injector (pure)

**Files:**
- Create: `cpp/src/pkmn_md/recovery_proto.hpp`, `cpp/src/pkmn_md/recovery_proto.cpp`.
- Create: `cpp/src/pkmn_md/retransmit_ring.hpp`, `cpp/src/pkmn_md/retransmit_ring.cpp`.
- Create: `cpp/src/pkmn_md/drop_injector.hpp`.
- Create: `cpp/tests/test_recovery_parts.cpp`.
- Modify: `cpp/CMakeLists.txt`.

**Interfaces:**
- Produces (namespace `pkmn::md`):
  - `enum class FrameType : std::uint8_t { Request = 1, Retransmit = 2, EpochStart = 3, End = 4, Unserved = 5 };`
  - Wire frame: a 12-byte little-endian header, then the body.
    - The header is `u8 type, u8 reserved[3] (zero), u32 epoch, u32 body_len`.
    - Bodies, all little-endian:

      | Frame | Body |
      |---|---|
      | Request | `i64 from, i64 to` (half-open `[from, to)`) |
      | Retransmit | the original UDP payload, unchanged |
      | EpochStart | `i64 prev_end_seq, i64 first_send_time` (`epoch` is the new epoch, at least 1; `prev_end_seq` is the old epoch's end) |
      | End | `i64 end_seq` |
      | Unserved | `i64 from, i64 to` |

    - Epochs are numbered from 0. Epoch 0 is implicit and has no EpochStart.
    - `inline constexpr std::size_t kFrameHeaderBytes = 12;`
  - `void encode_frame(std::vector<std::uint8_t>& out, FrameType t, std::uint32_t epoch, std::span<const std::uint8_t> body);` appends to `out`. There are also helpers `encode_range(out, FrameType, epoch, from, to)`, `encode_epoch_start(out, epoch, prev_end_seq, first_send_time)` and `encode_end(out, epoch, end_seq)`.
  - `struct Frame { FrameType type; std::uint32_t epoch; std::span<const std::uint8_t> body; };`
  - `class FrameParser { public: void feed(std::span<const std::uint8_t> bytes); bool next(Frame& out); };`. It reassembles a TCP byte stream. `out.body` stays valid until the next `feed`/`next`. A frame with an unknown type or `body_len > 65535 + 40` throws `std::runtime_error`.
  - `class RetransmitRing`:
    - `explicit RetransmitRing(std::size_t capacity_bytes);`
    - `void store(std::uint32_t epoch, std::int64_t first_seq, std::uint16_t count, std::span<const std::uint8_t> payload);` copies the payload. Heartbeats (count 0) are ignored. When the ring is full, it evicts the oldest stored entries first.
    - `bool collect(std::uint32_t epoch, std::int64_t from, std::int64_t to, std::vector<std::span<const std::uint8_t>>& out) const;` appends every stored packet of `epoch` whose range overlaps `[from, to)`, in ascending `first_seq` order. It returns false, with `out` unchanged, if the union of those ranges does not cover `[from, to)`.
    - `std::size_t bytes() const; std::uint64_t evicted() const;`
    - Lookup must be O(log n) per request (for example, an ordered index keyed by `(epoch, first_seq)` over a FIFO of owned buffers). The ring is not internally synchronized; the publisher locks around it.
  - `struct DropSpec { enum class Mode : std::uint8_t { None, Random, Burst } mode = Mode::None; double p = 0; std::uint32_t burst_len = 0; std::uint64_t seed = 0; };`
  - `std::optional<DropSpec> parse_drop_spec(std::string_view s, std::uint64_t seed);` accepts `none`, `random:<p>` and `burst:<p>:<len>`. It returns nullopt when p is outside [0,1] or `len == 0`.
  - `class DropInjector { public: explicit DropInjector(const DropSpec&); bool drop(); std::uint64_t dropped() const; std::uint64_t bursts() const; };`
    - Random: `std::mt19937_64(seed)` with `std::uniform_real_distribution<double>(0,1)(rng) < p`.
    - Burst: when not in a burst, the same draw `< p` starts a burst. The current packet and the next `burst_len - 1` packets are dropped.

- [ ] **Step 1: Write the failing tests** in `test_recovery_parts.cpp`:
  - `"proto: every frame type round-trips, header is 12 bytes LE"`.
  - `"proto: parser reassembles frames split at every byte boundary"`: feed a three-frame stream one byte at a time.
  - `"proto: unknown type throws"`.
  - `"ring: collect covers exact, straddling and multi-packet ranges"`.
  - `"ring: epochs are separate"`: the same seqs in epoch 0 and epoch 1 never mix.
  - `"ring: evicted range is not covered"`: with a capacity that fits only 2 packets, store 3, then `collect` the first one's range returns false and `evicted() == 1`.
  - `"drop: same seed same pattern; random rate within 3 sigma over 1e5"`.
  - `"drop: burst drops exactly burst_len consecutive"`.
  - `"drop: parse rejects p>1, len 0, garbage"`.
- [ ] **Step 2:** Build. Expected: compile failure.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run `ctest --test-dir cpp/build -R "proto|ring|drop"`. Expected: all pass.
- [ ] **Step 5: Commit** `feat(md): retransmit frame protocol, retransmit ring, seeded drop injector`.

### Task 3: `RecoveringHandler`, the in-process recovery core

**Files:**
- Create: `cpp/src/pkmn_md/gap_recovery.hpp`, `cpp/src/pkmn_md/gap_recovery.cpp`.
- Create: `cpp/tests/test_gap_recovery.cpp`.
- Modify: `cpp/CMakeLists.txt`.

**Interfaces:**
- Consumes:
  - `EpochDigest` and `digest_books` (Task 1);
  - `SequenceTracker` (`sequence.hpp`);
  - `parse_tp_header`, `for_each_message`, `kDeepPlus` and `valid_first_seq` (`iextp.hpp` / `sequence.hpp`);
  - `Segment` and `apply_deep_plus_segment` (`feed_reader.hpp` / `replay.hpp`). The handler builds a `Segment` with `restart = false` and the computed `skip`.
- Produces (namespace `pkmn::md`):

```cpp
struct RecoveryStats {
    std::uint64_t datagrams = 0, rejected = 0;           // rejected: bad header/framing, wrong protocol or stream
    std::uint64_t segments_applied = 0, messages_applied = 0;
    std::uint64_t gaps = 0, requests = 0, requested_messages = 0;
    std::uint64_t retransmit_segments = 0, duplicate_messages = 0;
    std::uint64_t epochs = 0, parked = 0, unrecoverable = 0;
    std::size_t held_max = 0;                            // peak held datagrams
};
class RequestSink {
  public:
    virtual ~RequestSink() = default;
    virtual void request(std::uint32_t epoch, std::int64_t from, std::int64_t to) = 0;  // [from, to)
};
class RecoveringHandler {
  public:
    explicit RecoveringHandler(RequestSink& sink);
    void on_datagram(std::span<const std::uint8_t> udp_payload);                 // multicast
    void on_retransmit(std::uint32_t epoch, std::span<const std::uint8_t> payload);
    void on_epoch_start(std::uint32_t epoch, std::int64_t prev_end_seq, std::int64_t first_send_time);
    void on_end(std::uint32_t epoch, std::int64_t end_seq);
    void on_unserved(std::uint32_t epoch, std::int64_t from, std::int64_t to);  // sets failed()
    bool done() const;    // End seen and every epoch finalized
    bool failed() const;  // unrecoverable > 0
    const std::vector<EpochDigest>& digests() const;
    const RecoveryStats& stats() const;
};
```

**Rules** (these come from the spec's Decisions; the builder writes the code):

1. **Stream filter.** The first datagram with a valid header, `protocol_id == kDeepPlus`, a `valid_first_seq` and correct framing fixes `(channel, session)`. Any datagram that fails these checks counts as `rejected` and is treated as not received.
2. **Epoch classification of a UDP datagram.** Let `starts[e]` be the announced `first_send_time` of each epoch; epoch 0 starts at minus infinity.
   - The datagram belongs to the highest announced `e` with `starts[e] <= send_time`.
   - If that `e` is the current epoch, `first_seq` is below the highest sequence end seen in it, and `send_time` is greater than every send time seen so far, the datagram may be an unannounced new epoch. **Park it** (count `parked`).
   - Re-classify parked datagrams on every `on_epoch_start`. On `on_end`, any datagram still parked is a duplicate.
   - Retransmits are never classified; they carry their epoch.
3. **Ordering.** Per epoch, keep a `SequenceTracker` and a held map `first_seq -> owned bytes`.
   - **Current epoch.**
     - A Next datagram, or one whose tail extends past `expected`, is applied (skip = `expected - first_seq` when it straddles, counted in `duplicate_messages`). The handler then drains held entries while `held.begin()->first <= expected`.
     - A whole duplicate is counted and dropped.
     - When `first_seq > expected`, the datagram is held. If `first_seq > requested_upto`, the handler calls `sink.request(epoch, max(expected, requested_upto), first_seq)`, counts `gaps`/`requests`/`requested_messages`, and sets `requested_upto = first_seq`.
   - **Future epochs.** Their datagrams are held until those epochs become current.
4. **Tail and finalize.**
   - `on_epoch_start(e, prev_end, t)` records `end_seq[e-1] = prev_end`. `on_end(e, end)` records `end_seq[e]`.
   - Whenever the current epoch's end is known and `expected < end` with nothing held below `end`, the handler requests the missing tail once (guarded by `requested_upto`).
   - When `expected == end_seq[current]`, and either epoch `current+1` is announced or End was seen for `current`, the handler finalizes the epoch, in this order:
     1. Push `EpochDigest{digest_books(books), books.order_count(), messages applied this epoch}`.
     2. If this is not the final epoch: `books.clear_all()`, increment `current`, and drain its held entries. The new epoch's tracker starts at 1, and a held first datagram with `first_seq > 1` triggers `request(current, 1, first_seq)`. This is what "restart packet dropped" exercises.
   - `done()` becomes true after the End epoch is finalized.
5. **Failure.** `on_unserved` increments `unrecoverable`. Afterwards `failed()` is true and nothing more is applied.

- [ ] **Step 1: Write the failing tests** in `test_gap_recovery.cpp`.
  - **Harness.** A `ScriptSink` records requests and, when asked, serves them from a `RetransmitRing` (Task 2) filled with every segment. A helper `run_with_loss(segments, drop_mask)` delivers the non-dropped datagrams in order, then delivers EpochStart/End at the positions the publisher would emit them (the EpochStart frame immediately before the first UDP send of the new epoch, End after the last). It then loops serving pending requests until `done()` or `failed()`. The reference is `reference_digests` (Task 1) on the same segments written as a gzipped fixture.
  - **Cases:**
    - `"recovery: lossless stream matches reference"`.
    - `"recovery: single mid-epoch gap is requested once and repaired"`. Assert `stats().gaps == 1`, one request with the exact `[from, to)`, and digests equal to the reference.
    - `"recovery: out-of-order arrival fills the hole without a duplicate apply"`. N+1 arrives before N, and N arrives before the request is served; then the served retransmit is a whole duplicate. `messages_applied` equals the reference total.
    - `"recovery: overlapping retransmit applies each message once"`. A Review Focus item.
    - `"recovery: restart packet dropped"`. Drop the epoch-1 seq-1 datagram, and deliver the next epoch-1 datagram **before** its EpochStart frame. It is parked, then re-classified. `request(1, 1, k)` is issued, and the digests equal the reference. A Review Focus item.
    - `"recovery: tail loss before restart and at end"`. Drop the last datagram of epoch 0 and the last of epoch 1. A Review Focus item.
    - `"recovery: merge-seam duplicate is not an epoch"`. Resend an old-send-time packet late; it is counted as a duplicate, not parked forever. A Review Focus item.
    - `"recovery: unserved request is fatal"`. A Review Focus item. Assert `failed()`, `!done()`, and `unrecoverable == 1`.
    - `"recovery: seeded 5% random loss over 2,000 synthetic segments matches reference"`. Build 2,000 segments of adds/modifies/deletes/executes over 20 symbols with one restart at segment 1,500, with `DropInjector` random:0.05 seed 7, and a second run with burst:0.01:25.
    - `"recovery: rejected datagram is a gap"`. A truncated copy of a segment (framing fails) counts as `rejected`, and the real one arrives by retransmit.
- [ ] **Step 2:** Build. Expected: compile failure.
- [ ] **Step 3:** Implement `gap_recovery.*`.
- [ ] **Step 4:** Run `ctest --test-dir cpp/build -R recovery`. Expected: all pass. Then run the full macOS ctest.
- [ ] **Step 5: Commit** `feat(md): RecoveringHandler, gap recovery core with epoch-aware retransmit`.

### Task 4: Socket tools `feed_publisher` and `feed_handler`

**Files:**
- Create: `cpp/tools/net_util.hpp`. It holds small RAII fd and socket helpers that compile on Linux and macOS: multicast sender and receiver (`IP_ADD_MEMBERSHIP`, `IP_MULTICAST_LOOP=1`, `IP_MULTICAST_TTL=0`, interface `127.0.0.1`), a TCP listen/accept/connect helper, `write_all`, and a `SO_RCVBUF` set that reports the size actually achieved.
- Create: `cpp/tools/feed_publisher.cpp`, `cpp/tools/feed_handler.cpp`.
- Create: `cpp/tests/test_feed_tools.cpp`. It runs both built binaries, Linux only; on other platforms it calls `SKIP("loopback multicast e2e runs in the Linux VM")`.
- Modify: `cpp/CMakeLists.txt`. Add both executables, linked to `pkmn_md` and `Threads::Threads`, plus `FEED_PUBLISHER_BIN`/`FEED_HANDLER_BIN`/`IEX_DIGEST_BIN` compile definitions on `md_tests`, following the `IEX_ORACLE_BIN` pattern.

**Interfaces:**
- Consumes: Tasks 1-3, `PcapngReader`/`SegmentFilter` (`feed_reader.hpp`).
- Produces:
  - **`feed_publisher`**
    - Usage: `feed_publisher <dplus.pcap.gz> [--group 239.255.0.1] [--port 31001] [--ctrl-port 31002] [--rate MSGS_PER_SEC|0=unthrottled] [--drop none|random:p|burst:p:len] [--seed N] [--ring-bytes N (default 268435456)] [--ram] [--packet-limit N]`.
    - Behaviour:
      1. Listen on TCP `127.0.0.1:ctrl-port` and accept one handler.
      2. With `--ram`, preload every accepted payload first, like `iex_bench` (the 7 GB VM fits the 3.4 GB day).
      3. Feed each UDP payload, in file order, through a `SegmentFilter(kDeepPlus)`. Skip non-accepted payloads, counted. For each accepted segment:
         1. On `seg.restart`, write `EpochStart(++epoch, seg.expected_before, seg.hdr.send_time)` to TCP **before** the UDP send.
         2. Store it in the ring under `epoch`.
         3. Unless `DropInjector::drop()` says otherwise, `sendto` the unchanged payload.
         4. Pace to `--rate` messages/sec with `steady_clock`.
      4. After the last packet, write `End(epoch, filter.tracker().expected())`.
    - A separate thread serves `Request` frames: `collect` under a mutex, then one `Retransmit` frame per packet, or `Unserved`. It exits when the handler closes TCP.
    - It prints one JSON object to stdout: `packets_read, segments_sent, messages_sent, dropped, bursts, epochs, retransmit_requests, retransmit_packets, unserved, ring_evicted, send_wall_ns, rate_target`.
  - **`feed_handler`**
    - Usage: `feed_handler [--group 239.255.0.1] [--port 31001] [--ctrl-host 127.0.0.1] [--ctrl-port 31002] [--rcvbuf BYTES (default 7500000)] [--expect DIGESTS_FILE] [--timeout-s 600]`.
    - Behaviour:
      1. Join the group **before** connecting TCP, so the publisher cannot start sending before the handler is listening.
      2. Run a `poll()` loop that drains TCP frames first, then UDP. It uses `recvmsg` and treats `MSG_TRUNC` as not received (counted as rejected). Its `RequestSink` writes `Request` frames.
      3. It exits when `done()` or `failed()`, or when the timeout expires.
    - It prints JSON with every `RecoveryStats` field, plus `rcvbuf_actual`, `wall_ns` (first datagram to done), `msgs_per_sec` (`messages_applied / wall`), the digests, and, with `--expect`, `digest_match: true|false`.
    - Exit codes: 0 means done and matched (or no `--expect`); 1 means a digest mismatch, printing the first differing epoch; 2 means unrecoverable or timeout.

- [ ] **Step 1: Write the failing test** `"feed tools: loopback multicast with 10% random loss matches iex_digest"`. Linux only. Write the Task 3 2,000-segment fixture to a temp `.pcap.gz`, and run `iex_digest --out ref`. Start `feed_handler --expect ref --port <random 30000-60000> --ctrl-port <port+1>`, then `feed_publisher ... --drop random:0.1 --seed 3 --rate 200000`. Assert:
  - the handler exits 0 with `"digest_match": true` and `"gaps"` > 0;
  - the publisher's `dropped` > 0 and `unserved == 0`.
- [ ] **Step 2:** Run in the VM (command in Global Constraints). Expected: FAIL (binaries missing).
- [ ] **Step 3:** Implement `net_util.hpp` and both tools.
- [ ] **Step 4:** Run the VM ctest. Expected: all pass, including `feed tools`. Run the macOS ctest. Expected: all pass, with `feed tools` skipped.
- [ ] **Step 5: Commit** `feat(tools): feed_publisher and feed_handler over loopback multicast with TCP retransmit`.

### Task 5: Real-day recovery run (MUST SHIP)

**Files:**
- Create: `scripts/bench_feed.sh`, modelled on `scripts/bench_iex.sh` (it re-execs in the VM when not on Linux).
- Create: `docs/benchmarks/feed-handler.md`.
- Create: `docs/benchmarks/feed-handler-raw/` (JSON per run).

**Interfaces:**
- `scripts/bench_feed.sh <scenario> [--no-record]`. The scenario is one of `random` (= `--drop random:0.01 --seed 1`, stream mode, `--rate 0`), `burst` (= `--drop burst:0.0005:100 --seed 2`, stream mode, `--rate 0`), or `sweep` (Task 7).
- It does the following:
  1. Builds the three tools Release in `cpp/build-linux-release`.
  2. Writes `data/feed/reference-20241223.digests` with `iex_digest` if it is missing. It goes under the gitignored `data/` tree, and is recomputed only when missing.
  3. Starts the handler with `--expect`, then the publisher.
  4. Saves both JSONs under `docs/benchmarks/feed-handler-raw/<date>-<scenario>-<sha>.json`.
  5. Prepends a section to `docs/benchmarks/feed-handler.md`, newest first. The section gives hardware, host, git SHA (dirty-aware, as in `bench_iex.sh`), compiler, flags, file and scenario, and a table with these columns: segments sent, messages, injected drops, gaps detected, messages requested, retransmitted packets, natural drops (gaps not explained by injection; state how this is computed), duplicates discarded, epochs, unrecoverable, held max, digest match per epoch, handler wall, and msgs/s.

- [ ] **Step 1:** Write `bench_feed.sh`. Dry-run it with `IEX_FILE=<fixture.pcap.gz> scripts/bench_feed.sh random --no-record` against the Task 4 fixture. Expected: digest match.
- [ ] **Step 2:** Run `scripts/bench_feed.sh random` on the real `data/iex/20241223_IEXTP1_DPLS1.0.pcap.gz`. Expected:
  - the handler exits 0;
  - both epoch digests match;
  - `unrecoverable 0`;
  - gaps > 0.

  If it fails, use superpowers:systematic-debugging. A mismatch is a bug until explained. Do not loosen the comparison.
- [ ] **Step 3:** Write the doc's fixed header. Cover what the run proves, the incident-day caveat (copy the substance of `docs/benchmarks/iex-book.md`'s header: the halt, the 11:32:50 ET restart, and the 50,878,184 / 80,992 message split), that this is loopback inside one VM and not a network, and the IEX citation line. Also add a "Resume bullets (draft)" section with 2-3 bullets, each quoting a number that appears in the table, in the style "Designed a UDP multicast feed handler that recovered N dropped packets via TCP retransmit across 42M+ real IEX packets with zero book divergence". No em dashes.
- [ ] **Step 4: Commit** `bench(feed): full-day 2024-12-23 DEEP+ recovery under 1% random loss` (script, doc and raw JSON).

### Task 6: Burst-loss real-day run (cut first if time is short)

- [ ] **Step 1:** Run `scripts/bench_feed.sh burst`. Expected: digest match, `unrecoverable 0`. If a burst outruns the ring (an unserved request), raise `--ring-bytes`, re-run, and record both runs. Never drop the failing one.
- [ ] **Step 2:** Update the bullets if the burst result strengthens one, for example "under random and burst loss".
- [ ] **Step 3: Commit** `bench(feed): burst-loss full-day run`.

### Task 7: Throughput sweep (cut second)

**Files:** Modify `scripts/bench_feed.sh` (the `sweep` scenario) and `docs/benchmarks/feed-handler.md`.

- [ ] **Step 1:** Implement `sweep`. It uses publisher `--ram --drop none` at target rates of 1M, 2M, 4M and 8M msgs/s and unthrottled (0), one run each, with every run checked against the reference.
- [ ] **Step 2:** Record a table with rate target, achieved msgs/s at the handler, natural drops, gaps recovered, and digest match. Define and state two figures:
  - **"clean sustained"**: the highest target rate with 0 gaps and a digest match;
  - **"unthrottled with recovery"**: achieved msgs/s, gaps recovered and digest match.

  Note the VM noise caveat (10% or more, per `iex-book.md`). This is n=1 per rate, so say so.
- [ ] **Step 3:** Update the bullets with the throughput number. Only a rate with a digest match may be quoted.
- [ ] **Step 4: Commit** `bench(feed): throughput sweep`.

### Task 8: Docs and status

**Files:** `CLAUDE.md` (Status bullet for Plan C; the `cpp/src/pkmn_md` and `cpp/tools` Layout entries; the bench commands), `README.md` (a short feed-handler paragraph with the headline numbers and the IEX citation), and `docs/superpowers/handoffs/` (not committed; leave it alone).

- [ ] **Step 1:** Write the CLAUDE.md status bullet, in the house style of the other bullets. It covers:
  - the test counts (pytest, Catch2 on macOS and in the VM);
  - what shipped;
  - the real-day numbers with run conditions;
  - what was cut (Task 6/7, if skipped);
  - known limitations: tiny-epoch misclassification (rule 2 needs a new epoch's first seen seq to sit below the old epoch's highest seq); retransmit only, no snapshot; loopback only.
- [ ] **Step 2:** Add the README paragraph. Plain prose, no em dashes.
- [ ] **Step 3:** Run all gates (macOS ctest, VM ctest, the four Python gates). Expected: green.
- [ ] **Step 4: Commit** `docs: Plan C feed handler status, README, bench commands`.
