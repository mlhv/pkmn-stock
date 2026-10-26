#!/usr/bin/env bash
# Pokemon wire replay throughput, with and without building each day's
# ladders of modelled resting orders (synthetic, built from the day's tcgcsv
# quote), via cpp/tools/pokemon_replay.
#
#   scripts/bench_pokemon_replay.sh
#
# Runs inside the Linux VM. Started on macOS, it re-executes itself in the
# OrbStack `ubuntu` VM (the Mac filesystem is mounted at the same path there),
# so it works from either side, like scripts/bench_iex.sh.
#
# Steps:
#   1. Configure + build pokemon_replay as Release in cpp/build-linux-release
#      (re-configured every time, so the flags reported are the ones built).
#   2. Three interleaved pairs: ladders on, ladders off, x3 (one pass each,
#      --repeat 1), each pass timed by the tool's own counter around the whole
#      pass including file reads.
#   3. Print a markdown section: msgs/s best and median per mode and the
#      spread ((max - min) / median), plus every run's JSON. It only prints;
#      docs/benchmarks/pokemon-book.md is hand-written from it.
#
# Environment: PKW_FILE (default data/wire/full.pkw, made by
# `pkmn wire encode`), HARDWARE (the results' hardware line).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ "$(uname -s)" != "Linux" ]]; then
    command -v orb >/dev/null || { echo "not Linux and no orb on PATH" >&2; exit 1; }
    exec orb -m ubuntu env PKW_FILE="${PKW_FILE:-}" HARDWARE="${HARDWARE:-}" \
        bash "$ROOT/scripts/bench_pokemon_replay.sh" "$@"
fi

[[ $# == 0 ]] || { echo "usage: $0" >&2; exit 2; }

FILE="${PKW_FILE:-$ROOT/data/wire/full.pkw}"
HARDWARE="${HARDWARE:-Apple M1 Pro, 7-vCPU OrbStack Ubuntu 24.04 arm64 VM}"
BUILD="$ROOT/cpp/build-linux-release"
[[ -f "$FILE" ]] || { echo "no wire file at $FILE (run pkmn wire encode first)" >&2; exit 1; }

OUT="$(mktemp -d)"
trap 'rc=$?; if [[ $rc == 0 ]]; then rm -rf "$OUT"; else echo "kept $OUT (exit $rc)" >&2; fi' EXIT

echo "== build (Release) in $BUILD" >&2
cmake -S "$ROOT/cpp" -B "$BUILD" -DCMAKE_BUILD_TYPE=Release -DPKMN_BUILD_TESTS=OFF >/dev/null
cmake --build "$BUILD" -j --target pokemon_replay >/dev/null

CXX_PATH="$(sed -n 's/^CMAKE_CXX_COMPILER:[A-Z]*=//p' "$BUILD/CMakeCache.txt")"
COMPILER="$("$CXX_PATH" --version | head -1)"
# The flags the target actually compiles with (the build type's flags plus the
# target's options), read from the generated build rules.
FLAGS="$(grep -h 'CXX_FLAGS' "$BUILD"/CMakeFiles/pokemon_replay.dir/flags.make | sed 's/^CXX_FLAGS = //')"
GIT_SHA="$(git -C "$ROOT" rev-parse HEAD)"
GIT_DIRTY="$([[ -n "$(git -C "$ROOT" status --porcelain)" ]] && echo true || echo false)"
FILE_BYTES="$(stat -c %s "$FILE")"

for i in 1 2 3; do
    echo "== pair $i: ladders" >&2
    "$BUILD/pokemon_replay" "$FILE" >"$OUT/on$i.json"
    echo "== pair $i: no ladders" >&2
    "$BUILD/pokemon_replay" "$FILE" --no-ladders >"$OUT/off$i.json"
done

python3 - "$OUT" "$HARDWARE" "$COMPILER" "$FLAGS" "$GIT_SHA" "$GIT_DIRTY" "$(hostname)" \
    "$FILE_BYTES" <<'PY'
import datetime, json, pathlib, statistics, sys

out, hardware, compiler, flags, sha, dirty, host, nbytes = sys.argv[1:9]
out = pathlib.Path(out)
runs = {m: [json.loads((out / f"{m}{i}.json").read_text()) for i in (1, 2, 3)] for m in ("on", "off")}
L = [f"## {datetime.date.today().isoformat()} — {pathlib.Path(runs['on'][0]['file']).name}", ""]
L.append(f"- Hardware: {hardware}")
L.append(f"- Host: `{host}`")
L.append(f"- git: `{sha}`" + (" (dirty)" if dirty == "true" else ""))
L.append(f"- Compiler: `{compiler}`")
L.append(f"- Flags: `{flags.strip()}`")
L.append(f"- File: {int(nbytes):,} bytes, {runs['on'][0]['messages']:,} messages, "
         f"{runs['on'][0]['instruments']:,} instruments, {runs['on'][0]['days']:,} days, "
         f"{runs['on'][0]['quotes']:,} quotes, {runs['on'][0]['ladders_built']:,} ladders built, "
         f"{runs['on'][0]['resting_orders_booked']:,} resting orders booked per ladder pass")
L.append("- Method: 3 interleaved pairs (ladders on, then off), one pass each; each pass "
         "timed by the tool's counter around the whole pass, file reads included.")
L.append("")
L.append("| mode | msgs/s best | msgs/s median | spread | seconds (runs 1-3) |")
L.append("|---|---:|---:|---:|---|")
for m, label in (("on", "ladders (decode + build each day's ladders)"), ("off", "no ladders (decode only)")):
    rates = [r["msgs_per_s"] for r in runs[m]]
    med = statistics.median(rates)
    spread = (max(rates) - min(rates)) / med * 100
    secs = ", ".join(f"{r['seconds']:.3f}" for r in runs[m])
    L.append(f"| {label} | {max(rates):,.0f} | {med:,.0f} | {spread:.1f}% | {secs} |")
L.append("")
L.append("<details><summary>Raw JSON (on 1-3, off 1-3)</summary>")
L.append("")
L.append("```json")
for m in ("on", "off"):
    for r in runs[m]:
        L.append(json.dumps(r, indent=2))
L.append("```")
L.append("")
L.append("</details>")
section = "\n".join(L) + "\n"
print(section)
PY
