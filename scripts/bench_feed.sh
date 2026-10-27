#!/usr/bin/env bash
# Full-day DEEP+ recovery run: feed_publisher replays the capture as loopback
# UDP multicast with seeded drop injection, feed_handler recovers every gap
# over the TCP retransmit channel, and its per-epoch book digests are checked
# against iex_digest's direct file replay.
#
#   scripts/bench_feed.sh <random|burst> [--no-record]
#
#   random  --drop random:0.01 --seed 1, stream mode, --rate 0
#   burst   --drop burst:0.0005:100 --seed 2, stream mode, --rate 0
#   sweep   not implemented here (Plan C Task 7)
#
# Runs inside the Linux VM. Started on macOS, it re-executes itself in the
# OrbStack `ubuntu` VM (the Mac filesystem is mounted at the same path there).
#
# Steps:
#   1. Configure + build iex_digest, feed_publisher and feed_handler as
#      Release in cpp/build-linux-release.
#   2. Reference digests: for the default capture, data/feed/reference-
#      20241223.digests, written with iex_digest only when missing (it is
#      never recomputed otherwise; its sha256 goes in the record). With
#      IEX_FILE set to another capture, a temporary reference is computed for
#      that file on every run (or pass REF_FILE).
#   3. Start feed_handler --expect <reference>, then feed_publisher, on a
#      random port pair in 20000-31999. /proc/net/snmp's Udp counters are
#      read before and after (VM-wide kernel counters).
#   4. Save one JSON (both tools' JSON, exit codes, commands, build, kernel
#      counter deltas) as docs/benchmarks/feed-handler-raw/<date>-<scenario>-
#      <sha>.json (a numeric suffix is added rather than overwrite a run).
#   5. Prepend a dated section to docs/benchmarks/feed-handler.md, newest
#      first, after the "runs" marker line.
#   --no-record skips 4 and 5 (the section is still printed).
#
# A failing run (either tool nonzero, or a per-epoch digest differing) is
# still recorded, and the script then exits 1.
#
# Environment: IEX_FILE (default data/iex/20241223_IEXTP1_DPLS1.0.pcap.gz),
# REF_FILE (reference digests to use instead of the default), HARDWARE,
# RING_BYTES (publisher --ring-bytes; default the tool's 268435456),
# RCVBUF (handler --rcvbuf; default the tool's 7500000), TIMEOUT_S (handler
# --timeout-s, default 1800).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ "$(uname -s)" != "Linux" ]]; then
    command -v orb >/dev/null || { echo "not Linux and no orb on PATH" >&2; exit 1; }
    exec orb -m ubuntu env \
        IEX_FILE="${IEX_FILE:-}" REF_FILE="${REF_FILE:-}" HARDWARE="${HARDWARE:-}" \
        RING_BYTES="${RING_BYTES:-}" RCVBUF="${RCVBUF:-}" TIMEOUT_S="${TIMEOUT_S:-}" \
        bash "$ROOT/scripts/bench_feed.sh" "$@"
fi

usage() { echo "usage: $0 <random|burst> [--no-record]" >&2; exit 2; }
SCENARIO=""
RECORD=1
for arg in "$@"; do
    case "$arg" in
        --no-record) RECORD=0 ;;
        random | burst | sweep) [[ -z "$SCENARIO" ]] || usage; SCENARIO="$arg" ;;
        *) usage ;;
    esac
done
case "$SCENARIO" in
    random) DROP="random:0.01"; SEED=1 ;;
    burst) DROP="burst:0.0005:100"; SEED=2 ;;
    sweep) echo "sweep is not implemented yet (Plan C Task 7)" >&2; exit 2 ;;
    *) usage ;;
esac

DEFAULT_FILE="$ROOT/data/iex/20241223_IEXTP1_DPLS1.0.pcap.gz"
FILE="${IEX_FILE:-$DEFAULT_FILE}"
HARDWARE="${HARDWARE:-Apple M1 Pro, 7-vCPU OrbStack Ubuntu 24.04 arm64 VM, loopback multicast}"
TIMEOUT_S="${TIMEOUT_S:-1800}"
RESULTS="$ROOT/docs/benchmarks/feed-handler.md"
RAW_DIR="$ROOT/docs/benchmarks/feed-handler-raw"
BUILD="$ROOT/cpp/build-linux-release"
[[ -f "$FILE" ]] || { echo "no capture at $FILE" >&2; exit 1; }

OUT="$(mktemp -d)"
# Kept on failure so finished runs are not lost.
trap 'rc=$?; if [[ $rc == 0 ]]; then rm -rf "$OUT"; else echo "kept $OUT (exit $rc)" >&2; fi' EXIT

