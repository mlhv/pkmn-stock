# Catalog Completeness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give every priced product a name, rarity, and `kind` by fetching the product catalog for **all** tcgcsv groups instead of only the price-tracked ones — then re-run the research suite and document how the corrected universe moves the numbers.

**Architecture:** `refresh_products` already takes a group list; today `ingest_range` passes it `tracked_groups(...)` (23 sets) and calls it only when `products.parquet` is missing. This plan passes it the **full** group list (217 sets) and calls it on every ingest, plus adds the `pkmn refresh-products` command that `ingest.py` already flags as future work. Price ingest still uses `tracked_groups` — which sets we ingest *prices* for is a separate decision (`MIN_SET_RELEASE`) deliberately not touched here.

**Tech Stack:** Python 3.12, httpx (`MockTransport` in tests), polars, DuckDB, typer, pytest.

## Background: the measured problem

Measured against the live 874-day warehouse on 2026-07-29:

- **6,665** distinct product_ids have prices; **4,648** of them have a row in `products.parquet`. **2,017 do not.**
- Those 2,017 are not new sets and not delisted: **1,992 (98.8%) are still actively pricing**, and **2,009 (99.6%) are present in tcgcsv's catalog right now** — filed under groups the ingest never asks about. Top holders: `Miscellaneous Cards & Products` (g=2374, 1,244 products), `ME05: Pitch Black` (g=24688, 146), `Blister Exclusives` (g=2289, 134), `Nintendo Promos` (g=1423, 72), `Alternate Art Promos` (g=1938, 57).
- Mechanism: TCGplayer **re-files products between groups over time**. A promo whose prices were archived under a tracked set now lists under an untracked promo group, so the one-time catalog fetch never sees it.
- Consequence: no catalog row means no `rarity`, so `classify_kind` never runs and the product is tagged `kind: "other"` (Plan 10). `"other"` matches neither `sealed` nor `single`, so **five of seven strategies cannot see these products at all**.
- Sampling group 2374's 1,244 affected products through `classify_kind`: **585 would be `single`, 478 `sealed`, 181 `excluded`.** They are promos and odd sealed items — Ancient Mew, E3 Stamped Pikachu, 2014 movie promo Pikachu, Water Web Holos, EX Trainer Kits, POP Series, blister exclusives.
- Re-fetching only the tracked groups resolves **14 of 2,017** — which is why the fix must widen *which groups* are asked, not *how often*.

**CLAUDE.md currently states a cause that is wrong.** Its Plan 10 bullet attributes the gap "mostly" to "`ingest.py`'s documented one-time catalog fetch never picking up new sets." Measured: that accounts for 0.7%. Task 1 corrects it.

## Global Constraints

- All four Python gates pass before every commit: `uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest`. Run `uv sync --group api` first if fastapi/pydantic import errors appear (the `api` group is not in a bare `uv sync`).
- **No C++ changes.** Nothing under `cpp/`. After editing anything under `cpp/` you would need `uv sync --reinstall-package pkmn-quant`; this plan should never need it.
- **Do not change `MIN_SET_RELEASE` or `tracked_groups`.** Which sets we ingest *prices* for is out of scope and belongs to a separate, much larger plan. This plan changes only which groups we fetch *names* for.
- `tests/test_cli_backtest.py` pins exact engine numbers against synthetic fixtures. Those goldens must not change — if one moves, stop and report; it means a code path changed that this plan should not have touched.
- `tests/test_native_parity.py` must stay untouched and green.
- `data/` is real and irreplaceable (874 days, ~40 min to re-ingest). Tasks 4 and 5 write to it. **Never delete anything under `data/`.** `products.parquet` is overwritten by design in Task 4 — back it up first, as that task specifies.
- Never use pip; `uv` manages everything.
- Report real command output, including failures. A surprising number is a finding, not something to smooth over.

## Reference: existing interfaces this plan builds on

