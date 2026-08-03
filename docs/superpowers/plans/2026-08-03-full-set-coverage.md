# Full Set Coverage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ingest prices for all 217 tcgcsv groups instead of 23, over the same date window, by rebuilding the warehouse into a shadow directory, verifying that existing data reproduces exactly, swapping, then re-running and re-documenting the research suite.

**Architecture:** Two small production changes (`Paths.warehouse_name`, and `tracked_groups` losing its lower bound) let a one-shot script rebuild the full-set warehouse into `data/warehouse-full/` from the already-cached archives, without touching the live warehouse. A verification script gates the swap on existing products reproducing byte-identically. Design spec: `docs/superpowers/specs/2026-08-03-full-set-coverage-design.md`.

**Tech Stack:** Python 3.12, polars, DuckDB, httpx (`MockTransport` in tests), pytest.

## Background: measured facts this plan rests on

- **~7x more products, same dates.** One archive: 6,342 price rows for the 23 tracked sets, 45,092 for all 217. Warehouse ~38 MB → ~270 MB.
- **No deeper history.** `EARLIEST_ARCHIVE_DATE = 2024-02-08`; each archive holds one day across every group. Old sets arrive priced from 2024-02-08 onward, not from release.
- **No downloads needed.** 904+ archives cached (2.8 GB); `download_archive` skips existing files. Measured 1.84s/day for 217 groups vs 1.45s for 23 — 7z decompression dominates. Full rebuild ≈ 30-50 min.
- **Current warehouse: 907 days, 2024-02-08 → 2026-08-02** (grows daily; the 09:00 launchd job keeps running).
- **Existing products are provably unaffected by the wider extract.** The jump gate joins on `(product_id, sub_type)` and the other gates are per-row, so extra products cannot change an existing product's outcome. Verified on 4 consecutive days: existing products' clean rows were byte-identical under both set lists. This is what makes Task 4's acceptance gate achievable.
- **Quarantine rises 0.32% → 1.69%**, almost entirely `null_market` (761 of 763 on a sample day) — thin old cards with no sale that day. Expected.

## Global Constraints

- All four Python gates pass before every commit: `uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest`. Run `uv sync --group api` first if fastapi/pydantic import errors appear.
- **Never run gates while anything else mutates the venv.** A `uv sync` concurrent with pytest collects the dashboard tests and then fails them on the vanished import — an artifact, not a regression. Baseline: **514 passed** with the dashboard group installed, or 509 passed + 1 skipped without it. State which you observed.
- **No C++ changes.** Nothing under `cpp/`.
- **No strategy, cost-model, or engine changes.** This plan changes what data exists, not how it is traded.
- `tests/test_cli_backtest.py` goldens and `tests/test_native_parity.py` run on synthetic fixtures and **must not move**. If either does, STOP and report — it means something changed that this plan should not have touched.
- **Never delete anything under `data/`.** Tasks 4-6 write new artifacts; that is expected. `data/warehouse-old/` is retained after the swap and this plan never removes it.
- Do not run `pkmn ingest` against the live warehouse — the rebuild script is the only thing that writes prices here, and it writes to the shadow.
- Never use pip; `uv` manages everything.

## Reference: existing interfaces this plan builds on

- `Paths(root: Path)` — frozen dataclass; `raw_archives` = `root/data/raw/archives`, `warehouse` = `root/data/warehouse`, `prices` = `warehouse/prices`, `quarantine` = `warehouse/quarantine`, `products` = `warehouse/products.parquet` (`src/pkmn_quant/config.py:17-40`).
- `MIN_SET_RELEASE = date(2024, 1, 1)`, `EARLIEST_ARCHIVE_DATE = date(2024, 2, 8)` (`config.py:12-14`).
- `tracked_groups(groups, today) -> list[Group]` — `MIN_SET_RELEASE <= g.published_on <= today` (`ingest.py:58-59`).
- `refresh_products(client, warehouse, groups) -> CatalogRefresh(rows, failed_groups)` — merges into the existing catalog, 0.90 completeness floor.
- `ingest_day(client, warehouse, paths, day, group_ids, previous) -> IngestStats` — uses `paths.raw_archives` for the archive and writes via `warehouse`. **That split is what makes a shadow build work: archives come from `root`, writes go to the shadow warehouse.**
- `Warehouse(paths)`: `has_day`, `write_prices`, `write_quarantine`, `load_day`, `load_products`, `stored_days`, `query`.
- `tcgcsv.make_client()`, `tcgcsv.fetch_groups(client)`.
- `apply_quality_gates(prices, previous)` — `previous` must be the prior day's CLEAN output.

---

### Task 1: Widen the ingest scope and enable shadow builds

