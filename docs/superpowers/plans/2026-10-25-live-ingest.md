# Live Price Ingest and Phone Alerts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the dead tcgcsv archive ingest in `pkmn daily` with a resumable, once-per-update capture from the live per-group `/prices` endpoints, plus rate-limited ntfy phone alerts.

**Architecture:** A new `data/live.py` captures one tcgcsv update into raw files under `data/raw/live/<UTC date>/` (resumable across runs through a per-day `manifest.json`), then finalizes completed days into the warehouse through the existing `prices_frame` + `apply_quality_gates` path. A new `live/alerts.py` holds a small persisted state file and the alert policy (once, then at most daily; recovery; staleness; signals de-duplication). `live/notify.py` fans every notification out to the macOS banner and ntfy. `pkmn daily` orchestrates them and runs several times a day under launchd.

**Tech Stack:** Python 3.12+, httpx (`MockTransport` in tests), polars, typer, tomllib, pytest.

**Spec:** `docs/superpowers/specs/2026-10-25-live-ingest-design.md`

## Global Constraints

- Always use `tcgcsv.make_client()`; tcgcsv 401s httpx's default User-Agent.
- Each group's `/prices` is fetched at most once per tcgcsv update; only `failed` groups are re-fetched.
- Snapshot day = UTC calendar date of `last-updated.txt`'s timestamp.
- A file whose `Last-Modified` is newer than the update timestamp is set aside, not saved. Older is fine.
- Group outcome is exactly one of `held` (200, `success: true`, including empty `results`), `absent` (404), `failed` (anything else).
- Per-group failure filter: `(httpx.HTTPError, ValueError, KeyError)`.
- Raw layout is fixed by the running stopgap: `data/raw/live/<YYYY-MM-DD>/<groupId>.json.gz` (gzip of the exact response bytes) plus `manifest.json` with top-level `last_updated` and `last_updated_after` (ISO strings) and `groups: {"<gid>": {"status", "http", "last_modified", "etag", "fetched_at", "rows", "error"?}}`.
- A day written from live data is final; nothing ever rewrites an existing warehouse partition.
- ntfy config lives in gitignored `data/config/notify.toml`; absent config means banners only. A notification failure never fails a run.
- No network in tests. Never delete or rewrite anything under `data/` in real runs.
- Gates before every commit: `uv sync --group api --group dashboard`, then `uv run pytest`, `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy`. No `cpp/` changes.
- **No real-data run, and no launchd change, until the owner confirms the formula baseline batch has finished** (Task 6).

## Review Focus

1. A non-ASCII title or body (existing messages contain "—") must reach ntfy intact. HTTP headers can't carry it, so publish as JSON. Pinned in Task 3.
2. A run just after 00:00 UTC, when the local date is still the previous day: the snapshot is dated by the UTC date of the update, never by the local date. Pinned in Task 1.
3. `last-updated.txt` returning 200 with HTML, or with an unparseable body: the run captures nothing and writes nothing, and raises a failure the caller records. Pinned in Task 1.
4. A crash mid-write leaves `<gid>.json.gz.tmp`: resume must treat that group as not held and re-fetch it. Pinned in Task 1.
5. `pkmn daily --paper` run four times on the same data must record paper fills once, not four times. Pinned in Task 5.

---

### Task 1: Live capture primitives (`data/live.py`, capture only)

**Files:**
- Modify: `src/pkmn_quant/config.py` (add `Paths.raw_live`)
- Create: `src/pkmn_quant/data/live.py`
- Test: `tests/test_live_capture.py`