- `tcgcsv.fetch_groups(client) -> list[Group]`; `Group(group_id: int, name: str, abbreviation: str, published_on: date)` (`src/pkmn_quant/data/tcgcsv.py:32-56`).
- `tcgcsv.fetch_products(client, group_id) -> list[dict[str, Any]]` — GETs `/tcgplayer/3/{group_id}/products`, returns `payload["results"]`.
- `ingest.refresh_products(client, warehouse, groups) -> int` (`src/pkmn_quant/data/ingest.py:36-56`) — loops groups, `products_frame(...)` each, warns and continues on `httpx.HTTPError`, raises `RuntimeError` if every group failed, `pl.concat`s, `warehouse.write_products(df)`, returns row count.
- `ingest.tracked_groups(groups, today) -> list[Group]` — `MIN_SET_RELEASE <= published_on <= today`. **Unchanged by this plan.**
- `transforms.products_frame(products) -> pl.DataFrame` with `PRODUCT_SCHEMA` = `product_id, group_id, name, rarity, kind, released_on`; `classify_kind(rarity) -> str` returns `sealed` / `single` / `excluded` / `other`.
- `Warehouse.write_products(df)` writes atomically via a `.tmp` rename (`src/pkmn_quant/data/warehouse.py:43-47`); `Warehouse.load_products()`; `Warehouse.query(sql)` gives DuckDB `prices` / `products` views.
- `Paths(root=...)` from `pkmn_quant.config`; `paths.products` is the parquet path.
- Test harness: `tests/test_ingest.py` drives `ingest_range` through `httpx.MockTransport`, with `GROUPS_JSON` (two groups: `24541` published 2025-02-20, and `3170` "Old Set" published 2020-01-01) and `PRODUCTS_JSON` (one product, `666906`). `make_archive` comes from `tests/test_tcgcsv.py`.
- CLI: `src/pkmn_quant/cli.py`, typer, commands declared with `@app.command()`; `ingest` at line 232 is the closest model for a new top-level command.

---

### Task 1: Catalog every group, on every ingest

**Files:**
- Modify: `src/pkmn_quant/data/ingest.py`
- Modify: `tests/test_ingest.py`
- Modify: `CLAUDE.md`

**Interfaces:**
- Consumes: `tcgcsv.fetch_groups`, `tcgcsv.fetch_products`, `transforms.products_frame`, `Warehouse.write_products`.
- Produces: `refresh_products(client, warehouse, groups)` now de-duplicates by `product_id`; `ingest_range` calls it with the **full** group list on every run rather than only when `products.parquet` is absent.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_ingest.py`. Note the existing module-level handler returns the same `PRODUCTS_JSON` for every `/products` path; these tests need per-group payloads, so they build their own handler.

```python
def _per_group_products(group_id: int, product_id: int, name: str) -> dict[str, Any]:
    return {
        "success": True,
        "results": [
            {
                "productId": product_id,
                "name": name,
                "groupId": group_id,
                "presaleInfo": {"releasedOn": "2025-02-20T00:00:00"},
                "extendedData": [],
            }
        ],
    }


def test_refresh_products_catalogs_untracked_groups(tmp_path: Path) -> None:
    """The catalog must cover EVERY group, not just the price-tracked ones.

    TCGplayer re-files products between groups over time, so a product whose
    prices were archived under a tracked set now lists under an untracked
    promo group. Asking only tracked groups leaves it nameless and therefore
    kind "other", invisible to every kind-filtered strategy.
    """
    d1 = date(2025, 6, 1)
    archives = {d1: make_archive(tmp_path / "fx", d1, GROUP_ID, [price_row(10.0)])}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/groups"):
            return httpx.Response(200, json=GROUPS_JSON)
        if path.endswith(f"/{GROUP_ID}/products"):
            return httpx.Response(200, json=_per_group_products(GROUP_ID, 666906, "Collection - Erika"))
        if path.endswith("/3170/products"):
            return httpx.Response(200, json=_per_group_products(3170, 83542, "Ancient Mew"))
        for day, archive in archives.items():
            if path.endswith(f"prices-{day.isoformat()}.ppmd.7z"):
                return httpx.Response(200, content=archive.read_bytes())
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    root = tmp_path / "proj"
    ingest_range(Paths(root=root), d1, d1, client=client)

    products = Warehouse(Paths(root=root)).load_products()
    ids = set(products["product_id"].to_list())
    assert 666906 in ids  # tracked group, as before
    assert 83542 in ids  # UNTRACKED group 3170 — the whole point


def test_ingest_refreshes_catalog_even_when_products_exist(tmp_path: Path) -> None:
    """A second ingest must re-fetch the catalog. Previously refresh_products
    ran only when products.parquet was missing, so a product re-filed into a
    new group stayed nameless forever."""
    d1, d2 = date(2025, 6, 1), date(2025, 6, 2)
    archives = {
        d1: make_archive(tmp_path / "fx1", d1, GROUP_ID, [price_row(10.0)]),
        d2: make_archive(tmp_path / "fx2", d2, GROUP_ID, [price_row(10.5)]),
    }
    seen: list[str] = []
    extra = {"on": False}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/groups"):
            return httpx.Response(200, json=GROUPS_JSON)
        if path.endswith("/products"):
            seen.append(path)
            if path.endswith("/3170/products") and extra["on"]:
                return httpx.Response(200, json=_per_group_products(3170, 999, "Late Promo"))
            gid = GROUP_ID if f"/{GROUP_ID}/" in path else 3170
            return httpx.Response(200, json=_per_group_products(gid, 666906 if gid == GROUP_ID else 83542, "X"))
        for day, archive in archives.items():
            if path.endswith(f"prices-{day.isoformat()}.ppmd.7z"):
                return httpx.Response(200, content=archive.read_bytes())
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    root = tmp_path / "proj"
    ingest_range(Paths(root=root), d1, d1, client=client)
    first_calls = len(seen)
    assert first_calls > 0

    extra["on"] = True
    ingest_range(Paths(root=root), d2, d2, client=client)
    assert len(seen) > first_calls, "second ingest did not re-fetch the catalog"

    ids = set(Warehouse(Paths(root=root)).load_products()["product_id"].to_list())
    assert 999 in ids, "a newly re-filed product was not picked up"


