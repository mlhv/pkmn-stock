"""Render docs/assets/oos_equity.png, the README hero chart.

Reads walk-forward artifacts under data/results/ (gitignored; needs a local
warehouse that has produced them) and plots out-of-sample equity as percent
return. Each strategy uses its latest local artifact, so the cost regime
differs per series and is named in the legend, mirroring the README results
table. Re-running flat-cost walkforwards to unify regimes would overwrite
the impact-on artifacts in the same result dirs, so the mix is deliberate.

Colors are categorical slots 1-6 (light mode) of the reference dataviz
palette, validated 2026-07-18 (adjacent CVD dE >= 9.1, normal-vision
dE >= 19.6). Three slots sit below 3:1 contrast on this surface; the
README's results table directly below the image is the required relief.

Usage: uv run --group viz python scripts/render_readme_chart.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import matplotlib
import polars as pl

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # (backend must be set first; ruff's E402 exempts matplotlib.use)

SURFACE = "#fcfcfb"
INK_PRIMARY = "#0b0b0b"
INK_SECONDARY = "#52514e"
INK_MUTED = "#898781"
GRID = "#e1e0d9"
BASELINE = "#c3c2b7"

SERIES: list[tuple[str, str, str]] = [
    (
        "data/results/buy-and-hold-sealed-2024-08-28-2026-06-30/equity.parquet",
        "buy-and-hold sealed, flat-cost (+151.1%)",
        "#2a78d6",
    ),
    (
        "data/results/wf-sealed-accumulation-2024-03-01-2026-06-30/stitched_equity.parquet",
        "sealed-accumulation, impact-on (−7.4%)",  # noqa: RUF001
        "#008300",
    ),
    (
        "data/results/wf-ml-ranker-2024-03-01-2026-06-30/stitched_equity.parquet",
        "ml-ranker, impact-on (−7.5%)",  # noqa: RUF001
        "#e87ba4",
    ),
    (
        "data/results/wf-dip-buyer-2024-03-01-2026-06-30/stitched_equity.parquet",
        "dip-buyer, flat-cost (−9.0%)",  # noqa: RUF001
        "#eda100",
    ),
    (
        "data/results/wf-cost-aware-reversion-2024-03-01-2026-06-30/stitched_equity.parquet",
        "cost-aware-reversion, flat-cost (−10.2%)",  # noqa: RUF001
        "#1baf7a",
    ),
    (
        "data/results/wf-xs-momentum-2024-03-01-2026-06-30/stitched_equity.parquet",
        "xs-momentum, flat-cost (−25.1%)",  # noqa: RUF001
        "#eb6834",
    ),
]

OUT = Path("docs/assets/oos_equity.png")


def main() -> int:
    missing = [p for p, _, _ in SERIES if not Path(p).exists()]
    if missing:
        for p in missing:
            print(f"missing artifact: {p}", file=sys.stderr)
        print("run from the repo root with a populated data/results/", file=sys.stderr)
        return 2

    fig, ax = plt.subplots(figsize=(10.0, 5.2), dpi=200)
    fig.set_facecolor(SURFACE)
    ax.set_facecolor(SURFACE)

    for path, label, color in SERIES:
        df = pl.read_parquet(path)
        pct = (df["equity"] / df["equity"][0] - 1.0) * 100.0
        ax.plot(df["date"], pct, color=color, linewidth=2.0, label=label)

    bench = pl.read_parquet(SERIES[0][0])
    end_pct = (bench["equity"][-1] / bench["equity"][0] - 1.0) * 100.0
    ax.annotate(
        "buy-and-hold sealed",
        xy=(bench["date"][-1], end_pct),
        xytext=(-8, 10),
        textcoords="offset points",
        ha="right",
        color=INK_PRIMARY,
        fontsize=9,
    )

    ax.axhline(0.0, color=BASELINE, linewidth=1.0, zorder=1)
    ax.grid(axis="y", color=GRID, linewidth=0.75)
    ax.set_axisbelow(True)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(BASELINE)
    ax.tick_params(colors=INK_MUTED, labelsize=9)
    ax.set_ylabel("out-of-sample return (%)", color=INK_MUTED, fontsize=9)
    ax.set_title(
        "Out-of-sample equity, 2024-08 to 2026-06 (walk-forward, stitched)",
        color=INK_PRIMARY,
        fontsize=12,
        loc="left",
        pad=12,
    )
    ax.legend(frameon=False, fontsize=8.5, labelcolor=INK_SECONDARY, loc="upper left")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT, facecolor=fig.get_facecolor(), bbox_inches="tight")
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
