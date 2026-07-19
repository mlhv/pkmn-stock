"""Markdown report for a walk-forward run: fold table + honest summary."""

from __future__ import annotations

from pkmn_quant.research.stats import BootstrapCI
from pkmn_quant.research.walkforward import WalkForwardResult


def format_params(params: dict[str, float | int]) -> str:
    """Compact one-line params: floats to 4 significant digits, ints as-is."""
    if not params:
        return "-"
    parts = [f"{k}={v:.4g}" if isinstance(v, float) else f"{k}={v}" for k, v in params.items()]
    return ", ".join(parts)


def render_markdown(
    result: WalkForwardResult, strategy_name: str, ci: BootstrapCI | None = None
) -> str:
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
            f"| {format_params(f.params)} "
            f"| {f.is_summary['total_return']:.2%} "
            f"| {f.oos_summary['total_return']:.2%} |"
        )
    lines += ["", "## Summary", ""]
    for key, value in result.summary.items():
        lines.append(f"- {key}: {value:.4f}")
    if ci is not None:
        lines += [
            "",
            "## Rigor",
            "",
            f"- stitched OOS total return {ci.point:.2%}, "
            f"{ci.level:.0%} CI [{ci.lo:.2%}, {ci.hi:.2%}]",
            f"  (stationary block bootstrap: n_boot={ci.n_boot}, "
            f"mean block {ci.mean_block:g}d, seed {ci.seed})",
            "- CIs inherit the mark-smoothing Sharpe inflation noted above;",
            "  treat the band as optimistic, not gospel; fold-seam days",
            "  (mark-carryover, no liquidation costs) are resampled as",
            "  ordinary days by the bootstrap.",
        ]
    return "\n".join(lines) + "\n"
