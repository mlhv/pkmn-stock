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
#   sweep   --ram --drop none, one run per target rate in SWEEP_RATES (default
#           1000000 2000000 4000000 8000000 0 msgs/s; 0 = unthrottled), each
#           checked against the reference; every run is recorded as its own
#           section/raw JSON (scenario sweep-<rate>), then a summary section
#           with the table and the two defined figures is prepended last.
#           A failed rate is recorded as failed and never quoted.
#
# Runs inside the Linux VM. Started on macOS, it re-executes itself in the
# OrbStack `ubuntu` VM (the Mac filesystem is mounted at the same path there).
#
# Steps:
#   1. Configure + build iex_digest, feed_publisher and feed_handler as
#      Release in cpp/build-linux-release.
#   2. Reference digests with mid-epoch checkpoints every CHECKPOINT_EVERY
#      messages (book_digest.hpp): for the default capture, data/feed/
#      reference-20241223-ckpt<N>.digests, written with iex_digest only when
#      missing (never recomputed otherwise; its sha256 goes in the record).
#      With IEX_FILE set to another capture, a temporary reference is
#      computed for that file on every run (or pass REF_FILE).
#   3. Start feed_handler --expect <reference> --checkpoint-every N, then
#      feed_publisher, on a random port pair in 20000-31999. /proc/net/snmp's
#      Udp counters are read before and after (VM-wide kernel counters).
#      When either tool exits, the other gets GRACE_S (30) seconds to finish
#      and is then killed (recorded), so an early death cannot leave the
#      script waiting (the publisher blocks in accept() until a handler
#      connects).
#   4. Save one JSON (both tools' JSON, exit codes, commands, build, kernel
#      counter deltas) as docs/benchmarks/feed-handler-raw/<date>-<scenario>-
#      <sha>.json (a numeric suffix is added rather than overwrite a run).
#   5. Prepend a dated section to docs/benchmarks/feed-handler.md, newest
#      first, after the "runs" marker line.
#   --no-record skips 4 and 5 (the section is still printed).
#
# A run passes when both tools exit 0, unrecoverable is 0, gaps > 0, every
# epoch entry and every checkpoint equals the reference (same count, same
# order), and the handler's own --expect comparison agrees. A failing run is
# still recorded, and the script then exits 1.
#
# Environment: IEX_FILE (default data/iex/20241223_IEXTP1_DPLS1.0.pcap.gz),
# REF_FILE (reference digests to use instead of the default), HARDWARE,
# RING_BYTES (publisher --ring-bytes; default the tool's 268435456),
# RCVBUF (handler --rcvbuf; default the tool's 7500000), TIMEOUT_S (handler
# --timeout-s, default 1800), CHECKPOINT_EVERY (default 100000).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ "$(uname -s)" != "Linux" ]]; then
    command -v orb >/dev/null || { echo "not Linux and no orb on PATH" >&2; exit 1; }
    exec orb -m ubuntu env \
        SWEEP_RATES="${SWEEP_RATES:-}" SWEEP_RATE="${SWEEP_RATE:-}" SWEEP_LIST="${SWEEP_LIST:-}" \
        IEX_FILE="${IEX_FILE:-}" REF_FILE="${REF_FILE:-}" HARDWARE="${HARDWARE:-}" \
        RING_BYTES="${RING_BYTES:-}" RCVBUF="${RCVBUF:-}" TIMEOUT_S="${TIMEOUT_S:-}" \
        CHECKPOINT_EVERY="${CHECKPOINT_EVERY:-}" \
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
    sweep) DROP="none"; SEED=1 ;;
    *) usage ;;
esac

