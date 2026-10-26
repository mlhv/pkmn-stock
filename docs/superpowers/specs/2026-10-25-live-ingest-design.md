# Live price ingest and phone alerts: design

Date: 2026-10-25. Branch: `feat/live-ingest`, cut from
`feat/instrument-repair-horizon` (owner decision, below).

## Why

tcgcsv.com took its daily price archive offline. Every
`/archive/tcgplayer/prices-YYYY-MM-DD.ppmd.7z` URL now returns 403 from
CloudFront, including dates already ingested, with this body (abridged):

> The price archive has been temporarily removed due to rising server costs
> and its growing moderation burden. [...] please instead process
> categories, groups, and prices by making multiple requests. [...] avoid
> requesting the same price file more than once in a 24 hour period.

The per-group live endpoints still work:
`GET https://tcgcsv.com/tcgplayer/3/{groupId}/prices` returns
`{"success": true, "errors": [], "results": [...]}`, the same shape as each
`<date>/3/<groupId>/prices` file inside the old archives
(`tcgcsv.extract_group_prices` already parses it), with a `Last-Modified`
header (e.g. `Sat, 24 Oct 2026 20:03:25 GMT`). tcgcsv publishes one update a
day around 20:00 UTC; `https://tcgcsv.com/last-updated.txt` gives its
timestamp (e.g. `2026-10-23T20:06:07+0000`).

