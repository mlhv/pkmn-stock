"""Resumable capture of tcgcsv's live per-group `/prices` endpoints.

tcgcsv stopped publishing its daily price archive, so each update is saved
raw to `data/raw/live/<UTC date of update>/<groupId>.json.gz` plus a
`manifest.json`. The layout and manifest fields are shared with the earlier
throwaway stopgap script, so its directories are read as already held.

Capture only: turning a captured day into warehouse rows is a separate step.
Raw responses are kept byte-for-byte so a later parser change can be replayed.
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

from pkmn_quant.config import POKEMON_CATEGORY_ID, TCGCSV_BASE_URL, Paths

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
