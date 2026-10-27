# Feed handler benchmarks

Results of `scripts/bench_feed.sh` (`cpp/tools/feed_publisher.cpp`, `cpp/tools/feed_handler.cpp`,
reference from `cpp/tools/iex_digest.cpp`), newest first.

## What a run proves

`feed_publisher` replays the IEX DEEP+ capture as UDP multicast, one datagram per IEX-TP payload,
byte for byte unchanged, and drops packets on the send side with a seeded injector. Dropped packets
stay in its retransmit ring. `feed_handler` joins the group, detects gaps by sequence number, holds
the packets behind each gap, requests the missing range over a TCP side channel, and applies every
message to its books in sequence order. It takes a canonical digest of its books (FNV-1a over
every resting order's symbol, side, price, id and quantity, in price-time order, so queue priority
counts) at two kinds of points:

- **Checkpoints** (since run 3): immediately after applying the message whose sequence number,
  within its epoch, is a multiple of N (`--checkpoint-every N`, N = 100,000 in these runs). Both
  sides apply messages in strict sequence order, so the books at a given sequence number do not
  depend on segment boundaries, straddles or retransmits. A checkpoint records the epoch, the
  sequence number, the digest and the resting-order count.
- **Epoch ends**: just before the restart clear and at the end of the stream, with the message count.

The reference is not the handler's recovery code. `iex_digest` replays the same file directly through
the existing `FeedReader` path and writes the same entries to
`data/feed/reference-20241223-ckpt100000.digests` (computed once, its sha256 recorded with each run;
the earlier checkpoint-less `reference-20241223.digests` is kept for runs 1-2). It shares the DEEP+
decoder and the `OrderBook` code with the handler, so it is independent of the transport and the gap
recovery, not of the book itself; a decoder or book bug would show up on both sides. A run passes
only when both tools exit 0 (no unserved request, no timeout, End delivered), unrecoverable is 0,
gaps are above 0, and every checkpoint and every epoch entry equals the reference, with the same
number of checkpoints in the same order. The script checks this entry by entry itself, in addition
to the handler's own `--expect` comparison.

**What the checkpoints add.** On this capture the books end empty at both epoch ends (below), so the
epoch-end digest alone could not tell two different mid-day books apart. With N = 100,000 the
reference has 508 checkpoints, all in epoch 0, every one with resting orders (1,159 to 43,927). A
pass therefore means the handler's books, after recovering every gap, were identical to the direct
replay's (every order, price, quantity and queue position) at 508 points spread across the morning.
Epoch 1 (80,992 messages) is shorter than N and gets no checkpoint; a trial reference at N = 10,000
gave it 8 checkpoints, all of empty books, so its books are compared only at its end.

What a pass does not show:

- **This is loopback inside one VM, not a network.** Publisher and handler are two processes on
  the same 7-vCPU OrbStack VM talking through the kernel's loopback multicast path. There is no
  NIC, no switch, no wire latency and no second host. Loss is injected by the publisher; natural
  kernel loss is measured but was not induced.
- **The final books on this day are empty.** The DEEP+ stream itself deletes every resting order before the
  restart and again before its end, so the reference has 0 orders at the end of
  both epochs and both digests are the FNV-1a offset basis (`cbf29ce484222325`). The end-of-epoch
  digest cannot tell two different mid-day books apart on this capture; it pins only that both
  sides end empty after applying the same number of messages in each epoch (50,878,184 and 80,992).
  Runs 1 and 2 had only that check. The checkpoints (runs 3 and 4) are what compare populated
  books. A Catch2 test (`recovery: a checkpoint catches a divergence the end-of-epoch digest
  misses`, and its tool-level twin in `test_feed_tools.cpp`) shows the gap: two feeds that end
  empty with equal epoch digests but differ mid-epoch fail on the checkpoints.
- **Checkpoints are samples.** Books are compared every 100,000 messages, not after every message.
  A divergence that appears and heals between two checkpoints would not be seen.
- **Correct against the direct replay, not against IEX.** The direct replay itself does not match
  IEX's DEEP and TOPS feeds at every point on this day (see `docs/iex-oracle.md`). The handler
  reproduces the direct replay; the oracle question is separate and still open.

