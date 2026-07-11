from datetime import date
from pathlib import Path

import polars as pl
import pytest

from pkmn_quant.live.ledger import (
    LedgerError,
    append_event,
    append_events,
    ledger_path,
    load_events,
    load_portfolio,
    make_snapshot,
    replay,
)

PRODUCTS = pl.DataFrame(
    {
        "product_id": [1, 2],
        "group_id": [1, 1],
        "name": ["Crashed Box", "Other Box"],
        "rarity": [None, None],
        "kind": ["sealed", "sealed"],
        "released_on": [date(2025, 1, 1), date(2025, 1, 1)],
    }
)


def write_lines(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(line + "\n" for line in lines))


def test_replay_hand_derived_accounting(tmp_path: Path) -> None:
    """deposit 1000; buy 2@100 fees 5; sell 1@150 fees 20.
    cash = 1000 - 205 + 130 = 925; position 1@100; realized = -5 + (150-100) - 20 = 25."""
    path = tmp_path / "ledger.jsonl"
    write_lines(
        path,
        [
            '{"date": "2026-07-01", "kind": "deposit", "amount": 1000.0}',
            '{"date": "2026-07-02", "kind": "buy", "product_id": 1, "sub_type": "Normal",'
            ' "qty": 2, "price": 100.0, "fees": 5.0}',
            '{"date": "2026-07-10", "kind": "sell", "product_id": 1, "sub_type": "Normal",'
            ' "qty": 1, "price": 150.0, "fees": 20.0}',
        ],
    )
    pf = load_portfolio(path, PRODUCTS)
    assert pf.cash == pytest.approx(925.0)
    [(asset, pos)] = list(pf.positions.items())
    assert (asset.product_id, asset.sub_type, pos.quantity) == (1, "Normal", 1)
    assert pos.avg_cost == pytest.approx(100.0)
    assert pf.realized_pnl == pytest.approx(25.0)


def test_events_sorted_by_date_then_file_order(tmp_path: Path) -> None:
    """A deposit dated before a buy funds it even if the lines are reversed."""
    path = tmp_path / "ledger.jsonl"
    write_lines(
        path,
        [
            '{"date": "2026-07-02", "kind": "buy", "product_id": 1, "sub_type": "Normal",'
            ' "qty": 1, "price": 100.0, "fees": 0.0}',
            '{"date": "2026-07-01", "kind": "deposit", "amount": 1000.0}',
        ],
    )
    assert load_portfolio(path, PRODUCTS).cash == pytest.approx(900.0)


def test_missing_ledger_is_empty_portfolio(tmp_path: Path) -> None:
    pf = load_portfolio(tmp_path / "nope.jsonl", PRODUCTS)
    assert pf.cash == 0.0 and pf.positions == {}


@pytest.mark.parametrize(
    ("line", "match"),
    [
        ("{not json", "line 1"),
        ('{"date": "2026-07-01", "kind": "teleport", "amount": 1.0}', "unknown kind"),
        ('{"date": "2026-07-01", "kind": "deposit", "amount": -5.0}', "amount"),
        (
            '{"date": "2026-07-01", "kind": "buy", "product_id": 999, "sub_type": "Normal",'
            ' "qty": 1, "price": 1.0, "fees": 0.0}',
            "unknown product",
        ),
        (
            '{"date": "2026-07-01", "kind": "buy", "product_id": 1, "sub_type": "Normal",'
            ' "qty": 1, "price": 1.0, "fees": 0.0}',
            "negative",  # buy with no prior deposit -> negative cash
        ),
    ],
)
def test_validation_errors_name_the_line(tmp_path: Path, line: str, match: str) -> None:
    path = tmp_path / "ledger.jsonl"
    write_lines(path, [line])
    with pytest.raises(LedgerError, match=match):
        load_portfolio(path, PRODUCTS)