**Interfaces:**
- Produces:
  - `Paths.raw_live -> Path` = `root / "data" / "raw" / "live"`.
  - `GroupStatus = Literal["held", "absent", "failed"]`
  - `@dataclass(frozen=True) class Capture: day: date; update_ts: datetime; held: tuple[int, ...]; absent: tuple[int, ...]; failed: tuple[int, ...]; moved_on: bool; collision: bool` (`moved_on` means `last-updated.txt` changed during the run. `collision` means the day's manifest already names a different `last_updated`, i.e. a second update mapped to the same UTC day: then nothing is fetched and the manifest is left untouched, so two updates' files never mix). Property `complete -> bool` = `not self.failed`.
  - `class LiveIngestError(Exception)`: raised when `last-updated.txt` can't be fetched or parsed.
  - `fetch_last_updated(client: httpx.Client) -> datetime`: aware UTC datetime, parsed with `%Y-%m-%dT%H:%M:%S%z`. Raises `LiveIngestError` on an HTTP error or a parse failure.
  - `update_day(ts: datetime) -> date` = `ts.astimezone(UTC).date()`.
  - `load_manifest(day_dir: Path) -> dict[str, Any]` (empty `{"groups": {}}` when missing) and `save_manifest(day_dir: Path, manifest: dict[str, Any]) -> None`. The save is atomic: write a temp file, then rename.
  - `capture_update(client: httpx.Client, paths: Paths, group_ids: Sequence[int], *, retries: int = 3, backoff_s: float = 2.0, pause_s: float = 0.2, sleep: Callable[[float], None] = time.sleep) -> Capture`
  - `MAX_RETRIES = 3` as the default (named constant).

- [ ] **Step 1: Write the failing tests** in `tests/test_live_capture.py`. Build an httpx `MockTransport` handler that serves `/last-updated.txt` and `/tcgplayer/3/{gid}/prices`, with per-gid scripted responses and a request counter. Tests:
  - `test_update_day_uses_utc_date`: `update_day(datetime(2026,10,25,0,30,tzinfo=UTC)) == date(2026,10,25)`, and the same instant expressed in `America/New_York` (10-24 20:30 local) also gives `date(2026,10,25)`.
  - `test_capture_writes_raw_and_manifest`: two groups both 200. `data/raw/live/2026-10-24/{gid}.json.gz` gunzips to the exact response bytes. The manifest records `status == "held"`, `rows`, `http == 200`, `last_modified` and `etag`, and `last_updated == "2026-10-24T20:05:44+00:00"`. `Capture.complete` is True.
  - `test_empty_results_is_held_and_404_is_absent`: `{"success": true, "results": []}` gives held with `rows == 0`; a 404 gives absent with no file written.
  - `test_failures_retry_then_stay_failed`: a group answering 503 three times ends `failed` after exactly 3 requests (`sleep` stubbed, called with 2.0 and then 4.0). A group answering 200 with HTML ends `failed`. Neither writes a file.
  - `test_resume_skips_held_and_absent`: run once with one group failing, then again with all succeeding. The second run requests only the failed group; held and absent groups get 0 new requests.
  - `test_resume_treats_tmp_as_not_held` (Review Focus 4): pre-create `{gid}.json.gz.tmp` with no manifest entry. The run fetches that group and leaves a real `.json.gz`.
  - `test_reads_stopgap_manifest`: write a manifest in the stopgap format (fields listed under Global Constraints) with matching `.json.gz` files. The run makes 0 `/prices` requests and returns those gids as held.
  - `test_file_newer_than_update_is_set_aside`: `Last-Modified: Sun, 25 Oct 2026 20:01:00 GMT` against update `2026-10-24T20:05:44+0000` gives `failed` with `error` containing `"newer than update"`, and no file. `Last-Modified` from 2024-10-31 gives held.
  - `test_second_update_same_day_does_not_mix`: the manifest for 2026-10-25 names `last_updated` `2026-10-25T00:30:00+00:00`, and the current update is `2026-10-25T20:05:00+0000`. Then `Capture.collision` is True, 0 `/prices` requests are made, and the manifest's bytes are unchanged.
  - `test_moved_on_detected`: `last-updated.txt` returns a different timestamp on the second read, so `Capture.moved_on` is True.
  - `test_bad_last_updated_raises_and_writes_nothing` (Review Focus 3): a 200 with `<html>` makes `capture_update` raise `LiveIngestError`, and `data/raw/live` has no files.

- [ ] **Step 2: Run** `uv run pytest tests/test_live_capture.py -v`. Expected: FAIL (ImportError on `pkmn_quant.data.live`).

