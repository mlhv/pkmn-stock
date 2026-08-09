"""Render a SignalReport as markdown (for stdout) and JSON (artifact)."""

from __future__ import annotations

import json
from dataclasses import asdict

from pkmn_quant.live.signals import SignalReport
from pkmn_quant.research.report import format_params

# One-line thesis per strategy (the "strategy reasoning" the spec attaches to
# recommendations; strategies themselves don't emit per-order rationales).
THESIS = {
    "sealed-accumulation": (
        "Sealed products crash post-release then grind up as supply dries;"
        " buy aged drawdowns, sell at a target multiple."
    ),
    "dip-buyer": (
        "Sharp one-week dips in singles may mean-revert; buy the dip, exit on"
        " time or profit target."
    ),
    "xs-momentum": (
        "Winners keep winning: hold the top trailing performers among singles,"
        " rebalance periodically."
    ),
    "cost-aware-reversion": (
        "Cards trading well below recent highs tend to revert; buy dips that clear"
        " ~12.75% round-trip cost hurdle, sell at profit target or time limit."
    ),
    "ml-ranker": "Gradient-boosted model ranks all products by predicted"
    " forward return from trailing features; hold the top names, retrain"
    " each rebalance from history only.",
    "ml-ranker-v2": "Gradient-boosted model ranks products by predicted"
    " forward return NET of round-trip cost, using friction/momentum-shape"
    " features and in-loop purged model selection; hold the top names,"
    " retrain each rebalance from history only.",
}


def render_signals_markdown(report: SignalReport) -> str:
    lines = [
        f"# Signals{' (PAPER)' if report.paper else ''}: {report.strategy} — {report.as_of}",
        "",
        f"Thesis: {THESIS.get(report.strategy, 'n/a')}",
        f"Params (last walk-forward fold): {format_params(report.params)}",
        f"Walk-forward record ({report.wf_run_dir}):",
    ]
    lines += [f"- {k}: {v:.4f}" for k, v in report.wf_summary.items()]
    lines.append("")
    if not report.recommendations:
        lines.append("No recommendations today.")
    else:
        # `exec` and `outlay` are the actionable pair: `market` is a print
        # derived from recent SALES and a buy cannot fill below the cheapest
        # current LISTING, so quoting only the print understates the cash
        # required (see Recommendation.exec_price).
        lines += [
            "| action | product | sub_type | qty | market | exec | outlay |",
            "|--------|---------|----------|-----|--------|------|--------|",
        ]
        lines += [
            f"| {r.action} | {r.name} | {r.sub_type} | {r.quantity}"
            f" | ${r.market_price:.2f} | ${r.exec_price:.2f} | ${r.exec_notional:.2f} |"
            for r in report.recommendations
        ]
        if any(r.exec_price > r.market_price for r in report.recommendations):
            lines += [
                "",
                "`exec` is max(market, lowest listing): tcgcsv's market price comes from"
                " recent SALES, and where it sits below the cheapest current listing a buy"
                " cannot fill there. `outlay` is qty x exec, before shipping, fees and any"
                " walk up the book.",
            ]
    if report.portfolio_snapshot is not None:
        snap = report.portfolio_snapshot
        lines += ["", "## Portfolio", ""]
        exits = [r for r in report.recommendations if r.action == "SELL"]
        exited_keys = {(r.product_id, r.sub_type) for r in exits}
        for r in exits:
            if r.avg_cost is not None and r.gain_pct is not None:
                suffix = f", basis ${r.avg_cost:.2f}, gain {r.gain_pct:+.1%}"
            elif r.avg_cost is not None:
                suffix = f", basis ${r.avg_cost:.2f}, gain n/a"
            else:
                suffix = ", basis n/a"
            lines.append(f"- EXIT {r.name}: {r.quantity} @ mark ${r.market_price:.2f}{suffix}")
        for p in snap.positions:
            if (p.product_id, p.sub_type) in exited_keys:
                continue  # already rendered as EXIT above; skip the conflicting HOLD line
            lines.append(
                f"- HOLD {p.name} ({p.sub_type}) x{p.quantity}: avg ${p.avg_cost:.2f},"
                f" mark ${p.mark:.2f}, unrealized ${p.unrealized_pnl:+.2f}"
            )
        lines += [
            f"- cash: ${snap.cash:.2f}",
            f"- realized P&L: ${snap.realized_pnl:+.2f}",
            f"- equity: ${snap.equity:.2f}",
        ]
    lines += [
        "",
        "Not financial advice; thin-market marks, ~12-15% round-trip costs.",
        "See docs/research-findings-2026-07.md for the honest track record.",
    ]
    return "\n".join(lines) + "\n"


def signals_to_json(report: SignalReport) -> str:
    payload = asdict(report)
    payload["as_of"] = report.as_of.isoformat()
    # asdict() serializes fields only, and the executable price is a property
    # (derived from `low`, never stored twice). Emit it explicitly so a JSON
    # consumer sees the same outlay the markdown does.
    for out, rec in zip(payload["recommendations"], report.recommendations, strict=True):
        out["exec_price"] = rec.exec_price
        out["exec_notional"] = rec.exec_notional
    return json.dumps(payload, indent=2) + "\n"
