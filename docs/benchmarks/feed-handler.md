# Feed handler benchmarks

Results of `scripts/bench_feed.sh` (`cpp/tools/feed_publisher.cpp`, `cpp/tools/feed_handler.cpp`,
reference from `cpp/tools/iex_digest.cpp`), newest first.

## What a run proves

`feed_publisher` replays the IEX DEEP+ capture as UDP multicast, one datagram per IEX-TP payload,
byte for byte unchanged, and drops packets on the send side with a seeded injector. Dropped packets
stay in its retransmit ring. `feed_handler` joins the group, detects gaps by sequence number, holds
the packets behind each gap, requests the missing range over a TCP side channel, and applies every
message to its books in sequence order. At the end of each sequence epoch it takes a canonical
digest of its books (FNV-1a over every resting order's symbol, side, price, id and quantity, in
price-time order) plus the resting-order and message counts.

The reference is not the handler. `iex_digest` replays the same file directly through the existing
`FeedReader` path and writes the same three numbers per epoch to `data/feed/reference-20241223.digests`
(computed once, its sha256 recorded with each run). A run passes only when both tools exit 0
(no unserved request, no timeout, End delivered) and every epoch's digest, order count and message
count equal the reference. The script checks this line by line itself, in addition to the handler's
own `--expect` comparison.

What a pass does not show:

- **This is loopback inside one VM, not a network.** Publisher and handler are two processes on
  the same 7-vCPU OrbStack VM talking through the kernel's loopback multicast path. There is no
  NIC, no switch, no wire latency and no second host. Loss is injected by the publisher; natural
  kernel loss is measured but was not induced.
- **The final books on this day are empty.** The DEEP+ stream itself deletes every resting order before the
  restart and again before its end, so the reference has 0 orders at the end of
  both epochs and both digests are the FNV-1a offset basis (`cbf29ce484222325`). The end-of-epoch
  digest therefore cannot tell two different mid-day books apart on this capture. What the
  comparison does pin is that both sides end empty after applying exactly the same number of
  messages in each epoch (50,878,184 and 80,992), with no message lost or applied twice. Order of
  application is enforced by the handler's design and covered by the Catch2 recovery tests, whose
  fixtures do end with non-empty books; it is not independently checked by this run.
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
- **Messages requested** and **duplicates discarded** can include benign tail re-requests: near End
  or the restart, a datagram still queued in the kernel can be requested as a missing tail, then
  arrive twice (UDP and retransmit); the second copy is a counted duplicate.
- **Natural drops** are datagrams the publisher sent (`sendto` succeeded) that the handler never
  read: segments sent minus the handler's `udp_received`. This is an upper bound on kernel loss,
  because the handler stops as soon as its books are complete and a datagram still queued then
  would count. Two cross-checks are printed with each run: the kernel's own `Udp RcvbufErrors`
  delta from `/proc/net/snmp` over the run (VM-wide), and messages requested beyond the messages
  in injected-drop packets (reference messages minus the publisher's `messages_sent`), which tail
  re-requests would inflate. No sysctl was changed: `net.core.rmem_max` is 7,500,000 and the
  kernel doubles the handler's 7,500,000-byte `SO_RCVBUF` request to 15,000,000.
- **Handler wall** runs from the first datagram the handler receives to its books being complete;
  **msgs/s** is messages applied divided by that. It is the rate of the whole pipeline at `--rate 0`
  (publisher inflating the gzip and sending, kernel, handler recovering), not a measured limit of the
  handler alone.

Data provided for free by IEX. By accessing or using IEX Historical Data, you agree to the IEX Historical Data Terms of Use.

<!-- runs: newest first, inserted by scripts/bench_feed.sh -->

## 2026-10-26, random: 20241223_IEXTP1_DPLS1.0.pcap.gz (PASS)

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
- Messages requested and duplicates discarded include benign tail re-requests: near End or a restart, a datagram still queued in the kernel can be requested as a missing tail, and then arrives twice (UDP and retransmit); the second copy is a counted duplicate. The file's own merge-seam duplicates are counted there too.
- (a) Natural drops = segments sent - datagrams the handler read (42,066,928 - 42,060,654). It is an upper bound on loss in the kernel: the handler stops once its books are complete, so a datagram still queued in its socket then (already recovered by retransmit) counts too. Cross-checks: the kernel's Udp RcvbufErrors delta over the run 6,274 (VM-wide counter, receive-buffer overflow); messages requested beyond the 509,142 messages in injected-drop packets 7,896 (inflated by tail re-requests). Kernel Udp deltas: InDatagrams 42,060,654, InErrors 6,274, OutDatagrams 42,066,928. Publisher sendto retries (ENOBUFS/EAGAIN) 0.
- Held max: the most packets the handler held behind a gap at one time. Parked: 0. Rejected datagrams: 0 (truncated 0).
- Handler wall: first datagram received to books complete. msgs/s = messages / handler wall. Publisher send wall 79,968,739,064 ns.
- Digest match per epoch: the handler's final book digest, order count and message count for each epoch, compared by this script with the reference file line by line (and by the handler's own `--expect`, which reported `digest_match: true`).
- **Empty final books (generated):** the reference has 0 resting orders at the end of epoch(s) 0, 1, so their digest is the FNV-1a offset basis of an empty book. For those epochs the digest comparison shows only that both sides end with empty books after applying the same number of messages; it does not by itself show the intermediate books agreed.