- [ ] **Step 3: Implement** `Paths.raw_live` and `data/live.py` with the interfaces above.
  - `capture_update` reads the update timestamp first, picks the day directory, and loads the manifest.
  - For each gid not already `held` (with its `.json.gz` on disk) or `absent`, it fetches `/tcgplayer/{POKEMON_CATEGORY_ID}/{gid}/prices` and classifies the result: 404 gives absent; a 200 that parses with `success` true and a `results` list gives held, unless its `Last-Modified` (parsed with `email.utils.parsedate_to_datetime`) is newer than the update; everything else gives failed. It retries failed groups with `sleep(backoff_s * attempt)`.
  - It writes each `.json.gz` atomically (gzip of the raw `r.content`, temp file, then rename) and saves the manifest after each group, so a crash loses at most one group.
  - It calls `sleep(pause_s)` between groups, re-reads `last-updated.txt` at the end, and sets `last_updated_after`.

- [ ] **Step 4: Run** `uv run pytest tests/test_live_capture.py -v`. Expected: PASS.

- [ ] **Step 5: Gates and commit**
```bash
git add src/pkmn_quant/config.py src/pkmn_quant/data/live.py tests/test_live_capture.py
git commit -m "feat(live): resumable once-per-update capture of tcgcsv /prices"
```

### Task 2: Finalize days into the warehouse, and the live ingest entry point

**Files:**
- Modify: `src/pkmn_quant/data/live.py`
- Test: `tests/test_live_finalize.py`

