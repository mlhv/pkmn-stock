"""Purged chronological validation for in-loop model selection.

Forward-return labels are time-correlated: a random train/validation split
leaks regime information (and sklearn's early_stopping silently makes such
a split above 10,000 samples). This module provides the honest alternative:
validate on the most recent training dates, with an embargo of one label
horizon between train and validation so no label window spans the boundary,
and select from a small fixed grid by cross-sectional rank quality
(Spearman), which is what a ranking strategy actually needs.

Deterministic by construction: fixed grid order, strictly-greater
comparison (ties keep the earlier entry), random_state=0, and
early_stopping=False on every fit (all model construction goes through
_make_model so tests can pin the guards).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta

import polars as pl
from sklearn.ensemble import HistGradientBoostingRegressor


@dataclass(frozen=True)
class ModelConfig:
    max_iter: int
    learning_rate: float


DEFAULT_GRID: tuple[ModelConfig, ...] = (
    ModelConfig(100, 0.1),
    ModelConfig(200, 0.05),
    ModelConfig(50, 0.15),
    ModelConfig(300, 0.03),
)


def _make_model(config: ModelConfig, min_samples_leaf: int) -> HistGradientBoostingRegressor:
    """The only model constructor: leak guards live here, tests pin them."""
    return HistGradientBoostingRegressor(
        max_iter=config.max_iter,
        learning_rate=config.learning_rate,
        min_samples_leaf=min_samples_leaf,
        random_state=0,
        early_stopping=False,
    )


def purged_date_split(
    dates: Sequence[date], horizon_days: int, val_frac: float = 0.15
) -> tuple[list[date], list[date]]:
    """Most recent ~val_frac of distinct dates become validation; train
    dates end at least `horizon_days` before the first validation date, so
    no train label window overlaps validation. Empty train signals the
    caller to skip selection."""
    ds = sorted(set(dates))
    n_val = max(1, round(len(ds) * val_frac))
    val = ds[-n_val:]
    cutoff = val[0] - timedelta(days=horizon_days)
    train = [d for d in ds[:-n_val] if d <= cutoff]
    return train, val


def select_config(
    training: pl.DataFrame,
    feature_cols: Sequence[str],
    horizon_days: int,
    *,
    grid: tuple[ModelConfig, ...] = DEFAULT_GRID,
    min_samples_leaf: int = 20,
    min_val_dates: int = 2,
    min_train_rows: int = 50,
) -> ModelConfig:
    """Pick the grid config with the best mean per-date Spearman rank
    correlation on the purged validation split; grid[0] whenever the data
    is too thin to validate honestly (never crash, never a random split)."""
    from scipy.stats import spearmanr

    train_dates, val_dates = purged_date_split(training["date"].to_list(), horizon_days)
    if len(val_dates) < min_val_dates or not train_dates:
        return grid[0]
    tr = training.filter(pl.col("date").is_in(train_dates))
    va = training.filter(pl.col("date").is_in(val_dates))
    if tr.height < min_train_rows or va.height == 0:
        return grid[0]
    usable = [c for c in feature_cols if tr[c].null_count() < tr.height]
    if not usable:
        return grid[0]

    best = grid[0]
    best_score = float("-inf")
    for config in grid:
        model = _make_model(config, min_samples_leaf)
        model.fit(tr.select(usable).to_numpy(), tr["label"].to_numpy())
        preds = va.with_columns(pl.Series("_pred", model.predict(va.select(usable).to_numpy())))
        scores: list[float] = []
        for _, day_df in preds.group_by("date"):
            if day_df.height < 3:
                continue
            rho = spearmanr(day_df["_pred"].to_numpy(), day_df["label"].to_numpy()).statistic
            if rho == rho:  # not NaN (zero-variance cross-sections)
                scores.append(float(rho))
        if not scores:
            return grid[0]  # nothing scorable: selection is meaningless
        score = sum(scores) / len(scores)
        if score > best_score:  # strictly greater: ties keep the earlier entry
            best, best_score = config, score
    return best