**Files:**
- Modify: `src/pkmn_quant/config.py`
- Modify: `src/pkmn_quant/data/ingest.py`
- Modify: `tests/test_config.py`
- Modify: `tests/test_ingest.py`

**Interfaces:**
- Produces: `Paths(root, warehouse_name="warehouse")` — `warehouse` becomes `root/data/<warehouse_name>`; every other property derives from it unchanged. `tracked_groups(groups, today)` returns every group published on or before `today`, with no lower bound.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_config.py`:

```python
def test_paths_shadow_warehouse() -> None:
    """A non-default warehouse name relocates the warehouse and everything
    under it, while archives stay rooted at the project — that split is what
    lets a shadow rebuild reuse the cached archives."""
    paths = Paths(root=Path("/tmp/proj"), warehouse_name="warehouse-full")
    assert paths.warehouse == Path("/tmp/proj/data/warehouse-full")
    assert paths.prices == Path("/tmp/proj/data/warehouse-full/prices")
    assert paths.quarantine == Path("/tmp/proj/data/warehouse-full/quarantine")
    assert paths.products == Path("/tmp/proj/data/warehouse-full/products.parquet")
    assert paths.raw_archives == Path("/tmp/proj/data/raw/archives")


def test_paths_default_warehouse_name_is_unchanged() -> None:
    """Every existing caller constructs Paths(root=...) positionally or by
    keyword with no warehouse_name; the default must keep them identical."""
    assert Paths(root=Path("/tmp/proj")).warehouse == Path("/tmp/proj/data/warehouse")
    assert Paths(root=Path("/tmp/proj")) == Paths(root=Path("/tmp/proj"), warehouse_name="warehouse")
```

Add to `tests/test_ingest.py`:

```python
def test_tracked_groups_has_no_lower_bound() -> None:
    """Since full-set coverage, prices cover every group, matching the
    catalog. Only the future-publication bound remains."""
    groups = [
        Group(group_id=1, name="Base Set", abbreviation="BS", published_on=date(1999, 1, 9)),
        Group(group_id=2, name="Recent", abbreviation="R", published_on=date(2025, 2, 20)),
        Group(group_id=3, name="Unreleased", abbreviation="U", published_on=date(2027, 1, 1)),
    ]
    tracked = tracked_groups(groups, today=date(2026, 6, 1))
    assert [g.group_id for g in tracked] == [1, 2]
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_config.py tests/test_ingest.py -k "shadow or default_warehouse or lower_bound" -v`
Expected: FAIL — `Paths() got an unexpected keyword argument 'warehouse_name'`; `tracked_groups` drops the 1999 group.

- [ ] **Step 3: Add `warehouse_name` to `Paths`**

In `src/pkmn_quant/config.py`, add the field and derive `warehouse` from it. The field must have a default so existing callers are unaffected, and it must come after `root`:

```python
@dataclass(frozen=True)
class Paths:
    """Filesystem layout rooted at the project directory.

    `warehouse_name` exists so a full-set rebuild can write to a shadow
    warehouse (`data/warehouse-full/`) while reading the SAME cached
    archives — `raw_archives` stays derived from `root`, so nothing is
    re-downloaded and the live warehouse is never written.
    """

    root: Path
    warehouse_name: str = "warehouse"

    @property
    def raw_archives(self) -> Path:
        return self.root / "data" / "raw" / "archives"

    @property
    def warehouse(self) -> Path:
        return self.root / "data" / self.warehouse_name
```

Leave `prices`, `quarantine`, and `products` exactly as they are — they already derive from `warehouse`.

- [ ] **Step 4: Drop the lower bound in `tracked_groups`**

In `src/pkmn_quant/data/ingest.py`:

```python
def tracked_groups(groups: list[Group], today: date) -> list[Group]:
    """Every set published on or before `today`.

    Full-set coverage (spec 2026-08-03) removed the MIN_SET_RELEASE lower
    bound: prices now cover the same 217 groups the catalog does, so the
    catalog-vs-prices scope split from the previous plan no longer exists.
    The upper bound stays — a set published in the future has no prices.
    """
    return [g for g in groups if g.published_on <= today]
```

Remove `MIN_SET_RELEASE` entirely. It appears in exactly three places (verified — if you find a fourth, STOP and report rather than leaving a partial removal):

1. `config.py:14` — the constant and its comment. Delete both; a dead constant that no longer bounds anything is how the next reader gets misled.
2. `ingest.py:12` — the import. Remove it from the `from pkmn_quant.config import ...` line, keeping `Paths`.
3. `ingest.py:170` — the comment reading "Prices come from tracked sets only (MIN_SET_RELEASE); NAMES come from every set…". That comment documents the catalog-vs-prices scope split, which **this task abolishes**. Rewrite it to say prices and names now cover the same set of groups.

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/test_config.py tests/test_ingest.py -v`
Expected: PASS. Note `test_tracked_groups_filters_old_sets` (the pre-existing test asserting the *old* behavior) will now fail — it pins exactly what this task removes. Update it to assert the new contract, or delete it as superseded by `test_tracked_groups_has_no_lower_bound`; say which you chose and why in your report. Do not weaken any other assertion.