## The capture: an incident day

Every result here is measured on one capture, the 2024-12-23 DPLS file
(`data/iex/20241223_IEXTP1_DPLS1.0.pcap.gz`, 42,492,551 packets, 50,959,176 messages), so it
describes that day's message mix, not a normal session's. That day was an incident: every symbol was
operationally halted at 10:22:29 ET, the feed restarted at 11:32:50 ET, 50,878,184 of its 50,959,176
messages come before the restart and 80,992 after, and the last book event is at 13:45:40 ET (see
`docs/iex-oracle.md`). The restart is why every run has two sequence epochs. Throughput and recovery
counts on a normal full day may differ.

## How the columns are counted

- **Injected drops** are packets the publisher's seeded injector did not send. **Gaps detected** are
  holes the handler found and requested (one request per gap). They differ from injected drops
  because consecutive dropped packets form one gap, and a dropped zero-message packet (heartbeat)
  opens no gap at all.
- **Messages requested** is the messages in injected-drop packets, plus the messages in naturally
  dropped packets, plus any benign tail re-request: near End or the restart, a datagram still queued
  in the kernel can be requested as a missing tail and then arrive twice (UDP and retransmit), the
  second copy being a counted **duplicate**. So duplicates discarded bounds the tail re-requests: in
  run 2 there were 0 duplicates, and its 7,896 extra requested messages were exactly the messages of
  its 6,274 naturally dropped packets (its retransmitted packets exceed run 1's, at the same seed, by
  exactly 6,274). Run 3 had 1 duplicate, one tail re-request.
- **Natural drops** are datagrams the publisher sent (`sendto` succeeded) that the handler never
  read: segments sent minus the handler's `udp_received`. This is an upper bound on kernel loss,
  because the handler stops as soon as its books are complete and a datagram still queued then
  would count. Two cross-checks are printed with each run: the kernel's own `Udp RcvbufErrors`
  delta from `/proc/net/snmp` over the run (VM-wide), and messages requested beyond the messages
  in injected-drop packets (reference messages minus the publisher's `messages_sent`), which is the
  natural-drop messages plus any tail re-requests (see duplicates). Retransmitted packets minus
  injected drops minus natural drops is -259 in runs 1, 2 and 4: at seed 1 the injector drops 259
  zero-message packets (heartbeats), which open no gap and are never retransmitted. No sysctl was changed: `net.core.rmem_max` is 7,500,000 and the
  kernel doubles the handler's 7,500,000-byte `SO_RCVBUF` request to 15,000,000.
- **Checkpoints matched** is matched / expected, compared index by index (epoch, sequence number,
  digest, resting orders); a missing or extra checkpoint fails the run.
- **Handler wall** runs from the first datagram the handler receives to its books being complete;
  **msgs/s** is messages applied divided by that. It is the rate of the whole pipeline at `--rate 0`
  (publisher inflating the gzip and sending, kernel, handler recovering), not a measured limit of the
  handler alone.

**Provenance (git SHA).** The stamp is dirty-aware: `-dirty` means `git status --porcelain -- cpp
scripts` was non-empty, and each section lists the dirty paths. Runs 1 and 2 are `e04ae67b2924-dirty`
only because `scripts/bench_feed.sh` was still untracked; run 3 is `754f455a51b2-dirty` because the
checkpoint code was not yet committed (it is the code committed as `2c5ef85`); run 4 is the clean
commit `2c5ef85ee055`.

**Cost of the checkpoints.** Measured on the reference side (`iex_digest`, the same VM, wall clock,
two runs each): 17.27 and 17.48 s with no checkpoints, 18.97 and 18.37 s at N = 100,000 (508
checkpoints, about 1-1.5 s or 2-3 ms per digest of up to 43,927 orders), 27.36 and 27.19 s at
N = 10,000, 116.04 s at N = 1,000 (one run). N = 100,000 keeps the cost near 8% of a direct replay
while comparing populated books 508 times. The handler's wall time did not visibly move (77.770 s in
run 1 without checkpoints, 77.470 s in run 4 with them). The epoch lines of the N = 100,000 reference
are byte-identical to the checkpoint-less reference file.