RAM=0
RATE=0
if [[ "$SCENARIO" == sweep ]]; then
    if [[ -z "${SWEEP_RATE:-}" ]]; then
        # Driver: one child run per rate (each builds, runs, records), then the summary.
        SWEEP_RATES="${SWEEP_RATES:-1000000 2000000 4000000 8000000 0}"
        SWEEP_LIST="$(mktemp)"
        export SWEEP_LIST
        EXTRA=()
        [[ "$RECORD" == 1 ]] || EXTRA+=(--no-record)
        for r in $SWEEP_RATES; do
            [[ "$r" =~ ^[0-9]+$ ]] || { echo "bad rate $r" >&2; exit 2; }
            echo "== sweep: target rate $r" >&2
            SWEEP_RATE="$r" bash "${BASH_SOURCE[0]}" sweep "${EXTRA[@]}" || echo "== rate $r run failed (recorded)" >&2
        done
        set +e
        SUMMARY_RECORD="$RECORD" SWEEP_REQ="$SWEEP_RATES" python3 - "$SWEEP_LIST" "$ROOT/docs/benchmarks/feed-handler.md" <<'PYS'
import datetime, json, os, pathlib, sys

runs = [json.loads(l) for l in pathlib.Path(sys.argv[1]).read_text().splitlines() if l.strip()]
md = pathlib.Path(sys.argv[2])
today = datetime.date.today().isoformat()


def n(v):
    return f"{v:,}" if isinstance(v, int) else "n/a"


rows, clean, unthr, best_clean = [], None, None, None
requested = [int(x) for x in os.environ.get("SWEEP_REQ", "").split()]
missing = [x for x in requested if x not in {r["rate"] for r in runs}]
for r in runs:
    h, p = r["handler"], r["publisher"]
    target = r["rate"]
    ok = r["pass"]
    nd = r["natural_drops"]["sent_minus_received"]
    pub = p.get("messages_sent", 0) / (p["send_wall_ns"] / 1e9) if p.get("send_wall_ns") else 0
    rows.append((target, ok, h, nd, pub, r))
    reached = target > 0 and pub >= 0.95 * target
    if ok and h.get("gaps") == 0 and reached and (clean is None or target > clean[0]):
        clean = (target, h)
    if ok and h.get("gaps") == 0 and (best_clean is None or h.get("msgs_per_sec", 0) > best_clean.get("msgs_per_sec", 0)):
        best_clean = h
    if target == 0:
        unthr = (ok, h, nd)
L = [f"## {today}, throughput sweep (n=1 per rate)", ""]
L.append("`--ram --drop none`, handler checked against the reference at every rate (epoch and checkpoint "
         "digests). Each row is one run; the per-run sections below carry the full detail.")
L.append("")
L.append("| target msgs/s | publisher achieved msgs/s | handler msgs/s | natural drops | gaps recovered "
         "| unrecoverable | digest match | result |")
L.append("|---:|---:|---:|---:|---:|---:|---|---|")
for target, ok, h, nd, pub, r in rows:
    dm = r["checks"]["epochs_match"] and r["checks"]["checkpoints_match"] and r["checks"]["handler_digest_match"]
    L.append(f"| {'unthrottled (0)' if target == 0 else n(target)} | {pub:,.0f} | {h.get('msgs_per_sec', 0):,.0f} "
             f"| {n(nd)} | {n(h.get('gaps'))} | {n(h.get('unrecoverable'))} | {'yes' if dm else 'NO'} "
             f"| {'pass' if ok else 'FAILED, not quotable'} |")
for target in missing:  # a child that died before writing a record still gets a row
    L.append(f"| {'unthrottled (0)' if target == 0 else n(target)} | n/a | n/a | n/a | n/a | n/a | NO "
             "| FAILED, no record (run died before finishing), not quotable |")
L.append("")
L.append("Two figures. Their definitions come from the plan and the spec; the extra rule that a paced target "
         "counts only if the publisher reached 95% of it was added after the first results, because the "
         "unmodified definition would have called an unreached 4M target \"sustained\" (95% is a judgement "
         "call, not from the spec):")
L.append("")
if clean:
    L.append(f"- **Clean sustained:** the highest target rate with 0 gaps and a digest match: "
             f"**{n(clean[0])} msgs/s target**, handler {clean[1].get('msgs_per_sec', 0):,.0f} msgs/s achieved.")