- [ ] **Step 6: Run the four gates**

Run: `uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest`
Expected: all PASS. `tests/test_cli_backtest.py` goldens unchanged.

- [ ] **Step 7: Commit**

```bash
git add src/pkmn_quant/config.py src/pkmn_quant/data/ingest.py tests/test_config.py tests/test_ingest.py
git commit -m "feat(config): shadow-warehouse Paths; ingest every set, not just post-2024"
```

---

### Task 2: The rebuild script

**Files:**
- Create: `scripts/rebuild_warehouse.py`
- Create: `tests/test_rebuild_warehouse.py`

**Interfaces:**
- Consumes: `Paths(root, warehouse_name=)` (Task 1), `tracked_groups`, `refresh_products`, `ingest_day`, `Warehouse`.
- Produces: `rebuild(root, warehouse_name, start, end, client=None) -> list[IngestStats]` — builds a full-set warehouse at `root/data/<warehouse_name>` from cached archives. Resumable: days already present in the shadow are skipped and used as the gate baseline.

- [ ] **Step 1: Write the failing test**

Create `tests/test_rebuild_warehouse.py`:

```python
"""The shadow rebuild: full-set prices into an alternate warehouse, reading
the SAME cached archives, never touching the live warehouse."""

from datetime import date, timedelta
from pathlib import Path
from typing import Any

import httpx
import polars as pl

from pkmn_quant.config import Paths
from pkmn_quant.data.warehouse import Warehouse
from scripts.rebuild_warehouse import rebuild
from tests.test_tcgcsv import make_archive

D1 = date(2025, 6, 1)

GROUPS_JSON: dict[str, Any] = {
    "success": True,
    "results": [
        {"groupId": 100, "name": "Recent", "abbreviation": "R",
         "publishedOn": "2025-02-20T00:00:00", "categoryId": 3},
        {"groupId": 200, "name": "Ancient", "abbreviation": "A",
         "publishedOn": "1999-01-09T00:00:00", "categoryId": 3},
    ],
}


def _products(group_id: int, product_id: int) -> dict[str, Any]:
    return {"success": True, "results": [{
        "productId": product_id, "name": f"P{product_id}", "groupId": group_id,
        "presaleInfo": {"releasedOn": "2025-02-20T00:00:00"}, "extendedData": [],
    }]}


def _price(product_id: int, market: float) -> dict[str, Any]:
    return {"productId": product_id, "lowPrice": 1.0, "midPrice": 2.0, "highPrice": 3.0,
            "marketPrice": market, "directLowPrice": None, "subTypeName": "Normal"}


def _client(tmp_path: Path, days: int = 3) -> httpx.Client:
    archives = {}
    for i in range(days):
        d = D1 + timedelta(days=i)
        archives[d] = make_archive(
            tmp_path / f"fx{i}", d, 100, [_price(1001, 10.0 + i)],
            extra_groups={200: [_price(2001, 50.0 + i)]},
        )

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/groups"):
            return httpx.Response(200, json=GROUPS_JSON)
        if path.endswith("/100/products"):
            return httpx.Response(200, json=_products(100, 1001))
        if path.endswith("/200/products"):
            return httpx.Response(200, json=_products(200, 2001))
        for d, a in archives.items():
            if path.endswith(f"prices-{d.isoformat()}.ppmd.7z"):
                return httpx.Response(200, content=a.read_bytes())
        return httpx.Response(404)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_rebuild_writes_the_shadow_and_includes_ancient_sets(tmp_path: Path) -> None:
    client = _client(tmp_path)
    rebuild(root=tmp_path, warehouse_name="warehouse-full",
            start=D1, end=D1 + timedelta(days=2), client=client)

    shadow = Warehouse(Paths(root=tmp_path, warehouse_name="warehouse-full"))
    assert shadow.stored_days() == [D1 + timedelta(days=i) for i in range(3)]
    ids = set(shadow.load_prices()["product_id"].to_list())
    assert ids == {1001, 2001}, "the 1999 set's product must be ingested"
    assert set(shadow.load_products()["product_id"].to_list()) == {1001, 2001}


def test_rebuild_never_touches_the_live_warehouse(tmp_path: Path) -> None:
    live = Warehouse(Paths(root=tmp_path))
    live.write_products(pl.DataFrame({
        "product_id": [7], "group_id": [1], "name": ["Live"], "rarity": [None],
        "kind": ["sealed"], "released_on": [date(2024, 1, 1)]}))

    rebuild(root=tmp_path, warehouse_name="warehouse-full",
            start=D1, end=D1 + timedelta(days=2), client=_client(tmp_path))

    assert live.load_products()["product_id"].to_list() == [7]
    assert live.stored_days() == []


def test_rebuild_is_resumable(tmp_path: Path) -> None:
    """A crashed rebuild must resume, and the resumed days must use the prior
    day's CLEAN output as their gate baseline — the chain cannot restart."""
    rebuild(root=tmp_path, warehouse_name="warehouse-full",
            start=D1, end=D1, client=_client(tmp_path))
    shadow = Warehouse(Paths(root=tmp_path, warehouse_name="warehouse-full"))
    assert shadow.stored_days() == [D1]

    stats = rebuild(root=tmp_path, warehouse_name="warehouse-full",
                    start=D1, end=D1 + timedelta(days=2), client=_client(tmp_path))
    assert shadow.stored_days() == [D1 + timedelta(days=i) for i in range(3)]
    assert [s.day for s in stats] == [D1 + timedelta(days=1), D1 + timedelta(days=2)], \
        "the already-stored day must be skipped, not re-ingested"
```

