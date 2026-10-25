"""Resumable capture of tcgcsv's live per-group `/prices` endpoints.

tcgcsv stopped publishing its daily price archive, so each update is saved
raw to `data/raw/live/<UTC date of update>/<groupId>.json.gz` plus a
`manifest.json`. The layout and manifest fields are shared with the earlier
throwaway stopgap script, so its directories are read as already held.

Raw responses are kept byte-for-byte so a later parser change can be replayed.
`finalize_day` then turns a captured day into warehouse rows, once and for all:
a day written from live data is final and nothing ever rewrites a partition.
"""

from __future__ import annotations

import gzip
import json
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Literal

import httpx
import polars as pl

from pkmn_quant.config import POKEMON_CATEGORY_ID, TCGCSV_BASE_URL, Paths
from pkmn_quant.data import tcgcsv
from pkmn_quant.data.ingest import (
    GAP_RESET_DAYS,
    IngestStats,
    refresh_products,
    tracked_groups,
)
from pkmn_quant.data.quality import apply_quality_gates
from pkmn_quant.data.transforms import prices_frame
from pkmn_quant.data.warehouse import Warehouse

GroupStatus = Literal["held", "absent", "failed"]

MAX_RETRIES = 3
_TS_FORMAT = "%Y-%m-%dT%H:%M:%S%z"
_MANIFEST = "manifest.json"


class LiveIngestError(Exception):
    """`last-updated.txt` could not be fetched or parsed."""


@dataclass(frozen=True)
class Capture:
    """Outcome of one capture run for one tcgcsv update.

    `moved_on`: `last-updated.txt` changed while we were fetching.
    `collision`: the day's manifest already names a different update (two
    updates mapped to one UTC day); nothing was fetched or written, so the
    two updates' files never mix.
    """

    day: date
    update_ts: datetime
    held: tuple[int, ...]
    absent: tuple[int, ...]
    failed: tuple[int, ...]
    moved_on: bool
    collision: bool

    @property
    def complete(self) -> bool:
        return not self.failed


def fetch_last_updated(client: httpx.Client) -> datetime:
    """The current update timestamp, as an aware datetime."""
    try:
        r = client.get(f"{TCGCSV_BASE_URL}/last-updated.txt")
        r.raise_for_status()
        return datetime.strptime(r.text.strip(), _TS_FORMAT)
    except (httpx.HTTPError, ValueError) as exc:
        raise LiveIngestError(f"cannot read last-updated.txt: {exc!r}") from exc


def update_day(ts: datetime) -> date:
    """The warehouse day an update is filed under: its UTC date."""
    return ts.astimezone(UTC).date()


def load_manifest(day_dir: Path) -> dict[str, Any]:
    path = day_dir / _MANIFEST
    if not path.exists():
        return {"groups": {}}
    manifest: dict[str, Any] = json.loads(path.read_text())
    manifest.setdefault("groups", {})
    return manifest


def _atomic_write(path: Path, data: bytes) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)


def save_manifest(day_dir: Path, manifest: dict[str, Any]) -> None:
    """Write the manifest via a temp file and rename, so a crash can't truncate it."""
    day_dir.mkdir(parents=True, exist_ok=True)
    text = json.dumps(manifest, indent=1, sort_keys=True) + "\n"
    _atomic_write(day_dir / _MANIFEST, text.encode())


def _fetch_group(
    client: httpx.Client, gid: int, day_dir: Path, update_ts: datetime
) -> dict[str, Any]:
    """One attempt. Returns the manifest record; a non-failed one has been persisted."""
    try:
        r = client.get(f"{TCGCSV_BASE_URL}/tcgplayer/{POKEMON_CATEGORY_ID}/{gid}/prices")
    except httpx.HTTPError as exc:
        return {"status": "failed", "error": repr(exc)[:200]}
    rec: dict[str, Any] = {
        "http": r.status_code,
        "last_modified": r.headers.get("last-modified"),
        "etag": r.headers.get("etag"),
        "fetched_at": datetime.now(UTC).isoformat(),
    }
    if r.status_code == 404:
        rec["status"] = "absent"
        return rec
    try:
        r.raise_for_status()
        payload = r.json()
        if payload["success"] is not True or not isinstance(payload["results"], list):
            raise ValueError("success is not true or results is not a list")
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
        return {**rec, "status": "failed", "error": repr(exc)[:200]}
    lm = r.headers.get("last-modified")
    if lm is not None:
        try:
            stamp = parsedate_to_datetime(lm)
        except (TypeError, ValueError):
            stamp = None
        if stamp is not None and stamp.tzinfo is not None and stamp > update_ts:
            return {**rec, "status": "failed", "error": f"newer than update: {lm}"}
    _atomic_write(day_dir / f"{gid}.json.gz", gzip.compress(r.content))
    return {**rec, "status": "held", "rows": len(payload["results"])}