Data provided for free by IEX. By accessing or using IEX Historical Data, you agree to the IEX Historical Data Terms of Use.

<!-- runs: newest first, inserted by scripts/bench_feed.sh -->

## 2026-10-26, burst: 20241223_IEXTP1_DPLS1.0.pcap.gz (PASS)

- Hardware: Apple M1 Pro, 7-vCPU OrbStack Ubuntu 24.04 arm64 VM, loopback multicast
- Host: `ubuntu Linux 7.2.12-orbstack-00394-gbecdd6130068 aarch64`
- git: `4c5a815fc384`
- Compiler: `c++ (Ubuntu 13.3.0-6ubuntu2~24.04) 13.3.0`
- Flags: `CMAKE_BUILD_TYPE=Release: -O3 -DNDEBUG -ffp-contract=off`
- Scenario: `burst` = `--drop burst:0.0005:100 --seed 2`, stream mode (zlib while sending), `--rate 0` (unthrottled); ring 268,435,456 bytes; handler SO_RCVBUF requested 7,500,000, got 15,000,000
- Reference: `data/feed/reference-20241223-ckpt100000.digests` (iex_digest direct file replay), sha256 `d9904b639023c929...`, 2 epoch(s)
- Exit codes: handler 0 (status `done`), publisher 0
- Checks: handler_exit_0 yes, publisher_exit_0 yes, no_tool_killed yes, unrecoverable_0 yes, gaps_positive yes, epochs_match yes, handler_digest_match yes, interval_agrees yes, checkpoints_match yes, handler_checkpoint_match yes, handler_counts_agree yes

| segments sent | messages | injected drops | gaps detected | messages requested | retransmitted packets | natural drops | duplicates discarded | epochs | unrecoverable | held max | digest match per epoch | checkpoints matched | handler wall | msgs/s |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---:|---:|---:|
| 40,473,651 | 50,959,176 | 2,018,900 | 20,175 | 2,389,439 | 2,017,913 | 0 (a) | 0 | 2 | 0 | 6,048 | 0: yes, 1: yes | 508 / 508 | 75.205 s | 677,599 |

- Segments sent: datagrams the publisher put on the wire (accepted IEX-TP segments minus injected drops). Accepted segments 42,492,551 of 42,492,551 packets read (0 skipped by SegmentFilter); 48,569,737 messages went out by UDP.
- Messages: messages the handler applied to its books, in sequence order (`messages_applied`).
- Gaps detected: holes the handler found and requested (`gaps`, always equal to `requests`, here 20,175). The publisher answered 20,175 requests, 0 unserved, ring evicted 39,281,642 packets.
- Messages requested = messages in injected-drop packets + messages in naturally dropped packets + any benign tail re-request (near End or the restart, a datagram still queued in the kernel can be requested as a missing tail and then arrive twice; the second copy is a counted duplicate, as are the file's own merge-seam duplicates). Here duplicates discarded = 0, so no tail re-request happened and every requested message was in a dropped packet.
- (a) Natural drops = segments sent - datagrams the handler read (40,473,651 - 40,473,651). It is an upper bound on loss in the kernel: the handler stops once its books are complete, so a datagram still queued in its socket then (already recovered by retransmit) counts too. Cross-checks: the kernel's Udp RcvbufErrors delta over the run 0 (VM-wide counter, receive-buffer overflow); messages requested beyond the 2,389,439 messages in injected-drop packets 0 (the messages of the naturally dropped packets, nothing else, since duplicates are 0); retransmitted packets - injected drops - natural drops = -987 (a positive value counts packets re-requested as a tail; a negative one counts dropped zero-message packets, since a heartbeat opens no gap and is never retransmitted). Kernel Udp deltas: InDatagrams 40,473,651, InErrors 0, OutDatagrams 40,473,651. Publisher sendto retries (ENOBUFS/EAGAIN) 0.
- Held max: the most packets the handler held behind a gap at one time. Parked: 0. Rejected datagrams: 0 (truncated 0).
- Handler wall: first datagram received to books complete. msgs/s = messages / handler wall. Publisher send wall 75,205,027,520 ns.
- Digest match per epoch: the handler's final book digest, order count and message count for each epoch, compared by this script with the reference file line by line (and by the handler's own `--expect`, which reported `digest_match: true`).
- Checkpoints matched: book digest and order count after every message whose sequence number is a multiple of 100,000, compared index by index with the reference (a missing or extra checkpoint fails). Per epoch in the reference: 0: 508, 1: 0; 508 of 508 have resting orders (1,159 to 43,927).
- **Empty final books (generated):** the reference has 0 resting orders at the end of epoch(s) 0, 1, so their digest is the FNV-1a offset basis of an empty book. The end-of-epoch comparison there shows only that both sides end empty after applying the same number of messages; the populated checkpoints are what compare mid-epoch books.
- **Epoch(s) with no populated checkpoint (generated):** 1. Their books are compared only at the epoch end.

