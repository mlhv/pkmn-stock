# Plan C: IEX DEEP+ Multicast Feed Handler with Gap Recovery: Design

Status: draft, awaiting owner approval (2026-10-26).
Parent spec: `docs/superpowers/specs/2026-10-22-market-data-and-clob-design.md`
(its Transport and Measurement sections). This document narrows Plan C to
what can ship tonight and settles what the parent left open.

## Intent and why

The owner wants a resume line in the style of "Designed a feed handler
sustaining X M messages/sec with zero gaps, duplicates, or resets across N M
packets." The deadline is **end of day 2026-10-26**. That deadline is the
reason this plan comes before Plan D: D is a research re-run whose expected
headline is "little or nothing changes", while C is the systems build that
completes the interview story (wire protocol, feed handler, order book,
strategy, backtest, statistics).

The owner's stated priority, in order:

1. **Loss-recovery proof**: a book rebuilt from a lossy multicast stream,
   with gaps repaired by retransmit, is identical to the book rebuilt
   directly from the file. This must ship.
2. **Throughput number**: sustained messages/sec through the multicast path,
   if time allows.

Whatever ships must yield concrete, honest numbers for resume bullets.

## Owner decisions

- **(owner) The stream on the wire is real IEX DEEP+**, the 2024-12-23 DPLS
  capture already on disk (`data/iex/20241223_IEXTP1_DPLS1.0.pcap.gz`,
  42,492,551 packets, 50,959,176 messages). Not the Pokemon wire format.
- **(owner) Recovery proof first, throughput second.**
- **(owner) C before D.** D stays as written in the handoff
  (`docs/superpowers/handoffs/2026-10-26-handoff.md`) for a later session.

## Goals

1. **Publisher** (C++20, `cpp/tools/`). It reads the DEEP+ capture and sends
   each IEX-TP UDP payload **byte for byte unchanged** as one UDP multicast
   datagram. Rate: unthrottled, or a target messages/sec. It keeps a ring
   buffer of recently sent packets and answers retransmit requests from it.
   Source packets go out in file order, including the file's own merge-seam
   duplicates. The handler must cope with real duplicates, not only injected
   ones.
2. **Drop injection** in the publisher, seeded and reproducible, in two
   modes: random (each packet dropped with probability p) and burst (runs of
   N consecutive packets dropped at a given rate). Dropped packets are still
   stored in the ring buffer, so they can be retransmitted. Injection applies
   to the multicast send only, never to retransmits.
3. **Feed handler** (C++20). It joins the group and frames packets with the
   existing IEX-TP code. It detects gaps and duplicates by sequence number,
   **holds packets that arrive after a gap until the gap is filled**,
   requests the missing range over a TCP side channel, and applies messages
   to the Plan A `BookSet` in strict sequence order. A request the ring
   buffer can no longer serve is a **loud failure**: a nonzero exit and a
   named counter. It never silently resyncs.
4. **Correctness proof**: a canonical book digest. The final books from the
   handler under induced loss must equal the final books from a direct
   file replay (the existing `FeedReader` + `apply_deep_plus_segment` path).
   There are two levels:
   - CI: Catch2 tests over small hand-built fixtures, plus an in-process
     loss-and-recovery test that needs no sockets.
   - Real day: the full 2024-12-23 capture over real loopback multicast in
     the VM, with random loss and with burst loss, digest equal to the
     direct replay.
5. **Measurements**, recorded in `docs/benchmarks/feed-handler.md` with
   hardware, date, git SHA, compiler and flags, in the style of
   `docs/benchmarks/iex-book.md`. For each run:
   - packets and messages sent, received, dropped by injection, and lost
     to the kernel (natural drops);
   - gaps detected, messages requested, packets retransmitted, duplicates
     discarded, stream resets;
   - unrecoverable requests (must be 0);
   - digest match yes/no, wall time, and msgs/s.
6. **Resume bullets**: two or three draft bullets, each backed by a number in
   the benchmark doc, with no em dashes and no AI-sounding phrasing. They go
   at the end of the benchmark doc or in the hand-off. The README and
   CLAUDE.md get a status bullet.

## Non-goals

