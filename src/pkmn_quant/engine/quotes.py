"""Per-day quote fields the impact model needs beyond the market print."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Quote:
    """As-printed mid (median listing) and low (lowest listing) for one asset-day.

    None when the source row has no value; consumers treat missing fields as
    zero impact — never fill them in (spec: no invented numbers).
    """

    mid: float | None
    low: float | None