Raw JSON: `docs/benchmarks/feed-handler-raw/2026-10-26-burst-4c5a815fc384.json`

<details><summary>Per-epoch digests</summary>

| epoch | handler digest | reference digest | orders (handler / reference) | messages (handler / reference) |
|---:|---|---|---:|---:|
| 0 | `cbf29ce484222325` | `cbf29ce484222325` | 0 / 0 | 50,878,184 / 50,878,184 |
| 1 | `cbf29ce484222325` | `cbf29ce484222325` | 0 / 0 | 80,992 / 80,992 |

</details>


## 2026-10-26, random: 20241223_IEXTP1_DPLS1.0.pcap.gz (PASS) (run 4, checkpoints every 100,000, clean commit)

- Hardware: Apple M1 Pro, 7-vCPU OrbStack Ubuntu 24.04 arm64 VM, loopback multicast
- Host: `ubuntu Linux 7.2.12-orbstack-00394-gbecdd6130068 aarch64`
- git: `2c5ef85ee055`
- Compiler: `c++ (Ubuntu 13.3.0-6ubuntu2~24.04) 13.3.0`
- Flags: `CMAKE_BUILD_TYPE=Release: -O3 -DNDEBUG -ffp-contract=off`
- Scenario: `random` = `--drop random:0.01 --seed 1`, stream mode (zlib while sending), `--rate 0` (unthrottled); ring 268,435,456 bytes; handler SO_RCVBUF requested 7,500,000, got 15,000,000
- Reference: `data/feed/reference-20241223-ckpt100000.digests` (iex_digest direct file replay), sha256 `d9904b639023c929...`, 2 epoch(s)
- Exit codes: handler 0 (status `done`), publisher 0
- Checks: handler_exit_0 yes, publisher_exit_0 yes, no_tool_killed yes, unrecoverable_0 yes, gaps_positive yes, epochs_match yes, handler_digest_match yes, interval_agrees yes, checkpoints_match yes, handler_checkpoint_match yes, handler_counts_agree yes

| segments sent | messages | injected drops | gaps detected | messages requested | retransmitted packets | natural drops | duplicates discarded | epochs | unrecoverable | held max | digest match per epoch | checkpoints matched | handler wall | msgs/s |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---:|---:|---:|
| 42,066,928 | 50,959,176 | 425,623 | 421,146 | 509,142 | 425,364 | 0 (a) | 0 | 2 | 0 | 5,079 | 0: yes, 1: yes | 508 / 508 | 77.470 s | 657,789 |

