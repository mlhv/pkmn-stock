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

import pkmn_quant.live.ledger as ledger_mod
from pkmn_quant.config import Paths
from pkmn_quant.data.warehouse import Warehouse
from pkmn_quant.engine.data import MarketData
from pkmn_quant.live.ledger import LedgerError, ledger_path, make_snapshot

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

tab_wf, tab_signals, tab_prices, tab_trades, tab_portfolio = st.tabs(
    ["Walk-forward", "Signals", "Prices", "Trades", "Portfolio"]
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

with tab_portfolio:
    # Alerts strip: recent daily runs, newest first.
    daily_dirs = sorted(RESULTS.glob("daily-*/daily.json"), reverse=True)
    if not daily_dirs:
        st.caption("No daily runs yet — schedule `pkmn daily` (see README).")
    else:
        st.subheader("Daily runs")
        for meta_path in daily_dirs[:14]:
            meta = json.loads(meta_path.read_text())
            actionable = (meta.get("n_buys", 0) + meta.get("n_sells", 0)) > 0
            failed = meta.get("status") != "ok"
            if failed and meta.get("as_of") is None:
                label = f"🔴 {meta.get('date')} — FAILED: {meta.get('error')}"
            elif actionable:
                suffix = " — ingest problem, prices may be stale" if failed else ""
                label = (
                    f"🟡 {meta.get('date')} — {meta['n_buys']} buys,"
                    f" {meta['n_sells']} sells ({meta.get('strategy')}){suffix}"
                )
            else:
                suffix = " — ingest problem" if failed else ""
                label = f"⚪ {meta.get('date')} — nothing to do{suffix}"
            with st.expander(label, expanded=False):
                md = meta_path.parent / "signals.md"
                if md.exists():
                    st.markdown(md.read_text())
                else:
                    st.write(meta)

    lp = ledger_path(ROOT)
    if not lp.exists():
        st.info("No ledger yet. Record trades with `uv run pkmn portfolio buy ...`.")
    else:
        warehouse = Warehouse(Paths(root=ROOT))
        products = load_products()
        # Parse once; reuse for both the snapshot and the equity chart.
        events = ledger_mod._parse_lines(lp.read_text().splitlines())
        try:
            pf = ledger_mod._replay(events, products)
            days = warehouse.stored_days()
            latest = days[-1]
            market = MarketData.from_warehouse(warehouse, latest, latest, warmup_days=365)
            names = {
                int(r["product_id"]): str(r["name"])
                for r in products.select("product_id", "name").iter_rows(named=True)
            }
            snap = make_snapshot(pf, market.marks_on(latest), names)
        except (LedgerError, IndexError) as exc:
            st.error(f"cannot value portfolio: {exc}")
        else:
            c1, c2, c3 = st.columns(3)
            c1.metric("Equity", f"${snap.equity:,.2f}")
            c2.metric("Cash", f"${snap.cash:,.2f}")
            c3.metric("Realized P&L", f"${snap.realized_pnl:+,.2f}")
            if snap.positions:
                st.dataframe(
                    pl.DataFrame(
                        [
                            {
                                "product": p.name,
                                "sub_type": p.sub_type,
                                "qty": p.quantity,
                                "avg cost": p.avg_cost,
                                "mark": p.mark,
                                "unrealized": p.unrealized_pnl,
                            }
                            for p in snap.positions
                        ]
                    ).to_pandas(),
                    hide_index=True,
                )

            # Equity over time: replay the ledger day by day against
            # forward-filled marks for the assets ever held. Demo-grade.
            if events:
                held_ids = {e.asset.product_id for e in events if e.asset is not None}
                prices = load_prices()
                hist = (
                    prices.filter(pl.col("product_id").is_in(sorted(held_ids))).sort("date")
                    if held_ids
                    else prices.head(0)
                )
                first = min(e.day for e in events)
                all_days = sorted(d for d in prices["date"].unique().to_list() if d >= first)
                series = []
                for d in all_days:
                    pf_d = ledger_mod._replay([e for e in events if e.day <= d], products)
                    value = 0.0
                    ok = True
                    for asset, pos in pf_d.positions.items():
                        m = hist.filter(
                            (pl.col("product_id") == asset.product_id)
                            & (pl.col("sub_type") == asset.sub_type)
                            & (pl.col("date") <= d)
                        )["market"]
                        if m.len() == 0:
                            ok = False
                            break
                        value += pos.quantity * float(m[-1])
                    if ok:
                        series.append({"date": d, "equity": pf_d.cash + value})
                if series:
                    st.line_chart(pl.DataFrame(series).to_pandas().set_index("date"))
