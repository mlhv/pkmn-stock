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
    report = _report([sell])
    report = SignalReport(
        as_of=report.as_of,
        strategy=report.strategy,
        params=report.params,
        wf_summary=report.wf_summary,
        wf_run_dir=report.wf_run_dir,
        recommendations=[sell],
        portfolio_snapshot=snap,
    )
    md = render_signals_markdown(report)
    assert "## Portfolio" in md
    assert "$500.00" in md  # cash
    assert "+66.7%" in md  # exit gain line