- Segments sent: datagrams the publisher put on the wire (accepted IEX-TP segments minus injected drops). Accepted segments 42,492,551 of 42,492,551 packets read (0 skipped by SegmentFilter); 50,450,034 messages went out by UDP.
- Messages: messages the handler applied to its books, in sequence order (`messages_applied`).
- Gaps detected: holes the handler found and requested (`gaps`, always equal to `requests`, here 421,146). The publisher answered 421,146 requests, 0 unserved, ring evicted 39,281,642 packets.
- Messages requested = messages in injected-drop packets + messages in naturally dropped packets + any benign tail re-request (near End or the restart, a datagram still queued in the kernel can be requested as a missing tail and then arrive twice; the second copy is a counted duplicate, as are the file's own merge-seam duplicates). Here duplicates discarded = 0, so no tail re-request happened and every requested message was in a dropped packet.
- (a) Natural drops = segments sent - datagrams the handler read (42,066,928 - 42,066,928). It is an upper bound on loss in the kernel: the handler stops once its books are complete, so a datagram still queued in its socket then (already recovered by retransmit) counts too. Cross-checks: the kernel's Udp RcvbufErrors delta over the run 0 (VM-wide counter, receive-buffer overflow); messages requested beyond the 509,142 messages in injected-drop packets 0 (the messages of the naturally dropped packets, nothing else, since duplicates are 0); retransmitted packets - injected drops - natural drops = -259 (a positive value counts packets re-requested as a tail; a negative one counts dropped zero-message packets, since a heartbeat opens no gap and is never retransmitted). Kernel Udp deltas: InDatagrams 42,066,928, InErrors 0, OutDatagrams 42,066,928. Publisher sendto retries (ENOBUFS/EAGAIN) 0.
- Held max: the most packets the handler held behind a gap at one time. Parked: 0. Rejected datagrams: 0 (truncated 0).
- Handler wall: first datagram received to books complete. msgs/s = messages / handler wall. Publisher send wall 77,469,584,825 ns.
- Digest match per epoch: the handler's final book digest, order count and message count for each epoch, compared by this script with the reference file line by line (and by the handler's own `--expect`, which reported `digest_match: true`).
- Checkpoints matched: book digest and order count after every message whose sequence number is a multiple of 100,000, compared index by index with the reference (a missing or extra checkpoint fails). Per epoch in the reference: 0: 508, 1: 0; 508 of 508 have resting orders (1,159 to 43,927).
- **Empty final books (generated):** the reference has 0 resting orders at the end of epoch(s) 0, 1, so their digest is the FNV-1a offset basis of an empty book. The end-of-epoch comparison there shows only that both sides end empty after applying the same number of messages; the populated checkpoints are what compare mid-epoch books.
- **Epoch(s) with no populated checkpoint (generated):** 1. Their books are compared only at the epoch end.

Raw JSON: `docs/benchmarks/feed-handler-raw/2026-10-26-random-2c5ef85ee055.json`

<details><summary>Per-epoch digests</summary>

| epoch | handler digest | reference digest | orders (handler / reference) | messages (handler / reference) |
|---:|---|---|---:|---:|
| 0 | `cbf29ce484222325` | `cbf29ce484222325` | 0 / 0 | 50,878,184 / 50,878,184 |
| 1 | `cbf29ce484222325` | `cbf29ce484222325` | 0 / 0 | 80,992 / 80,992 |

</details>


## 2026-10-26, random: 20241223_IEXTP1_DPLS1.0.pcap.gz (PASS) (run 3, checkpoints every 100,000, pre-commit build)

- Hardware: Apple M1 Pro, 7-vCPU OrbStack Ubuntu 24.04 arm64 VM, loopback multicast
- Host: `ubuntu Linux 7.2.12-orbstack-00394-gbecdd6130068 aarch64`
- git: `754f455a51b2-dirty`, working-tree hash `7e976ca64ebfbbf8582245b8570bdfc96d38bb9ef01c67f2b7b119b028f24f26`; dirty paths: `M cpp/src/pkmn_md/book_digest.cpp`, `M cpp/src/pkmn_md/book_digest.hpp`, `M cpp/src/pkmn_md/gap_recovery.cpp`, `M cpp/src/pkmn_md/gap_recovery.hpp`, `M cpp/tests/feed_fixtures.hpp`, `M cpp/tests/test_book_digest.cpp`, `M cpp/tests/test_feed_tools.cpp`, `M cpp/tests/test_gap_recovery.cpp`, `M cpp/tools/feed_handler.cpp`, `M cpp/tools/iex_digest.cpp`, `M scripts/bench_feed.sh`
- Compiler: `c++ (Ubuntu 13.3.0-6ubuntu2~24.04) 13.3.0`
- Flags: `CMAKE_BUILD_TYPE=Release: -O3 -DNDEBUG -ffp-contract=off`
- Scenario: `random` = `--drop random:0.01 --seed 1`, stream mode (zlib while sending), `--rate 0` (unthrottled); ring 268,435,456 bytes; handler SO_RCVBUF requested 7,500,000, got 15,000,000
- Reference: `data/feed/reference-20241223-ckpt100000.digests` (iex_digest direct file replay), sha256 `d9904b639023c929...`, 2 epoch(s)
- Exit codes: handler 0 (status `done`), publisher 0
- Checks: handler_exit_0 yes, publisher_exit_0 yes, no_tool_killed yes, unrecoverable_0 yes, gaps_positive yes, epochs_match yes, handler_digest_match yes, interval_agrees yes, checkpoints_match yes, handler_checkpoint_match yes, handler_counts_agree yes