def capture_update(
    client: httpx.Client,
    paths: Paths,
    group_ids: Sequence[int],
    *,
    retries: int = MAX_RETRIES,
    backoff_s: float = 2.0,
    pause_s: float = 0.2,
    sleep: Callable[[float], None] = time.sleep,
) -> Capture:
    """Fetch every group in `group_ids` not already held for the current update.

    Held groups (with their file on disk) and absent groups are skipped, so a
    rerun resumes. Failed groups retry with `sleep(backoff_s * attempt)` and
    are otherwise left for the next run. The manifest is saved after every
    group, so a crash loses at most the group in flight.
    """
    update_ts = fetch_last_updated(client)
    day = update_day(update_ts)
    day_dir = paths.raw_live / day.isoformat()
    manifest = load_manifest(day_dir)
    groups: dict[str, dict[str, Any]] = manifest["groups"]

    def split() -> tuple[tuple[int, ...], tuple[int, ...], tuple[int, ...]]:
        out: dict[str, list[int]] = {"held": [], "absent": [], "failed": []}
        for gid in group_ids:
            st = groups.get(str(gid), {}).get("status")
            if st in out:
                out[st].append(gid)
        return tuple(out["held"]), tuple(out["absent"]), tuple(out["failed"])

    named = manifest.get("last_updated")
    if named is not None and datetime.strptime(named, "%Y-%m-%dT%H:%M:%S%z") != update_ts:
        held, absent, failed = split()
        return Capture(day, update_ts, held, absent, failed, False, True)

    manifest["last_updated"] = update_ts.isoformat()
    fetched_any = False
    for gid in group_ids:
        prev = groups.get(str(gid), {})
        if prev.get("status") == "absent":
            continue
        if prev.get("status") == "held" and (day_dir / f"{gid}.json.gz").exists():
            continue
        if fetched_any:
            sleep(pause_s)
        fetched_any = True
        day_dir.mkdir(parents=True, exist_ok=True)
        for attempt in range(1, retries + 1):
            rec = _fetch_group(client, gid, day_dir, update_ts)
            if rec["status"] != "failed" or "newer than update" in rec.get("error", ""):
                break
            if attempt < retries:
                sleep(backoff_s * attempt)
        groups[str(gid)] = rec
        save_manifest(day_dir, manifest)

    after = fetch_last_updated(client)
    manifest["last_updated_after"] = after.isoformat()
    save_manifest(day_dir, manifest)
    held, absent, failed = split()
    return Capture(day, update_ts, held, absent, failed, after != update_ts, False)


def pending_days(paths: Paths) -> list[date]:
    """Captured days (a manifest on disk) not yet in the warehouse, ascending."""
    root = paths.raw_live
    if not root.exists():
        return []
    warehouse = Warehouse(paths)
    days: list[date] = []
    for d in root.iterdir():
        if not (d.is_dir() and (d / _MANIFEST).exists()):
            continue
        try:
            day = date.fromisoformat(d.name)
        except ValueError:
            continue
        if not warehouse.has_day(day):
            days.append(day)
    return sorted(days)


