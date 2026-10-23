#!/usr/bin/env bash
# Baseline latency/throughput for DEEP+ decode + BookSet::apply (iex_bench).
#
#   scripts/bench_iex.sh [--profile] [--no-record]
#
# Runs inside the Linux VM. Started on macOS, it re-executes itself in the
# OrbStack `ubuntu` VM (the Mac filesystem is mounted at the same path there),
# so `scripts/bench_iex.sh` works from either side.
#
# Steps:
#   1. Configure + build iex_bench as Release in cpp/build-linux-release
#      (re-configured every time: the binary's git SHA and flags are captured
#      at configure time).
#   2. iex_bench --mode ram three times, --mode stream once.
#   3. Prepend a dated section to docs/benchmarks/iex-book.md (newest first):
#      hardware line, git SHA, compiler, flags, the best ram msgs/sec, the
#      median ram run's percentiles (median by msgs/sec), every run's JSON.
#      --no-record skips this step (results still printed).
#
# --profile first (so a profile failure costs no bench time) builds cpp/build-linux-prof (same flags plus
# -g -fno-omit-frame-pointer), records `perf record -F 999 -g` over one ram
# run, and writes docs/benchmarks/iex-book-flamegraph.svg from the stacks
# under the throughput pass (replay<CallStep>) only, i.e. not the load. Needs
# perf (linux-tools-generic; the OrbStack kernel has no matching
# linux-tools package, so the newest /usr/lib/linux-tools-*/perf is used
# directly) and FLAMEGRAPH_DIR pointing at a clone of
# https://github.com/brendangregg/FlameGraph (never cloned into the repo).
#
# Environment: IEX_FILE (default data/iex/20241223_IEXTP1_DPLS1.0.pcap.gz),
# RAM_BYTES (default: iex_bench's 4 GiB), HARDWARE (the results' hardware
# line), FLAMEGRAPH_DIR (for --profile).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ "$(uname -s)" != "Linux" ]]; then
    command -v orb >/dev/null || { echo "not Linux and no orb on PATH" >&2; exit 1; }
    exec orb -m ubuntu env \
        IEX_FILE="${IEX_FILE:-}" RAM_BYTES="${RAM_BYTES:-}" HARDWARE="${HARDWARE:-}" \
        FLAMEGRAPH_DIR="${FLAMEGRAPH_DIR:-}" \
        bash "$ROOT/scripts/bench_iex.sh" "$@"
fi

PROFILE=0
RECORD=1
for arg in "$@"; do
    case "$arg" in
        --profile) PROFILE=1 ;;
        --no-record) RECORD=0 ;;
        *) echo "usage: $0 [--profile] [--no-record]" >&2; exit 2 ;;
    esac
done

FILE="${IEX_FILE:-$ROOT/data/iex/20241223_IEXTP1_DPLS1.0.pcap.gz}"
HARDWARE="${HARDWARE:-Apple M1 Pro, 7-vCPU OrbStack Ubuntu 24.04 arm64 VM}"
RESULTS="$ROOT/docs/benchmarks/iex-book.md"
BUILD="$ROOT/cpp/build-linux-release"
RAM_ARGS=()
[[ -n "${RAM_BYTES:-}" ]] && RAM_ARGS=(--ram-bytes "$RAM_BYTES")
[[ -f "$FILE" ]] || { echo "no capture at $FILE" >&2; exit 1; }

OUT="$(mktemp -d)"
mkdir -p "$ROOT/docs/benchmarks"
# Kept on failure so finished runs are not lost.
trap 'rc=$?; if [[ $rc == 0 ]]; then rm -rf "$OUT"; else echo "kept $OUT (exit $rc)" >&2; fi' EXIT

echo "== build (Release) in $BUILD" >&2
cmake -S "$ROOT/cpp" -B "$BUILD" -DCMAKE_BUILD_TYPE=Release -DPKMN_BUILD_TESTS=OFF >/dev/null
cmake --build "$BUILD" -j --target iex_bench >/dev/null

