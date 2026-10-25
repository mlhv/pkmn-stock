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


def _warn(path: Path, msg: str) -> None:
    print(f"warning: {path}: {msg}", file=sys.stderr)


def _aware_iso(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        return datetime.fromisoformat(value).tzinfo is not None
    except ValueError:
        return False


def _iso_date(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


def load_state(root: Path) -> AlertState:
    """Total: never raises. Bad entries or fields are dropped, the rest kept.

    A dropped alert entry just re-fires once, the safe direction.
    """
    path = _path(root)
    if not path.exists():
        return AlertState()
    try:
        raw = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        _warn(path, f"ignoring unreadable state: {exc}")
        return AlertState()
    if not isinstance(raw, dict):
        _warn(path, "ignoring state of wrong shape")
        return AlertState()

    state = AlertState()
    dropped: list[str] = []
    active = raw.get("active", {})
    if isinstance(active, dict):
        for k, v in active.items():
            if _aware_iso(v):
                state.active[str(k)] = v
            else:
                dropped.append(f"active[{k}]")
    else:
        dropped.append("active")
    signals = raw.get("signals_as_of", {})
    if isinstance(signals, dict):
        for k, v in signals.items():
            if _iso_date(v):
                state.signals_as_of[str(k)] = v
            else:
                dropped.append(f"signals_as_of[{k}]")
    else:
        dropped.append("signals_as_of")
    probe = raw.get("archive_probe_on")
    if probe is None or _iso_date(probe):
        state.archive_probe_on = probe
    else:
        dropped.append("archive_probe_on")
    if dropped:
        _warn(path, "dropped invalid fields: " + ", ".join(dropped))
    return state


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
