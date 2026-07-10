import json
from datetime import date

from pkmn_quant.live.report import THESIS, render_signals_markdown, signals_to_json
from pkmn_quant.live.signals import Recommendation, SignalReport
from pkmn_quant.research.registry import REGISTRY


def _report(recs: list[Recommendation]) -> SignalReport:
    return SignalReport(
        as_of=date(2026, 6, 30),
        strategy="sealed-accumulation",
        params={"min_drawdown": 0.25, "take_profit": 1.5, "min_age_days": 60},
        wf_summary={"stitched_total_return": 0.136, "overfitting_gap": 0.0476},
        wf_run_dir="data/results/wf-sealed-accumulation-2024-03-01-2026-06-30",
        recommendations=recs,
    )


REC = Recommendation(
    action="BUY",
    product_id=1,
    sub_type="Normal",
    name="Crashed Box",
    quantity=2,
    market_price=100.0,
    notional=200.0,
)


def test_markdown_contains_recommendation_and_wf_record() -> None:
    md = render_signals_markdown(_report([REC]))
    assert "sealed-accumulation" in md
    assert "2026-06-30" in md
    assert "Crashed Box" in md and "BUY" in md and "$200.00" in md
    assert "stitched_total_return" in md  # OOS record travels with the signal
    assert "min_drawdown=0.25" in md
    assert "Thesis:" in md and "supply dries" in md  # strategy reasoning line


def test_thesis_covers_registry() -> None:
    """Every tunable strategy must have a thesis line; a new registry entry
    without one would silently render 'Thesis: n/a'."""
    assert set(THESIS) == set(REGISTRY)


def test_markdown_no_recommendations() -> None:
    md = render_signals_markdown(_report([]))
    assert "No recommendations" in md


def test_json_round_trips() -> None:
    raw = json.loads(signals_to_json(_report([REC])))
    assert raw["as_of"] == "2026-06-30"
    assert raw["strategy"] == "sealed-accumulation"
    assert raw["recommendations"][0]["name"] == "Crashed Box"
    assert raw["recommendations"][0]["notional"] == 200.0
    assert raw["wf_summary"]["overfitting_gap"] == 0.0476


def test_markdown_renders_portfolio_section_and_exits() -> None:
    from pkmn_quant.live.ledger import PositionView, Snapshot

    sell = Recommendation(
        action="SELL",
        product_id=1,
        sub_type="Normal",
        name="Crashed Box",
        quantity=2,
        market_price=100.0,
        notional=200.0,
        avg_cost=60.0,
        gain_pct=100.0 / 60.0 - 1.0,
    )
    snap = Snapshot(
        cash=500.0,
        realized_pnl=25.0,
        equity=700.0,
        positions=[PositionView(1, "Normal", "Crashed Box", 2, 60.0, 100.0, 80.0)],
    )
    report = SignalReport(
        as_of=date(2026, 6, 30),
        strategy="sealed-accumulation",
        params={"min_drawdown": 0.25, "take_profit": 1.5, "min_age_days": 60},
        wf_summary={"stitched_total_return": 0.136, "overfitting_gap": 0.0476},
        wf_run_dir="data/results/wf-sealed-accumulation-2024-03-01-2026-06-30",
        recommendations=[sell],
        portfolio_snapshot=snap,
    )
    md = render_signals_markdown(report)
    assert "## Portfolio" in md
    assert "$500.00" in md  # cash
    assert "+66.7%" in md  # exit gain line
    # The exited position must NOT also appear as a HOLD line
    assert "EXIT Crashed Box" in md
    assert "HOLD Crashed Box" not in md


def test_markdown_portfolio_exited_not_held_non_exited_is_held() -> None:
    """An exited position renders only as EXIT; a non-exited position renders as HOLD."""
    from pkmn_quant.live.ledger import PositionView, Snapshot

    sell = Recommendation(
        action="SELL",
        product_id=1,
        sub_type="Normal",
        name="Crashed Box",
        quantity=2,
        market_price=100.0,
        notional=200.0,
        avg_cost=60.0,
        gain_pct=100.0 / 60.0 - 1.0,
    )
    snap = Snapshot(
        cash=200.0,
        realized_pnl=10.0,
        equity=650.0,
        positions=[
            PositionView(1, "Normal", "Crashed Box", 2, 60.0, 100.0, 80.0),  # being exited
            PositionView(2, "Normal", "Held Item", 3, 50.0, 75.0, 75.0),  # still held
        ],
    )
    report = SignalReport(
        as_of=date(2026, 6, 30),
        strategy="sealed-accumulation",
        params={"min_drawdown": 0.25, "take_profit": 1.5, "min_age_days": 60},
        wf_summary={"stitched_total_return": 0.136, "overfitting_gap": 0.0476},
        wf_run_dir="data/results/wf-sealed-accumulation-2024-03-01-2026-06-30",
        recommendations=[sell],
        portfolio_snapshot=snap,
    )
    md = render_signals_markdown(report)
    assert "EXIT Crashed Box" in md
    assert "HOLD Crashed Box" not in md  # exited position suppressed from HOLD
    assert "HOLD Held Item" in md  # non-exited position rendered as HOLD


def test_markdown_exit_renders_without_avg_cost() -> None:
    """A SELL recommendation with avg_cost=None must render without crashing."""
    from pkmn_quant.live.ledger import Snapshot

    sell = Recommendation(
        action="SELL",
        product_id=1,
        sub_type="Normal",
        name="Mystery Box",
        quantity=1,
        market_price=50.0,
        notional=50.0,
        avg_cost=None,
        gain_pct=None,
    )
    snap = Snapshot(cash=100.0, realized_pnl=0.0, equity=150.0, positions=[])
    report = SignalReport(
        as_of=date(2026, 6, 30),
        strategy="sealed-accumulation",
        params={"min_drawdown": 0.25, "take_profit": 1.5, "min_age_days": 60},
        wf_summary={"stitched_total_return": 0.136, "overfitting_gap": 0.0476},
        wf_run_dir="data/results/wf-sealed-accumulation-2024-03-01-2026-06-30",
        recommendations=[sell],
        portfolio_snapshot=snap,
    )
    md = render_signals_markdown(report)
    assert "EXIT Mystery Box" in md
    assert "basis n/a" in md  # fallback rendered instead of crashing