**Interfaces:**
- Consumes: Task 1's `capture_update`, `load_manifest`, `update_day`, `Capture`, `LiveIngestError`. From `ingest.py`: `tracked_groups`, `refresh_products`, `IngestStats`, `GAP_RESET_DAYS`. From `transforms.py`: `prices_frame`. From `quality.py`: `apply_quality_gates`.
- Produces:
  - `pending_days(paths: Paths) -> list[date]`: dated directories under `raw_live` that hold a `manifest.json` and are not warehouse days, sorted ascending.
  - `finalize_day(paths: Paths, day: date) -> IngestStats`: builds `rows_by_group` from the held files (`payload["results"]`), runs `prices_frame(day, rows_by_group)`, then `apply_quality_gates(prices, previous)`. `previous` is the latest stored day before `day` if it is within `GAP_RESET_DAYS`, else `None`. It writes the price and quarantine partitions. It raises `ValueError` if `day` is already stored.
  - `@dataclass(frozen=True) class LiveIngestReport: capture: Capture | None;` (None when the update's day was already stored, so nothing was fetched), written: tuple[IngestStats, ...]; partial: dict[date, tuple[int, ...]]; collision: bool; catalog_failures: tuple[int, ...]`
  - `run_live_ingest(paths: Paths, client: httpx.Client | None = None, *, sleep: Callable[[float], None] = time.sleep) -> LiveIngestReport`
  - `probe_archive(client: httpx.Client, day: date) -> bool`: streams a GET of `tcgcsv.archive_url(day)`, closes it without reading the body, and returns `status == 200`. Errors return False.

- [ ] **Step 1: Write the failing tests** in `tests/test_live_finalize.py`. Use the Task 1 mock handler (extract it to `tests/live_helpers.py`, and import it in both test files) plus `/tcgplayer/3/groups` and `/tcgplayer/3/{gid}/products` routes for `refresh_products`. Tests:
  - `test_finalized_day_matches_archive_day_schema`: finalize a captured day. `Warehouse.load_day(day)` has `PRICE_SCHEMA` and the same rows that `prices_frame(day, same_rows)` plus the gates produce for the archive path on identical input.
  - `test_complete_current_update_is_written`: `run_live_ingest` on a complete capture writes that day; `report.written` has one entry and `partial == {}`.
  - `test_incomplete_current_update_is_not_written`: one group failing means no partition, `written == ()`, and the raw files remain.
  - `test_older_pending_day_is_finalized_partial_with_missing_recorded`: a pending 2026-10-24 directory with one failed group, while the current update is 2026-10-25 and complete. Both days are written, in date order: 10-24 is written first and is the `previous` baseline for 10-25. `partial == {date(2026,10,24): (gid,)}`.
  - `test_stored_day_is_never_rewritten`: the update's day is already stored and was written by an earlier update (manifest `last_updated` differs from the current one). Then `collision` is True, the partition's bytes are unchanged, and no `/prices` request is made.
  - `test_already_stored_same_update_is_noop`: the update's day is stored with the same `last_updated`. No `/prices` requests, `written == ()`, `collision` False.
  - `test_catalog_refreshed_only_when_writing`: `/products` routes are requested only on the run that writes a day.
  - `test_gap_resets_quality_baseline`: the previous stored day is 2026-09-13 and the finalized day is 2026-10-24. `previous` is None, so a 10x price move vs 09-13 is not quarantined as `price_jump`.
  - `test_probe_archive`: 403 gives False, 200 gives True; the body is never read (the handler's stream would raise if consumed).

- [ ] **Step 2: Run** `uv run pytest tests/test_live_finalize.py -v`. Expected: FAIL (ImportError on `finalize_day`).

- [ ] **Step 3: Implement** `pending_days`, `finalize_day`, `run_live_ingest` and `probe_archive`. `run_live_ingest` order:
  1. `fetch_last_updated`. If the update's day is already stored, return early (`capture=None`), with `collision = (manifest last_updated != current)`. If `capture.collision` comes back True for an unstored day, finalize that day as it stands (partial allowed, missing gids in `partial`) and set the report's `collision`.
  2. `fetch_groups`, then `tracked_groups`, then `capture_update`.
  3. Collect the days to write: every pending day before the update's day (finalized even if partial, with its failed gids in `partial`), plus the update's day if `capture.complete` and not `capture.moved_on`.
  4. If there is anything to write, call `refresh_products(client, warehouse, groups)` once, then `finalize_day` in ascending date order.

- [ ] **Step 4: Run** `uv run pytest tests/test_live_finalize.py tests/test_live_capture.py -v`. Expected: PASS.

- [ ] **Step 5: Gates and commit**
```bash
git add src/pkmn_quant/data/live.py tests/test_live_finalize.py tests/test_live_capture.py tests/live_helpers.py
git commit -m "feat(live): finalize captured updates into the warehouse, never rewriting a day"
```

### Task 3: ntfy notifications

**Files:**
- Modify: `src/pkmn_quant/live/notify.py`
- Modify: `src/pkmn_quant/cli.py` (new `notify-test` command)
- Test: `tests/test_notify.py`

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) class NtfyConfig: topic: str; server: str = "https://ntfy.sh"`
  - `load_ntfy_config(root: Path) -> NtfyConfig | None`: reads `root/data/config/notify.toml`, table `[ntfy]`, keys `topic` (required) and `server` (optional). Returns None if the file or the topic is missing, and also on a TOML parse error, after a stderr warning.
  - `configure(root: Path) -> None`: sets the module-level `_ntfy: NtfyConfig | None`.
  - `send_ntfy(config: NtfyConfig, title: str, body: str) -> bool`: `httpx.post(config.server, json={"topic": config.topic, "title": title, "message": body}, timeout=10.0)`. Returns False on any `httpx.HTTPError` or non-2xx, after a stderr warning. Never raises.
  - `send_notification(title: str, body: str) -> None` keeps its signature (tests monkeypatch it as `lambda t, b`). It shows the banner, then calls `send_ntfy` if `_ntfy` is set.
  - CLI `pkmn notify-test [--root PATH]`: calls `configure`, sends `("pkmn test", "If you can read this on your phone, ntfy works.")`, and exits 1 with a message if no config is found or `send_ntfy` returned False.

- [ ] **Step 1: Write the failing tests** in `tests/test_notify.py`. Patch `httpx.post` with a `MockTransport`-backed client, or monkeypatch `notify.httpx.post`:
  - `test_load_config_absent_returns_none`, `test_load_config_reads_topic_and_default_server`, `test_load_config_bad_toml_returns_none`.
  - `test_send_ntfy_posts_json_with_unicode` (Review Focus 1): title `"pkmn daily: ingest problem"`, body containing `"—"`. The captured request is a JSON POST to `https://ntfy.sh` whose decoded body has the exact `topic`, `title` and `message`.
  - `test_send_ntfy_failure_returns_false`: `httpx.ConnectError` gives False and no exception; a 500 gives False.
  - `test_send_notification_without_config_makes_no_http_call`.
  - `test_notify_test_command_exits_1_without_config`.

- [ ] **Step 2: Run** `uv run pytest tests/test_notify.py -v`. Expected: FAIL.

- [ ] **Step 3: Implement** the interfaces above, with `tomllib` for parsing.

- [ ] **Step 4: Run** `uv run pytest tests/test_notify.py -v`. Expected: PASS.

- [ ] **Step 5: Gates and commit**
```bash
git add src/pkmn_quant/live/notify.py src/pkmn_quant/cli.py tests/test_notify.py
git commit -m "feat(notify): ntfy phone alerts beside the macOS banner"
```

### Task 4: Alert state and policy (`live/alerts.py`)

**Files:**
- Create: `src/pkmn_quant/live/alerts.py`
- Test: `tests/test_alerts.py`

**Interfaces:**
- Produces (all pure except `load`/`save`):
  - `STATE_FILE = "daily-state.json"`, under `root/data/results/`.
  - `REPEAT_EVERY = timedelta(hours=24)`.
  - `STALE_AFTER = timedelta(hours=48)`, measured from 20:00 UTC on the newest stored day. That is: the next update has been out about 24 hours and still isn't stored.
  - `AT_RISK_LOCAL_HOUR = 21`. A capture still incomplete on a run at or after 21:00 local is at risk.
  - `@dataclass class AlertState: active: dict[str, str]` (key to ISO `last_sent`), `signals_as_of: dict[str, str]` (`"real"`/`"paper"` to ISO date), `archive_probe_on: str | None`.
  - `load_state(root: Path) -> AlertState` (missing or corrupt file gives an empty state, with a stderr warning when corrupt) and `save_state(root: Path, state: AlertState) -> None` (atomic).
  - `should_alert(state: AlertState, key: str, now: datetime) -> bool`: True if `key` is not active, or if its `last_sent` was at least `REPEAT_EVERY` ago. When True, it records `now` as `last_sent`.
  - `resolve(state: AlertState, key: str) -> bool`: removes `key`. Returns True if it was active, meaning the caller sends a "recovered" alert.
  - `is_stale(newest_stored: date | None, now: datetime) -> bool`
  - `capture_at_risk(capture_failed: bool, now_local: datetime) -> bool`
  - Alert keys used by Task 5: `"capture"`, `"stale"`, `"run_error"`, `"partial:<date>"`, `"collision:<date>"`, `"archive_back"`.

- [ ] **Step 1: Write the failing tests** in `tests/test_alerts.py`:
  - `test_first_alert_then_suppressed_then_daily`: True at t0, False at t0+1h, True at t0+24h.
  - `test_resolve_reports_recovery_once`: after an alert, `resolve` returns True, then False the second time.
  - `test_is_stale_boundary`: newest stored 2026-10-24 gives not stale at 2026-10-26T19:59Z and stale at 2026-10-26T20:01Z. `None` is stale.
  - `test_capture_at_risk_only_late`: a failure at 13:00 local gives False; at 21:00 local, True; no failure, False.
  - `test_state_round_trip_and_corrupt_file`.

- [ ] **Step 2: Run** `uv run pytest tests/test_alerts.py -v`. Expected: FAIL.

- [ ] **Step 3: Implement** the interfaces above.

- [ ] **Step 4: Run** `uv run pytest tests/test_alerts.py -v`. Expected: PASS.

- [ ] **Step 5: Gates and commit**
```bash
git add src/pkmn_quant/live/alerts.py tests/test_alerts.py
git commit -m "feat(alerts): rate-limited alert state, staleness and at-risk rules"
```

### Task 5: Wire into `pkmn daily`, add `pkmn live-ingest`, schedule, docs

**Files:**
- Modify: `src/pkmn_quant/cli.py` (`daily` ingest block, lines ~793-810, and its notification tail, ~855-882; new `live-ingest` command)
- Modify: `scripts/com.pkmn-quant.daily.plist`
- Modify: `tests/test_cli_daily.py` (the three tests that monkeypatch `pkmn_quant.cli.ingest_range` now monkeypatch `pkmn_quant.cli.run_live_ingest`)
- Modify: `CLAUDE.md`, `README.md`
- Test: `tests/test_cli_daily.py`, `tests/test_cli_live_ingest.py`

**Interfaces:**
- Consumes: `run_live_ingest`, `LiveIngestReport`, `LiveIngestError`, `probe_archive` (Task 2); `notify.configure` and `send_notification` (Task 3); everything in `alerts.py` (Task 4).
- Produces:
  - `daily.json` gains `"live"`: `{"update_day", "held", "absent", "failed", "written": [iso dates], "partial": {iso: [gids]}, "collision", "new_day": bool}`. `null` under `--skip-ingest`.
  - CLI `pkmn live-ingest [--root PATH]`: one `run_live_ingest`. It prints the update day, the held/absent/failed counts and the days written, and exits 1 if the capture is incomplete.

- [ ] **Step 1: Write the failing tests.** In `tests/test_cli_daily.py` (with `run_live_ingest` monkeypatched to return scripted `LiveIngestReport`s, `sent` capturing notifications, and the alert clock injected by monkeypatching a module-level `_now()` in `cli.py`):
  - `test_daily_new_day_runs_signals_and_records_live`: `report.written` non-empty means signals run, and `daily.json["live"]["new_day"]` is True.
  - `test_daily_same_as_of_twice_does_not_renotify`: two runs, both with no new day. Buy/sell notifications are sent on the first only.
  - `test_daily_paper_same_as_of_records_fills_once` (Review Focus 5): two `--paper` runs on the same data, and the paper ledger event count is unchanged after the second.
  - `test_daily_transient_capture_failure_is_not_alerted`: an incomplete capture at 13:00 local sends no notification, exits 0, and records `failed` in `daily.json`.
  - `test_daily_capture_at_risk_alerts_once_then_recovers`: an incomplete capture at 21:00 sends one alert; a repeat at 22:00 sends none; a later run that writes the day sends exactly one "recovered".
  - `test_daily_stale_warehouse_alerts`: newest stored day far behind `_now()` gives one stale alert.
  - `test_daily_partial_and_collision_alert`: a report with `partial` and `collision` gives one alert each, naming the date and the gids.
  - `test_daily_live_ingest_error_records_and_alerts_only_when_stale`: `LiveIngestError` is recorded in `daily.json` either way, and alerted only when `is_stale` holds.
  - `test_daily_archive_probe_once_per_day_and_alerts_when_back`: `probe_archive` is called on the first run of a day only; True gives one `"archive is back"` alert.
  - The existing catalog-failure test keeps its assertion, driven through `LiveIngestReport.catalog_failures`.

  In `tests/test_cli_live_ingest.py`: `test_live_ingest_command_prints_counts_and_exit_code`, with both a complete and an incomplete report.

- [ ] **Step 2: Run** `uv run pytest tests/test_cli_daily.py tests/test_cli_live_ingest.py -v`. Expected: FAIL.

- [ ] **Step 3: Implement.** In `daily`:
  1. Call `notify.configure(root)` first.
  2. Replace the `ingest_range` block with `run_live_ingest(Paths(root=root))` inside the existing try, catching `Exception` into `ingest_error` as now.
  3. Probe the archive (for the newest stored day + 1) only when `state.archive_probe_on != today`.
  4. Skip signals and paper fills when `warehouse.stored_days()[-1]` equals `state.signals_as_of[mode]`. Write `daily.json` with status `"ok"` and `new_day` False, and send no buy/sell notification.
  5. Apply the alert policy:
     - capture problems alert only when `capture_at_risk` or `is_stale` holds;
     - `run_error` covers non-ingest failures (signals, ledger, unexpected exceptions) and keeps the existing `_fail` path, gated by `should_alert`;
     - each `resolve` that returns True sends `"<title>: recovered"`.
  6. Save the state on every path, including `_fail`.

  Exit code: 1 when an alert-level problem fired or `_fail` ran. A transient capture failure is 0.

  Plist: `StartCalendarInterval` becomes an array of four dicts, at 09:00, 13:00, 17:00 and 21:00.

  Docs:
  - **CLAUDE.md:** a status bullet (live ingest; the 2026-09-14..2026-10-23 gap is lost; the 2026-10-24 stopgap capture); the commands `pkmn live-ingest` and `pkmn notify-test`; and gotchas: archive removed; the UTC dating convention and its unproven archive-equivalence assumption; `data/config/notify.toml`; the plist must be reinstalled to take effect; the throwaway stopgap script is superseded.
  - **README:** the ingest box in the mermaid diagram says "tcgcsv.com live per-group prices (archive offline since 2026-09-14)", plus one sentence in the data section.

- [ ] **Step 4: Run** the full gates. Expected: all pass.

- [ ] **Step 5: Commit**
```bash
git add src/pkmn_quant/cli.py scripts/com.pkmn-quant.daily.plist tests/test_cli_daily.py tests/test_cli_live_ingest.py CLAUDE.md README.md
git commit -m "feat(daily): live ingest, alert policy and four runs a day"
```

### Task 5b: Catalog hash in every run's data fingerprint (owner-approved addition, 2026-10-25)

**Why:** the 2026-10-25 formula baseline found that the ml-ranker walk-forward no longer reproduced its 2026-08-10 run. Code, dependencies and in-window prices were unchanged; `products.parquet`, rewritten by every daily run, was the only input that differed. `data_fingerprint` covers prices only, so the registry could not show it.

**Files:**
- Modify: `src/pkmn_quant/research/runs.py:69-78`
- Test: `tests/research/test_runs.py` (update the exact-dict assertion at line 63)

**Interfaces:**
- Produces: `data_fingerprint(warehouse)` gains `"catalog_sha256"`: the hex SHA-256 of the raw bytes of `warehouse.paths.products`, or `None` if the file is absent. Existing keys are unchanged, so older records stay readable (`api/models.py` already types the field as `dict[str, Any]`).

- [ ] **Step 1: Write the failing tests:** `test_fingerprint_includes_catalog_hash` (the value equals `hashlib.sha256(products_bytes).hexdigest()`, and it changes when `write_products` writes different rows) and `test_fingerprint_catalog_absent_is_none`. Update the line-63 assertion to include the new key.
- [ ] **Step 2: Run** `uv run pytest tests/research/test_runs.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** with `hashlib.sha256(path.read_bytes())`.
- [ ] **Step 4: Run** the full gates. Expected: pass. The goldens are unaffected; the fingerprint is not part of the config hash.
- [ ] **Step 5: Commit** `feat(runs): record the catalog's sha256 in every run's data fingerprint`, and add a CLAUDE.md gotcha: the catalog is unversioned and mutates daily; compare `catalog_sha256` before calling two ML runs comparable.

### Task 6: Real-data acceptance (only after the owner confirms the baseline batch is done)

**Files:**
- Modify: `CLAUDE.md` (record the measured result in the status bullet)

- [ ] **Step 1: Confirm** with the owner that the baseline batch has finished. No step below runs before that.
- [ ] **Step 2: Run** `uv run pkmn live-ingest --root /Users/ml3787/pkmn-stock` from the worktree's environment. Expected: the stopgap days (2026-10-24, plus any later ones) are finalized; exit 0.
- [ ] **Step 3: Verify** each written day: its warehouse row count plus its quarantine row count equals the sum of `rows` over held groups in that day's manifest, and the distinct group ids in the partition match the held groups with `rows > 0`. Record the numbers.
- [ ] **Step 4: The owner** creates `data/config/notify.toml` with their topic. Run `uv run pkmn notify-test`, and the owner confirms the phone alert arrived.
- [ ] **Step 5: With owner approval,** reinstall the launchd job from the updated template, and confirm `launchctl list | grep pkmn` shows it. Record the date.
- [ ] **Step 6: Commit** the CLAUDE.md result update, with the gates first.