echo "== build (Release) in $BUILD" >&2
cmake -S "$ROOT/cpp" -B "$BUILD" -DCMAKE_BUILD_TYPE=Release -DPKMN_BUILD_TESTS=OFF >/dev/null
cmake --build "$BUILD" -j --target iex_digest feed_publisher feed_handler >/dev/null

# Source stamp, the same rule as cpp/cmake/bench_build_info.cmake: short HEAD,
# "-dirty" when `git status --porcelain -- cpp scripts` is non-empty
# (untracked files included). The tree hash here is sha256 over `git diff
# --binary HEAD -- cpp scripts` plus each untracked file's path and raw
# contents (sorted); it names the exact source but is not byte-compatible
# with the cmake stamp's hash, which hex-encodes untracked contents.
GIT_SHA="$(git -C "$ROOT" rev-parse --short=12 HEAD 2>/dev/null || echo unknown)"
DIRTY_PATHS="$(git -C "$ROOT" status --porcelain -- cpp scripts 2>/dev/null || true)"
TREE_HASH=""
if [[ -n "$DIRTY_PATHS" && "$GIT_SHA" != unknown ]]; then
    GIT_SHA="$GIT_SHA-dirty"
    TREE_HASH="$({
        git -C "$ROOT" diff --binary HEAD -- cpp scripts
        git -C "$ROOT" ls-files --others --exclude-standard -- cpp scripts | LC_ALL=C sort |
            while IFS= read -r f; do printf '\nuntracked %s\n' "$f"; cat "$ROOT/$f"; done
    } | sha256sum | cut -d' ' -f1)"
fi
CXX_PATH="$(sed -n 's/^CMAKE_CXX_COMPILER:[A-Z]*=//p' "$BUILD/CMakeCache.txt")"
COMPILER="$("$CXX_PATH" --version | head -1)"
CXX_FLAGS="$(sed -n 's/^CMAKE_CXX_FLAGS:[A-Z]*=//p' "$BUILD/CMakeCache.txt")"
CXX_FLAGS_REL="$(sed -n 's/^CMAKE_CXX_FLAGS_RELEASE:[A-Z]*=//p' "$BUILD/CMakeCache.txt")"
FLAGS="CMAKE_BUILD_TYPE=Release: $(echo "$CXX_FLAGS $CXX_FLAGS_REL -ffp-contract=off" | xargs)"

# Reference digests from the direct file replay.
if [[ -n "${REF_FILE:-}" ]]; then
    REF="$REF_FILE"
elif [[ "$FILE" -ef "$DEFAULT_FILE" ]]; then
    REF="$ROOT/data/feed/reference-20241223.digests"
else
    REF="$OUT/reference.digests"
fi
if [[ ! -s "$REF" ]]; then
    echo "== reference digests (iex_digest) -> $REF" >&2
    mkdir -p "$(dirname "$REF")"
    "$BUILD/iex_digest" "$FILE" --out "$REF.tmp"
    mv "$REF.tmp" "$REF"
else
    echo "== reference digests: reusing $REF" >&2
fi

free -b | awk '/^Mem:/ {printf "== memory: %.1f GB total, %.1f GB available\n", $2/1e9, $7/1e9}' >&2

PORT=$((20000 + RANDOM % 11999))
CPORT=$((PORT + 1))
HCMD=("$BUILD/feed_handler" --port "$PORT" --ctrl-port "$CPORT" --timeout-s "$TIMEOUT_S" --expect "$REF")
[[ -n "${RCVBUF:-}" ]] && HCMD+=(--rcvbuf "$RCVBUF")
PCMD=("$BUILD/feed_publisher" "$FILE" --port "$PORT" --ctrl-port "$CPORT" --rate 0 --drop "$DROP" --seed "$SEED")
[[ -n "${RING_BYTES:-}" ]] && PCMD+=(--ring-bytes "$RING_BYTES")
printf '%s\n' "${HCMD[*]}" >"$OUT/handler.cmd"
printf '%s\n' "${PCMD[*]}" >"$OUT/publisher.cmd"

grep '^Udp:' /proc/net/snmp >"$OUT/snmp_before.txt"
echo "== $SCENARIO: handler on $PORT/$CPORT, then publisher ($DROP, seed $SEED, rate 0, stream)" >&2
"${HCMD[@]}" >"$OUT/handler.json" 2>"$OUT/handler.err" &
HPID=$!
"${PCMD[@]}" >"$OUT/publisher.json" 2>"$OUT/publisher.err" &
PUB_PID=$!
set +e
wait "$PUB_PID"; PRC=$?
wait "$HPID"; HRC=$?
set -e
grep '^Udp:' /proc/net/snmp >"$OUT/snmp_after.txt"
echo "== handler exit $HRC, publisher exit $PRC" >&2
sed 's/^/   handler: /' "$OUT/handler.err" >&2
grep -v '^  sent ' "$OUT/publisher.err" | sed 's/^/   publisher: /' >&2