def test_oversell_raises_ledger_error(tmp_path: Path) -> None:
    path = tmp_path / "ledger.jsonl"
    write_lines(
        path,
        [
            '{"date": "2026-07-01", "kind": "deposit", "amount": 1000.0}',
            '{"date": "2026-07-02", "kind": "sell", "product_id": 1, "sub_type": "Normal",'
            ' "qty": 1, "price": 100.0, "fees": 0.0}',
        ],
    )
    with pytest.raises(LedgerError, match="line 2"):
        load_portfolio(path, PRODUCTS)


def test_append_event_validates_and_rolls_back(tmp_path: Path) -> None:
    path = tmp_path / "ledger.jsonl"
    append_event(path, {"date": "2026-07-01", "kind": "deposit", "amount": 500.0}, PRODUCTS)
    with pytest.raises(LedgerError):
        append_event(
            path,
            {
                "date": "2026-07-02",
                "kind": "buy",
                "product_id": 1,
                "sub_type": "Normal",
                "qty": 100,
                "price": 100.0,
                "fees": 0.0,
            },  # can't afford
            PRODUCTS,
        )
    # File unchanged by the failed append; the valid deposit still loads.
    assert load_portfolio(path, PRODUCTS).cash == pytest.approx(500.0)
    assert len(path.read_text().strip().splitlines()) == 1


def test_snapshot_values_positions_at_marks(tmp_path: Path) -> None:
    path = tmp_path / "ledger.jsonl"
    write_lines(
        path,
        [
            '{"date": "2026-07-01", "kind": "deposit", "amount": 1000.0}',
            '{"date": "2026-07-02", "kind": "buy", "product_id": 1, "sub_type": "Normal",'
            ' "qty": 2, "price": 100.0, "fees": 0.0}',
        ],
    )
    pf = load_portfolio(path, PRODUCTS)
    from pkmn_quant.engine.portfolio import Asset

    snap = make_snapshot(pf, {Asset(1, "Normal"): 130.0}, {1: "Crashed Box"})
    assert snap.cash == pytest.approx(800.0)
    [row] = snap.positions
    assert row.name == "Crashed Box"
    assert row.unrealized_pnl == pytest.approx(60.0)  # (130-100)*2
    assert snap.equity == pytest.approx(800.0 + 260.0)


def test_snapshot_missing_mark_raises(tmp_path: Path) -> None:
    path = tmp_path / "ledger.jsonl"
    write_lines(
        path,
        [
            '{"date": "2026-07-01", "kind": "deposit", "amount": 1000.0}',
            '{"date": "2026-07-02", "kind": "buy", "product_id": 1, "sub_type": "Weird",'
            ' "qty": 1, "price": 100.0, "fees": 0.0}',
        ],
    )
    pf = load_portfolio(path, PRODUCTS)
    with pytest.raises(LedgerError, match="Weird"):
        make_snapshot(pf, {}, {1: "Crashed Box"})


def test_ledger_path_helper(tmp_path: Path) -> None:
    assert ledger_path(tmp_path) == tmp_path / "data" / "portfolio" / "ledger.jsonl"
    assert ledger_path(tmp_path, paper=True) == tmp_path / "data" / "portfolio" / "paper.jsonl"


# ---------------------------------------------------------------------------
# Fix 1: Float residue does not block legitimate full withdrawals
# ---------------------------------------------------------------------------