`make_archive` in `tests/test_tcgcsv.py:60` currently takes `(tmp_path, day, group_id, rows)` and writes exactly one `<day>/3/<group_id>/prices` file. You must add an `extra_groups: dict[int, list[dict[str, Any]]] | None = None` keyword that writes one additional `prices` file per entry into the same archive, before the `writeall`. It is a trailing keyword with a `None` default, so no existing caller changes — verify that by running `tests/test_tcgcsv.py` and `tests/test_ingest.py` unchanged after the edit.

This is a test-fixture change, not a production one, but it is load-bearing: without a multi-group archive, Task 2's tests cannot show that an *untracked-under-the-old-rule* set (the 1999 group) actually gets ingested, which is the whole point of the task.

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_rebuild_warehouse.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'scripts.rebuild_warehouse'`.

- [ ] **Step 3: Implement the script**

Create `scripts/rebuild_warehouse.py`:

```python
"""Rebuild the price warehouse for EVERY tcgcsv set into a shadow directory.

Why a rebuild and not an incremental ingest: `apply_quality_gates` compares
each day against the PREVIOUS day's clean output, so widening the set list
changes that baseline too and the chain must be rebuilt in order from the
start. `ingest_range` also skips days it already has, so simply re-running
`pkmn ingest` after widening the filter would do nothing at all.

Why a shadow: the live warehouse is never written, so a failed or abandoned
rebuild costs nothing and rollback is a directory move.

Archives are read from `root/data/raw/archives` and are already cached, so
this does no downloading.
"""

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

import httpx

from pkmn_quant.config import Paths
from pkmn_quant.data import tcgcsv
from pkmn_quant.data.ingest import IngestStats, ingest_day, refresh_products, tracked_groups
from pkmn_quant.data.warehouse import Warehouse


def rebuild(
    root: Path,
    warehouse_name: str,
    start: date,
    end: date,
    client: httpx.Client | None = None,
) -> list[IngestStats]:
    """Build a full-set warehouse at `root/data/<warehouse_name>`.

    Resumable: days already present in the shadow are skipped and loaded as
    the next day's gate baseline, so a crash costs only the unfinished tail.
    """
    src = Paths(root=root)  # archives live under the project root
    shadow = Warehouse(Paths(root=root, warehouse_name=warehouse_name))
    owns_client = client is None
    client = client or tcgcsv.make_client()
    stats: list[IngestStats] = []
    try:
        groups = tcgcsv.fetch_groups(client)
        group_ids = {g.group_id for g in tracked_groups(groups, today=end)}
        print(f"rebuilding {start}..{end} over {len(group_ids)} sets", file=sys.stderr)
        refresh_products(client, shadow, groups)

        previous = None
        day = start
        while day <= end:
            if shadow.has_day(day):
                previous = shadow.load_day(day)
            else:
                stats.append(ingest_day(client, shadow, src, day, group_ids, previous))
                previous = shadow.load_day(day)
                if len(stats) % 50 == 0:
                    print(f"  {day}: {len(stats)} days built", file=sys.stderr)
            day += timedelta(days=1)
    finally:
        if owns_client:
            client.close()
    return stats


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", type=Path, default=Path("."))
    ap.add_argument("--warehouse-name", default="warehouse-full")
    ap.add_argument("--start", type=date.fromisoformat, default=None)
    ap.add_argument("--end", type=date.fromisoformat, default=None)
    args = ap.parse_args()

    live_days = Warehouse(Paths(root=args.root)).stored_days()
    if not live_days:
        raise SystemExit("live warehouse has no stored days; nothing to mirror")
    start = args.start or live_days[0]
    end = args.end or live_days[-1]
    stats = rebuild(args.root, args.warehouse_name, start, end)
    clean = sum(s.rows_clean for s in stats)
    quarantined = sum(s.rows_quarantined for s in stats)
    print(f"built {len(stats)} days: {clean} clean rows, {quarantined} quarantined")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_rebuild_warehouse.py -v`
Expected: PASS — all three cases.

- [ ] **Step 5: Run the four gates**

Run: `uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add scripts/rebuild_warehouse.py tests/test_rebuild_warehouse.py tests/test_tcgcsv.py
git commit -m "feat(scripts): rebuild_warehouse — full-set shadow rebuild from cached archives"
```

---

### Task 3: The verification gate

**Files:**
- Create: `scripts/verify_rebuild.py`
- Create: `tests/test_verify_rebuild.py`

**Interfaces:**
- Consumes: `Paths(root, warehouse_name=)`, `Warehouse`.
- Produces: `verify(root, warehouse_name) -> list[str]` — an empty list means the shadow is safe to swap in; each string is one human-readable problem.

**This is the acceptance gate, so it must be proven to FAIL when data differs**, not merely to pass when it matches. A verifier that always returns `[]` would pass a test suite that only checks the happy path, and would then wave through a corrupted warehouse.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_verify_rebuild.py`:

```python
"""The swap gate: every product already in the live warehouse must reproduce
exactly in the shadow. New products are additive and ignored."""

from datetime import date
from pathlib import Path

import polars as pl

from pkmn_quant.config import Paths
from pkmn_quant.data.transforms import PRICE_SCHEMA
from pkmn_quant.data.warehouse import Warehouse
from scripts.verify_rebuild import verify

D = date(2025, 6, 1)


def _row(pid: int, market: float) -> dict[str, object]:
    return {"date": D, "product_id": pid, "sub_type": "Normal",
            "low": 1.0, "mid": 2.0, "high": 3.0, "market": market}


def _write(root: Path, name: str, rows: list[dict[str, object]], day: date = D) -> Warehouse:
    wh = Warehouse(Paths(root=root, warehouse_name=name))
    wh.write_prices(day, pl.DataFrame(rows, schema=PRICE_SCHEMA))
    return wh


def test_clean_when_shadow_is_a_superset(tmp_path: Path) -> None:
    _write(tmp_path, "warehouse", [_row(1, 10.0)])
    _write(tmp_path, "warehouse-full", [_row(1, 10.0), _row(2, 99.0)])
    assert verify(tmp_path, "warehouse-full") == []


def test_detects_a_changed_price(tmp_path: Path) -> None:
    _write(tmp_path, "warehouse", [_row(1, 10.0)])
    _write(tmp_path, "warehouse-full", [_row(1, 10.5), _row(2, 99.0)])
    problems = verify(tmp_path, "warehouse-full")
    assert problems and any("2025-06-01" in p for p in problems)


def test_detects_a_missing_product(tmp_path: Path) -> None:
    _write(tmp_path, "warehouse", [_row(1, 10.0), _row(3, 5.0)])
    _write(tmp_path, "warehouse-full", [_row(1, 10.0)])
    assert verify(tmp_path, "warehouse-full") != []


def test_detects_a_missing_day(tmp_path: Path) -> None:
    _write(tmp_path, "warehouse", [_row(1, 10.0)])
    _write(tmp_path, "warehouse", [_row(1, 11.0)], day=date(2025, 6, 2))
    _write(tmp_path, "warehouse-full", [_row(1, 10.0)])
    problems = verify(tmp_path, "warehouse-full")
    assert problems and any("2025-06-02" in p for p in problems)


def test_detects_a_flipped_quarantine_decision(tmp_path: Path) -> None:
    """A quarantine decision that changed for an existing product means the
    gate chain diverged, even if the clean rows happen to match."""
    live = _write(tmp_path, "warehouse", [_row(1, 10.0)])
    shadow = _write(tmp_path, "warehouse-full", [_row(1, 10.0)])
    q = pl.DataFrame([{**_row(9, 0.0), "reason": "nonpositive_market"}])
    live.write_quarantine(D, q)
    # shadow writes no quarantine for product 9 -> divergence
    assert verify(tmp_path, "warehouse-full") != []
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_verify_rebuild.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'scripts.verify_rebuild'`.

- [ ] **Step 3: Implement the verifier**

Create `scripts/verify_rebuild.py`:

```python
"""Gate the warehouse swap: every product already in the live warehouse must
reproduce EXACTLY in the shadow.

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
        return pl.DataFrame({"product_id": [], "sub_type": [], "reason": []})
    return pl.read_parquet(f).select("product_id", "sub_type", "reason")


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

        qa = _quarantine(live_paths, day).sort(["product_id", "sub_type"])
        qb = (
            _quarantine(shadow_paths, day)
            .join(qa.select("product_id", "sub_type"), on=["product_id", "sub_type"], how="inner")
            .sort(["product_id", "sub_type"])
        )
        if not qa.equals(qb):
            problems.append(f"{day}: quarantine decisions differ for existing products")

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
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_verify_rebuild.py -v`
Expected: PASS — all five cases, including the four that must detect a difference.

- [ ] **Step 5: Run the four gates**

Run: `uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add scripts/verify_rebuild.py tests/test_verify_rebuild.py
git commit -m "feat(scripts): verify_rebuild — gate the swap on existing data reproducing exactly"
```

---

### Task 4: Rebuild and verify against real data

**Files:**
- Modify: none (this task produces measurements, not code)

**Interfaces:**
- Consumes: `scripts/rebuild_warehouse.py`, `scripts/verify_rebuild.py`.
- Produces: a verified `data/warehouse-full/` plus the coverage numbers Task 6 quotes.

This is the first task touching real data. It writes only `data/warehouse-full/`. **Report the real output of every command**, including anything surprising.

- [ ] **Step 1: Record the before state**

```bash
uv run python -c "
from pkmn_quant.config import Paths
from pkmn_quant.data.warehouse import Warehouse
from pathlib import Path
wh = Warehouse(Paths(root=Path('.')))
d = wh.stored_days()
print('days:', len(d), d[0], '->', d[-1])
print(wh.query('SELECT count(*) AS rows, count(DISTINCT product_id) AS products FROM prices'))
" | tee /tmp/fullset-before.txt
```

Record the real numbers.

- [ ] **Step 2: Run the rebuild**

```bash
time caffeinate -i uv run python scripts/rebuild_warehouse.py --root . 2>&1 | tail -20
```

`caffeinate -i` is not optional: a machine sleep previously killed a long run silently, leaving zero output and no traceback. Expect roughly 30-50 minutes. **Report the real elapsed time, the day count, and the clean/quarantined row totals.**

- [ ] **Step 3: Check the growth floor**

```bash
uv run python -c "
from pkmn_quant.config import Paths
from pkmn_quant.data.warehouse import Warehouse
from pathlib import Path
live = Warehouse(Paths(root=Path('.')))
full = Warehouse(Paths(root=Path('.'), warehouse_name='warehouse-full'))
a = live.query('SELECT count(*) n, count(DISTINCT product_id) p FROM prices')
b = full.query('SELECT count(*) n, count(DISTINCT product_id) p FROM prices')
print('live:', a.row(0), 'full:', b.row(0))
print('row multiplier: %.2fx' % (b['n'][0]/a['n'][0]))
" | tee /tmp/fullset-growth.txt
```

Expected roughly 7x. **If the multiplier is below 4x, STOP and report** — a silently-narrow extract would pass verification trivially, because verification only checks that old data survived.

- [ ] **Step 4: Verify**

```bash
uv run python scripts/verify_rebuild.py --root . ; echo "exit=$?"
```

Expected: `VERIFICATION PASSED`, exit 0.

**If it reports missing days**, the 09:00 daily job added a day mid-rebuild. That is the anticipated race: re-run Step 2 (it is incremental, so only the new day is built — seconds) and verify again.

**If it reports differing prices or quarantine decisions, STOP and report.** That means the rebuild diverged, which contradicts the property this plan is built on, and no swap should happen.

- [ ] **Step 5: Collect coverage statistics**

```bash
uv run python -c "
from pkmn_quant.config import Paths
from pkmn_quant.data.warehouse import Warehouse
from pathlib import Path
full = Warehouse(Paths(root=Path('.'), warehouse_name='warehouse-full'))
print(full.query('''SELECT p.kind, count(DISTINCT s.product_id) AS n
FROM (SELECT DISTINCT product_id FROM prices) s
LEFT JOIN products p ON p.product_id = s.product_id GROUP BY 1 ORDER BY 2 DESC'''))
print(full.query('''WITH d AS (SELECT count(DISTINCT date) AS nd FROM prices),
c AS (SELECT product_id, count(DISTINCT date) AS days FROM prices GROUP BY 1)
SELECT CASE WHEN days*1.0/(SELECT nd FROM d) >= 0.9 THEN 'prices >=90% of days'
            WHEN days*1.0/(SELECT nd FROM d) >= 0.5 THEN '50-90%'
            WHEN days*1.0/(SELECT nd FROM d) >= 0.1 THEN '10-50%'
            ELSE '<10% of days' END AS sparsity,
       count(*) AS n_products FROM c GROUP BY 1 ORDER BY 2 DESC'''))
" | tee /tmp/fullset-coverage.txt
```