set +e
python3 - "$OUT" "$RESULTS" "$RAW_DIR" "$RECORD" "$SCENARIO" "$FILE" "$REF" "$HARDWARE" "$GIT_SHA" \
    "$TREE_HASH" "$COMPILER" "$FLAGS" "$HRC" "$PRC" "$DROP" "$SEED" "$DIRTY_PATHS" <<'PY'
import datetime, hashlib, json, os, pathlib, platform, sys

(out, results, raw_dir, record, scenario, cap, ref, hardware, sha, tree_hash, compiler, flags,
 hrc, prc, drop, seed, dirty_paths) = sys.argv[1:18]
out = pathlib.Path(out)
repo = pathlib.Path(results).parents[2]


def rel_to_repo(x):
    q = pathlib.Path(x)
    return str(q.relative_to(repo)) if q.is_relative_to(repo) else str(q)


hrc, prc = int(hrc), int(prc)


def load_json(name):
    try:
        return json.loads((out / name).read_text())
    except (OSError, ValueError) as e:  # a tool that died prints no JSON
        return {"unparsed": (out / name).read_text() if (out / name).exists() else "", "error": str(e)}


h = load_json("handler.json")
p = load_json("publisher.json")


def snmp(name):
    rows = (out / name).read_text().split("\n")
    keys, vals = rows[0].split()[1:], rows[1].split()[1:]
    return dict(zip(keys, map(int, vals)))


before, after = snmp("snmp_before.txt"), snmp("snmp_after.txt")
kernel = {k: after[k] - before[k] for k in after}

ref_text = pathlib.Path(ref).read_text()
expected = []
for line in ref_text.splitlines():
    f = line.split()
    expected.append({"epoch": int(f[1]), "digest": f[3], "orders": int(f[5]), "messages": int(f[7])})
got = h.get("digests", [])
per_epoch = []
for i in range(max(len(got), len(expected))):
    g = got[i] if i < len(got) else None
    e = expected[i] if i < len(expected) else None
    ok = (g is not None and e is not None and g["digest"] == e["digest"] and g["orders"] == e["orders"]
          and g["messages"] == e["messages"])
    per_epoch.append({"epoch": i, "match": ok, "got": g, "expected": e})
all_match = bool(per_epoch) and all(x["match"] for x in per_epoch)
passed = hrc == 0 and prc == 0 and all_match and h.get("digest_match") is True

# Natural drops: datagrams the publisher put on the wire (sendto succeeded)
# that the handler never read. Primary, per run: segments_sent - udp_received.
# This is an upper bound on kernel loss: the handler stops as soon as its
# books are complete, so a datagram still queued in its socket at that moment
# (its messages already recovered by retransmit) is counted here too.
# Cross-checks:
# - the kernel's own Udp RcvbufErrors delta (receive-buffer overflow drops;
#   VM-wide, so any other UDP traffic in the window counts too);
# - messages requested beyond those injected: requested_messages minus the
#   messages in injected-drop packets, the latter being the reference's total
#   messages minus the publisher's messages_sent. Benign tail re-requests
#   inflate it (each such message also shows as a duplicate).
# Retransmitted packets - injected drops is NOT used: a dropped zero-message
# packet (heartbeat) opens no gap and is never retransmitted, so the
# difference can go negative.
sent = p.get("segments_sent")
recv = h.get("udp_received")
natural_sent_recv = sent - recv if isinstance(sent, int) and isinstance(recv, int) else None
retx, dropped = p.get("retransmit_packets"), p.get("dropped")
ref_msgs = sum(e["messages"] for e in expected)
msent, req = p.get("messages_sent"), h.get("requested_messages")
injected_msgs = ref_msgs - msent if isinstance(msent, int) else None
extra_requested = req - injected_msgs if isinstance(req, int) and injected_msgs is not None else None
empty_epochs = [e["epoch"] for e in expected if e["orders"] == 0]