free -b | awk '/^Mem:/ {printf "== memory: %.1f GB total, %.1f GB available\n", $2/1e9, $7/1e9}' >&2

if [[ "$PROFILE" == 1 ]]; then
    [[ -n "${FLAMEGRAPH_DIR:-}" && -x "$FLAMEGRAPH_DIR/flamegraph.pl" ]] ||
        { echo "--profile needs FLAMEGRAPH_DIR (a FlameGraph clone)" >&2; exit 1; }
    PERF="$(command -v perf 2>/dev/null || true)"
    if [[ -z "$PERF" ]] || ! "$PERF" --version >/dev/null 2>&1; then
        PERF="$(ls -d /usr/lib/linux-tools-*/perf 2>/dev/null | sort -V | tail -1)"
    fi
    [[ -x "$PERF" ]] || { echo "no usable perf" >&2; exit 1; }
    PROF="$ROOT/cpp/build-linux-prof"
    echo "== profile build in $PROF; perf $("$PERF" --version)" >&2
    cmake -S "$ROOT/cpp" -B "$PROF" -DCMAKE_BUILD_TYPE=Release -DPKMN_BUILD_TESTS=OFF \
        "-DCMAKE_CXX_FLAGS=-g -fno-omit-frame-pointer" >/dev/null
    cmake --build "$PROF" -j --target iex_bench >/dev/null
    "$PERF" record -F 999 -g -o "$OUT/perf.data" -- \
        "$PROF/iex_bench" "$FILE" --mode ram "${RAM_ARGS[@]}" >"$OUT/prof.json"
    "$PERF" script -i "$OUT/perf.data" 2>/dev/null | "$FLAMEGRAPH_DIR/stackcollapse-perf.pl" >"$OUT/all.folded"
    grep -F 'replay<pkmn::md::CallStep>' "$OUT/all.folded" >"$OUT/hot.folded" || true
    [[ -s "$OUT/hot.folded" ]] || { echo "no stacks under replay<CallStep>" >&2; exit 1; }
    "$FLAMEGRAPH_DIR/flamegraph.pl" --title "iex_bench ram throughput pass (decode + apply), perf -F 999 -g" \
        "$OUT/hot.folded" >"$ROOT/docs/benchmarks/iex-book-flamegraph.svg"
    cp "$OUT/hot.folded" "$ROOT/docs/benchmarks/iex-book-profile.folded"
    { "$PERF" --version; "$PERF" evlist -i "$OUT/perf.data" 2>/dev/null; } | tr '\n' ' ' >"$OUT/perf_event.txt"
    # Self time by leaf frame, under the throughput pass.
    awk '{n=$NF; $NF=""; k=split($0,f,";"); leaf=f[k]; sub(/ +$/,"",leaf); s[leaf]+=n; t+=n}
         END {for (l in s) printf "%6.2f%%  %s\n", 100*s[l]/t, l}' "$OUT/hot.folded" |
        sort -rn | head -15 | cut -c1-170 >"$OUT/top.txt"
    # Inclusive time by frame below replay<CallStep> (each frame once per stack).
    awk '{n=$NF; $NF=""; k=split($0,f,";"); on=0; delete seen
          for (i=1; i<=k; i++) { g=f[i]; sub(/ +$/,"",g)
            if (on && !(g in seen)) { seen[g]=1; s[g]+=n }
            if (index(g, "replay<pkmn::md::CallStep>")) on=1 }
          t+=n}
         END {for (l in s) printf "%6.2f%%  %s\n", 100*s[l]/t, l}' "$OUT/hot.folded" |
        sort -rn | head -15 | cut -c1-170 >"$OUT/incl.txt"
    echo "== profile: top self-time leaves under the throughput pass" >&2
    cat "$OUT/top.txt" >&2
    echo "== profile: top inclusive frames below replay<CallStep>" >&2
    cat "$OUT/incl.txt" >&2
fi

for i in 1 2 3; do
    echo "== ram run $i" >&2
    "$BUILD/iex_bench" "$FILE" --mode ram "${RAM_ARGS[@]}" >"$OUT/ram$i.json"