Raw JSON: `docs/benchmarks/feed-handler-raw/2026-10-26-random-e04ae67b2924-dirty-2.json`

<details><summary>Per-epoch digests</summary>

| epoch | handler digest | reference digest | orders (handler / reference) | messages (handler / reference) |
|---:|---|---|---:|---:|
| 0 | `cbf29ce484222325` | `cbf29ce484222325` | 0 / 0 | 50,878,184 / 50,878,184 |
| 1 | `cbf29ce484222325` | `cbf29ce484222325` | 0 / 0 | 80,992 / 80,992 |

</details>


## 2026-10-26, random: first run, generated section not kept (PASS)

The first full-day `random` run (same command, same seed, same build) also passed: handler exit 0,
publisher exit 0, both epoch digests equal (0 orders, 50,878,184 and 80,992 messages), 0
unrecoverable, 0 unserved. Its counts: 42,066,928 segments sent, 425,623 injected drops, 421,146
gaps, 509,142 messages requested (exactly the messages in the injected-drop packets), 425,364
retransmitted packets, 0 natural drops (kernel `RcvbufErrors` delta 0), 0 duplicates, held max
6,970, handler wall 77.770 s, 655,254 msgs/s. The run above differs because the kernel dropped
6,274 datagrams in that one and none in this one, at the same seed. Its generated section is not
kept because its natural-drop cross-check was wrong: it printed retransmitted packets minus injected
drops, which came out at -259 because a dropped zero-message packet (heartbeat) opens no gap and is
never retransmitted. The script now uses the message-based cross-check described above. Raw JSON:
`docs/benchmarks/feed-handler-raw/2026-10-26-random-e04ae67b2924-dirty.json`.

## Resume bullets (draft)

Each number is from the 2026-10-26 `random` run's table above (Apple M1 Pro, 7-vCPU OrbStack
Ubuntu 24.04 arm64 VM, loopback multicast).

- Built a C++20 UDP multicast feed handler with TCP retransmit recovery and replayed a full day of
  IEX DEEP+ data through it (42,066,928 datagrams, 50,959,176 messages) under 1% seeded packet
  loss, recovering all 421,085 sequence gaps with 0 unrecoverable requests.
- Recovered 6,274 real kernel receive-buffer drops on top of 425,623 injected ones, applying all
  50,959,176 messages in sequence with per-epoch message counts and final books equal to an
  independent direct file replay.
- Applied 637,238 messages/s end to end over loopback multicast while holding at most 5,734
  packets behind open gaps.

Caveats to keep with these bullets: the 6,274 natural drops are from one run (the first run at the
same seed had 0); the final books on this incident day are empty at both epoch ends, so "zero book
divergence" would claim more than the digest shows; "a full day" means the whole 2024-12-23 file,
an incident day with a mid-session restart, not a normal session; and this is loopback in one VM,
not a network.