- Snapshot recovery (a full book image after falling too far behind).
  This is a stretch goal only; the ring buffer is sized so the real-day run
  never needs it.
- Pokemon wire format over multicast.
- A real network, a second host, kernel bypass, or busy-polling tuning
  beyond what throughput needs.
- Plan D, the IEX second-day oracle, and the parked live-ingest minors.
- Python bindings. This is a C++-only plan. The Python gates still run
  because they are project rules, but no Python code should change.

## Anti-goals (a failure even if it technically works)

- **A resume number the benchmark doc does not back**, or one that hides a
  caveat that changes its meaning. Two examples: a throughput figure taken
  at a rate where the digest did not match, or "zero gaps" when gaps were
  detected and recovered. The honest claims are "N gaps recovered, zero
  divergence" and "X msgs/s with digest match".
- **A test that compares the handler against itself.** The reference book
  must come from the existing direct file-replay path.
- **Silent resync**: dropping the gap and carrying on.
- Breaking any existing gate, parity test, or the Plan A oracle and bench
  tools.

## Constraints

- Hardware and host as in the parent spec. Headline numbers come from the
  OrbStack VM (`ubuntu`, 7 vCPUs, about 17 GB disk free), and every number
  is stated with "M1 Pro, 7-vCPU OrbStack Ubuntu 24.04 VM, loopback
  multicast". The Mac has about 24 GB free.
- Never decompress the capture to disk. The publisher may preload payloads
  into RAM, as `iex_bench` does (about 3.4 GB of payload). It may also
  stream through zlib, which caps at about 2.5M msgs/s.
- Everything builds and the Catch2 suite passes on macOS **and** in the VM.
  The new market-data code stays out of the Python wheel build, as
  `pkmn_md` does today.
- Project rules hold: four gates before every commit, no fast-math or
  fp-contract=fast, the IEX citation line wherever IEX results are
  published, and nothing under `data/` deleted.
- Branch `feat/feed-handler`, cut from `main` (`3ce6d42`).
- Workflow per the handoff: writing-plans, then subagent-driven development
  (one implementer and one reviewer per task, a final whole-branch review,
  one fix wave, a scoped re-review), no pauses between tasks, stop only for
  merge, push, destructive actions or live writes. Because of tonight's
  deadline, **order tasks so the recovery proof is complete and committed
  before any throughput work starts.**

## Decisions (designer's calls, made in this conversation, not owner-approved
## individually; the owner approves them by approving this document)

- **Recovery is retransmit-only**, over a TCP request channel to the
  publisher. A request is a sequence range. The reply is the original
  packets covering that range, unchanged. Requests the publisher can no
  longer serve fail loudly.
- **The 11:32:50 ET mid-day restart is a stream reset, handled the way
  `iex_oracle` handles it**: `is_stream_restart`, `tracker.reset(1)`,
  `BookSet::clear_all()`. It is counted and reported. Because sequence
  numbers restart at 1, the ring buffer and the retransmit request must
  identify the epoch, not only the sequence number. This day was chosen
  over a clean session because a clean session costs about 5 GB of new
  downloads.
- **Digest at every epoch boundary, not only at the end.** The 11:32:50
  restart clears every book, and only 80,992 of about 51M messages come
  after it. A final-books digest alone would never see whether recovery
  before the restart worked. The handler and the direct replay therefore
  each digest the books **just before every restart clear** (the
  `before_clear` hook) **and at end of stream**. The proof is "every digest
  in the sequence matches". Periodic mid-epoch checkpoints are optional.
- **Restart detection must survive loss of the restart packet.** Today
  `is_stream_restart` fires only on `first_seq == 1` with a newer send
  time. If that packet is dropped, the next new-epoch packet (seq 40, say)
  sits far below `expected` and would be discarded as a duplicate: the
  silent loss this plan forbids. The handler's rule is that **a segment
  with `first_seq < expected` and a send time newer than every send time
  seen so far is a new epoch**. The handler then digests and clears, resets
  the tracker to 1, and treats `[1, first_seq)` as a gap to request from
  the new epoch. Merge-seam duplicates carry old send times, so they never
  trip this rule. The handler counts epochs. A retransmit request carries
  (epoch index, sequence range), and the publisher's ring buffer is keyed
  the same way. Drop injection applies to the restart packet like any
  other; nothing is exempt. The existing `SegmentFilter` rule used by the
  direct replay and the Plan A tools stays unchanged.