done
echo "== stream run" >&2
"$BUILD/iex_bench" "$FILE" --mode stream >"$OUT/stream.json"

python3 - "$OUT" "$RESULTS" "$HARDWARE" "$RECORD" "$PROFILE" <<'PY'
import datetime, json, pathlib, sys

out, results, hardware, record, profile = sys.argv[1:6]
out = pathlib.Path(out)
rams = [json.loads((out / f"ram{i}.json").read_text()) for i in (1, 2, 3)]
stream = json.loads((out / "stream.json").read_text())
by_rate = sorted(rams, key=lambda r: r["msgs_per_sec"])
best, median = by_rate[-1], by_rate[1]
rates = [r["msgs_per_sec"] for r in rams]
spread = (max(rates) - min(rates)) / median["msgs_per_sec"] * 100
head = rams[0]
res = head["timer_resolution_ns"]
# Within this many ticks a percentile's +-1-tick quantization is >= 10% of it.
QUANT_TICKS = 10
PCTS = (("p50", "p50_ticks"), ("p99", "p99_ticks"), ("p99.9", "p999_ticks"))


def band(ticks):
    return "n/a" if ticks == 0 else f"±{100 / ticks:.1f}%"


def pct_cell(r, name, key):
    ns_key = {"p50": "p50_ns", "p99": "p99_ns", "p99.9": "p999_ns"}[name]
    return f"{r[ns_key]:,} ns ({r[key]} ticks, {band(r[key])})"


coarse = [(name, median[key]) for name, key in PCTS if median[key] <= QUANT_TICKS]

L = []
L.append(f"## {datetime.date.today().isoformat()} — {pathlib.Path(head['file']).name}")
L.append("")
L.append(f"- Hardware: {hardware}")
L.append(f"- Host: `{head['host']}`")
L.append(f"- git: `{head['git_sha']}`" + (f", working-tree hash `{head['tree_hash']}`" if head.get("tree_hash") else ""))
L.append(f"- Compiler: `{head['compiler']}`")
L.append(f"- Flags: `{head['flags']}`")
L.append(f"- Timer: {head['timer']}, cntfrq {head['cntfrq_hz']:,} Hz, calibrated "
         f"{head['ticks_per_ns']:.6f} ticks/ns, resolution {head['timer_resolution_ns']:.1f} ns per tick; "
         f"empty ticks() pair {head['timer_overhead_ns']:.1f} ns mean (p50 {head['timer_overhead_ticks_p50']} ticks); "
         f"steady_clock resolution {head['steady_clock_resolution_ns']:.0f} ns")
L.append(f"- ram held {head['held_bytes']:,} payload bytes in {head['held_packets']:,} packets "
         f"(cap {head['ram_bytes']:,}, truncated: {str(head['ram_truncated']).lower()}); "
         f"{head['messages']:,} messages, {head['restarts']} restart(s)")
L.append("")
if coarse:
    L.append(f"> **Quantization warning (generated):** {', '.join(f'{n} = {t} ticks ({band(t)})' for n, t in coarse)} "
             f"of a {res:.1f} ns counter. Each sample is a whole number of ticks, so a percentile at or under "
             f"{QUANT_TICKS} ticks carries a ±1-tick uncertainty of 10% or more; a change smaller than one "
             f"tick ({res:.1f} ns) at that percentile cannot be seen. Use the timed-region mean (not "
             "quantized) and msgs/s for small changes.")
    L.append("")
L.append("| | msgs/s | mean ns/msg | p50 | p99 | p99.9 | max |")
L.append("|---|---:|---:|---:|---:|---:|---:|")
L.append(f"| ram throughput pass, best of 3 | {best['msgs_per_sec']:,.0f} | {best['batch_ns_per_msg']:.1f} (a) | | | | |")
L.append(f"| ram latency pass, median run | | {median['timed_region_mean_ns']:.1f} (b) | "
         + " | ".join(pct_cell(median, n, k) for n, k in PCTS) + f" | {median['max_ns']:,} ns |")
