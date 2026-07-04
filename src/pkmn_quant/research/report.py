"""Markdown report for a walk-forward run: fold table + honest summary."""

from __future__ import annotations

from pkmn_quant.research.walkforward import WalkForwardResult


def render_markdown(result: WalkForwardResult, strategy_name: str) -> str:
    lines = [
        f"# Walk-forward report: {strategy_name}",
        "",
        "Out-of-sample segments only; the stitched curve is the honest track record.",
        "Note: Sharpe/Sortino are inflated by mark smoothing (thin markets,",
        "carry-forward marks) - compare strategies against each other and the",
        "buy-and-hold benchmark, not against equities numbers.",
        "",
        "## Folds",
        "",
        "| # | IS window | OOS window | params | IS ret | OOS ret |",
        "|---|-----------|------------|--------|--------|---------|",
    ]
    for i, f in enumerate(result.folds):
        lines.append(
            f"| {i} | {f.fold.is_start} .. {f.fold.is_end} "
            f"| {f.fold.oos_start} .. {f.fold.oos_end} "
            f"| {f.params} "
            f"| {f.is_summary['total_return']:.2%} "
            f"| {f.oos_summary['total_return']:.2%} |"
        )
    lines += ["", "## Summary", ""]
    for key, value in result.summary.items():
        lines.append(f"- {key}: {value:.4f}")
    return "\n".join(lines) + "\n"