The sparsity table is what tells a reader how much of the new universe is real versus nominally present. **Record it — Task 6 quotes it.** It does not gate anything.

- [ ] **Step 6: Report**

No commit — this task produces no tracked files. Report the before state, elapsed time, growth multiplier, verification result, and the full coverage tables.

---

### Task 5: Swap

**Files:**
- Modify: none

**Interfaces:**
- Consumes: the verified `data/warehouse-full/` from Task 4.
- Produces: the full-set warehouse live at `data/warehouse/`, with the previous one retained at `data/warehouse-old/`.

**Do not start this task unless Task 4's verification passed.**

- [ ] **Step 1: Re-verify immediately before swapping**

```bash
uv run python scripts/verify_rebuild.py --root . ; echo "exit=$?"
```

Time has passed since Task 4 and the daily job may have added a day. Re-verifying here keeps the unguarded window as small as possible. If it fails on a missing day, re-run the rebuild (incremental, seconds) and verify again.

- [ ] **Step 2: Swap**

```bash
mv data/warehouse data/warehouse-old
mv data/warehouse-full data/warehouse
ls -d data/warehouse data/warehouse-old
```

- [ ] **Step 3: Smoke check**

```bash
uv run pkmn runs list 2>&1 | head -3
uv run pkmn backtest --start 2026-05-01 --end 2026-06-30 2>&1 | tail -10
```

Expected: the registry still reads, and a short buy-and-hold backtest completes against the wider universe. **Report its total_return and fill count** — it will differ from the pre-swap value, which is the expected consequence of the wider universe, not a fault.

**If anything fails, roll back** and report:

```bash
mv data/warehouse data/warehouse-full && mv data/warehouse-old data/warehouse
```

- [ ] **Step 4: Confirm the live warehouse**

```bash
uv run python -c "
from pkmn_quant.config import Paths
from pkmn_quant.data.warehouse import Warehouse
from pathlib import Path
wh = Warehouse(Paths(root=Path('.')))
print('days:', len(wh.stored_days()))
print(wh.query('SELECT count(*) AS rows, count(DISTINCT product_id) AS products FROM prices'))"
```

Expected: the same day count as before and roughly 7x the products.

- [ ] **Step 5: Report**

No commit. Report the swap result, the smoke-check numbers, and confirm `data/warehouse-old/` is in place. **Do not delete it** — this plan never does.

---

### Task 6: Re-run the research suite and document

**Files:**
- Modify: `docs/research-findings-2026-07.md`
- Modify: `CLAUDE.md`
- Modify: `README.md`

**Interfaces:**
- Consumes: the swapped warehouse (Task 5), Task 4's coverage numbers.
- Produces: a new findings section and updated project docs.

Every documented number was computed on a 23-set universe and is now genuinely superseded — unlike the previous catalog plan, where the correction proved immaterial on this window.

- [ ] **Step 1: Re-run the benchmark**

```bash
uv run pkmn backtest --start 2024-03-01 --end 2026-06-30 2>&1 | tail -20
```

The pre-swap reference is **+183.67%** (impact-on). Record the new total_return, fill count, and run_id.

- [ ] **Step 2: Re-run every strategy's walk-forward**

Run the four native strategies first — each should stay in single-digit minutes:

```bash
for s in sealed-accumulation dip-buyer xs-momentum cost-aware-reversion; do
  echo "=== $s ==="
  caffeinate -i uv run pkmn walkforward --strategy "$s" --start 2024-03-01 --end 2026-06-30 --trials 15 2>&1 | tail -8
done
```

Then the two ML strategies **one at a time, never in parallel**:

```bash
caffeinate -i uv run pkmn walkforward --strategy ml-ranker --start 2024-03-01 --end 2026-06-30 --trials 15 2>&1 | tail -8
caffeinate -i uv run pkmn walkforward --strategy ml-ranker-v2 --start 2024-03-01 --end 2026-06-30 --trials 15 2>&1 | tail -8
```

These two dominate the schedule: ml-ranker took ~22 minutes on 23 sets and its feature frame scales with product count, so **expect 1-3 hours each**. Running them in parallel previously tripled wall-clock under memory pressure and risked both. If either dies, read its output and report the failure before re-running; do not re-run blindly.

Record every stitched OOS total return and run_id, including any failure.

- [ ] **Step 3: Re-run the rigor evaluation**

```bash
uv run pkmn evaluate 2>&1 | tail -30
```

Record the deflated Sharpe per strategy and the joint Reality Check p-value. The pre-swap reference is p = 1.0000 with every DSR between 0.000 and 0.017.

- [ ] **Step 4: Write the findings section**