| segments sent | messages | injected drops | gaps detected | messages requested | retransmitted packets | natural drops | duplicates discarded | epochs | unrecoverable | held max | digest match per epoch | checkpoints matched | handler wall | msgs/s |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---:|---:|---:|
| 42,066,928 | 50,959,176 | 425,623 | 421,147 | 509,143 | 425,365 | 0 (a) | 1 | 2 | 0 | 5,582 | 0: yes, 1: yes | 508 / 508 | 78.550 s | 648,746 |

- Segments sent: datagrams the publisher put on the wire (accepted IEX-TP segments minus injected drops). Accepted segments 42,492,551 of 42,492,551 packets read (0 skipped by SegmentFilter); 50,450,034 messages went out by UDP.
- Messages: messages the handler applied to its books, in sequence order (`messages_applied`).
- Gaps detected: holes the handler found and requested (`gaps`, always equal to `requests`, here 421,147). The publisher answered 421,147 requests, 0 unserved, ring evicted 39,281,642 packets.
- Messages requested = messages in injected-drop packets + messages in naturally dropped packets + any benign tail re-request (near End or the restart, a datagram still queued in the kernel can be requested as a missing tail and then arrive twice; the second copy is a counted duplicate, as are the file's own merge-seam duplicates). Here duplicates discarded = 1, so some requested messages may be tail re-requests.
- (a) Natural drops = segments sent - datagrams the handler read (42,066,928 - 42,066,928). It is an upper bound on loss in the kernel: the handler stops once its books are complete, so a datagram still queued in its socket then (already recovered by retransmit) counts too. Cross-checks: the kernel's Udp RcvbufErrors delta over the run 0 (VM-wide counter, receive-buffer overflow); messages requested beyond the 509,142 messages in injected-drop packets 1 (the messages of the naturally dropped packets, plus any tail re-requests); retransmitted packets - injected drops - natural drops = -258 (a positive value counts packets re-requested as a tail; a negative one counts dropped zero-message packets, since a heartbeat opens no gap and is never retransmitted). Kernel Udp deltas: InDatagrams 42,066,928, InErrors 0, OutDatagrams 42,066,928. Publisher sendto retries (ENOBUFS/EAGAIN) 0.
- Held max: the most packets the handler held behind a gap at one time. Parked: 0. Rejected datagrams: 0 (truncated 0).
- Handler wall: first datagram received to books complete. msgs/s = messages / handler wall. Publisher send wall 78,549,190,550 ns.
- Digest match per epoch: the handler's final book digest, order count and message count for each epoch, compared by this script with the reference file line by line (and by the handler's own `--expect`, which reported `digest_match: true`).
- Checkpoints matched: book digest and order count after every message whose sequence number is a multiple of 100,000, compared index by index with the reference (a missing or extra checkpoint fails). Per epoch in the reference: 0: 508, 1: 0; 508 of 508 have resting orders (1,159 to 43,927).
- **Empty final books (generated):** the reference has 0 resting orders at the end of epoch(s) 0, 1, so their digest is the FNV-1a offset basis of an empty book. The end-of-epoch comparison there shows only that both sides end empty after applying the same number of messages; the populated checkpoints are what compare mid-epoch books.
- **Epoch(s) with no populated checkpoint (generated):** 1. Their books are compared only at the epoch end.