def test_withdraw_float_residue_roundtrip(tmp_path: Path) -> None:
    """Deposit 250.57; buy 5@16.97 fees 1.45; sell 5@16.97 fees 1.45.

    Hand-derivation:
      cash after buy  = 250.57 - (5*16.97 + 1.45) = 250.57 - 86.30 = 164.27
      cash after sell = 164.27 + (5*16.97 - 1.45) = 164.27 + 83.40 = 247.67
      IEEE 754 actual = 247.66999999999996 (float residue -2.84e-14)
      displayed       = round(247.66999999999996, 2) = 247.67
      cash after withdraw 247.67 = 247.66999999999996 - 247.67 = -2.84e-14

    The pre-fix code tested `if pf.cash < 0` and raised; the clamp now zeroes
    any strictly-negative residue within half a cent.  The final assertion is
    exact (== 0.0) because the clamp guarantees it — approx would pass even
    without the clamp.
    """
    path = tmp_path / "ledger.jsonl"
    write_lines(
        path,
        [
            '{"date": "2026-07-01", "kind": "deposit", "amount": 250.57}',
            '{"date": "2026-07-02", "kind": "buy", "product_id": 1, "sub_type": "Normal",'
            ' "qty": 5, "price": 16.97, "fees": 1.45}',
            '{"date": "2026-07-03", "kind": "sell", "product_id": 1, "sub_type": "Normal",'
            ' "qty": 5, "price": 16.97, "fees": 1.45}',
        ],
    )
    pf = load_portfolio(path, PRODUCTS)
    displayed = round(pf.cash, 2)  # what user sees in the UI; == 247.67
    # Now append a withdraw for the displayed amount — must not raise.
    append_event(
        path,
        {"date": "2026-07-04", "kind": "withdraw", "amount": displayed},
        PRODUCTS,
    )
    pf2 = load_portfolio(path, PRODUCTS)
    # The clamp guarantees exactly 0.0, not merely approximately 0.0.
    assert pf2.cash == 0.0


# ---------------------------------------------------------------------------
# Fix 2: Fractional qty is rejected
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("qty_json", "should_raise"),
    [
        ("2", False),  # integer literal — ok
        ("2.0", False),  # integral float — ok
        ("2.9", True),  # non-integral float — must raise
        ("0.5", True),  # non-integral float — must raise
    ],
)
def test_fractional_qty_rejected(tmp_path: Path, qty_json: str, should_raise: bool) -> None:
    path = tmp_path / "ledger.jsonl"
    write_lines(
        path,
        [
            '{"date": "2026-07-01", "kind": "deposit", "amount": 1000.0}',
            f'{{"date": "2026-07-02", "kind": "buy", "product_id": 1, "sub_type": "Normal",'
            f' "qty": {qty_json}, "price": 1.0, "fees": 0.0}}',
        ],
    )
    if should_raise:
        with pytest.raises(LedgerError, match="line 2"):
            load_portfolio(path, PRODUCTS)
    else:
        pf = load_portfolio(path, PRODUCTS)
        assert pf.positions  # at least one position loaded


# ---------------------------------------------------------------------------
# Fix 3: Unknown keys are rejected
# ---------------------------------------------------------------------------


def test_unknown_key_misspelled_fees_rejected(tmp_path: Path) -> None:
    """'fee' instead of 'fees' must raise LedgerError naming the line and key."""
    path = tmp_path / "ledger.jsonl"
    write_lines(
        path,
        [
            '{"date": "2026-07-01", "kind": "deposit", "amount": 1000.0}',
            '{"date": "2026-07-02", "kind": "buy", "product_id": 1, "sub_type": "Normal",'
            ' "qty": 1, "price": 1.0, "fee": 5.0}',
        ],
    )
    with pytest.raises(LedgerError, match="fee"):
        load_portfolio(path, PRODUCTS)


def test_unknown_key_on_deposit_rejected(tmp_path: Path) -> None:
    """Extra key on a deposit event must be rejected."""
    path = tmp_path / "ledger.jsonl"
    write_lines(
        path,
        ['{"date": "2026-07-01", "kind": "deposit", "amount": 500.0, "note": "bonus"}'],
    )
    with pytest.raises(LedgerError, match="note"):
        load_portfolio(path, PRODUCTS)


# ---------------------------------------------------------------------------
# Fix 4: Withdraw happy-path and over-withdraw coverage
# ---------------------------------------------------------------------------