def test_refresh_products_dedupes_by_product_id(tmp_path: Path) -> None:
    """Fetching every group means a product listed under two groups would
    otherwise appear twice, and a duplicate product row fans out every join
    against the price frame."""
    from pkmn_quant.data.ingest import refresh_products

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/products"):
            return httpx.Response(200, json=_per_group_products(GROUP_ID, 666906, "Dup"))
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    warehouse = Warehouse(Paths(root=tmp_path))
    groups = [
        Group(group_id=GROUP_ID, name="A", abbreviation="A", published_on=date(2025, 2, 20)),
        Group(group_id=3170, name="B", abbreviation="B", published_on=date(2020, 1, 1)),
    ]
    rows = refresh_products(client, warehouse, groups)

    assert rows == 1, f"expected the duplicate to be collapsed, got {rows} rows"
    assert warehouse.load_products()["product_id"].to_list() == [666906]
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_ingest.py -k "untracked or refreshes_catalog or dedupes" -v`
Expected: FAIL — `83542 not in ids` (only tracked groups are catalogued); the second-ingest test fails at `len(seen) > first_calls` (catalog fetched only when the file is missing); the dedup test reports 2 rows.

- [ ] **Step 3: De-duplicate in `refresh_products`**

In `src/pkmn_quant/data/ingest.py`, replace the docstring and the concat tail of `refresh_products`:

```python
def refresh_products(client: httpx.Client, warehouse: Warehouse, groups: list[Group]) -> int:
    """Fetch and store the product catalog. One failing set does not abort the rest.

    Callers pass EVERY tcgcsv group, not just the price-tracked ones:
    TCGplayer re-files products between groups over time, so a product whose
    prices were archived under a tracked set can now list under an untracked
    promo group. Asking only tracked groups leaves 2,017 priced products
    (measured 2026-07-29) with no rarity, hence kind "other", hence invisible
    to every kind-filtered strategy. Fetching names is cheap and independent
    of which sets we ingest prices for.
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
        raise RuntimeError("No product data fetched for any set; aborting.")
    # A product listed under two groups would otherwise duplicate, and a
    # duplicate product row fans out every join against the price frame.
    df = pl.concat(frames).unique(subset=["product_id"], keep="first").sort("product_id")
    warehouse.write_products(df)
    return df.height
```

- [ ] **Step 4: Refresh on every ingest, over every group**

In `ingest_range`, replace the conditional catalog fetch. The existing block reads:

```python
        groups = tcgcsv.fetch_groups(client)
        tracked = tracked_groups(groups, today=end)
        group_ids = {g.group_id for g in tracked}
        if not paths.products.exists():
            print("fetching product catalog (one request per set)...", file=sys.stderr)
            refresh_products(client, warehouse, tracked)
```

Replace with:

```python
        groups = tcgcsv.fetch_groups(client)
        # Prices come from tracked sets only (MIN_SET_RELEASE); NAMES come from
        # every set, because TCGplayer re-files products across groups.
        tracked = tracked_groups(groups, today=end)
        group_ids = {g.group_id for g in tracked}
        print(f"fetching product catalog ({len(groups)} sets)...", file=sys.stderr)
        refresh_products(client, warehouse, groups)
```

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/test_ingest.py -v`
Expected: PASS — the three new cases plus every pre-existing one. `test_ingest_range_end_to_end` still asserts `load_products()["kind"] == ["sealed"]`: its handler returns the same `PRODUCTS_JSON` for both groups, and the new dedup collapses them to that one row, so it keeps passing.

- [ ] **Step 6: Correct CLAUDE.md's false cause**

In `CLAUDE.md`'s Plan 10 status bullet, the parenthetical currently reads (approximately):

> warehouse-wide, past the window, the gap is far larger — 1,845 of 6,493 as of this run, mostly explained by a second, distinct cause: `ingest.py`'s documented one-time catalog fetch never picking up new sets

Replace the explanation with the measured one. Keep the original observation (the gap is real and large) and correct only the cause, noting the re-measurement date:

> warehouse-wide, past the window, the gap is far larger — re-measured 2026-07-29 at 2,017 of 6,665. The cause originally recorded here (the one-time catalog fetch missing new sets) was wrong: measured, that explains 14 of 2,017. The real cause is that TCGplayer **re-files products between groups**, so a promo whose prices were archived under a tracked set now lists under an untracked group (`Miscellaneous Cards & Products`, `Blister Exclusives`, `Nintendo Promos`, …), and the catalog fetch only ever asked the 23 price-tracked sets. Fixed by the catalog-completeness plan (2026-07-29): the catalog now covers all 217 groups on every ingest.

Match the surrounding bullets' voice and density. Do not touch any other status bullet.

- [ ] **Step 7: Run the four gates**

Run: `uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest`
Expected: all PASS. `tests/test_cli_backtest.py` goldens unchanged — they run on synthetic fixtures, not the catalog.

- [ ] **Step 8: Commit**

```bash
git add src/pkmn_quant/data/ingest.py tests/test_ingest.py CLAUDE.md
git commit -m "fix(ingest): catalog every group, every run — TCGplayer re-files products across sets"
```

---

### Task 2: `pkmn refresh-products` command

**Files:**
- Modify: `src/pkmn_quant/cli.py`
- Create: `tests/test_cli_refresh_products.py`

**Interfaces:**
- Consumes: `ingest.refresh_products` (Task 1), `tcgcsv.fetch_groups`, `tcgcsv.make_client`, `Warehouse`, `Paths`.
- Produces: CLI command `pkmn refresh-products [--root PATH]` — refreshes the catalog without ingesting any prices.

`ingest.py`'s docstring has flagged this command as future work since Plan 1. It exists so you can repair the catalog in ~2 minutes without a ~40-minute price ingest.

- [ ] **Step 1: Write the failing test**

Create `tests/test_cli_refresh_products.py`:

```python
"""`pkmn refresh-products` — repair the catalog without a price ingest."""

from datetime import date
from pathlib import Path
from typing import Any

import httpx
import polars as pl
from typer.testing import CliRunner

from pkmn_quant.cli import app
from pkmn_quant.config import Paths
from pkmn_quant.data.warehouse import Warehouse

runner = CliRunner()

GROUPS_JSON: dict[str, Any] = {
    "success": True,
    "results": [
        {
            "groupId": 24541,
            "name": "ME: Ascended Heroes",
            "abbreviation": "MEG",
            "publishedOn": "2025-02-20T00:00:00",
            "categoryId": 3,
        },
        {
            "groupId": 2374,
            "name": "Miscellaneous Cards & Products",
            "abbreviation": "MISC",
            "publishedOn": "2010-01-01T00:00:00",
            "categoryId": 3,
        },
    ],
}


def _products(group_id: int, product_id: int, name: str) -> dict[str, Any]:
    return {
        "success": True,
        "results": [
            {
                "productId": product_id,
                "name": name,
                "groupId": group_id,
                "presaleInfo": {"releasedOn": "2025-02-20T00:00:00"},
                "extendedData": [],
            }
        ],
    }


def _client() -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/groups"):
            return httpx.Response(200, json=GROUPS_JSON)
        if path.endswith("/24541/products"):
            return httpx.Response(200, json=_products(24541, 666906, "Collection - Erika"))
        if path.endswith("/2374/products"):
            return httpx.Response(200, json=_products(2374, 83542, "Ancient Mew"))
        return httpx.Response(404)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_refresh_products_writes_all_groups(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("pkmn_quant.cli.tcgcsv.make_client", _client)
    result = runner.invoke(app, ["refresh-products", "--root", str(tmp_path)])

    assert result.exit_code == 0, result.output
    ids = set(Warehouse(Paths(root=tmp_path)).load_products()["product_id"].to_list())
    assert ids == {666906, 83542}
    assert "2" in result.output  # reports how many products were written


def test_refresh_products_overwrites_an_existing_catalog(tmp_path: Path, monkeypatch) -> None:
    """The command repairs a stale catalog, so it must replace rather than
    append — and must not lose the products it just fetched."""
    wh = Warehouse(Paths(root=tmp_path))
    wh.write_products(
        pl.DataFrame(
            {
                "product_id": [111],
                "group_id": [1],
                "name": ["Stale"],
                "rarity": [None],
                "kind": ["other"],
                "released_on": [date(2020, 1, 1)],
            }
        )
    )
    monkeypatch.setattr("pkmn_quant.cli.tcgcsv.make_client", _client)
    result = runner.invoke(app, ["refresh-products", "--root", str(tmp_path)])

    assert result.exit_code == 0, result.output
    ids = set(wh.load_products()["product_id"].to_list())
    assert ids == {666906, 83542}
    assert 111 not in ids
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_cli_refresh_products.py -v`
Expected: FAIL — typer exits non-zero with "No such command 'refresh-products'".

- [ ] **Step 3: Add the command**

In `src/pkmn_quant/cli.py`, confirm `tcgcsv` is imported at module scope (add `from pkmn_quant.data import tcgcsv` alongside the existing data imports if it is not — the test monkeypatches `pkmn_quant.cli.tcgcsv.make_client`, so the name must be reachable there). Add after the `ingest` command:

```python
@app.command("refresh-products")
def refresh_products_cmd(
    root: Path = typer.Option(Path("."), help="Project root holding the data/ directory."),
) -> None:
    """Re-fetch the product catalog for every set, without ingesting prices.

    Names come from every tcgcsv group, not just the price-tracked ones:
    TCGplayer re-files products between groups, so a product priced under a
    tracked set can end up listed under an untracked promo group. A product
    with no catalog row has no rarity, so it is tagged kind "other" and is
    invisible to every kind-filtered strategy.
    """
    with tcgcsv.make_client() as client:
        groups = tcgcsv.fetch_groups(client)
        typer.echo(f"fetching product catalog for {len(groups)} sets...")
        rows = refresh_products(client, Warehouse(Paths(root=root)), groups)
    typer.echo(f"wrote {rows} products")
```

Import `refresh_products` from `pkmn_quant.data.ingest` at module scope beside the existing `ingest_range` import.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_cli_refresh_products.py -v`
Expected: PASS — both cases.

- [ ] **Step 5: Run the four gates**

Run: `uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add src/pkmn_quant/cli.py tests/test_cli_refresh_products.py
git commit -m "feat(cli): pkmn refresh-products — repair the catalog without a price ingest"
```

---

### Task 3: Unpriced catalog rows must not leak into any universe

**Files:**
- Create: `tests/test_catalog_superset.py`

**Interfaces:**
- Consumes: `PreparedMarket`, `Backtest` (Python engine), `NativeBacktest` (C++ engine), the strategy registry.
- Produces: no source change if the guard already holds — this task is a regression net.

**Why this task exists.** Before this plan, `products.parquet` was a *subset* problem: rows missing for priced ids, which crashed `NativeBacktest.run()` with a `KeyError` on the first full-data run (Plan 10, fixed by tagging missing rows kind "other"). This plan inverts it: the catalog now covers all 217 groups, so it contains **thousands of products that have no price rows at all** in the tracked-set price history. Strategies filter `ctx.products` by `kind` and `released_on` to build candidate sets. If a strategy can propose an order for a product that has no mark, that is a real bug — and the two engines must agree about it. Verify, don't assume.

- [ ] **Step 1: Write the tests**

Create `tests/test_catalog_superset.py`:

```python
"""The catalog is a SUPERSET of the priced universe.