Append a new section to `docs/research-findings-2026-07.md`, matching the voice of the existing sections (they state the hypothesis, the measurement, and the honest conclusion, including negative results). It must contain:

- **What changed:** prices now cover all 217 groups rather than 23; the universe is derived from what is priced, so it widened with the data. Quote Task 4's real growth and coverage numbers.
- **The sparsity table** from Task 4 Step 5, and what it implies about how much of the new universe is meaningfully tradeable.
- **A before/after table** for all six strategies plus the benchmark, stating the cost regime of each number. Take the pre-swap figures from this same file's catalog-completeness section, which is the most recent impact-on set.
- **The rigor comparison** — DSR per strategy and the joint Reality Check p, before and after.
- **The honest conclusion.** State plainly whether the wider universe changes any research conclusion. If nothing beats buy-and-hold — the standing result across every prior plan — say so directly. A larger universe is not an improvement by itself.
- **A note that all prior sections are superseded**, this time genuinely: every earlier number was computed on a 23-set universe.

- [ ] **Step 5: Update CLAUDE.md**

- Add a status bullet for this plan matching the existing bullets' density: what shipped (all-set ingest, shadow rebuild + verified swap), the measured before/after, the test counts from your own gate run, and the honest conclusion from Step 4.
- **Update the Conventions bullet that pins the buy-and-hold benchmark** (+186.0% flat-cost / +183.7% impact-on). That figure is the comparison reference for every future strategy, so a stale value there misleads every later comparison.
- Update the `data/` layout bullet: the new day count, row count, and that prices and the catalog now cover the same 217 groups — the catalog-vs-prices scope split from the previous plan no longer exists.
- Note `data/warehouse-old/` exists and why, so a future reader does not delete it thinking it is stale.
- Restate the mark-smoothing caveat with the new sparsity numbers: ~7x more thin products makes carry-forward mark smoothing worse, and Sharpe/Sortino here were already caveated for it.
- Add `scripts/rebuild_warehouse.py` and `scripts/verify_rebuild.py` to the Commands block.

- [ ] **Step 6: Update README.md**

Update any line describing the data scope (day count, product count, "sets released since 2024") to the new reality, and check no line still implies the universe is post-2024 sets only.

- [ ] **Step 7: Full verification sweep**

```bash
uv sync --group api && uv run pytest
uv run ruff check . && uv run ruff format --check . && uv run mypy
uv run --group dashboard pytest tests/test_dashboard.py
cd web && npm run check && npm run build
```

Run these with nothing else touching the venv. Report the real counts.

- [ ] **Step 8: Commit**

```bash
git add docs/research-findings-2026-07.md CLAUDE.md README.md
git commit -m "docs: full set coverage — universe shift and full re-run findings"
```

---

## Self-review notes

- **Spec coverage:** `Paths.warehouse_name` and the `tracked_groups` lower bound (Task 1) ✓; shadow rebuild from cached archives, resumable (Task 2) ✓; verification gate proven to fail on differences, comparing data not bytes (Task 3) ✓; the two hard stops — verification failure and sub-4x growth (Task 4) ✓; the daily-job race handled by re-verifying immediately before the swap (Tasks 4 and 5) ✓; swap with retained `warehouse-old` and a rollback command (Task 5) ✓; coverage and sparsity statistics reported not gated (Task 4, quoted in Task 6) ✓; full re-run, sequential ML runs under `caffeinate`, docs including the benchmark reference figure (Task 6) ✓. Out of scope and in no task: deeper history, liquidity floors, strategy/engine/cost-model changes, re-downloading archives ✓.
- **Type consistency:** `rebuild(root, warehouse_name, start, end, client=None) -> list[IngestStats]` and `verify(root, warehouse_name) -> list[str]` are used with those exact signatures in their tests and their `main()`s. `Paths(root, warehouse_name="warehouse")` keeps `root` first and positional, so every existing `Paths(root=...)` call is unaffected. `ingest_day(client, warehouse, paths, day, group_ids, previous)` is called with the shadow `Warehouse` and the root `Paths` — the split that makes archive reuse work.
- **The one production risk** is Task 1's removal of `MIN_SET_RELEASE`: it is imported by `ingest.py` and defined in `config.py`, and the task says to STOP and report if any other module imports it rather than leaving a partial removal.
- **The pre-existing `test_tracked_groups_filters_old_sets` will fail** by design — it pins exactly the behavior Task 1 removes. Task 1 Step 5 names it explicitly and requires the implementer to say whether it was updated or deleted, so a reviewer sees a deliberate decision rather than a quietly weakened suite.
- **Tasks 4-6 touch real data and must run in order.** Task 5 must not start unless Task 4's verification passed; Task 6's numbers are meaningless unless Task 5's swap succeeded.
