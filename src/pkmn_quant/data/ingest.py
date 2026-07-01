"""Daily ingestion: download archives, transform, gate, and store."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import date, timedelta

import httpx
import polars as pl

from pkmn_quant.config import MIN_SET_RELEASE, Paths
from pkmn_quant.data import tcgcsv
from pkmn_quant.data.quality import apply_quality_gates
from pkmn_quant.data.tcgcsv import Group
from pkmn_quant.data.transforms import prices_frame, products_frame
from pkmn_quant.data.warehouse import Warehouse

# Reset the day-over-day jump baseline when resuming after a gap longer than
# this; comparing prices across months would quarantine legitimate moves.
GAP_RESET_DAYS = 7


@dataclass(frozen=True)
class IngestStats:
    day: date
    rows_clean: int
    rows_quarantined: int


def tracked_groups(groups: list[Group], today: date) -> list[Group]:
    """The tradeable universe: sets released between MIN_SET_RELEASE and today."""
    return [g for g in groups if MIN_SET_RELEASE <= g.published_on <= today]


def refresh_products(client: httpx.Client, warehouse: Warehouse, groups: list[Group]) -> int:
    """Fetch and store the product catalog. One failing set does not abort the rest.

    Called by ingest_range only when products.parquet is missing — new sets
    released after the first run are not picked up until the file is deleted
    (known Plan 1 limitation; a `pkmn refresh-products` command is future work).
    """
    if not groups:
        return 0
    frames = []
    for g in groups:
        try:
            frames.append(products_frame(tcgcsv.fetch_products(client, g.group_id)))
        except httpx.HTTPError as exc:
            print(f"warning: products fetch failed for group {g.group_id}: {exc}", file=sys.stderr)
    if not frames:
        raise RuntimeError("No product data fetched for any tracked set; aborting.")
    df = pl.concat(frames)
    warehouse.write_products(df)
    return df.height


def ingest_day(
    client: httpx.Client,
    warehouse: Warehouse,
    paths: Paths,
    day: date,
    group_ids: set[int],
    previous: pl.DataFrame | None,
) -> IngestStats:
    archive = tcgcsv.download_archive(client, day, paths.raw_archives)
    raw = tcgcsv.extract_group_prices(archive, day, group_ids)
    prices = prices_frame(day, raw)
    clean, quarantined = apply_quality_gates(prices, previous)
    warehouse.write_prices(day, clean)
    warehouse.write_quarantine(day, quarantined)
    return IngestStats(day=day, rows_clean=clean.height, rows_quarantined=quarantined.height)


def ingest_range(
    paths: Paths, start: date, end: date, client: httpx.Client | None = None
) -> list[IngestStats]:
    """Ingest all missing days in [start, end]. Already-stored days are skipped."""
    owns_client = client is None
    if client is None:
        client = tcgcsv.make_client()
    warehouse = Warehouse(paths)
    stats: list[IngestStats] = []
    try:
        groups = tcgcsv.fetch_groups(client)
        tracked = tracked_groups(groups, today=end)
        group_ids = {g.group_id for g in tracked}
        if not paths.products.exists():
            print("fetching product catalog (one request per set)...", file=sys.stderr)
            refresh_products(client, warehouse, tracked)

        stored = warehouse.stored_days()
        previous: pl.DataFrame | None = None
        # Seed from the latest stored day strictly BEFORE start (backfilling
        # must never compare against a future baseline), if within the window.
        prior_days = [d for d in stored if d < start]
        if prior_days and (start - prior_days[-1]).days <= GAP_RESET_DAYS:
            previous = warehouse.load_day(prior_days[-1])
        day = start
        while day <= end:
            if warehouse.has_day(day):
                previous = warehouse.load_day(day)
            else:
                stats.append(ingest_day(client, warehouse, paths, day, group_ids, previous))
                previous = warehouse.load_day(day)
            day += timedelta(days=1)
    finally:
        if owns_client:
            client.close()
    return stats