Since the catalog-completeness plan, products.parquet covers all 217 tcgcsv
groups while prices cover only the tracked sets, so thousands of catalog rows
have no price data. Strategies build candidates from ctx.products; a candidate
with no mark must never become an order, and both engines must agree.
"""

from datetime import date, timedelta
from pathlib import Path

import polars as pl
import pytest

from pkmn_quant.config import Paths
from pkmn_quant.data.transforms import PRICE_SCHEMA
from pkmn_quant.data.warehouse import Warehouse

START = date(2025, 1, 1)
DAYS = 40


def _seed(root: Path) -> None:
    """One priced sealed product, plus THREE catalog-only sealed products of
    the same vintage that never print a price. The decoys are age-eligible for
    sealed-accumulation, so a strategy that trusts ctx.products blindly will
    try to trade them."""
    wh = Warehouse(Paths(root=root))
    for i in range(DAYS):
        day = START + timedelta(days=i)
        wh.write_prices(
            day,
            pl.DataFrame(
                [
                    {
                        "date": day,
                        "product_id": 1,
                        "sub_type": "Normal",
                        "low": 9.0 + i,
                        "mid": 11.5 + i,
                        "high": 30.0 + i,
                        "market": 10.0 + i,
                    }
                ],
                schema=PRICE_SCHEMA,
            ),
        )
    wh.write_products(
        pl.DataFrame(
            {
                "product_id": [1, 900, 901, 902],
                "group_id": [1, 2374, 2289, 1423],
                "name": [
                    "Alpha Booster Box",
                    "Ancient Mew",
                    "Blister Exclusive",
                    "Nintendo Promo",
                ],
                "rarity": [None, "Promo", "Promo", "Promo"],
                "kind": ["sealed", "sealed", "sealed", "sealed"],
                "released_on": [date(2024, 6, 1)] * 4,
            }
        )
    )


@pytest.mark.parametrize("strategy_name", ["sealed-accumulation", "dip-buyer", "xs-momentum"])
def test_no_fill_on_a_catalog_only_product(tmp_path: Path, strategy_name: str) -> None:
    """A product in the catalog but absent from the price history must never
    be filled — it has no mark, so any fill price would be fabricated."""
    from pkmn_quant.research.backtest_run import run_single_backtest

    _seed(tmp_path)
    out = run_single_backtest(
        root=tmp_path,
        strategy_name=strategy_name,
        params={},
        cash=10_000.0,
        holdings=[],
        start=START,
        end=START + timedelta(days=DAYS - 1),
    )
    fills = pl.read_parquet(out.artifact_dir / "fills.parquet")
    traded = set(fills["product_id"].to_list())
    assert traded <= {1}, f"{strategy_name} traded catalog-only products: {traded - {1}}"


def test_both_engines_agree_with_a_superset_catalog(tmp_path: Path) -> None:
    """Bit-for-bit parity must survive a catalog that is a strict superset of
    the priced universe. Plan 10's differential test covered the opposite gap
    (catalog rows MISSING for priced ids); this covers the new shape."""
    from pkmn_quant.engine.backtest import Backtest
    from pkmn_quant.engine.native import NativeBacktest, NativeStrategySpec
    from pkmn_quant.research.prepared import PreparedMarket
    from pkmn_quant.strategies.sealed_accumulation import SealedAccumulation

    _seed(tmp_path)
    end = START + timedelta(days=DAYS - 1)
    prepared = PreparedMarket.prepare(Paths(root=tmp_path), START, end)

    py = Backtest(market=prepared.market, strategy=SealedAccumulation(), cash=10_000.0).run()
    native = NativeBacktest(
        market=prepared.market,
        strategy=NativeStrategySpec(name="sealed-accumulation", params={}),
        cash=10_000.0,
    ).run()

    assert py.equity_curve["equity"].to_list() == native.equity_curve["equity"].to_list()
    assert len(py.fills) == len(native.fills)
    for a, b in zip(py.fills, native.fills, strict=True):
        assert (a.day, a.asset, a.quantity, a.price) == (b.day, b.asset, b.quantity, b.price)
```

Adapt the `Backtest` / `NativeBacktest` / `PreparedMarket` construction to the exact signatures in `tests/test_native_parity.py` — read that file first and mirror its setup rather than inventing arguments. If a strategy in the parametrize list has required params with no default, pass its `ParamSpec` defaults via `resolve_params(strategy_name, {})`.

- [ ] **Step 2: Run the tests**

Run: `uv run pytest tests/test_catalog_superset.py -v`
Expected: **PASS is the likely outcome** — strategies intersect their candidates against price history before ordering, so the guard should already hold. This task is a regression net, not a known bug.

**If any case FAILS, stop and report it.** A fill on a product with no mark is a real defect with real research consequences, and the fix belongs in the strategy or engine — not in this test. Do not weaken the assertion. Report which strategy, which product, and the fill price it invented.

- [ ] **Step 3: Run the four gates**

Run: `uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest`
Expected: all PASS.

- [ ] **Step 4: Commit**

```bash
git add tests/test_catalog_superset.py
git commit -m "test: pin that catalog-only products never trade, both engines"
```

---

### Task 4: Refresh the real catalog and measure the universe shift

**Files:**
- Create: `scripts/catalog_shift.py`
- Modify: `data/warehouse/products.parquet` (regenerated — back it up first)

**Interfaces:**
- Consumes: `pkmn refresh-products` (Task 2), `Warehouse.query`.
- Produces: a before/after table of the priced universe by `kind`, and the exact count of newly-named products. Task 5 quotes these numbers.

This is the first task that touches real data. It writes `products.parquet` and nothing else.

- [ ] **Step 1: Back up the current catalog**

```bash
cp data/warehouse/products.parquet /tmp/products-before-catalog-fix.parquet
ls -la /tmp/products-before-catalog-fix.parquet
```

Confirm the copy exists before continuing. If anything later looks wrong, this file restores the prior state exactly.

- [ ] **Step 2: Write the measurement script**

Create `scripts/catalog_shift.py`:

```python
"""Measure how completing the catalog changes the priced universe.