L.append(f"| stream, end to end incl. gunzip | {stream['msgs_per_sec']:,.0f} | "
         f"{stream['seconds'] * 1e9 / stream['messages']:.1f} (c) | | | | |")
L.append("")
L.append("- (a) throughput pass wall time / messages, no per-message timer: SegmentFilter (IEX-TP framing, "
         "sequence tracking) + decode + `BookSet::apply`.")
L.append(f"- (b) timed-region mean: sum of per-message tick deltas / messages / ticks_per_ns, the mean of "
         f"exactly what the percentiles measure (decode + apply plus one ticks() pair, whose empty mean is "
         f"{median['timer_overhead_ns']:.1f} ns). Not quantized: per-sample rounding averages out. It excludes "
         "SegmentFilter, so it is not comparable to (a).")
L.append(f"- (c) stream wall time / messages, including inflate and pcapng parsing.")
L.append(f"- Percentiles: whole ticks converted to ns; the band is ±1 tick relative to the value. "
         f"Max is exact; a single sample of milliseconds is most likely the VM being descheduled or a page fault (not verified: no hardware counters), not decode + apply.")
L.append(f"- Latency pass wall time / messages (median run): {median['latency_pass_wall_ns_per_msg']:.1f} ns. "
         "This is the cost of the instrumented loop (SegmentFilter, decode + apply, the timer pair, two "
         "histogram records and an llround per message), not a latency figure.")
L.append("")
L.append(f"Run-to-run ram throughput: {', '.join(f'{r:,.0f}' for r in rates)} msgs/s "
         f"(spread {spread:.1f}% of the median).")
L.append("")
L.append("| ram run | msgs/s | timed-region mean ns | p50 ticks | p99 ticks | p99.9 ticks | max ns |")
L.append("|---|---:|---:|---:|---:|---:|---:|")
for i, r in enumerate(rams, 1):
    L.append(f"| {i} | {r['msgs_per_sec']:,.0f} | {r['timed_region_mean_ns']:.1f} | {r['p50_ticks']} | "
             f"{r['p99_ticks']} | {r['p999_ticks']} | {r['max_ns']:,} |")
L.append("")
if profile == "1":
    L.append("Profile: `docs/benchmarks/iex-book-flamegraph.svg` (throughput pass only, "
             "cpp/build-linux-prof = these flags plus `-g -fno-omit-frame-pointer`, one separate ram run "
             f"under `perf record -F 999 -g`; perf: `{(out / 'perf_event.txt').read_text().strip()}`). "
             "Top self-time leaves:")
    L.append("")
    L.append("```")
    L.append((out / "top.txt").read_text().rstrip())
    L.append("```")
    L.append("")
    L.append("Top inclusive frames below the throughput pass's replay loop:")
    L.append("")
    L.append("```")
    L.append((out / "incl.txt").read_text().rstrip())
    L.append("```")
    L.append("")
L.append("<details><summary>Raw JSON (ram runs 1-3, stream)</summary>")
L.append("")
L.append("```json")
for r in rams + [stream]:
    L.append(json.dumps(r, indent=2))
L.append("```")
L.append("")
L.append("</details>")
L.append("")
section = "\n".join(L) + "\n"
print(section)

if record == "1":
    path = pathlib.Path(results)
    header = ("# IEX book benchmarks\n\n"
              "Results of `scripts/bench_iex.sh` (DEEP+ decode + `BookSet::apply`, `cpp/tools/iex_bench.cpp`), "
              "newest first.\n\n")
    body = path.read_text() if path.exists() else header
    if not body.startswith("# IEX book benchmarks"):
        raise SystemExit(f"{path} does not start with the expected header; not editing it")
    first = body.find("\n## ")
    if first < 0:
        new = body.rstrip("\n") + "\n\n" + section
    else:
        new = body[: first + 1] + section + "\n" + body[first + 1 :]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(new)
    print(f"recorded in {path}", file=sys.stderr)
PY
