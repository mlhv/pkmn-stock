"""Daily ingestion: download archives, transform, gate, and store."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import date, timedelta

import httpx
import polars as pl

from pkmn_quant.config import Paths
from pkmn_quant.data import tcgcsv
from pkmn_quant.data.quality import apply_quality_gates
from pkmn_quant.data.tcgcsv import Group
from pkmn_quant.data.transforms import prices_frame, products_frame
from pkmn_quant.data.warehouse import Warehouse

# Reset the day-over-day jump baseline when resuming after a gap longer than
# this; comparing prices across months would quarantine legitimate moves.
GAP_RESET_DAYS = 7

# A refresh whose freshly fetched catalog holds less than this fraction of the
# rows already on disk is treated as a mass fetch failure, not as genuine
# upstream shrinkage. See refresh_products for how the number was chosen.
CATALOG_FLOOR = 0.90


@dataclass(frozen=True)
class IngestStats:
    day: date
    rows_clean: int
    rows_quarantined: int


@dataclass(frozen=True)
class CatalogRefresh:
    """Outcome of one product-catalog refresh.

    ``rows`` is the size of the catalog now on disk; ``failed_groups`` lists
    the groups whose fetch failed this run, so an unattended caller can report
    a degraded refresh instead of silently succeeding.
    """

    rows: int
    failed_groups: tuple[int, ...]


@dataclass(frozen=True)
class IngestReport:
    """Outcome of one ingest_range call: the days stored, plus catalog health."""

    days: list[IngestStats]
    catalog_failures: tuple[int, ...]


def tracked_groups(groups: list[Group]) -> list[Group]:
    """Every tcgcsv group: prices now cover the same universe the catalog does.

    There is deliberately no date bound in either direction. The old
    MIN_SET_RELEASE lower bound went with full-set coverage (spec
    2026-08-03). The upper bound (`published_on <= today`) went too, because
    `published_on` is not a reliable publication date: for promo and
    catch-all groups TCGplayer keeps re-filing products into, it is a
    mutating "last touched" timestamp that reads as *today*, so any bound
    against it drops those groups from a historical backfill while letting
    them through a same-day ingest. A group with no price file in a given
    day's archive simply contributes nothing, so no bound is needed.
    """
    return list(groups)


def refresh_products(
    client: httpx.Client, warehouse: Warehouse, groups: list[Group]
) -> CatalogRefresh:
    """Fetch the product catalog and MERGE it into the one already on disk.

    Callers pass EVERY tcgcsv group, not just the price-tracked ones:
    TCGplayer re-files products between groups over time, so a product whose
    prices were archived under a tracked set can now list under an untracked
    promo group. Asking only tracked groups leaves 2,017 priced products
    (measured 2026-07-29) with no rarity, hence kind "other", hence invisible
    to every kind-filtered strategy. Fetching names is cheap and independent
    of which sets we ingest prices for.

    Merge, not replace. One failing group does not abort the rest, and since
    this now runs on EVERY ingest (including the unattended 09:00 `pkmn daily`
    job, which swallows ingest errors) a replace would let one transient
    connection reset silently delete a whole group from the catalog. That
    happened on 2026-07-30: group 2534 vanished and the run reported success.
    Merging keeps the previous rows for a group that failed this run; fresh
    rows still win on `product_id`, so a genuine upstream update is picked up.
    The accepted trade-off is that a product genuinely delisted upstream
    lingers in the catalog: a stale name/rarity row is harmless (it is only
    metadata for products the price feed still decides), whereas losing a row
    silently re-creates the kind-"other" bug this catalog work exists to fix.

    Completeness floor. Merging means the stored catalog can never shrink, so
    a floor on the merged frame would be vacuous; the side that can collapse
    is the fetch. If the freshly fetched frame holds less than CATALOG_FLOOR
    (90%) of the rows already on disk, we refuse to write and raise. 90% is
    ~3,250 rows of the current 32.5k catalog, i.e. dozens of groups: loose
    enough for real upstream removals, for the three known-empty groups
    (23095, 609, 610), and for one transient failure even on the largest
    group (2374, 1,244 products = 3.8%), which merging already handles
    gracefully; strict enough that a half-broken network run is loud instead
    of quietly writing a partial catalog whose fresh rows overwrite good ones.
    """
    if not groups:
        return CatalogRefresh(rows=0, failed_groups=())
    frames = []
    failed: list[int] = []
    for g in groups:
        try:
            frames.append(products_frame(tcgcsv.fetch_products(client, g.group_id)))
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            # ValueError covers json.JSONDecodeError (a 200 carrying HTML) and
            # KeyError a payload with no "results"; neither is an HTTPError,
            # and either used to abort the ingest before one price row landed.
            failed.append(g.group_id)
            print(f"warning: products fetch failed for group {g.group_id}: {exc}", file=sys.stderr)
    if not frames:
        raise RuntimeError("No product data fetched for any set; aborting.")
    # A product listed under two groups would otherwise duplicate, and a
    # duplicate product row fans out every join against the price frame.
    fresh = pl.concat(frames).unique(subset=["product_id"], keep="first", maintain_order=True)
    merged = fresh
    if warehouse.paths.products.exists():
        existing = warehouse.load_products()
        if fresh.height < CATALOG_FLOOR * existing.height:
            raise RuntimeError(
                f"Refusing to write the product catalog: fetched {fresh.height} rows against "
                f"{existing.height} already stored ({len(failed)} of {len(groups)} groups "
                "failed). Existing catalog left untouched."
            )
        # Fresh first + keep="first": an updated row wins over the stored one,
        # while a group that failed this run keeps the rows it had.
        # cast(): a catalog written before PRODUCT_SCHEMA was pinned (or one
        # hand-built in a test) can carry a Null column where the fetch has a
        # String one, and vertical concat requires identical dtypes.
        merged = pl.concat([fresh, existing.select(fresh.columns).cast(fresh.schema)])
    df = merged.unique(subset=["product_id"], keep="first", maintain_order=True).sort("product_id")
    warehouse.write_products(df)
    return CatalogRefresh(rows=df.height, failed_groups=tuple(failed))


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
) -> IngestReport:
    """Ingest all missing days in [start, end]. Already-stored days are skipped.

    The returned report carries the catalog refresh's failed groups so an
    unattended caller (`pkmn daily`) can record a degraded run rather than
    reporting plain success.
    """
    owns_client = client is None
    if client is None:
        client = tcgcsv.make_client()
    warehouse = Warehouse(paths)
    stats: list[IngestStats] = []
    catalog_failures: tuple[int, ...] = ()
    try:
        groups = tcgcsv.fetch_groups(client)
        # Prices and names now cover the same groups (full-set coverage,
        # spec 2026-08-03) with no date bound in either direction (see
        # tracked_groups); tracked is every group, same as the catalog fetch
        # below.
        tracked = tracked_groups(groups)
        group_ids = {g.group_id for g in tracked}
        print(f"fetching product catalog ({len(groups)} sets)...", file=sys.stderr)
        catalog = refresh_products(client, warehouse, groups)
        catalog_failures = catalog.failed_groups
        if catalog_failures:
            print(
                f"warning: catalog refresh: {len(catalog_failures)} of {len(groups)} groups "
                "failed to fetch; their previously catalogued rows were kept",
                file=sys.stderr,
            )

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
    return IngestReport(days=stats, catalog_failures=catalog_failures)