def finalize_day(paths: Paths, day: date) -> IngestStats:
    """Write one captured day's held files into the warehouse.

    Mirrors the archive path (`ingest_day`): same transform, same quality
    gates, same baseline rule. The baseline is the latest stored day before
    `day`, dropped when the gap exceeds GAP_RESET_DAYS. Never overwrites.
    """
    warehouse = Warehouse(paths)
    if warehouse.has_day(day):
        raise ValueError(f"{day} is already in the warehouse; a stored day is never rewritten")
    day_dir = paths.raw_live / day.isoformat()
    groups: dict[str, dict[str, Any]] = load_manifest(day_dir)["groups"]
    rows_by_group: dict[int, list[dict[str, Any]]] = {}
    for gid_s, rec in sorted(groups.items(), key=lambda kv: int(kv[0])):
        f = day_dir / f"{gid_s}.json.gz"
        if rec.get("status") == "held" and f.exists():
            rows_by_group[int(gid_s)] = json.loads(gzip.decompress(f.read_bytes()))["results"]
    prices = prices_frame(day, rows_by_group)
    previous: pl.DataFrame | None = None
    prior = [d for d in warehouse.stored_days() if d < day]
    if prior and (day - prior[-1]).days <= GAP_RESET_DAYS:
        previous = warehouse.load_day(prior[-1])
    clean, quarantined = apply_quality_gates(prices, previous)
    warehouse.write_prices(day, clean)
    warehouse.write_quarantine(day, quarantined)
    return IngestStats(day=day, rows_clean=clean.height, rows_quarantined=quarantined.height)


@dataclass(frozen=True)
class LiveIngestReport:
    """Outcome of one `run_live_ingest`.

    `capture` is None when the update's day was already stored (nothing was
    fetched). `partial` maps each written day that lacks some groups to those
    group ids. `collision`: the update's day holds a different update.
    """

    capture: Capture | None
    written: tuple[IngestStats, ...]
    partial: dict[date, tuple[int, ...]]
    collision: bool
    catalog_failures: tuple[int, ...]


def _missing(paths: Paths, day: date, group_ids: Sequence[int]) -> tuple[int, ...]:
    groups = load_manifest(paths.raw_live / day.isoformat())["groups"]
    return tuple(
        gid for gid in group_ids if groups.get(str(gid), {}).get("status") not in ("held", "absent")
    )


def run_live_ingest(
    paths: Paths,
    client: httpx.Client | None = None,
    *,
    sleep: Callable[[float], None] = time.sleep,
) -> LiveIngestReport:
    """Capture the current tcgcsv update and finalize every day that is ready.

    Ready: any older captured day (written even if partial, with the missing
    groups reported), plus the update's own day once it is complete and the
    feed has not moved on. The catalog is refreshed only when a day is written.
    """
    owns_client = client is None
    if client is None:
        client = tcgcsv.make_client()
    try:
        warehouse = Warehouse(paths)
        ts = fetch_last_updated(client)
        today = update_day(ts)
        if warehouse.has_day(today):
            named = load_manifest(paths.raw_live / today.isoformat()).get("last_updated")
            differs = named is not None and datetime.strptime(named, _TS_FORMAT) != ts
            return LiveIngestReport(None, (), {}, differs, ())
        groups = tcgcsv.fetch_groups(client)
        group_ids = [g.group_id for g in tracked_groups(groups)]
        capture = capture_update(client, paths, group_ids, sleep=sleep)
        to_write = [d for d in pending_days(paths) if d < capture.day]
        if capture.collision or (capture.complete and not capture.moved_on):
            to_write.append(capture.day)
        partial: dict[date, tuple[int, ...]] = {}
        for d in to_write:
            if missing := _missing(paths, d, group_ids):
                partial[d] = missing
        written: list[IngestStats] = []
        catalog_failures: tuple[int, ...] = ()
        if to_write:
            catalog_failures = refresh_products(client, warehouse, groups).failed_groups
            written = [finalize_day(paths, d) for d in sorted(to_write)]
        return LiveIngestReport(
            capture, tuple(written), partial, capture.collision, catalog_failures
        )
    finally:
        if owns_client:
            client.close()


def probe_archive(client: httpx.Client, day: date) -> bool:
    """True if tcgcsv serves the day's archive again. The body is never read."""
    try:
        with client.stream("GET", tcgcsv.archive_url(day)) as r:
            return r.status_code == 200
    except httpx.HTTPError:
        return False
