"""Gate the warehouse swap: every product already in the live warehouse must
reproduce EXACTLY in the shadow — in prices, in quarantine decisions, and in
the product catalog.

This compares DATA, not file bytes. Parquet row order and compression can
differ harmlessly between runs, so byte-comparing files would raise false
failures and teach the operator to ignore this gate.

New products are ignored by construction — they are the point of the
rebuild, not a risk to existing data.
"""

import argparse
import sys
from datetime import date
from pathlib import Path

import polars as pl

from pkmn_quant.config import Paths
from pkmn_quant.data.warehouse import Warehouse

PRICE_COLS = ["product_id", "sub_type", "low", "mid", "high", "market"]


def _quarantine(paths: Paths, day: date) -> pl.DataFrame:
    """Quarantine rows for a day, or an empty frame — write_quarantine skips
    days with nothing to quarantine, so a missing file is normal."""
    f = paths.quarantine / f"date={day.isoformat()}" / "data.parquet"
    if not f.exists():
        return pl.DataFrame(schema={"product_id": pl.Int64, "sub_type": pl.Utf8, "reason": pl.Utf8})
    return pl.read_parquet(f).select("product_id", "sub_type", "reason")


def _catalog_problems(live: Warehouse, shadow: Warehouse) -> list[str]:
    """Every product_id in the live catalog must survive into the shadow's.

    Worth checking separately from prices because the two catalogs are built
    by asymmetric paths. The live one is MERGED into on every ingest
    (`ingest.refresh_products`, with a CATALOG_FLOOR refusal), so a transient
    per-group fetch failure leaves the previous rows in place. A shadow's is
    a single fresh fetch with no prior rows to merge against and no floor
    that can bite, so one reset connection ships a catalog SMALLER than the
    live one — silently re-creating the uncataloged-product bug the previous
    plan exists to fix. The price comparison cannot see that: prices and the
    catalog are separate files, and a priced product with no catalog row
    still reproduces its prices exactly.

    Extra products are fine, as everywhere else here: the shadow's catalog
    covers more groups by design.
    """
    if not live.paths.products.exists():
        return []  # nothing to protect
    if not shadow.paths.products.exists():
        return ["catalog: the live warehouse has products.parquet, the shadow has none"]
    live_ids = set(live.load_products()["product_id"].to_list())
    missing = sorted(live_ids - set(shadow.load_products()["product_id"].to_list()))
    if not missing:
        return []
    shown = ", ".join(str(p) for p in missing[:10])
    more = f", +{len(missing) - 10} more" if len(missing) > 10 else ""
    return [
        f"catalog: {len(missing)} of {len(live_ids)} live product_id(s) are missing "
        f"from the shadow's catalog ({shown}{more})"
    ]


def verify(root: Path, warehouse_name: str) -> list[str]:
    live_paths = Paths(root=root)
    shadow_paths = Paths(root=root, warehouse_name=warehouse_name)
    live, shadow = Warehouse(live_paths), Warehouse(shadow_paths)
    problems: list[str] = []

    live_days, shadow_days = set(live.stored_days()), set(shadow.stored_days())
    for day in sorted(live_days - shadow_days):
        problems.append(f"{day}: present in the live warehouse, missing from the shadow")

    for day in sorted(live_days & shadow_days):
        a = live.load_day(day).select(PRICE_COLS).sort(["product_id", "sub_type"])
        keys = a.select("product_id", "sub_type")
        b = (
            shadow.load_day(day)
            .join(keys, on=["product_id", "sub_type"], how="inner")
            .select(PRICE_COLS)
            .sort(["product_id", "sub_type"])
        )
        if a.height != b.height:
            problems.append(
                f"{day}: {a.height} existing rows live, {b.height} matched in the shadow"
            )
        elif not a.equals(b):
            problems.append(f"{day}: existing product prices differ between live and shadow")

        # Both sides are narrowed to the LIVE quarantine's keys, so a shadow
        # that quarantines an additional (new) product is not a divergence —
        # only a decision that changed for a product the live warehouse
        # already had counts.
        qa = _quarantine(live_paths, day).sort(["product_id", "sub_type"])
        qb = (
            _quarantine(shadow_paths, day)
            .join(qa.select("product_id", "sub_type"), on=["product_id", "sub_type"], how="inner")
            .sort(["product_id", "sub_type"])
        )
        if not qa.equals(qb):
            problems.append(f"{day}: quarantine decisions differ for existing products")

    problems.extend(_catalog_problems(live, shadow))
    return problems


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", type=Path, default=Path("."))
    ap.add_argument("--warehouse-name", default="warehouse-full")
    args = ap.parse_args()

    problems = verify(args.root, args.warehouse_name)
    if problems:
        print(f"VERIFICATION FAILED: {len(problems)} problem(s)", file=sys.stderr)
        for p in problems[:40]:
            print(f"  {p}", file=sys.stderr)
        raise SystemExit(1)
    print("VERIFICATION PASSED: every existing product reproduces exactly")


if __name__ == "__main__":
    main()