today = datetime.date.today().isoformat()
record_obj = {
    "date": today,
    "scenario": scenario,
    "file": rel_to_repo(cap),
    "drop": drop,
    "seed": int(seed),
    "mode": "stream",
    "rate": 0,
    "hardware": hardware,
    "host": " ".join([platform.node(), platform.system(), platform.release(), platform.machine()]),
    "git_sha": sha,
    "tree_hash": tree_hash,
    "dirty_paths": dirty_paths.splitlines(),
    "compiler": compiler,
    "flags": flags,
    "reference": {"path": rel_to_repo(ref),
                  "sha256": hashlib.sha256(ref_text.encode()).hexdigest(), "epochs": expected},
    "handler_cmd": (out / "handler.cmd").read_text().strip(),
    "publisher_cmd": (out / "publisher.cmd").read_text().strip(),
    "handler_exit": hrc,
    "publisher_exit": prc,
    "handler": h,
    "publisher": p,
    "handler_stderr": (out / "handler.err").read_text(),
    "publisher_stderr": "".join(l for l in (out / "publisher.err").read_text().splitlines(True)
                                if not l.startswith("  sent ")),
    "kernel_udp_delta": kernel,
    "natural_drops": {"sent_minus_received": natural_sent_recv, "kernel_rcvbuf_errors": kernel.get("RcvbufErrors"),
                      "injected_messages": injected_msgs, "requested_beyond_injected_messages": extra_requested},
    "per_epoch": per_epoch,
    "pass": passed,
}


def n(v):
    return f"{v:,}" if isinstance(v, int) else "n/a"


wall = h.get("wall_ns")
digest_cell = ", ".join(f"{x['epoch']}: {'yes' if x['match'] else 'NO'}" for x in per_epoch) or "none"
L = []
L.append(f"## {today}, {scenario}: {os.path.basename(cap)} ({'PASS' if passed else 'FAIL'})")
L.append("")
L.append(f"- Hardware: {hardware}")
L.append(f"- Host: `{record_obj['host']}`")
dirty_note = ""
if dirty_paths:
    dirty_note = f", working-tree hash `{tree_hash}`; dirty paths: " + ", ".join(
        f"`{x.strip()}`" for x in dirty_paths.splitlines())
L.append(f"- git: `{sha}`{dirty_note}")
L.append(f"- Compiler: `{compiler}`")
L.append(f"- Flags: `{flags}`")
L.append(f"- Scenario: `{scenario}` = `--drop {drop} --seed {seed}`, stream mode (zlib while sending), `--rate 0` "
         f"(unthrottled); ring {n(p.get('ring_bytes'))} bytes; handler SO_RCVBUF requested {n(h.get('rcvbuf_requested'))}, "
         f"got {n(h.get('rcvbuf_actual'))}")
L.append(f"- Reference: `{record_obj['reference']['path']}` (iex_digest direct file replay), sha256 "
         f"`{record_obj['reference']['sha256'][:16]}...`, {len(expected)} epoch(s)")
L.append(f"- Exit codes: handler {hrc} (status `{h.get('status', 'n/a')}`), publisher {prc}")
L.append("")
L.append("| segments sent | messages | injected drops | gaps detected | messages requested | retransmitted packets "
         "| natural drops | duplicates discarded | epochs | unrecoverable | held max | digest match per epoch "
         "| handler wall | msgs/s |")
L.append("|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---:|---:|")
L.append(f"| {n(sent)} | {n(h.get('messages_applied'))} | {n(dropped)} | {n(h.get('gaps'))} "
         f"| {n(h.get('requested_messages'))} | {n(retx)} | {n(natural_sent_recv)} (a) "
         f"| {n(h.get('duplicate_messages'))} | {n(h.get('epochs'))} | {n(h.get('unrecoverable'))} "
         f"| {n(h.get('held_max'))} | {digest_cell} "
         f"| {wall / 1e9:.3f} s | {h.get('msgs_per_sec', 0):,.0f} |" if isinstance(wall, int) else
         f"| {n(sent)} | {n(h.get('messages_applied'))} | {n(dropped)} | {n(h.get('gaps'))} | | | | | | | | {digest_cell} | | |")
L.append("")
L.append("- Segments sent: datagrams the publisher put on the wire (accepted IEX-TP segments minus injected drops). "
         f"Accepted segments {n(p.get('segments'))} of {n(p.get('packets_read'))} packets read "
         f"({n(p.get('skipped'))} skipped by SegmentFilter); {n(p.get('messages_sent'))} messages went out by UDP.")
L.append("- Messages: messages the handler applied to its books, in sequence order (`messages_applied`).")
L.append(f"- Gaps detected: holes the handler found and requested (`gaps`, always equal to `requests`, here "
         f"{n(h.get('requests'))}). The publisher answered {n(p.get('retransmit_requests'))} requests, "
         f"{n(p.get('unserved'))} unserved, ring evicted {n(p.get('ring_evicted'))} packets.")
