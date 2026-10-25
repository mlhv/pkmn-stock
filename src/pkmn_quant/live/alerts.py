"""Rate-limited alert state for the unattended ``pkmn daily`` loop.

An alert key fires once, then at most every ``REPEAT_EVERY`` while it stays
active; ``resolve`` clears it and tells the caller to send a "recovered"
message. State persists in ``data/results/daily-state.json``.
"""

from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

STATE_FILE = "daily-state.json"
REPEAT_EVERY = timedelta(hours=24)
# Measured from 20:00 UTC on the newest stored day: the next update has been
# out about 24 hours and still isn't stored.
STALE_AFTER = timedelta(hours=48)
# A capture still incomplete on a run at or after 21:00 local is at risk.
AT_RISK_LOCAL_HOUR = 21


@dataclass
class AlertState:
    """The one deliberately mutable state object; helpers mutate it in place."""

    active: dict[str, str] = field(default_factory=dict)  # key -> ISO last_sent
    signals_as_of: dict[str, str] = field(default_factory=dict)  # real/paper -> ISO date
    archive_probe_on: str | None = None


def _path(root: Path) -> Path:
    return root / "data" / "results" / STATE_FILE


def load_state(root: Path) -> AlertState:
    path = _path(root)
    if not path.exists():
        return AlertState()
    try:
        raw = json.loads(path.read_text())
        active = raw["active"]
        signals = raw["signals_as_of"]
        probe = raw["archive_probe_on"]
        if not (
            isinstance(active, dict)
            and isinstance(signals, dict)
            and (probe is None or isinstance(probe, str))
            and all(isinstance(k, str) and isinstance(v, str) for k, v in active.items())
            and all(isinstance(k, str) and isinstance(v, str) for k, v in signals.items())
        ):
            raise ValueError("wrong shape")
        return AlertState(active=dict(active), signals_as_of=dict(signals), archive_probe_on=probe)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"warning: ignoring unreadable {path}: {exc}", file=sys.stderr)
        return AlertState()


def save_state(root: Path, state: AlertState) -> None:
    path = _path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(asdict(state), indent=1, sort_keys=True) + "\n")
    tmp.replace(path)


def should_alert(state: AlertState, key: str, now: datetime) -> bool:
    """True if ``key`` is new or last sent >= REPEAT_EVERY ago; records ``now`` then."""
    last = state.active.get(key)
    if last is not None and now - datetime.fromisoformat(last) < REPEAT_EVERY:
        return False
    state.active[key] = now.isoformat()
    return True


def resolve(state: AlertState, key: str) -> bool:
    """Clear ``key``; True if it was active (caller sends a "recovered" alert)."""
    return state.active.pop(key, None) is not None


def is_stale(newest_stored: date | None, now: datetime) -> bool:
    if newest_stored is None:
        return True
    return now > datetime.combine(newest_stored, time(20, 0), UTC) + STALE_AFTER


def capture_at_risk(capture_failed: bool, now_local: datetime) -> bool:
    return capture_failed and now_local.hour >= AT_RISK_LOCAL_HOUR