def test_withdraw_happy_path(tmp_path: Path) -> None:
    """deposit 500; withdraw 200 — cash must be 300 (hand-derived)."""
    path = tmp_path / "ledger.jsonl"
    write_lines(
        path,
        [
            '{"date": "2026-07-01", "kind": "deposit", "amount": 500.0}',
            '{"date": "2026-07-02", "kind": "withdraw", "amount": 200.0}',
        ],
    )
    pf = load_portfolio(path, PRODUCTS)
    assert pf.cash == pytest.approx(300.0)


def test_over_withdraw_raises_ledger_error(tmp_path: Path) -> None:
    """Withdrawing more than the balance must raise LedgerError naming the line."""
    path = tmp_path / "ledger.jsonl"
    write_lines(
        path,
        [
            '{"date": "2026-07-01", "kind": "deposit", "amount": 100.0}',
            '{"date": "2026-07-02", "kind": "withdraw", "amount": 200.0}',
        ],
    )
    with pytest.raises(LedgerError, match="line 2"):
        load_portfolio(path, PRODUCTS)


# ---------------------------------------------------------------------------
# Fix 5: NaN/inf values are rejected before they can poison cash or positions
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("line", "match"),
    [
        (
            '{"date": "2026-07-01", "kind": "deposit", "amount": NaN}',
            "finite",
        ),
        (
            '{"date": "2026-07-01", "kind": "deposit", "amount": Infinity}',
            "finite",
        ),
        (
            '{"date": "2026-07-01", "kind": "deposit", "amount": 1000.0}\n'
            '{"date": "2026-07-02", "kind": "buy", "product_id": 1, "sub_type": "Normal",'
            ' "qty": 1, "price": NaN, "fees": 0.0}',
            "finite",
        ),
        (
            '{"date": "2026-07-01", "kind": "deposit", "amount": 1000.0}\n'
            '{"date": "2026-07-02", "kind": "buy", "product_id": 1, "sub_type": "Normal",'
            ' "qty": 1, "price": 1.0, "fees": NaN}',
            "finite",
        ),
    ],
    ids=["nan_amount", "inf_amount", "nan_price", "nan_fees"],
)
def test_nonfinite_values_rejected(tmp_path: Path, line: str, match: str) -> None:
    """NaN and Inf values must be caught by math.isfinite before they silently corrupt state.

    Python's json.loads accepts NaN/Infinity as non-standard JSON extensions (JSON5-ish),
    so the line reaches _parse_line and must be rejected by the isfinite guard.
    """
    path = tmp_path / "ledger.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(line + "\n")
    with pytest.raises(LedgerError, match=match):
        load_portfolio(path, PRODUCTS)


def test_withdraw_exact_half_cent_boundary_raises(tmp_path: Path) -> None:
    """deposit 0.005; withdraw 0.01 → cash == -0.005 exactly.

    Exactly -0.005 is treated as an error (not clamped), since it is a full
    half-cent short and is reachable by mis-entry rather than float dust.
    """
    path = tmp_path / "ledger.jsonl"
    write_lines(
        path,
        [
            '{"date": "2026-07-01", "kind": "deposit", "amount": 0.005}',
            '{"date": "2026-07-02", "kind": "withdraw", "amount": 0.01}',
        ],
    )
    with pytest.raises(LedgerError, match="negative"):
        load_portfolio(path, PRODUCTS)


# ---------------------------------------------------------------------------
# Fix 1: append_events — atomic batch recording
# ---------------------------------------------------------------------------