- **The "known limitation" needs no change to shipped Plan B code.** The
  limitation is in the Pokemon wire decoder (`pkmn_wire`). The IEX path
  already rejects a truncated segment whole in `SegmentFilter::accept`
  (counted as a `tp_parse_failure`) before the tracker advances. The
  handler only has to treat a rejected datagram, or a kernel-truncated one
  (`MSG_TRUNC`), as not received, so the hole it leaves is requested as a
  gap. The Pokemon decoder and its tests are not touched.
- **The reorder-at-merge-seam limitation in `sequence.hpp`** (N+1 arriving
  before N is read as a gap, and then N as a duplicate) is exactly what
  hold-and-request fixes in the handler: the late packet fills the hole
  before or instead of the retransmit. The direct-replay reference still
  uses the old rule. 2024-12-23 shows 0 gaps on direct replay, so the two
  agree on this day. If they ever disagree, that is a finding to report,
  not to paper over.
- **Book equality is a canonical digest**, for example a 64-bit hash over
  every symbol's book in a fixed order: each side, each level, and each
  order in priority order (id, price, size). This needs a new read-only
  accessor or visitor on `OrderBook`/`BookSet`. It must not change apply
  behaviour or slow the hot path. A digest mismatch prints the first
  differing symbol.
- **Unit and recovery tests use an in-process transport.** The
  hold/request/apply logic sits behind an interface that a test drives
  with a scripted packet sequence, so it runs on macOS CI with no sockets.
  The real-socket end-to-end runs in the VM.

## Left to the builder

- Ring-buffer size, and the hold-queue limit. The full-day run under the
  chosen loss rates must need no unserved request; state the sizes used.
- Loss rates and burst lengths for the real-day runs. Pick something
  non-trivial (for example 1% random, and bursts of tens to hundreds of
  packets) and state it.
- The TCP request/reply wire format, and whether retransmits come back
  over TCP or are re-multicast. Over TCP is simpler and cannot itself be
  lost.
- The multicast group, port, TTL 0/1, `IP_MULTICAST_LOOP`, and receive
  buffer size (`SO_RCVBUF`; the VM's `net.core.rmem_max` may need raising,
  so document any sysctl used).
- Whether publisher and handler are separate processes (preferred, for
  realism) or threads, and the test harness script (for example
  `scripts/bench_feed.sh`, re-execing in the VM like `bench_iex.sh`).
- How "sustained msgs/s" is defined. A suggestion: the highest
  publisher target rate at which the handler keeps up with zero
  unrecoverable requests and a matching digest, plus the unthrottled
  figure with its natural-drop and recovery counts.
- Latency histograms are optional. The parent spec's latency figures
  already exist for decode+apply. Include them if they are cheap.

## Cut line (owner, 2026-10-26)

If tonight runs short, the work ships in this order, and nothing later
starts until everything earlier is committed:

1. **Must ship:** the in-process recovery tests (criterion 1), plus **one**
   full-day 2024-12-23 run in the VM at 1% seeded random loss, with every
   epoch digest matching the direct replay and the recovery counts
   recorded in `docs/benchmarks/feed-handler.md` (criteria 2-4 for that
   run only).
2. Cut first: the burst-loss full-day run.
3. Cut second: the throughput sweep.

## Success criteria

1. Catch2: gap, duplicate, straddle, reorder, restart, truncated-packet and
   unserved-request cases are covered with the in-process transport. There
   is a loss-and-recovery test whose digest equals the direct replay. Green
   on macOS and in the VM.
2. Real day, in the VM: the full 2024-12-23 DEEP+ capture over loopback
   multicast under seeded random loss and seeded burst loss gives 0
   unrecoverable requests and a final digest equal to the direct replay's.
   The counts are recorded.
3. `docs/benchmarks/feed-handler.md` records criterion 2, and the
   throughput figure if reached, with hardware, method, date, SHA and the
   IEX citation line.
4. Draft resume bullets, each traceable to a line in that doc.
5. All existing gates green. Plan A tools and the oracle are unchanged in
   output.