Effect so far: `pkmn daily` has failed its ingest every day since
2026-09-14 (33 failures in `data/results/daily.log` by 2026-10-24). The
warehouse stops at 2026-09-13. The live endpoints serve only the current
update, so **every day without a working ingest is a day of history lost
permanently**, unless tcgcsv republishes the archive (it says prices "are
still being archived behind-the-scenes"; nothing is promised).

The failure was reported only as a macOS banner ("pkmn daily: ingest
problem", `cli.py:878`) plus `daily.json`. The owner did not notice those
banners for weeks and says phone notifications are what actually reach them.

### What the data is for

Research history, not live trading. The owner wants the history to keep
growing, complete and gap-free, so that any later backtest (including one
seeded with a fixed portfolio of cards) has as much real data as possible.
Completeness and continuity matter; latency does not (same-day capture is
nice, next-morning is fine).

## Goals

1. **Capture one complete price snapshot per tcgcsv update**, from the live
   per-group endpoints, for every group the archive path would ingest
   (`tracked_groups(fetch_groups(client))`, currently all ~220), and write it
   to the warehouse as that day's partition through the existing
   `prices_frame` and `apply_quality_gates` path, so a live day is
   indistinguishable downstream from an archive day.
2. **Be robust to the Mac's schedule.** The Mac is usually on but the lid is
   sometimes closed in the morning, and the network is sometimes down
   (2026-10-25's run hit `[Errno 8] nodename nor servname`). The job runs
   several times a day; each run captures only if tcgcsv has an update we
   have not stored yet, so a missed morning is caught later in the day.
3. **Respect tcgcsv's request.** Each group's price file is fetched at most
   once per tcgcsv update, i.e. at most once per 24 hours, except a re-fetch
   of a group whose previous fetch failed. No hammering, no retry storms.
4. **Keep the raw responses on disk**, as the archives are kept today, so the
   warehouse can still be rebuilt from raw data later.
5. **Fail loudly, on the phone.** Alerts go to the owner's phone via ntfy,
   in addition to the existing macOS banner.
6. **Detect the slow failure**, where no single run errors but no new data
   arrives: an alert when the newest stored day is older than expected.

## Non-goals

- Recovering 2026-09-14 up to the first live capture. That data is not
  available to us. Record the gap; do not fabricate or interpolate it.
- Changing `scripts/rebuild_warehouse.py` or `verify_rebuild.py` to replay
  live snapshots. Raw files are kept so that is possible later; it is not in
  this branch.
- Removing the archive path. `pkmn ingest --start/--end` and the archive
  download stay as they are, for the case where tcgcsv restores the archive.
  But `pkmn daily` **stops** calling `ingest_range` over every missing day
  (several runs a day would mean ~40 403s per run). Instead it probes one
  archive URL at most once a day, for the newest missing date, and sends an
  alert if it ever returns 200 ("archive is back: the 2026-09-14 onward gap
  can be backfilled"). That backfill is a manual `pkmn ingest` for gap days
  only. **A day written from live data is final**: a restored archive never
  overwrites it, consistent with never rewriting partitions. (Builder's
  call, settled in review.)
- Changing the catalog refresh logic (`refresh_products`, its merge and
  `CATALOG_FLOOR`) beyond when it is called (see "builder's calls").
- Any change to backtest, strategy, engine or research code, or to any
  existing warehouse partition. One owner-approved exception (2026-10-25):
  `research/runs.py`'s `data_fingerprint` gains the catalog's SHA-256,
  because the formula baseline showed the daily catalog refresh silently
  changing ML results with no trace in the registry.
- Other notification channels (Pushover, iMessage). ntfy only.

## Anti-goals (failure even if it "works")

- Writing a day that is silently incomplete (missing groups with no record),
  or dated to the wrong day.
- Requesting the same price file repeatedly within a day, or retrying in a
  tight loop.
- Alerts that buzz the phone every run during a multi-day outage, so the
  owner learns to ignore them, as happened with the banners.
- The ntfy topic name ending up in git. Anyone who knows a public ntfy topic
  can read and post to it.
- A notification failure (ntfy down, no network) breaking or failing the run.
- Touching the live warehouse or the installed launchd job during
  development: a formula baseline batch is reading the warehouse and
  catalog until it finishes. Development and tests use fixtures and scratch
  roots only; the first real capture happens after the owner says the batch
  is done.

## Stopgap capture already running (owner-approved 2026-10-25)

So no day is lost while this is built, a throwaway script
(`capture_raw.py`, kept outside the repo) saves each update's raw `/prices`
files to `data/raw/live/<UTC date>/<groupId>.json.gz` plus a
`manifest.json`, which records per group: status held/absent/failed, HTTP
code, `Last-Modified`, ETag, fetch time and row count, along with
`last_updated` before and after the run. It writes nothing else. The
builder's capture code must **read these existing directories as already
held**, and finalize them into the warehouse in date order, so the stopgap
days land as ordinary days. The layout above is therefore no longer free:
keep `data/raw/live/<date>/<groupId>.json.gz` and the manifest fields, or
migrate them in code.

First capture, 2026-10-25 16:33 UTC: update `2026-10-24T20:05:44+00:00`,
day 2026-10-24, 220 of 220 groups held, 45,400 price rows (the last archive
day, 2026-09-13, has 44,478 clean rows), 5 groups held-empty, 0 files newer
than the update. **The oldest `Last-Modified` was 2024-10-31**, which
confirms the review's concern: groups whose prices never change keep old
stamps, so a strict date-match rule would never complete. Took 49 s and
1.2 MB on disk.

## Decided by the owner

- **ntfy** for phone alerts (chosen over Pushover and iMessage-to-self).
  Mac banners stay as a second channel.
- **Branch from `feat/instrument-repair-horizon`**, because the 9:00 launchd
  job runs whatever this checkout has, and that branch is checked out; the
  fix reaches the scheduled job as soon as it merges there. It reaches
  `main` only with that branch.
- History completeness matters more than latency.

## Design

### Dating a snapshot

A live snapshot is stored as the warehouse day **equal to the UTC calendar
date of tcgcsv's update timestamp** (`last-updated.txt`, cross-checked
against the price files' `Last-Modified`). The update lands around 20:00
UTC, so a 2026-10-24T20:06Z update is day 2026-10-24 regardless of when the
Mac fetched it.

Assumption to state in code and docs: the archive file `prices-D` held the
update published on UTC day D. There is no overlap between archive days and
live days to prove this, so it is a stated convention, not a verified fact.
If evidence later shows the archive was offset by a day, the convention
changes in one place.

**`Last-Modified` rule (builder's call, settled in review).** A file's
`Last-Modified` will never equal the update timestamp exactly (a group file
stamped 20:03:25 against an update stamped 20:06:07 is normal), and a group
whose prices did not change may keep an older stamp. So the only rule is:
a fetched file belongs to the current update unless its `Last-Modified` is
**newer** than `last-updated.txt`'s timestamp, which means tcgcsv has moved
on mid-capture. Older is fine. A file newer than the update is set aside,
and the run re-reads `last-updated.txt` before finalizing anything. A strict
date match is ruled out, because it would defer finalizing forever on a
group that never changes.

**Two updates on one UTC day (builder's call).** If a late update (after
00:00 UTC) is followed by a normal one the same UTC day, both map to one
date. Accepted as rare: the second is not stored over the first, and the run
alerts that two distinct update timestamps mapped to one day, so the
missing neighbouring day is visible rather than silent.

### Capture, across runs

A run:

1. Reads `last-updated.txt` (one tiny request). If that UTC date is already
   a stored warehouse day, the run is a no-op for ingest.
2. Otherwise it fetches each tracked group's `/prices` it does not already
   hold for that update, saving each raw response to disk as it arrives.
   Failed groups are retried a small bounded number of times with backoff
   within the run, then left for the next run.
3. When every tracked group is held for that update, it builds the day
   through `prices_frame` + `apply_quality_gates` (seeded with the previous
   stored day, honoring the existing `GAP_RESET_DAYS` rule) and writes the
   price and quarantine partitions.
4. If a newer tcgcsv update appears before the previous one completed, the
   previous one is finalized with what was captured (groups that never
   arrived are absent from that day, as a group missing from an archive
   would be), and the missing group ids are recorded and alerted. It is
   never silently dropped and never silently partial.

**Group outcomes (builder's call, settled in review).** Each group fetch for
an update ends in exactly one recorded state:

- **held**: a 200 with `success: true`, including an empty `results` list
  (an empty group is a real answer, and is not re-fetched);
- **absent**: a 404, meaning tcgcsv has no price file for that group (the
  live counterpart of a group missing from an archive); recorded and not
  re-fetched for that update;
- **failed**: anything else, such as a network error, timeout, 5xx, 403, a
  200 carrying HTML, or no `results` key. Only failed groups are retried.

A day is complete when no group is in `failed`. Held-empty and absent groups
are listed in the capture record, so a real failure can't pass as
"genuinely empty" without a trace. The cached archives can be used to
sanity-check the absent list (which groups never had a price file). If
absent counts jump between updates, that is worth a warning but does not
block the day.

### Schedule

The launchd template (`scripts/com.pkmn-quant.daily.plist`) runs
`pkmn daily` several times a day instead of once at 09:00 (e.g. 09:00,
13:00, 17:00, 21:00 local; 17:00 and later can catch the same-day ~20:00
UTC update). macOS runs a calendar-interval job on wake if the Mac was
asleep at the scheduled time; it is missed only if the Mac is off all day.

`pkmn daily` on a run that captured nothing new must not re-send buy/sell
notifications already sent for the same data. Today the daily signals step
runs every invocation; with several runs a day it should act only when a
new day landed (or on the first run of the day if nothing landed, so that
`daily.json` still reflects the current state). Exact rule: builder's call,
constrained by "no duplicate signal notifications for the same `as_of`".

Reinstalling the launchd job (`~/Library/LaunchAgents/
com.pkmn-quant.daily.plist`) is an owner-approved step after merge, not
something the branch does.

### Alerts

`live/notify.py` gains an ntfy sender beside the banner: one HTTP POST to
`{server}/{topic}` with a title and short body (ntfy's plain HTTP publish
API; `https://ntfy.sh` by default). Topic and server come from a local,
gitignored config file under `data/` (e.g. `data/config/notify.toml`);
absent config means banners only, exactly as today. The ntfy call has a
short timeout and swallows its own errors (logged to stderr), so it can never
fail the run. Message bodies carry only operational text (counts, dates,
error strings), never ledger contents or positions.

Alert policy, with a small persisted state file under `data/` so it survives
across runs:

- **Run failure** (builder's call, settled in review). A fetch failure that
  a later run the same day clears up is **not** an alert: it is logged and
  recorded in `daily.json`. Otherwise every closed-lid or DNS-blip morning
  would send a "failed" and a "recovered" alert, which is exactly the fatigue
  the anti-goals rule out. Capture problems alert only once the update is
  actually at risk: still incomplete on the last scheduled run of the
  day, or caught by the staleness rule below. Non-transient failures
  (signals failure, ledger error, an unexpected exception) alert the first
  time, then at most once per 24 hours while they persist.
- **Staleness**: alert when the newest stored day is more than a threshold
  behind the expected latest update (e.g. no new day for more than ~36
  hours after an expected update). Same once-then-daily rate limit.
- **Partial day**: alert when a day is finalized with missing groups.
- **Recovered**: one alert when data starts landing again after a failure
  or staleness alert.
- **Existing signal notifications** (buys/sells, catalog degraded) also go
  to ntfy.

`daily.json` keeps its existing fields and gains whatever capture outcome
fields the builder needs (update date, groups fetched / missing, whether a
new day was written).

### Raw storage

Each fetched group response is stored compressed under `data/raw/` in a
layout keyed by update date and group id (e.g.
`data/raw/live/2026-10-24/<groupId>.json.gz`), written atomically
(temp file + rename) the way `download_archive` writes archives. These
files are what lets a run resume a partial capture and what a future
rebuild would replay.

## Constraints

- Use `tcgcsv.make_client()` (tcgcsv 401s httpx's default User-Agent).
- Keep the `(httpx.HTTPError, ValueError, KeyError)` per-group failure
  filter style: a 200 carrying HTML or no `results` is a failed group, not
  an aborted run.
- No network in tests: httpx `MockTransport` fixtures, scratch roots
  (`tmp_path`), monkeypatched notifier.
- All four gates pass before every commit (`uv run pytest`, ruff check,
  ruff format --check, mypy strict), with `uv sync --group api --group
  dashboard` first. No `cpp/` changes are expected.
- Never delete or rewrite anything under `data/`.
- Update `CLAUDE.md` (status bullet, commands, gotchas: archive removed,
  dating convention, ntfy config location) and the README where it
  describes ingest.

## Left to the builder

- Module boundaries (a new `data/live.py` beside `ingest.py` is the
  natural shape), the raw file layout, the state-file format, retry counts
  and backoff, the completeness floor, the staleness threshold, the exact
  schedule times, and whether the catalog refresh runs on every invocation
  or only on runs that capture a new day (fewer requests to tcgcsv argues
  for the latter).
- Whether `pkmn ingest` gains a `--live` flag for a manual capture. Useful
  for the first real-data check; not required.

## Done means

- Tests cover: no-op when the update is already stored; full capture writes
  a day identical in schema to an archive day; partial capture resumes on
  the next run without re-fetching held groups; finalize-partial on a newer
  update records and alerts missing groups; dating from `last-updated.txt`;
  `Last-Modified` disagreement defers; alert rate limiting and recovery;
  ntfy failure does not fail the run; absent config sends banners only.
- After the owner confirms the baseline batch has finished: one real
  capture into the live warehouse, the written day checked against its raw
  files (row count, groups present), and a test ntfy alert received on the
  owner's phone.