elif best_clean:
    L.append("- **Clean sustained:** no target rate was reached by the publisher (achieved under 95% of target "
             "in every run), so there is no paced-target figure. The fastest run with 0 gaps and a digest match "
             f"read {best_clean.get('msgs_per_sec', 0):,.0f} msgs/s at the handler; it was limited by the "
             "publisher, so it is a lower bound on what the handler can absorb, not a ceiling.")
else:
    L.append("- **Clean sustained:** no rate in this sweep had 0 gaps and a digest match, so there is no figure.")
if unthr and unthr[0]:
    h = unthr[1]
    L.append(f"- **Unthrottled with recovery:** {h.get('msgs_per_sec', 0):,.0f} msgs/s at the handler (the "
             f"rate-0 run), {n(h.get('gaps'))} gaps recovered ({n(h.get('requested_messages'))} messages requested), "
             f"{n(unthr[2])} natural drops, digest match yes.")
else:
    L.append("- **Unthrottled with recovery:** the unthrottled run failed or is missing, so there is no figure.")
L.append("")
L.append("Caveats: n=1 per rate, no repeats. VM noise is 10% or more (`iex-book.md`), so differences "
         "inside that band are not differences. A target rate the publisher cannot reach shows as a lower "
         "publisher-achieved column; msgs/s at the handler is messages divided by the handler's own wall "
         "(first datagram to books complete). A rate whose run did not match the reference is marked FAILED "
         "and is never quoted. Natural drops are an upper bound (see the per-run notes).")
L.append("")
L.append("Data provided for free by IEX. By accessing or using IEX Historical Data, you agree to the IEX "
         "Historical Data Terms of Use (`docs/iex-hist-terms.md`).")
L.append("")
sec = "\n".join(L) + "\n"
if os.environ.get("SUMMARY_RECORD") == "1":
    marker = "<!-- runs: newest first, inserted by scripts/bench_feed.sh -->"
    body = md.read_text()
    i = body.index(marker) + len(marker)
    rest = body[i:].lstrip("\n")
    md.write_text(body[:i] + "\n\n" + sec + ("\n" + rest if rest else ""))
print(sec)
sys.exit(0 if all(r["pass"] for r in runs) and not missing else 1)
PYS
        SRC=$?
        set -e
        rm -f "$SWEEP_LIST"
        [[ "$SRC" == 0 ]] || echo "== sweep: at least one rate failed or has no record" >&2
        exit "$SRC"
    fi
    RAM=1
    RATE="$SWEEP_RATE"
    SCENARIO="sweep-$RATE"
fi

DEFAULT_FILE="$ROOT/data/iex/20241223_IEXTP1_DPLS1.0.pcap.gz"
FILE="${IEX_FILE:-$DEFAULT_FILE}"
HARDWARE="${HARDWARE:-Apple M1 Pro, 7-vCPU OrbStack Ubuntu 24.04 arm64 VM, loopback multicast}"
TIMEOUT_S="${TIMEOUT_S:-1800}"
CHECKPOINT_EVERY="${CHECKPOINT_EVERY:-100000}"
[[ "$CHECKPOINT_EVERY" =~ ^[1-9][0-9]*$ ]] || { echo "CHECKPOINT_EVERY must be a positive integer" >&2; exit 2; }
GRACE_S=30
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
    REF="$ROOT/data/feed/reference-20241223-ckpt$CHECKPOINT_EVERY.digests"
else
    REF="$OUT/reference.digests"
fi
if [[ ! -s "$REF" ]]; then
    echo "== reference digests (iex_digest) -> $REF" >&2
    mkdir -p "$(dirname "$REF")"
    "$BUILD/iex_digest" "$FILE" --checkpoint-every "$CHECKPOINT_EVERY" --out "$REF.tmp"
    mv "$REF.tmp" "$REF"
else
    echo "== reference digests: reusing $REF" >&2
fi

free -b | awk '/^Mem:/ {printf "== memory: %.1f GB total, %.1f GB available\n", $2/1e9, $7/1e9}' >&2