L.append("- Messages requested and duplicates discarded include benign tail re-requests: near End or a restart, a "
         "datagram still queued in the kernel can be requested as a missing tail, and then arrives twice "
         "(UDP and retransmit); the second copy is a counted duplicate. The file's own merge-seam duplicates are "
         "counted there too.")
L.append(f"- (a) Natural drops = segments sent - datagrams the handler read ({n(sent)} - {n(recv)}). It is an upper "
         "bound on loss in the kernel: the handler stops once its books are complete, so a datagram still queued "
         "in its socket then (already recovered by retransmit) counts too. Cross-checks: the kernel's Udp "
         f"RcvbufErrors delta over the run {n(kernel.get('RcvbufErrors'))} (VM-wide counter, receive-buffer "
         f"overflow); messages requested beyond the {n(injected_msgs)} messages in injected-drop packets "
         f"{n(extra_requested)} (inflated by tail re-requests). "
         f"Kernel Udp deltas: InDatagrams {n(kernel.get('InDatagrams'))}, InErrors {n(kernel.get('InErrors'))}, "
         f"OutDatagrams {n(kernel.get('OutDatagrams'))}. Publisher sendto retries (ENOBUFS/EAGAIN) "
         f"{n(p.get('send_retries'))}.")
L.append(f"- Held max: the most packets the handler held behind a gap at one time. Parked: {n(h.get('parked'))}. "
         f"Rejected datagrams: {n(h.get('rejected'))} (truncated {n(h.get('truncated'))}).")
L.append("- Handler wall: first datagram received to books complete. msgs/s = messages / handler wall. "
         f"Publisher send wall {n(p.get('send_wall_ns'))} ns.")
L.append("- Digest match per epoch: the handler's final book digest, order count and message count for each "
         "epoch, compared by this script with the reference file line by line (and by the handler's own "
         f"`--expect`, which reported `digest_match: {str(h.get('digest_match')).lower()}`).")
if empty_epochs:
    L.append(f"- **Empty final books (generated):** the reference has 0 resting orders at the end of epoch(s) "
             f"{', '.join(map(str, empty_epochs))}, so their digest is the FNV-1a offset basis of an empty book. "
             "For those epochs the digest comparison shows only that both sides end with empty books after "
             "applying the same number of messages; it does not by itself show the intermediate books agreed.")
L.append("")
L.append("<details><summary>Per-epoch digests</summary>")
L.append("")
L.append("| epoch | handler digest | reference digest | orders (handler / reference) | messages (handler / reference) |")
L.append("|---:|---|---|---:|---:|")
for x in per_epoch:
    g, e = x["got"] or {}, x["expected"] or {}
    L.append(f"| {x['epoch']} | `{g.get('digest', '')}` | `{e.get('digest', '')}` | {n(g.get('orders'))} / "
             f"{n(e.get('orders'))} | {n(g.get('messages'))} / {n(e.get('messages'))} |")
L.append("")
L.append("</details>")
L.append("")
section = "\n".join(L) + "\n"

if record == "1":
    raw = pathlib.Path(raw_dir)
    raw.mkdir(parents=True, exist_ok=True)
    stem = f"{today}-{scenario}-{sha}"
    dest, k = raw / f"{stem}.json", 2
    while dest.exists():  # never overwrite an earlier run
        dest, k = raw / f"{stem}-{k}.json", k + 1
    rel = dest.relative_to(repo)
    section = section.replace("\n<details>", f"\nRaw JSON: `{rel}`\n\n<details>", 1)
    dest.write_text(json.dumps(record_obj, indent=2) + "\n")
    print(f"raw JSON in {dest}", file=sys.stderr)
    path = pathlib.Path(results)
    marker = "<!-- runs: newest first, inserted by scripts/bench_feed.sh -->"
    body = path.read_text() if path.exists() else f"# Feed handler benchmarks\n\n{marker}\n"
    if not body.startswith("# Feed handler benchmarks") or marker not in body:
        raise SystemExit(f"{path} lacks the expected header or runs marker; not editing it")
    i = body.index(marker) + len(marker)
    rest = body[i:].lstrip("\n")
    path.write_text(body[:i] + "\n\n" + section + ("\n" + rest if rest else ""))
    print(f"recorded in {path}", file=sys.stderr)
print(section)
sys.exit(0 if passed else 1)
PY
PYRC=$?
set -e
[[ "$PYRC" == 0 ]] || { echo "== run did not pass (see the section above)" >&2; exit 1; }
