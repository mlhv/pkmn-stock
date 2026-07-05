"""Streamlit results explorer for pkmn_quant.

Run from the repo root (after at least one `pkmn walkforward` run):

    uv run --group dashboard streamlit run app/dashboard.py

Reads data/results/ artifacts and the Parquet warehouse. Demo tool only:
not type-checked, not imported by the package or tests.
"""

from __future__ import annotations

import json
from pathlib import Path

import polars as pl
import streamlit as st

ROOT = Path(".")
RESULTS = ROOT / "data" / "results"


def wf_runs() -> list[Path]:
    if not RESULTS.exists():
        return []
    return sorted(p for p in RESULTS.iterdir() if p.is_dir() and (p / "walkforward.json").exists())


def benchmark_runs() -> list[Path]:
    if not RESULTS.exists():
        return []
    return sorted(
        p
        for p in RESULTS.iterdir()
        if p.is_dir() and (p / "equity.parquet").exists() and not p.name.startswith("wf-")
    )


def signal_runs() -> list[Path]:
    if not RESULTS.exists():
        return []
    return sorted(
        p
        for p in RESULTS.iterdir()
        if p.is_dir() and p.name.startswith("signals-") and (p / "signals.md").exists()
    )


@st.cache_data
def load_prices() -> pl.DataFrame:
    return pl.read_parquet("data/warehouse/prices/**/*.parquet")


@st.cache_data
def load_products() -> pl.DataFrame:
    return pl.read_parquet("data/warehouse/products.parquet")


st.set_page_config(page_title="pkmn_quant", layout="wide")
st.title("pkmn_quant — results explorer")
st.caption(
    "Sharpe/Sortino inflated by mark smoothing (thin markets, carry-forward marks). "
    "Compare against the buy-and-hold benchmark, not equities."
)

tab_wf, tab_signals, tab_prices, tab_trades = st.tabs(
    ["Walk-forward", "Signals", "Prices", "Trades"]
)

with tab_wf:
    runs = wf_runs()
    if not runs:
        st.info("No walk-forward runs found. Run `uv run pkmn walkforward ...` first.")
    else:
        run_dir = st.selectbox("Run", runs, format_func=lambda p: p.name)
        wf = json.loads((run_dir / "walkforward.json").read_text())

        stitched = pl.read_parquet(run_dir / "stitched_equity.parquet")
        curve = stitched.rename({"equity": wf["strategy"]})
        bench = benchmark_runs()
        if bench:
            bench_dir = st.selectbox("Benchmark overlay", bench, format_func=lambda p: p.name)
            b = pl.read_parquet(bench_dir / "equity.parquet")
            # Rescale benchmark to the stitched curve's starting level and
            # restrict to the stitched date range for a fair visual overlay.
            lo, hi = stitched["date"].min(), stitched["date"].max()
            b = b.filter((pl.col("date") >= lo) & (pl.col("date") <= hi))
            if b.height > 0:
                scale = float(stitched["equity"][0]) / float(b["equity"][0])
                b = b.with_columns((pl.col("equity") * scale).alias("benchmark"))
                curve = curve.join(b.select("date", "benchmark"), on="date", how="left")
        st.line_chart(curve.to_pandas().set_index("date"))

        st.subheader("Summary (stitched OOS)")
        st.dataframe(
            pl.DataFrame(
                {"metric": list(wf["summary"]), "value": list(wf["summary"].values())}
            ).to_pandas(),
            hide_index=True,
        )

        st.subheader("Folds")
        fold_rows = [
            {
                "IS": f"{f['is_start']} .. {f['is_end']}",
                "OOS": f"{f['oos_start']} .. {f['oos_end']}",
                "params": ", ".join(f"{k}={v:.4g}" for k, v in f["params"].items()),
                "IS ret": f["is_summary"]["total_return"],
                "OOS ret": f["oos_summary"]["total_return"],
            }
            for f in wf["folds"]
        ]
        st.dataframe(pl.DataFrame(fold_rows).to_pandas(), hide_index=True)

with tab_signals:
    sruns = signal_runs()
    if not sruns:
        st.info("No signal runs found. Run `uv run pkmn signals ...` first.")
    else:
        sdir = st.selectbox("Signal run", sruns, format_func=lambda p: p.name)
        st.markdown((sdir / "signals.md").read_text())

with tab_prices:
    try:
        products = load_products()
        prices = load_prices()
    except (FileNotFoundError, pl.exceptions.ComputeError):
        # ComputeError: the prices glob expands to nothing (empty/missing dir).
        st.info("No warehouse found. Run `uv run pkmn ingest ...` first.")
    else:
        kind = st.radio("Kind", ["sealed", "single"], horizontal=True)
        subset = products.filter(pl.col("kind") == kind).sort("name")
        if subset.height == 0:
            st.info(f"No {kind} products in the warehouse.")
        else:
            # Select by product_id: names repeat across sets (5 "Haunter"s),
            # so name-keyed selection would silently chart the wrong card.
            labels = {
                int(r["product_id"]): f"{r['name']} (set {r['group_id']})"
                for r in subset.iter_rows(named=True)
            }
            pid = st.selectbox(
                "Product", subset["product_id"].to_list(), format_func=lambda i: labels[i]
            )
            history = (
                prices.filter(pl.col("product_id") == pid)
                .sort("date")
                .select("date", "sub_type", "market")
            )
            chart = history.pivot(on="sub_type", index="date", values="market")
            st.line_chart(chart.to_pandas().set_index("date"))

with tab_trades:
    ledgers = [p for p in benchmark_runs() if (p / "fills.parquet").exists()]
    if not ledgers:
        st.info("No trade ledgers found. `uv run pkmn backtest ...` writes fills.parquet.")
    else:
        ldir = st.selectbox("Run with fills", ledgers, format_func=lambda p: p.name)
        fills = pl.read_parquet(ldir / "fills.parquet").sort("day")
        st.dataframe(fills.to_pandas(), hide_index=True)
        st.caption(f"{fills.height} fills")
