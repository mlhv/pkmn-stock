from datetime import date
from itertools import pairwise

import pytest

from pkmn_quant.research.folds import Fold, make_folds


def test_basic_folds() -> None:
    folds = make_folds(start=date(2024, 1, 1), end=date(2024, 12, 31), is_days=180, oos_days=60)
    f0 = folds[0]
    assert f0 == Fold(
        is_start=date(2024, 1, 1),
        is_end=date(2024, 6, 28),
        oos_start=date(2024, 6, 29),
        oos_end=date(2024, 8, 27),
    )
    # folds step by oos_days; every OOS day is covered exactly once
    for a, b in pairwise(folds):
        assert (b.oos_start - a.oos_start).days == 60
    assert folds[-1].oos_end <= date(2024, 12, 31)


def test_no_fold_when_range_too_short() -> None:
    assert make_folds(date(2024, 1, 1), date(2024, 3, 1), is_days=180, oos_days=60) == []


def test_invalid_params_raise() -> None:
    with pytest.raises(ValueError):
        make_folds(date(2024, 1, 1), date(2024, 12, 31), is_days=0, oos_days=60)
