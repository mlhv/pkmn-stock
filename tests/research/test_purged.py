"""Purged chronological split + deterministic in-loop config selection."""

from datetime import date, timedelta

import numpy as np
import polars as pl

from pkmn_quant.research.purged import (
    DEFAULT_GRID,
    ModelConfig,
    _make_model,
    purged_date_split,
    select_config,
)

D0 = date(2025, 1, 1)


def _dates(n: int, step: int = 7) -> list[date]:
    return [D0 + timedelta(days=i * step) for i in range(n)]


def test_split_embargo_and_chronology() -> None:
    ds = _dates(20)  # weekly dates over ~19 weeks
    train, val = purged_date_split(ds, horizon_days=30)
    assert val == ds[-3:]  # round(20 * 0.15) = 3 most recent dates
    assert train  # embargo leaves something
    assert max(train) <= min(val) - timedelta(days=30)  # the embargo
    assert train == sorted(train) and val == sorted(val)
    # every train date is strictly older than every val date
    assert max(train) < min(val)


def test_split_degrades_on_tiny_input() -> None:
    train, val = purged_date_split(_dates(2), horizon_days=30)
    # 1 val date; embargo eats the lone earlier date -> empty train signals skip
    assert len(val) == 1
    assert train == []


def test_make_model_pins_leak_guards() -> None:
    m = _make_model(ModelConfig(100, 0.1), min_samples_leaf=20)
    assert m.early_stopping is False
    assert m.random_state == 0
    assert m.max_iter == 100 and m.learning_rate == 0.1


def _panel(n_dates: int, n_assets: int, noise: float, seed: int) -> pl.DataFrame:
    """Synthetic panel where feature f1 truly predicts label (plus noise)."""
    rng = np.random.default_rng(seed)
    rows = []
    for i in range(n_dates):
        d = D0 + timedelta(days=i * 7)
        f1 = rng.normal(0, 1, n_assets)
        f2 = rng.normal(0, 1, n_assets)
        label = f1 * 0.05 + rng.normal(0, noise, n_assets)
        for j in range(n_assets):
            rows.append({"date": d, "f1": f1[j], "f2": f2[j], "label": label[j]})
    return pl.DataFrame(rows)


def test_select_config_deterministic_and_from_grid() -> None:
    panel = _panel(30, 25, noise=0.01, seed=11)
    a = select_config(panel, ["f1", "f2"], horizon_days=7)
    b = select_config(panel, ["f1", "f2"], horizon_days=7)
    assert a == b
    assert a in DEFAULT_GRID


def test_select_config_falls_back_on_thin_validation() -> None:
    panel = _panel(3, 25, noise=0.01, seed=12)  # 1 val date < min_val_dates
    assert select_config(panel, ["f1", "f2"], horizon_days=7) == DEFAULT_GRID[0]


def test_select_config_falls_back_on_thin_training() -> None:
    panel = _panel(30, 1, noise=0.01, seed=13)  # ~26 train rows < min_train_rows
    assert select_config(panel, ["f1", "f2"], horizon_days=7) == DEFAULT_GRID[0]