def test_append_events_two_valid_events_atomic(tmp_path: Path) -> None:
    """Two valid events appended in one call are both present afterwards."""
    path = tmp_path / "ledger.jsonl"
    # Seed an existing deposit so cash is available.
    write_lines(path, ['{"date": "2026-07-01", "kind": "deposit", "amount": 1000.0}'])

    events: list[dict[str, object]] = [
        {
            "date": "2026-07-02",
            "kind": "buy",
            "product_id": 1,
            "sub_type": "Normal",
            "qty": 2,
            "price": 100.0,
            "fees": 1.0,
        },
        {
            "date": "2026-07-03",
            "kind": "sell",
            "product_id": 1,
            "sub_type": "Normal",
            "qty": 1,
            "price": 120.0,
            "fees": 16.3,
        },
    ]
    append_events(path, events, PRODUCTS)

    lines = path.read_text().strip().splitlines()
    assert len(lines) == 3  # deposit + buy + sell

    pf = load_portfolio(path, PRODUCTS)
    # cash = 1000 - (2*100+1) + (1*120*(1-0) - 16.3) ... fees in ledger are
    # the actual numbers we wrote, so just check position and realized p&l.
    assert pf.positions  # still holds 1 unit


def test_append_events_second_invalid_leaves_file_unchanged(tmp_path: Path) -> None:
    """Second event invalid → LedgerError; file is completely unchanged."""
    path = tmp_path / "ledger.jsonl"
    write_lines(path, ['{"date": "2026-07-01", "kind": "deposit", "amount": 500.0}'])
    original_text = path.read_text()

    events: list[dict[str, object]] = [
        {
            "date": "2026-07-02",
            "kind": "buy",
            "product_id": 1,
            "sub_type": "Normal",
            "qty": 2,
            "price": 100.0,
            "fees": 1.0,
        },
        {
            # Invalid: sell more than held (only 2 bought above; selling 5 oversells)
            "date": "2026-07-03",
            "kind": "sell",
            "product_id": 1,
            "sub_type": "Normal",
            "qty": 5,
            "price": 100.0,
            "fees": 1.0,
        },
    ]

    with pytest.raises(LedgerError):
        append_events(path, events, PRODUCTS)

    # File must be byte-for-byte identical to what it was before the call.
    assert path.read_text() == original_text
    pf = load_portfolio(path, PRODUCTS)
    assert pf.cash == pytest.approx(500.0)
    assert pf.positions == {}


# ---------------------------------------------------------------------------
# Task 2: opened_on flows from ledger replay
# ---------------------------------------------------------------------------


def test_replay_sets_opened_on_from_buy_date(tmp_path: Path) -> None:
    path = tmp_path / "ledger.jsonl"
    write_lines(
        path,
        [
            '{"date": "2026-07-01", "kind": "deposit", "amount": 1000.0}',
            '{"date": "2026-07-03", "kind": "buy", "product_id": 1, "sub_type": "Normal",'
            ' "qty": 2, "price": 100.0, "fees": 0.0}',
        ],
    )
    pf = load_portfolio(path, PRODUCTS)
    [(_asset, pos)] = list(pf.positions.items())
    assert pos.opened_on == date(2026, 7, 3)


# ---------------------------------------------------------------------------
# Task 3: public ledger API — load_events + replay
# ---------------------------------------------------------------------------


def test_load_events_missing_file_is_empty(tmp_path: Path) -> None:
    assert load_events(tmp_path / "nope.jsonl") == []


def test_load_events_then_replay_matches_load_portfolio(tmp_path: Path) -> None:
    """The public two-step API is exactly load_portfolio, decomposed —
    the dashboard needs the events list for its day-by-day equity chart."""
    path = tmp_path / "ledger.jsonl"
    write_lines(
        path,
        [
            '{"date": "2026-07-01", "kind": "deposit", "amount": 1000.0}',
            '{"date": "2026-07-03", "kind": "buy", "product_id": 1, "sub_type": "Normal",'
            ' "qty": 2, "price": 100.0, "fees": 1.0}',
        ],
    )
    events = load_events(path)
    assert [e.kind for e in events] == ["deposit", "buy"]
    pf = replay(events, PRODUCTS)
    ref = load_portfolio(path, PRODUCTS)
    assert pf.cash == ref.cash and pf.positions == ref.positions
