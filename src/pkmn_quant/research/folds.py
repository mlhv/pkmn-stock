"""Rolling walk-forward windows: optimize in-sample, test out-of-sample."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta


@dataclass(frozen=True)
class Fold:
    is_start: date
    is_end: date
    oos_start: date
    oos_end: date


def make_folds(start: date, end: date, is_days: int, oos_days: int) -> list[Fold]:
    """Non-overlapping OOS segments; each fold's IS window precedes its OOS.

    Fold k: IS spans is_days calendar days starting at start + k*oos_days
    (both endpoints inclusive); OOS spans the next oos_days days.
    Folds are generated while the full OOS segment fits inside [start, end].
    """
    if is_days <= 0 or oos_days <= 0:
        raise ValueError("is_days and oos_days must be positive")
    folds: list[Fold] = []
    k = 0
    while True:
        is_start = start + timedelta(days=k * oos_days)
        is_end = is_start + timedelta(days=is_days - 1)
        oos_start = is_end + timedelta(days=1)
        oos_end = oos_start + timedelta(days=oos_days - 1)
        if oos_end > end:
            return folds
        folds.append(Fold(is_start=is_start, is_end=is_end, oos_start=oos_start, oos_end=oos_end))
        k += 1