PORT=$((20000 + RANDOM % 11999))
CPORT=$((PORT + 1))
HCMD=("$BUILD/feed_handler" --port "$PORT" --ctrl-port "$CPORT" --timeout-s "$TIMEOUT_S" --expect "$REF"
    --checkpoint-every "$CHECKPOINT_EVERY")
[[ -n "${RCVBUF:-}" ]] && HCMD+=(--rcvbuf "$RCVBUF")
PCMD=("$BUILD/feed_publisher" "$FILE" --port "$PORT" --ctrl-port "$CPORT" --rate "$RATE" --drop "$DROP" --seed "$SEED")
[[ "$RAM" == 1 ]] && PCMD+=(--ram)
[[ -n "${RING_BYTES:-}" ]] && PCMD+=(--ring-bytes "$RING_BYTES")
printf '%s\n' "${HCMD[*]}" >"$OUT/handler.cmd"
printf '%s\n' "${PCMD[*]}" >"$OUT/publisher.cmd"

grep '^Udp:' /proc/net/snmp >"$OUT/snmp_before.txt"
echo "== $SCENARIO: handler on $PORT/$CPORT, then publisher ($DROP, seed $SEED, rate $RATE, ram=$RAM)" >&2
"${HCMD[@]}" >"$OUT/handler.json" 2>"$OUT/handler.err" &
HPID=$!
"${PCMD[@]}" >"$OUT/publisher.json" 2>"$OUT/publisher.err" &
PUB_PID=$!
# Wait until one tool exits, then give the other GRACE_S seconds.
alive() { kill -0 "$1" 2>/dev/null; }
while alive "$HPID" && alive "$PUB_PID"; do sleep 0.2; done
KILLED=""
for pid_name in "$HPID:handler" "$PUB_PID:publisher"; do
    pid="${pid_name%%:*}"
    for ((i = 0; i < GRACE_S * 5; i++)); do alive "$pid" || break; sleep 0.2; done
    if alive "$pid"; then
        kill "$pid" 2>/dev/null || true
        KILLED="$KILLED ${pid_name#*:}"
        echo "== killed the ${pid_name#*:} after the other tool exited (${GRACE_S}s grace)" >&2
    fi
done
set +e
wait "$HPID"; HRC=$?
wait "$PUB_PID"; PRC=$?
set -e
export KILLED CHECKPOINT_EVERY RATE RAM
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
killed = os.environ.get("KILLED", "").split()
ckpt_every = int(os.environ["CHECKPOINT_EVERY"])


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
expected, exp_ck, ref_every = [], [], 0
for line in ref_text.splitlines():
    f = line.split()
    if f[0] == "checkpoint-every":
        ref_every = int(f[1])
    elif f[0] == "checkpoint":
        exp_ck.append({"epoch": int(f[1]), "seq": int(f[2]), "digest": f[4], "orders": int(f[6])})
    else:
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
got_ck = h.get("checkpoint_digests", [])
ck_matched = sum(1 for g, e in zip(got_ck, exp_ck) if g == e)
ck_all = len(got_ck) == len(exp_ck) and ck_matched == len(exp_ck)
ck_mismatch = next((i for i in range(max(len(got_ck), len(exp_ck)))
                    if i >= len(got_ck) or i >= len(exp_ck) or got_ck[i] != exp_ck[i]), None)