Run once before `pkmn refresh-products` and once after; the two outputs are
the before/after this plan documents.
"""

import sys
from pathlib import Path

from pkmn_quant.config import Paths
from pkmn_quant.data.warehouse import Warehouse


def main(root: Path) -> None:
    wh = Warehouse(Paths(root=root))
    print(
        wh.query(
            """
            SELECT coalesce(p.kind, 'UNCATALOGED') AS kind,
                   count(DISTINCT s.product_id) AS n_priced_products
            FROM (SELECT DISTINCT product_id FROM prices) s
            LEFT JOIN products p ON p.product_id = s.product_id
            GROUP BY 1 ORDER BY 2 DESC
            """
        )
    )
    print(wh.query("SELECT count(*) AS catalog_rows FROM products"))


if __name__ == "__main__":
    main(Path(sys.argv[1] if len(sys.argv) > 1 else "."))
```

- [ ] **Step 3: Capture the BEFORE state**

```bash
uv run python scripts/catalog_shift.py . | tee /tmp/catalog-before.txt
```

Expected (measured 2026-07-29, yours may differ slightly): roughly 2,017 `UNCATALOGED`, with the rest split across `sealed` / `single` / `excluded`, and ~4,673 catalog rows. **Record the real numbers.**

- [ ] **Step 4: Refresh the catalog**

```bash
time uv run pkmn refresh-products --root .
```

Expected: "fetching product catalog for 217 sets...", then "wrote N products" with N well above 4,673 (the catalog now spans every group, including sets whose prices are not ingested). Expect a couple of minutes. **Report the real N and the real elapsed time.** Report any `warning: products fetch failed for group ...` lines — a failed group means those products stay nameless.

- [ ] **Step 5: Capture the AFTER state**

```bash
uv run python scripts/catalog_shift.py . | tee /tmp/catalog-after.txt
diff /tmp/catalog-before.txt /tmp/catalog-after.txt || true
```

Expected: `UNCATALOGED` collapses from ~2,017 to a small residue (~8 products are in no tcgcsv group at all and legitimately stay `other`); `sealed` and `single` each grow by several hundred. **Record the real numbers — they are the input to Task 5.**

If `UNCATALOGED` does *not* drop substantially, stop and report. That would mean the refresh did not do what Task 1 intends, and Task 5's re-runs would be measuring nothing.

- [ ] **Step 6: Sanity-check that nothing lost its name**

```bash
uv run python -c "
import polars as pl
before = pl.read_parquet('/tmp/products-before-catalog-fix.parquet')
after = pl.read_parquet('data/warehouse/products.parquet')
lost = set(before['product_id'].to_list()) - set(after['product_id'].to_list())
print('products present before but MISSING after:', len(lost))
print(sorted(lost)[:20])
"
```

Expected: **0**. The new catalog is a superset. If any product disappeared, stop and report — restore with `cp /tmp/products-before-catalog-fix.parquet data/warehouse/products.parquet`.

- [ ] **Step 7: Run the four gates**

Run: `uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest`
Expected: all PASS. The suite uses synthetic fixtures, so the real catalog change must not move any test.

- [ ] **Step 8: Commit the script**

`products.parquet` is gitignored; only the script is committed.

```bash
git add scripts/catalog_shift.py
git commit -m "chore(scripts): catalog_shift — measure the priced universe by kind"
```

---

### Task 5: Re-run the research suite and document the shift

**Files:**
- Modify: `docs/research-findings-2026-07.md`
- Modify: `CLAUDE.md`

**Interfaces:**
- Consumes: the corrected catalog (Task 4), `pkmn backtest`, `pkmn walkforward`, `pkmn evaluate`, `pkmn runs list`.
- Produces: a new findings section with before/after numbers, and a CLAUDE.md status bullet.

**Why the full suite.** Task 4 moves ~1,063 products into the `sealed` and `single` universes. Five of seven strategies filter on `kind`, so their candidate sets grow and every backtest number moves. This repo's convention is that a change which moves results gets a documented re-run — that is what makes `docs/research-findings-2026-07.md` trustworthy. Prior results in that file were computed against the incomplete catalog and must be labelled as such rather than silently left to look comparable.

- [ ] **Step 1: Re-run the benchmark**

```bash
uv run pkmn backtest --start 2024-03-01 --end 2026-06-30 2>&1 | tail -20
```

Expected: the buy-and-hold sealed benchmark over the full range. The pre-catalog reference is **+183.7% total return (impact-on)**; the sealed universe is now larger, so expect movement. **Record the real total_return, the fill count, and the run_id.**

- [ ] **Step 2: Re-run every strategy's walk-forward**

Run each and record the stitched OOS total return and the run_id. Plan 11 measured ~20s per strategy on the native engine, so this is minutes:

```bash
for s in sealed-accumulation dip-buyer xs-momentum cost-aware-reversion ml-ranker ml-ranker-v2; do
  echo "=== $s ==="
  uv run pkmn walkforward --strategy "$s" --start 2024-03-01 --end 2026-06-30 --trials 15 2>&1 | tail -8
done
```

ml-ranker and ml-ranker-v2 run through the Python callback bridge and are slower than the native strategies; let them finish. **Report every strategy's real numbers, including any that error.**

- [ ] **Step 3: Re-run the cross-strategy rigor evaluation**

```bash
uv run pkmn evaluate 2>&1 | tail -30
```

Expected: a new `data/results/evaluate-<date>/` report plus a registry record covering the six strategies against the buy-and-hold benchmark. **Record the deflated Sharpe per strategy and the joint Reality Check p-value.** The pre-catalog reference is p = 1.0000 with every DSR between 0.000 and 0.017.

- [ ] **Step 4: Write the findings section**

Append a new section to `docs/research-findings-2026-07.md`, matching the voice and structure of the existing plan sections (they state the hypothesis, the measurement, and the honest conclusion — including negative results). It must contain:

- **What changed and why**: the catalog covered 23 of 217 groups; TCGplayer re-files products between groups; 2,017 priced products had no rarity and so were `kind: "other"`, invisible to the five kind-filtered strategies. Cite Task 4's real before/after table.
- **How many products moved**, by kind, from Task 4 Step 5's real output.
- **A before/after table** of every strategy's stitched OOS total return, with the pre-catalog numbers taken from the existing sections of this same file, plus the buy-and-hold benchmark.
- **The rigor comparison**: DSR per strategy and the joint Reality Check p-value, before and after.
- **The honest conclusion.** State plainly whether the corrected universe changes any research conclusion. If nothing beats buy-and-hold — the standing result across every prior plan — say so directly. Do not dress up a larger universe as an improvement if the numbers do not support it.
- **The caveat that prior sections predate this fix.** Add a one-line note at the top of the file, or on each superseded section, saying those numbers were computed against an incomplete catalog.

- [ ] **Step 5: Update CLAUDE.md**

- Add a status bullet for this plan, matching the existing bullets' density: what changed (catalog covers all 217 groups on every ingest, `pkmn refresh-products` added), the measured before/after (products by kind, strategy returns, Reality Check p), the test counts from your own gate run, and the honest conclusion from Step 4.
- In the `Commands` block, add `uv run pkmn refresh-products` with a one-line description.
- In the `src/pkmn_quant/data/` layout bullet, note that the catalog covers every group while prices cover only `MIN_SET_RELEASE`-onward sets, and that the two are deliberately different scopes.
- In the "Conventions and gotchas" section, update the buy-and-hold benchmark figures if Step 1 moved them — that bullet tells every future session which numbers to compare against, so a stale figure there misleads every later comparison.

- [ ] **Step 6: Full verification sweep**

```bash
uv sync --group api && uv run pytest
uv run ruff check . && uv run ruff format --check . && uv run mypy
uv run --group dashboard pytest tests/test_dashboard.py
cd web && npm run check && npm run build
```

Expected: all PASS. Report the real counts.

- [ ] **Step 7: Commit**

```bash
git add docs/research-findings-2026-07.md CLAUDE.md
git commit -m "docs: catalog completeness — universe shift and full re-run findings"
```

---

## Self-review notes

- **Spec coverage:** catalog every group (Task 1) ✓; refresh on every ingest rather than only when the file is missing (Task 1) ✓; `pkmn refresh-products` command, flagged as future work since Plan 1 (Task 2) ✓; dedup guard, newly required because fetching every group makes cross-listing possible (Task 1) ✓; the superset-catalog safety net, the inverse of the Plan 10 subset bug (Task 3) ✓; real-data refresh with a backup and a no-products-lost check (Task 4) ✓; full walk-forward re-run, `pkmn evaluate`, and documented before/after (Task 5) ✓; correcting CLAUDE.md's false cause (Task 1 Step 6) ✓. Out of scope and in no task: `MIN_SET_RELEASE` / `tracked_groups` changes, any price re-ingest, any `cpp/` change, any API or web change ✓.
- **Type consistency:** `refresh_products(client, warehouse, groups) -> int` keeps its signature across Tasks 1 and 2 — only its callers' argument changes, from `tracked` to `groups`. `tracked_groups` keeps its meaning (price scope) and is still used for `group_ids`. `Group` is constructed in Task 2's test with the same four fields the dataclass declares.
- **The one behavioral risk** is Task 3's: a catalog that is a strict superset of the priced universe is a shape the code has never seen. It is a regression net rather than a known bug, and the task says explicitly to stop and report rather than weaken the assertion if it fires.
- **Known deliberate tradeoff:** every `pkmn ingest` now spends ~2 minutes on 217 catalog requests before the ~40-minute price ingest. The alternative — refreshing only on demand — is how the gap opened in the first place, and it reopens silently every time TCGplayer re-files a product.
- **Sequencing constraint:** Tasks 4 and 5 touch real data and must run in order, after Tasks 1-3 are green. Task 5's numbers are meaningless if Task 4's refresh did not actually collapse the `UNCATALOGED` count, which is why Task 4 Step 5 says to stop and report if it does not.