Raw JSON: `docs/benchmarks/feed-handler-raw/2026-10-26-random-754f455a51b2-dirty.json`

<details><summary>Per-epoch digests</summary>

| epoch | handler digest | reference digest | orders (handler / reference) | messages (handler / reference) |
|---:|---|---|---:|---:|
| 0 | `cbf29ce484222325` | `cbf29ce484222325` | 0 / 0 | 50,878,184 / 50,878,184 |
| 1 | `cbf29ce484222325` | `cbf29ce484222325` | 0 / 0 | 80,992 / 80,992 |

</details>


## 2026-10-26, random: 20241223_IEXTP1_DPLS1.0.pcap.gz (PASS) (run 2, no checkpoints)

- Hardware: Apple M1 Pro, 7-vCPU OrbStack Ubuntu 24.04 arm64 VM, loopback multicast
- Host: `ubuntu Linux 7.2.12-orbstack-00394-gbecdd6130068 aarch64`
- git: `e04ae67b2924-dirty`, working-tree hash `e5b92930f410e810119020ffb9387eced00003bd47d3185d4f896ce162bdb31d`; dirty paths: `?? scripts/bench_feed.sh`
- Compiler: `c++ (Ubuntu 13.3.0-6ubuntu2~24.04) 13.3.0`
- Flags: `CMAKE_BUILD_TYPE=Release: -O3 -DNDEBUG -ffp-contract=off`
- Scenario: `random` = `--drop random:0.01 --seed 1`, stream mode (zlib while sending), `--rate 0` (unthrottled); ring 268,435,456 bytes; handler SO_RCVBUF requested 7,500,000, got 15,000,000
- Reference: `data/feed/reference-20241223.digests` (iex_digest direct file replay), sha256 `4151f8344771796d...`, 2 epoch(s)
- Exit codes: handler 0 (status `done`), publisher 0

| segments sent | messages | injected drops | gaps detected | messages requested | retransmitted packets | natural drops | duplicates discarded | epochs | unrecoverable | held max | digest match per epoch | handler wall | msgs/s |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---:|---:|
| 42,066,928 | 50,959,176 | 425,623 | 421,085 | 517,038 | 431,638 | 6,274 (a) | 0 | 2 | 0 | 5,734 | 0: yes, 1: yes | 79.969 s | 637,238 |

- Segments sent: datagrams the publisher put on the wire (accepted IEX-TP segments minus injected drops). Accepted segments 42,492,551 of 42,492,551 packets read (0 skipped by SegmentFilter); 50,450,034 messages went out by UDP.
- Messages: messages the handler applied to its books, in sequence order (`messages_applied`).
- Gaps detected: holes the handler found and requested (`gaps`, always equal to `requests`, here 421,085). The publisher answered 421,085 requests, 0 unserved, ring evicted 39,281,642 packets.
- *Corrected 2026-10-26, not re-run (numbers unchanged):* this line originally said that messages requested and duplicates discarded include benign tail re-requests. In this run duplicates are 0, so none happened: the 7,896 messages requested beyond the injected ones are the messages of the 6,274 naturally dropped packets (retransmitted packets 431,638 = run 1's 425,364 + 6,274).
- (a) Natural drops = segments sent - datagrams the handler read (42,066,928 - 42,060,654). It is an upper bound on loss in the kernel: the handler stops once its books are complete, so a datagram still queued in its socket then (already recovered by retransmit) counts too. Cross-checks: the kernel's Udp RcvbufErrors delta over the run 6,274 (VM-wide counter, receive-buffer overflow); messages requested beyond the 509,142 messages in injected-drop packets 7,896 (originally labelled "inflated by tail re-requests"; corrected: with 0 duplicates these are all natural-drop messages). Kernel Udp deltas: InDatagrams 42,060,654, InErrors 6,274, OutDatagrams 42,066,928. Publisher sendto retries (ENOBUFS/EAGAIN) 0.
- Held max: the most packets the handler held behind a gap at one time. Parked: 0. Rejected datagrams: 0 (truncated 0).
- Handler wall: first datagram received to books complete. msgs/s = messages / handler wall. Publisher send wall 79,968,739,064 ns.
- Digest match per epoch: the handler's final book digest, order count and message count for each epoch, compared by this script with the reference file line by line (and by the handler's own `--expect`, which reported `digest_match: true`).
- **Empty final books (generated):** the reference has 0 resting orders at the end of epoch(s) 0, 1, so their digest is the FNV-1a offset basis of an empty book. For those epochs the digest comparison shows only that both sides end with empty books after applying the same number of messages; it does not by itself show the intermediate books agreed. This run had no checkpoints.