ck_populated = sum(1 for e in exp_ck if e["orders"] > 0)
ck_orders = [e["orders"] for e in exp_ck if e["orders"] > 0]
ck_per_epoch = {i: sum(1 for e in exp_ck if e["epoch"] == i) for i in range(len(expected))}
checks = {
    "handler_exit_0": hrc == 0,
    "publisher_exit_0": prc == 0,
    "no_tool_killed": not killed,
    "unrecoverable_0": h.get("unrecoverable") == 0,
    # Key name kept for stored records. Meaning: "gaps > 0 when loss was injected"; with --drop none
    # (sweep) any gap count passes, because zero gaps is the clean outcome there.
    "gaps_positive": isinstance(h.get("gaps"), int) and (h["gaps"] > 0 or drop == "none"),
    "epochs_match": all_match,
    "handler_digest_match": h.get("digest_match") is True,
    "interval_agrees": ref_every == ckpt_every == h.get("checkpoint_every"),
    "checkpoints_match": ck_all,
    "handler_checkpoint_match": h.get("checkpoint_match") is True,
    "handler_counts_agree": h.get("checkpoints_matched") == ck_matched and h.get("checkpoints_expected") == len(exp_ck),
}
passed = all(checks.values())

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
#   messages minus the publisher's messages_sent. This is the messages of the
#   naturally dropped packets, plus any benign tail re-request (a message
#   requested while its datagram was still queued, which then arrives twice
#   and is counted as a duplicate). With 0 duplicates no tail re-request
#   happened and the whole excess is natural-drop messages.
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
    "mode": "ram" if os.environ.get("RAM") == "1" else "stream",
    "rate": int(os.environ["RATE"]),
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
    "checkpoint_every": ckpt_every,
    "checkpoints": {"expected": len(exp_ck), "got": len(got_ck), "matched": ck_matched,
                    "populated_expected": ck_populated, "per_epoch_expected": ck_per_epoch,
                    "first_mismatch_index": ck_mismatch, "expected_list": exp_ck},
    "killed": killed,
    "checks": checks,
    "pass": passed,
}


def n(v):
    return f"{v:,}" if isinstance(v, int) else "n/a"


wall = h.get("wall_ns")
ck_cell = f"{n(ck_matched)} / {n(len(exp_ck))}" + ("" if len(got_ck) == len(exp_ck) else f" (handler took {n(len(got_ck))})")
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
L.append(f"- Scenario: `{scenario}` = `--drop {drop} --seed {seed}`, {'ram mode (payloads preloaded)' if os.environ.get('RAM') == '1' else 'stream mode (zlib while sending)'}, "
         f"`--rate {os.environ['RATE']}` (0 = unthrottled); ring {n(p.get('ring_bytes'))} bytes; handler SO_RCVBUF requested {n(h.get('rcvbuf_requested'))}, "
         f"got {n(h.get('rcvbuf_actual'))}")
L.append(f"- Reference: `{record_obj['reference']['path']}` (iex_digest direct file replay), sha256 "
         f"`{record_obj['reference']['sha256'][:16]}...`, {len(expected)} epoch(s)")
L.append(f"- Exit codes: handler {hrc} (status `{h.get('status', 'n/a')}`), publisher {prc}"
         + (f"; killed after the grace period: {', '.join(killed)}" if killed else ""))
L.append(f"- Checks: " + ", ".join(f"{k} {'yes' if v else 'NO'}" for k, v in checks.items()))
L.append("")
L.append("| segments sent | messages | injected drops | gaps detected | messages requested | retransmitted packets "
         "| natural drops | duplicates discarded | epochs | unrecoverable | held max | digest match per epoch "
         "| checkpoints matched | handler wall | msgs/s |")
L.append("|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---:|---:|---:|")
L.append(f"| {n(sent)} | {n(h.get('messages_applied'))} | {n(dropped)} | {n(h.get('gaps'))} "
         f"| {n(h.get('requested_messages'))} | {n(retx)} | {n(natural_sent_recv)} (a) "
         f"| {n(h.get('duplicate_messages'))} | {n(h.get('epochs'))} | {n(h.get('unrecoverable'))} "
         f"| {n(h.get('held_max'))} | {digest_cell} | {ck_cell} "
         f"| {wall / 1e9:.3f} s | {h.get('msgs_per_sec', 0):,.0f} |" if isinstance(wall, int) else
         f"| {n(sent)} | {n(h.get('messages_applied'))} | {n(dropped)} | {n(h.get('gaps'))} | | | | | | | | {digest_cell} "
         f"| {ck_cell} | | |")
L.append("")
L.append("- Segments sent: datagrams the publisher put on the wire (accepted IEX-TP segments minus injected drops). "
         f"Accepted segments {n(p.get('segments'))} of {n(p.get('packets_read'))} packets read "
         f"({n(p.get('skipped'))} skipped by SegmentFilter); {n(p.get('messages_sent'))} messages went out by UDP.")
