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
}


def render_signals_markdown(report: SignalReport) -> str:
    lines = [
        f"# Signals: {report.strategy} — {report.as_of}",
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
        lines += [
            "| action | product | sub_type | qty | market | notional |",
            "|--------|---------|----------|-----|--------|----------|",
        ]
        lines += [
            f"| {r.action} | {r.name} | {r.sub_type} | {r.quantity}"
            f" | ${r.market_price:.2f} | ${r.notional:.2f} |"
            for r in report.recommendations
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
    return json.dumps(payload, indent=2) + "\n"