Raw JSON: `docs/benchmarks/feed-handler-raw/2026-10-26-random-e04ae67b2924-dirty-2.json`

<details><summary>Per-epoch digests</summary>

| epoch | handler digest | reference digest | orders (handler / reference) | messages (handler / reference) |
|---:|---|---|---:|---:|
| 0 | `cbf29ce484222325` | `cbf29ce484222325` | 0 / 0 | 50,878,184 / 50,878,184 |
| 1 | `cbf29ce484222325` | `cbf29ce484222325` | 0 / 0 | 80,992 / 80,992 |

</details>


## 2026-10-26, random: run 1, no checkpoints, generated section not kept (PASS)

The first full-day `random` run (same command, same seed, same build) also passed: handler exit 0,
publisher exit 0, both epoch digests equal (0 orders, 50,878,184 and 80,992 messages), 0
unrecoverable, 0 unserved. Its counts: 42,066,928 segments sent, 425,623 injected drops, 421,146
gaps, 509,142 messages requested (exactly the messages in the injected-drop packets), 425,364
retransmitted packets, 0 natural drops (kernel `RcvbufErrors` delta 0), 0 duplicates, held max
6,970, handler wall 77.770 s, 655,254 msgs/s. Run 2 differs because the kernel dropped 6,274
datagrams in that one and none in this one, at the same seed. Its generated section is not
kept because its natural-drop cross-check was wrong: it printed retransmitted packets minus injected
drops, which came out at -259 because a dropped zero-message packet (heartbeat) opens no gap and is
never retransmitted. The script now prints that difference net of natural drops, with the
heartbeat explanation, next to the message-based cross-check. Raw JSON:
`docs/benchmarks/feed-handler-raw/2026-10-26-random-e04ae67b2924-dirty.json`.

## Resume bullets (draft)

Numbers are from run 4's table (2026-10-26, commit `2c5ef85`, Apple M1 Pro, 7-vCPU OrbStack Ubuntu
24.04 arm64 VM, loopback multicast) unless a bullet names another run.

- Built a C++20 UDP multicast feed handler with TCP retransmit gap recovery and replayed a full
  captured day of IEX DEEP+ (42,066,928 datagrams, 50,959,176 messages) through it under 1% seeded
  packet loss: 425,623 dropped packets, 421,146 gaps recovered, 0 unrecoverable requests.
- Verified the recovered order books against a direct file replay at 508 of 508 mid-session
  checkpoints, every resting order, price, quantity and queue position equal after loss recovery.
- Sustained 657,789 messages/s end to end over loopback multicast under that loss, with all 508
  checkpoints matching and at most 5,079 packets held behind open gaps.
- Also recovered a full day under seeded burst loss (100-packet bursts, 2,018,900 dropped packets,
  20,175 gaps, 2,389,439 messages re-requested) with 0 unrecoverable requests, 508 of 508
  checkpoints matching and both epoch digests equal to the direct replay, at 677,599 messages/s
  (the 2026-10-26 burst run, commit `4c5a815`).

Caveats to keep with these bullets: the reference shares the DEEP+ decoder and order book code with
the handler, so "verified" means the transport and recovery reproduce the direct replay, not that
the book matches IEX (see `docs/iex-oracle.md`); checkpoints are every 100,000 messages, and the
short post-restart epoch has none; 657,789 msgs/s is the whole pipeline's rate unthrottled
(publisher inflating gzip, kernel, handler), not the handler's limit; "a full captured day" is the whole 2024-12-23 file, an incident day
with a halt and a mid-session restart, not a normal session; and this is loopback in one VM, not a
network.