L.append("- Messages: messages the handler applied to its books, in sequence order (`messages_applied`).")
L.append(f"- Gaps detected: holes the handler found and requested (`gaps`, always equal to `requests`, here "
         f"{n(h.get('requests'))}). The publisher answered {n(p.get('retransmit_requests'))} requests, "
         f"{n(p.get('unserved'))} unserved, ring evicted {n(p.get('ring_evicted'))} packets.")
dups = h.get("duplicate_messages")
L.append("- Messages requested = messages in injected-drop packets + messages in naturally dropped packets + any "
         "benign tail re-request (near End or the restart, a datagram still queued in the kernel can be requested "
         "as a missing tail and then arrive twice; the second copy is a counted duplicate, as are the file's own "
         f"merge-seam duplicates). Here duplicates discarded = {n(dups)}"
         + (", so no tail re-request happened and every requested message was in a dropped packet."
            if dups == 0 else ", so some requested messages may be tail re-requests."))
L.append(f"- (a) Natural drops = segments sent - datagrams the handler read ({n(sent)} - {n(recv)}). It is an upper "
         "bound on loss in the kernel: the handler stops once its books are complete, so a datagram still queued "
         "in its socket then (already recovered by retransmit) counts too. Cross-checks: the kernel's Udp "
         f"RcvbufErrors delta over the run {n(kernel.get('RcvbufErrors'))} (VM-wide counter, receive-buffer "
         f"overflow); messages requested beyond the {n(injected_msgs)} messages in injected-drop packets "
         f"{n(extra_requested)} (the messages of the naturally dropped packets"
         + (", nothing else, since duplicates are 0" if dups == 0 else ", plus any tail re-requests") + "); "
         f"retransmitted packets - injected drops - natural drops = "
         f"{n(retx - dropped - natural_sent_recv) if isinstance(retx, int) and isinstance(dropped, int) and isinstance(natural_sent_recv, int) else 'n/a'} "
         "(a positive value counts packets re-requested as a tail; a negative one counts dropped zero-message packets, since a heartbeat opens no gap and is never retransmitted). "
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
L.append(f"- Checkpoints matched: book digest and order count after every message whose sequence number is a "
         f"multiple of {n(ckpt_every)}, compared index by index with the reference (a missing or extra checkpoint "
         f"fails). Per epoch in the reference: "
         + ", ".join(f"{e}: {n(c)}" for e, c in ck_per_epoch.items())
         + f"; {n(ck_populated)} of {n(len(exp_ck))} have resting orders"
         + (f" ({n(min(ck_orders))} to {n(max(ck_orders))})." if ck_orders else ".")
         + (f" First mismatch at index {ck_mismatch}." if ck_mismatch is not None else ""))
if empty_epochs:
    L.append(f"- **Empty final books (generated):** the reference has 0 resting orders at the end of epoch(s) "
             f"{', '.join(map(str, empty_epochs))}, so their digest is the FNV-1a offset basis of an empty book. "
             "The end-of-epoch comparison there shows only that both sides end empty after applying the same "
             "number of messages; the populated checkpoints are what compare mid-epoch books.")
no_ck = [e for e, c in ck_per_epoch.items() if c == 0 or not any(x["orders"] for x in exp_ck if x["epoch"] == e)]
if no_ck:
    L.append(f"- **Epoch(s) with no populated checkpoint (generated):** {', '.join(map(str, no_ck))}. Their books "
             "are compared only at the epoch end.")
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
if os.environ.get("SWEEP_LIST"):
    with open(os.environ["SWEEP_LIST"], "a") as fh:
        slim = {k: v for k, v in record_obj.items() if k not in ("reference", "per_epoch", "checkpoints")}
        fh.write(json.dumps(slim) + "\n")
print(section)
sys.exit(0 if passed else 1)
PY
PYRC=$?
set -e
[[ "$PYRC" == 0 ]] || { echo "== run did not pass (see the section above)" >&2; exit 1; }
