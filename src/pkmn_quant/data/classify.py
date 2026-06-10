"""Sealed-vs-single classification for TCGplayer products."""

from __future__ import annotations

from typing import Literal

Kind = Literal["single", "sealed", "excluded"]


def classify_kind(rarity: str | None) -> Kind:
    """Classify a product by its TCGplayer rarity field.

    Singles always carry a rarity; sealed products (boxes, ETBs, collections)
    never do. Code cards are digital redemption codes, not tradeable assets.
    """
    if rarity is None:
        return "sealed"
    if rarity.strip().lower() == "code card":
        return "excluded"
    return "single"
