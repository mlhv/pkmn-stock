# pkmn_quant Plan 1: Foundation & Data Layer

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A tested, typed Python package that ingests tcgcsv.com daily price archives into a Parquet/DuckDB warehouse with quality gates, exposed via a `pkmn ingest` CLI.

**Architecture:** `src/pkmn_quant/data/` holds an httpx-based tcgcsv client, pure Polars transforms (JSON → typed frames + sealed/single classification), quality gates that quarantine bad rows with reason codes, and a Parquet warehouse partitioned by date and queried through DuckDB. `ingest.py` orchestrates; `cli.py` (Typer) is the entry point. This is Plan 1 of 3 (spec: `docs/superpowers/specs/2026-06-09-pkmn-quant-design.md`); the backtest engine (Plan 2) consumes only the warehouse.

**Tech Stack:** Python 3.12, uv, Polars, DuckDB, httpx, py7zr, Typer, pytest, ruff, mypy --strict, GitHub Actions.

**Verified API facts (checked live 2026-06-10):**
- Groups: `GET https://tcgcsv.com/tcgplayer/3/groups` → `{"results": [{"groupId": 24541, "name": "ME: Ascended Heroes", "abbreviation": "MEG", "publishedOn": "2026-02-20T00:00:00", ...}]}`
- Products: `GET https://tcgcsv.com/tcgplayer/3/{groupId}/products` → `{"results": [{"productId", "name", "groupId", "presaleInfo": {"releasedOn": ...}, "extendedData": [{"name": "Rarity", "value": ...}, ...]}]}`
- Prices (per group, current day): `GET https://tcgcsv.com/tcgplayer/3/{groupId}/prices` → `{"results": [{"productId", "lowPrice", "midPrice", "highPrice", "marketPrice", "directLowPrice", "subTypeName"}]}`
- Daily archives: `https://tcgcsv.com/archive/tcgplayer/prices-YYYY-MM-DD.ppmd.7z` (HTTP 200 confirmed, ~3.4 MB/day). Expected internal layout: `<YYYY-MM-DD>/<categoryId>/<groupId>/prices` where each `prices` file is the same JSON as the live endpoint. **Task 9 verifies this layout against a real archive; if it differs, fix `extract_group_prices` there.**
- Archives exist from 2024-02-08 onward. Pokemon categoryId is 3.

---

### Task 1: Package scaffold

**Files:**
- Create: `pyproject.toml`
- Create: `src/pkmn_quant/__init__.py`
- Create: `src/pkmn_quant/py.typed`
- Create: `src/pkmn_quant/config.py`
- Create: `tests/test_config.py`
- Modify: `.gitignore`

- [ ] **Step 1: Write pyproject.toml**

```toml
[project]
name = "pkmn-quant"
version = "0.1.0"
description = "Backtesting and signal generation for Pokemon card prices"
requires-python = ">=3.12"
dependencies = [
    "duckdb>=1.1",
    "httpx>=0.27",
    "polars>=1.10",
    "py7zr>=0.22",
    "typer>=0.12",
]

[project.scripts]
pkmn = "pkmn_quant.cli:app"

[dependency-groups]
dev = [
    "mypy>=1.13",
    "pytest>=8.3",
    "pytest-cov>=6.0",
    "ruff>=0.8",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/pkmn_quant"]

[tool.ruff]
line-length = 100
target-version = "py312"

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B", "SIM", "RUF"]

[tool.mypy]
strict = true
files = ["src"]

# NOTE: no ignore_missing_imports overrides — py7zr, duckdb, and polars all
# ship py.typed. If mypy errors on an import in a later task, add a targeted
# override for that one module then.

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-q"
```

Note: `[project.scripts] pkmn = "pkmn_quant.cli:app"` will fail to resolve until Task 8 creates `cli.py`; that's fine — uv only resolves it when the script is invoked.

- [ ] **Step 2: Write the failing test**

`tests/test_config.py`:

```python
from datetime import date
from pathlib import Path

from pkmn_quant.config import EARLIEST_ARCHIVE_DATE, POKEMON_CATEGORY_ID, Paths


def test_constants() -> None:
    assert POKEMON_CATEGORY_ID == 3
    # Literal on the left: ruff SIM300 treats ALL_CAPS names as constants and
    # flags constant == call() as a Yoda condition otherwise.
    assert date(2024, 2, 8) == EARLIEST_ARCHIVE_DATE


def test_paths_layout() -> None:
    paths = Paths(root=Path("/tmp/proj"))
    assert paths.raw_archives == Path("/tmp/proj/data/raw/archives")
    assert paths.warehouse == Path("/tmp/proj/data/warehouse")
    assert paths.prices == Path("/tmp/proj/data/warehouse/prices")
    assert paths.quarantine == Path("/tmp/proj/data/warehouse/quarantine")
    assert paths.products == Path("/tmp/proj/data/warehouse/products.parquet")
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/test_config.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'pkmn_quant'` (or import error for `config`)

- [ ] **Step 4: Write the package**

`src/pkmn_quant/__init__.py`:

```python
"""Backtesting and signal generation for Pokemon card prices."""

__version__ = "0.1.0"
```

`src/pkmn_quant/py.typed`: empty file (marks the package as typed for mypy).

`src/pkmn_quant/config.py`:

```python
"""Project-wide constants and filesystem layout."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

POKEMON_CATEGORY_ID = 3
TCGCSV_BASE_URL = "https://tcgcsv.com"
# tcgcsv daily price archives exist from this date onward.
EARLIEST_ARCHIVE_DATE = date(2024, 2, 8)
# Sets released on/after this date form the tradeable universe.
MIN_SET_RELEASE = date(2024, 1, 1)


@dataclass(frozen=True)
class Paths:
    """Filesystem layout rooted at the project directory."""

    root: Path

    @property
    def raw_archives(self) -> Path:
        return self.root / "data" / "raw" / "archives"

    @property
    def warehouse(self) -> Path:
        return self.root / "data" / "warehouse"

    @property
    def prices(self) -> Path:
        return self.warehouse / "prices"

    @property
    def quarantine(self) -> Path:
        return self.warehouse / "quarantine"

    @property
    def products(self) -> Path:
        return self.warehouse / "products.parquet"
```

- [ ] **Step 5: Sync and run tests to verify they pass**

Run: `uv sync && uv run pytest tests/test_config.py -v`
Expected: 2 PASSED

- [ ] **Step 6: Lint and typecheck**

Run: `uv run ruff check . && uv run ruff format . && uv run mypy`
Expected: no errors (ruff format may reformat files; that's fine)

- [ ] **Step 7: Ensure data/ stays out of git**

Check `.gitignore` contains `data/` (the repo already ignores it if `git status` shows clean with `data/MEAscendedHeroesProductsAndPrices-2.csv` present). If missing, append:

```
data/
```

Also append uv/python artifacts if not present:

```
.venv/
__pycache__/
*.egg-info/
.pytest_cache/
.mypy_cache/
.ruff_cache/
.coverage
```

- [ ] **Step 8: Commit**

```bash
git add pyproject.toml uv.lock src tests .gitignore
git commit -m "feat: scaffold pkmn_quant package with config and tooling"
```

---

### Task 2: CI workflow

**Files:**
- Create: `.github/workflows/ci.yml`

- [ ] **Step 1: Write the workflow**

```yaml
name: CI

on:
  push:
    branches: [main]
  pull_request:

jobs:
  checks:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
        with:
          python-version: "3.12"
      - run: uv sync
      - run: uv run ruff check .
      - run: uv run ruff format --check .
      - run: uv run mypy
      - run: uv run pytest --cov=pkmn_quant --cov-report=term-missing
```

- [ ] **Step 2: Verify the commands locally (CI parity)**

Run: `uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest --cov=pkmn_quant --cov-report=term-missing`
Expected: all pass

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: add lint, typecheck, and test workflow"
```

---

### Task 3: tcgcsv client (groups, archive download, extraction)

**Files:**
- Create: `src/pkmn_quant/data/__init__.py` (empty)
- Create: `src/pkmn_quant/data/tcgcsv.py`
- Test: `tests/test_tcgcsv.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_tcgcsv.py`:

```python
import json
from datetime import date
from pathlib import Path
from typing import Any

import httpx
import py7zr

from pkmn_quant.data import tcgcsv

GROUPS_PAYLOAD: dict[str, Any] = {
    "totalItems": 2,
    "success": True,
    "results": [
        {
            "groupId": 24541,
            "name": "ME: Ascended Heroes",
            "abbreviation": "MEG",
            "publishedOn": "2026-02-20T00:00:00",
            "categoryId": 3,
        },
        {
            "groupId": 3170,
            "name": "SV: Scarlet & Violet 151",
            "abbreviation": "MEW",
            "publishedOn": "2023-09-22T00:00:00",
            "categoryId": 3,
        },
    ],
}

PRICE_ROWS: list[dict[str, Any]] = [
    {
        "productId": 666906,
        "lowPrice": 23.95,
        "midPrice": 32.81,
        "highPrice": 55.0,
        "marketPrice": 32.98,
        "directLowPrice": None,
        "subTypeName": "Normal",
    }
]


def test_parse_groups() -> None:
    groups = tcgcsv.parse_groups(GROUPS_PAYLOAD)
    assert len(groups) == 2
    assert groups[0].group_id == 24541
    assert groups[0].name == "ME: Ascended Heroes"
    assert groups[0].published_on == date(2026, 2, 20)


def test_archive_url() -> None:
    assert (
        tcgcsv.archive_url(date(2025, 6, 1))
        == "https://tcgcsv.com/archive/tcgplayer/prices-2025-06-01.ppmd.7z"
    )


def make_archive(tmp_path: Path, day: date, group_id: int, rows: list[dict[str, Any]]) -> Path:
    """Build a miniature tcgcsv daily archive: <date>/3/<groupId>/prices inside a 7z."""
    src = tmp_path / "archive-src" / day.isoformat() / "3" / str(group_id)
    src.mkdir(parents=True)
    (src / "prices").write_text(json.dumps({"success": True, "results": rows}))
    archive = tmp_path / f"prices-{day.isoformat()}.ppmd.7z"
    with py7zr.SevenZipFile(archive, "w") as z:
        z.writeall(tmp_path / "archive-src" / day.isoformat(), arcname=day.isoformat())
    return archive


def test_extract_group_prices(tmp_path: Path) -> None:
    day = date(2025, 6, 1)
    archive = make_archive(tmp_path, day, 24541, PRICE_ROWS)
    out = tcgcsv.extract_group_prices(archive, day, {24541, 99999})
    assert out == {24541: PRICE_ROWS}


def test_extract_returns_empty_for_unknown_groups(tmp_path: Path) -> None:
    day = date(2025, 6, 1)
    archive = make_archive(tmp_path, day, 24541, PRICE_ROWS)
    assert tcgcsv.extract_group_prices(archive, day, {12345}) == {}


def test_download_archive_caches(tmp_path: Path) -> None:
    day = date(2025, 6, 1)
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, content=b"archive-bytes")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    first = tcgcsv.download_archive(client, day, tmp_path)
    second = tcgcsv.download_archive(client, day, tmp_path)
    assert first == second
    assert first.read_bytes() == b"archive-bytes"
    assert calls == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_tcgcsv.py -v`
Expected: FAIL with `ImportError` / `ModuleNotFoundError` for `pkmn_quant.data.tcgcsv`

- [ ] **Step 3: Implement the client**

`src/pkmn_quant/data/__init__.py`: empty file.

`src/pkmn_quant/data/tcgcsv.py`:

```python
"""HTTP client helpers for tcgcsv.com (a daily mirror of TCGplayer data)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

import httpx
import py7zr

from pkmn_quant.config import POKEMON_CATEGORY_ID, TCGCSV_BASE_URL


@dataclass(frozen=True)
class Group:
    """A TCGplayer group, i.e. one Pokemon set or product line."""

    group_id: int
    name: str
    abbreviation: str
    published_on: date


def parse_groups(payload: dict[str, Any]) -> list[Group]:
    return [
        Group(
            group_id=row["groupId"],
            name=row["name"],
            abbreviation=row["abbreviation"],
            published_on=datetime.fromisoformat(row["publishedOn"]).date(),
        )
        for row in payload["results"]
    ]


def fetch_groups(client: httpx.Client) -> list[Group]:
    resp = client.get(f"{TCGCSV_BASE_URL}/tcgplayer/{POKEMON_CATEGORY_ID}/groups")
    resp.raise_for_status()
    return parse_groups(resp.json())


def fetch_products(client: httpx.Client, group_id: int) -> list[dict[str, Any]]:
    resp = client.get(f"{TCGCSV_BASE_URL}/tcgplayer/{POKEMON_CATEGORY_ID}/{group_id}/products")
    resp.raise_for_status()
    results: list[dict[str, Any]] = resp.json()["results"]
    return results


def archive_url(day: date) -> str:
    return f"{TCGCSV_BASE_URL}/archive/tcgplayer/prices-{day.isoformat()}.ppmd.7z"


def download_archive(client: httpx.Client, day: date, dest_dir: Path) -> Path:
    """Download one daily price archive, skipping if already cached on disk."""
    dest = dest_dir / f"prices-{day.isoformat()}.ppmd.7z"
    if dest.exists():
        return dest
    dest_dir.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".tmp")
    with client.stream("GET", archive_url(day)) as resp:
        resp.raise_for_status()
        with tmp.open("wb") as fh:
            for chunk in resp.iter_bytes():
                fh.write(chunk)
    tmp.rename(dest)
    return dest


def extract_group_prices(
    archive: Path, day: date, group_ids: set[int]
) -> dict[int, list[dict[str, Any]]]:
    """Pull price rows for the given groups out of a daily archive.

    Archive layout: <YYYY-MM-DD>/<categoryId>/<groupId>/prices, each file the
    same JSON shape as the live /prices endpoint.
    """
    wanted = {f"{day.isoformat()}/{POKEMON_CATEGORY_ID}/{gid}/prices": gid for gid in group_ids}
    out: dict[int, list[dict[str, Any]]] = {}
    with py7zr.SevenZipFile(archive, mode="r") as z:
        names = [n for n in z.getnames() if n in wanted]
        if not names:
            return out
        extracted = z.read(targets=names)
    for name, bio in extracted.items():
        payload = json.loads(bio.read().decode("utf-8"))
        out[wanted[name]] = payload["results"]
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_tcgcsv.py -v`
Expected: 5 PASSED. If `z.read(targets=names)` raises a TypeError on your py7zr version, use `z.read(names)` (positional) — the parameter was renamed across versions.

- [ ] **Step 5: Lint, typecheck, commit**

```bash
uv run ruff check . && uv run ruff format . && uv run mypy
git add src/pkmn_quant/data tests/test_tcgcsv.py
git commit -m "feat: tcgcsv client - groups, archive download, price extraction"
```

---

### Task 4: Sealed/single classification

**Files:**
- Create: `src/pkmn_quant/data/classify.py`
- Test: `tests/test_classify.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_classify.py`:

```python
from pkmn_quant.data.classify import classify_kind


def test_rarity_means_single() -> None:
    assert classify_kind("Double Rare") == "single"
    assert classify_kind("Special Illustration Rare") == "single"
    assert classify_kind("Common") == "single"
    assert classify_kind("Promo") == "single"


def test_no_rarity_means_sealed() -> None:
    # Real sealed products from the ME: Ascended Heroes set have null extRarity.
    assert classify_kind(None) == "sealed"


def test_code_cards_are_excluded() -> None:
    assert classify_kind("Code Card") == "excluded"
    assert classify_kind("code card") == "excluded"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_classify.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement**

`src/pkmn_quant/data/classify.py`:

```python
"""Sealed-vs-single classification for TCGplayer products."""

from __future__ import annotations

from typing import Literal

Kind = Literal["single", "sealed", "excluded"]


def classify_kind(rarity: str | None) -> Kind:
    """Classify a product by its TCGplayer rarity field.

    Singles always carry a rarity; sealed products (boxes, ETBs, collections)
    never do. Code cards are digital redemption codes, not tradeable assets.
    """
    if rarity is None:
        return "sealed"
    if rarity.strip().lower() == "code card":
        return "excluded"
    return "single"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_classify.py -v`
Expected: 3 PASSED

- [ ] **Step 5: Commit**

```bash
git add src/pkmn_quant/data/classify.py tests/test_classify.py
git commit -m "feat: classify products as single/sealed/excluded via rarity"
```

---

### Task 5: Pure transforms (JSON → typed Polars frames)

**Files:**
- Create: `src/pkmn_quant/data/transforms.py`
- Test: `tests/test_transforms.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_transforms.py`:

```python
from datetime import date
from typing import Any

import polars as pl

from pkmn_quant.data.transforms import prices_frame, products_frame

PRODUCT_SINGLE: dict[str, Any] = {
    "productId": 666999,
    "name": "Mega Charizard X ex - 200/180",
    "groupId": 24541,
    "presaleInfo": {"isPresale": False, "releasedOn": "2026-02-20T00:00:00", "note": None},
    "extendedData": [
        {"name": "Number", "displayName": "Number", "value": "200/180"},
        {"name": "Rarity", "displayName": "Rarity", "value": "Special Illustration Rare"},
    ],
}

PRODUCT_SEALED: dict[str, Any] = {
    "productId": 666906,
    "name": "Mega Evolution: Ascended Heroes Collection - Erika",
    "groupId": 24541,
    "presaleInfo": {"isPresale": False, "releasedOn": None, "note": None},
    "extendedData": [],
}


def test_products_frame() -> None:
    df = products_frame([PRODUCT_SINGLE, PRODUCT_SEALED])
    assert df.height == 2
    single = df.filter(pl.col("product_id") == 666999)
    assert single["rarity"][0] == "Special Illustration Rare"
    assert single["kind"][0] == "single"
    assert single["released_on"][0] == date(2026, 2, 20)
    sealed = df.filter(pl.col("product_id") == 666906)
    assert sealed["rarity"][0] is None
    assert sealed["kind"][0] == "sealed"
    assert sealed["released_on"][0] is None


def test_prices_frame() -> None:
    day = date(2025, 6, 1)
    rows_by_group = {
        24541: [
            {
                "productId": 666906,
                "lowPrice": 23.95,
                "midPrice": 32.81,
                "highPrice": 55.0,
                "marketPrice": 32.98,
                "directLowPrice": None,
                "subTypeName": "Normal",
            }
        ]
    }
    df = prices_frame(day, rows_by_group)
    assert df.height == 1
    row = df.row(0, named=True)
    assert row["date"] == day
    assert row["product_id"] == 666906
    assert row["sub_type"] == "Normal"
    assert row["market"] == 32.98


def test_empty_frames_have_schema() -> None:
    df = prices_frame(date(2025, 6, 1), {})
    assert df.height == 0
    assert set(df.columns) == {"date", "product_id", "sub_type", "low", "mid", "high", "market"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_transforms.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement**

`src/pkmn_quant/data/transforms.py`:

```python
"""Pure transforms from tcgcsv JSON payloads to warehouse tables."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

import polars as pl

from pkmn_quant.data.classify import classify_kind

PRICE_SCHEMA: dict[str, pl.DataType | type[pl.DataType]] = {
    "date": pl.Date,
    "product_id": pl.Int64,
    "sub_type": pl.Utf8,
    "low": pl.Float64,
    "mid": pl.Float64,
    "high": pl.Float64,
    "market": pl.Float64,
}

PRODUCT_SCHEMA: dict[str, pl.DataType | type[pl.DataType]] = {
    "product_id": pl.Int64,
    "group_id": pl.Int64,
    "name": pl.Utf8,
    "rarity": pl.Utf8,
    "kind": pl.Utf8,
    "released_on": pl.Date,
}


def prices_frame(day: date, rows_by_group: dict[int, list[dict[str, Any]]]) -> pl.DataFrame:
    records = [
        {
            "date": day,
            "product_id": row["productId"],
            "sub_type": row["subTypeName"],
            "low": row["lowPrice"],
            "mid": row["midPrice"],
            "high": row["highPrice"],
            "market": row["marketPrice"],
        }
        for rows in rows_by_group.values()
        for row in rows
    ]
    return pl.DataFrame(records, schema=PRICE_SCHEMA)


def _rarity(product: dict[str, Any]) -> str | None:
    for item in product.get("extendedData") or []:
        if item["name"] == "Rarity":
            value: str = item["value"]
            return value
    return None


def _released_on(product: dict[str, Any]) -> date | None:
    raw = (product.get("presaleInfo") or {}).get("releasedOn")
    if raw is None:
        return None
    return datetime.fromisoformat(raw).date()


def products_frame(products: list[dict[str, Any]]) -> pl.DataFrame:
    records = []
    for p in products:
        rarity = _rarity(p)
        records.append(
            {
                "product_id": p["productId"],
                "group_id": p["groupId"],
                "name": p["name"],
                "rarity": rarity,
                "kind": classify_kind(rarity),
                "released_on": _released_on(p),
            }
        )
    return pl.DataFrame(records, schema=PRODUCT_SCHEMA)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_transforms.py -v`
Expected: 3 PASSED

- [ ] **Step 5: Lint, typecheck, commit**

```bash
uv run ruff check . && uv run ruff format . && uv run mypy
git add src/pkmn_quant/data/transforms.py tests/test_transforms.py
git commit -m "feat: pure transforms from tcgcsv JSON to typed Polars frames"
```

---

### Task 6: Quality gates

**Files:**
- Create: `src/pkmn_quant/data/quality.py`
- Test: `tests/test_quality.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_quality.py`:

```python
from datetime import date
from typing import Any

import polars as pl

from pkmn_quant.data.quality import apply_quality_gates
from pkmn_quant.data.transforms import PRICE_SCHEMA

DAY = date(2025, 6, 2)
PREV_DAY = date(2025, 6, 1)


def frame(rows: list[dict[str, Any]]) -> pl.DataFrame:
    return pl.DataFrame(rows, schema=PRICE_SCHEMA)


def row(product_id: int, market: float | None, day: date = DAY, sub_type: str = "Normal") -> dict[str, Any]:
    return {
        "date": day,
        "product_id": product_id,
        "sub_type": sub_type,
        "low": 1.0,
        "mid": 2.0,
        "high": 3.0,
        "market": market,
    }


def test_clean_rows_pass_through() -> None:
    clean, quarantined = apply_quality_gates(frame([row(1, 10.0)]), previous=None)
    assert clean.height == 1
    assert quarantined.height == 0


def test_null_and_nonpositive_market_quarantined() -> None:
    clean, quarantined = apply_quality_gates(
        frame([row(1, None), row(2, 0.0), row(3, 5.0)]), previous=None
    )
    assert clean["product_id"].to_list() == [3]
    reasons = dict(zip(quarantined["product_id"].to_list(), quarantined["reason"].to_list()))
    assert reasons == {1: "null_market", 2: "nonpositive_market"}


def test_duplicates_quarantined() -> None:
    clean, quarantined = apply_quality_gates(frame([row(1, 10.0), row(1, 11.0)]), previous=None)
    assert clean.height == 0
    assert quarantined["reason"].to_list() == ["duplicate", "duplicate"]


def test_same_product_different_subtype_not_duplicate() -> None:
    clean, _ = apply_quality_gates(
        frame([row(1, 10.0, sub_type="Normal"), row(1, 12.0, sub_type="Holofoil")]),
        previous=None,
    )
    assert clean.height == 2


def test_price_jump_quarantined() -> None:
    previous = frame([row(1, 10.0, day=PREV_DAY), row(2, 10.0, day=PREV_DAY)])
    clean, quarantined = apply_quality_gates(
        frame([row(1, 150.0), row(2, 11.0)]), previous=previous
    )
    assert clean["product_id"].to_list() == [2]
    assert quarantined["reason"].to_list() == ["price_jump"]


def test_new_product_without_history_passes() -> None:
    previous = frame([row(1, 10.0, day=PREV_DAY)])
    clean, quarantined = apply_quality_gates(frame([row(99, 500.0)]), previous=previous)
    assert clean.height == 1
    assert quarantined.height == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_quality.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement**

`src/pkmn_quant/data/quality.py`:

```python
"""Quality gates applied to each day's prices before they enter the warehouse.

Bad rows are quarantined with a reason code, never silently dropped.
"""

from __future__ import annotations

import polars as pl

# Day-over-day moves beyond this factor are treated as feed errors.
JUMP_FACTOR = 10.0


def apply_quality_gates(
    prices: pl.DataFrame, previous: pl.DataFrame | None
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Split a day's prices into (clean, quarantined-with-reason)."""
    df = prices.with_columns(
        pl.when(pl.col("market").is_null())
        .then(pl.lit("null_market"))
        .when(pl.col("market") <= 0)
        .then(pl.lit("nonpositive_market"))
        .when(pl.struct(["product_id", "sub_type"]).is_duplicated())
        .then(pl.lit("duplicate"))
        .otherwise(pl.lit(None, dtype=pl.Utf8))
        .alias("reason")
    )

    if previous is not None and previous.height > 0:
        prev = previous.select("product_id", "sub_type", pl.col("market").alias("prev_market"))
        ratio = pl.col("market") / pl.col("prev_market")
        df = (
            df.join(prev, on=["product_id", "sub_type"], how="left")
            .with_columns(
                pl.when(
                    pl.col("reason").is_null()
                    & pl.col("prev_market").is_not_null()
                    & ((ratio > JUMP_FACTOR) | (ratio < 1 / JUMP_FACTOR))
                )
                .then(pl.lit("price_jump"))
                .otherwise(pl.col("reason"))
                .alias("reason")
            )
            .drop("prev_market")
        )

    clean = df.filter(pl.col("reason").is_null()).drop("reason")
    quarantined = df.filter(pl.col("reason").is_not_null())
    return clean, quarantined
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_quality.py -v`
Expected: 6 PASSED

- [ ] **Step 5: Lint, typecheck, commit**

```bash
uv run ruff check . && uv run ruff format . && uv run mypy
git add src/pkmn_quant/data/quality.py tests/test_quality.py
git commit -m "feat: quality gates quarantine bad price rows with reason codes"
```

---

### Task 7: Warehouse (Parquet storage + DuckDB queries)

**Files:**
- Create: `src/pkmn_quant/data/warehouse.py`
- Test: `tests/test_warehouse.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_warehouse.py`:

```python
from datetime import date
from pathlib import Path
from typing import Any

import polars as pl
import pytest

from pkmn_quant.config import Paths
from pkmn_quant.data.transforms import PRICE_SCHEMA
from pkmn_quant.data.warehouse import Warehouse


@pytest.fixture
def warehouse(tmp_path: Path) -> Warehouse:
    return Warehouse(Paths(root=tmp_path))


def price_row(day: date, product_id: int, market: float) -> dict[str, Any]:
    return {
        "date": day,
        "product_id": product_id,
        "sub_type": "Normal",
        "low": 1.0,
        "mid": 2.0,
        "high": 3.0,
        "market": market,
    }


def test_write_and_load_day(warehouse: Warehouse) -> None:
    day = date(2025, 6, 1)
    df = pl.DataFrame([price_row(day, 1, 10.0)], schema=PRICE_SCHEMA)
    assert not warehouse.has_day(day)
    warehouse.write_prices(day, df)
    assert warehouse.has_day(day)
    assert warehouse.load_day(day).equals(df)


def test_stored_days_sorted(warehouse: Warehouse) -> None:
    d1, d2 = date(2025, 6, 1), date(2025, 6, 2)
    df = pl.DataFrame([price_row(d2, 1, 10.0)], schema=PRICE_SCHEMA)
    warehouse.write_prices(d2, df)
    warehouse.write_prices(d1, df.with_columns(pl.lit(d1).alias("date")))
    assert warehouse.stored_days() == [d1, d2]


def test_empty_quarantine_not_written(warehouse: Warehouse) -> None:
    day = date(2025, 6, 1)
    empty = pl.DataFrame([], schema={**PRICE_SCHEMA, "reason": pl.Utf8})
    warehouse.write_quarantine(day, empty)
    assert not (warehouse.paths.quarantine / f"date={day.isoformat()}").exists()


def test_duckdb_query_over_prices_and_products(warehouse: Warehouse) -> None:
    day = date(2025, 6, 1)
    prices = pl.DataFrame(
        [price_row(day, 1, 10.0), price_row(day, 2, 99.0)], schema=PRICE_SCHEMA
    )
    warehouse.write_prices(day, prices)
    products = pl.DataFrame(
        {
            "product_id": [1, 2],
            "group_id": [24541, 24541],
            "name": ["Card A", "Booster Box"],
            "rarity": ["Common", None],
            "kind": ["single", "sealed"],
            "released_on": [day, day],
        }
    )
    warehouse.write_products(products)
    out = warehouse.query(
        "SELECT p.kind, COUNT(*) AS n FROM prices pr "
        "JOIN products p USING (product_id) GROUP BY p.kind ORDER BY p.kind"
    )
    assert out["kind"].to_list() == ["sealed", "single"]
    assert out["n"].to_list() == [1, 1]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_warehouse.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement**

`src/pkmn_quant/data/warehouse.py`:

```python
"""Parquet-backed price warehouse with DuckDB query access."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import duckdb
import polars as pl

from pkmn_quant.config import Paths


class Warehouse:
    """Date-partitioned Parquet storage: prices/date=YYYY-MM-DD/data.parquet."""

    def __init__(self, paths: Paths) -> None:
        self.paths = paths

    def _day_dir(self, day: date) -> Path:
        return self.paths.prices / f"date={day.isoformat()}"

    def has_day(self, day: date) -> bool:
        return (self._day_dir(day) / "data.parquet").exists()

    def write_prices(self, day: date, df: pl.DataFrame) -> None:
        day_dir = self._day_dir(day)
        day_dir.mkdir(parents=True, exist_ok=True)
        df.write_parquet(day_dir / "data.parquet")

    def write_quarantine(self, day: date, df: pl.DataFrame) -> None:
        if df.height == 0:
            return
        day_dir = self.paths.quarantine / f"date={day.isoformat()}"
        day_dir.mkdir(parents=True, exist_ok=True)
        df.write_parquet(day_dir / "data.parquet")

    def write_products(self, df: pl.DataFrame) -> None:
        self.paths.warehouse.mkdir(parents=True, exist_ok=True)
        df.write_parquet(self.paths.products)

    def load_products(self) -> pl.DataFrame:
        return pl.read_parquet(self.paths.products)

    def load_day(self, day: date) -> pl.DataFrame:
        return pl.read_parquet(self._day_dir(day) / "data.parquet")

    def load_prices(self) -> pl.DataFrame:
        """All stored price days as one frame (the `date` column is in the data)."""
        return pl.read_parquet(self.paths.prices / "**" / "*.parquet")

    def stored_days(self) -> list[date]:
        if not self.paths.prices.exists():
            return []
        return sorted(
            date.fromisoformat(p.name.removeprefix("date="))
            for p in self.paths.prices.iterdir()
            if p.name.startswith("date=")
        )

    def query(self, sql: str) -> pl.DataFrame:
        """Run DuckDB SQL with `prices` and `products` views available."""
        con = duckdb.connect()
        prices_glob = str(self.paths.prices / "**" / "*.parquet")
        con.execute(f"CREATE VIEW prices AS SELECT * FROM read_parquet('{prices_glob}')")
        if self.paths.products.exists():
            con.execute(
                f"CREATE VIEW products AS SELECT * FROM read_parquet('{self.paths.products}')"
            )
        return con.execute(sql).pl()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_warehouse.py -v`
Expected: 4 PASSED

- [ ] **Step 5: Lint, typecheck, commit**

```bash
uv run ruff check . && uv run ruff format . && uv run mypy
git add src/pkmn_quant/data/warehouse.py tests/test_warehouse.py
git commit -m "feat: date-partitioned Parquet warehouse with DuckDB query views"
```

---

### Task 8: Ingest orchestration + CLI

**Files:**
- Create: `src/pkmn_quant/data/ingest.py`
- Create: `src/pkmn_quant/cli.py`
- Test: `tests/test_ingest.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_ingest.py` (reuses `make_archive` from `tests/test_tcgcsv.py`):

```python
import json
from datetime import date
from pathlib import Path
from typing import Any

import httpx

from pkmn_quant.config import Paths
from pkmn_quant.data.ingest import ingest_range, tracked_groups
from pkmn_quant.data.tcgcsv import Group
from pkmn_quant.data.warehouse import Warehouse
from tests.test_tcgcsv import make_archive

GROUP_ID = 24541

GROUPS_JSON = {
    "success": True,
    "results": [
        {
            "groupId": GROUP_ID,
            "name": "ME: Ascended Heroes",
            "abbreviation": "MEG",
            "publishedOn": "2025-02-20T00:00:00",
            "categoryId": 3,
        },
        {
            "groupId": 3170,
            "name": "Old Set",
            "abbreviation": "OLD",
            "publishedOn": "2020-01-01T00:00:00",
            "categoryId": 3,
        },
    ],
}

PRODUCTS_JSON = {
    "success": True,
    "results": [
        {
            "productId": 666906,
            "name": "Collection - Erika",
            "groupId": GROUP_ID,
            "presaleInfo": {"releasedOn": "2025-02-20T00:00:00"},
            "extendedData": [],
        }
    ],
}


def price_row(market: float) -> dict[str, Any]:
    return {
        "productId": 666906,
        "lowPrice": 1.0,
        "midPrice": 2.0,
        "highPrice": 3.0,
        "marketPrice": market,
        "directLowPrice": None,
        "subTypeName": "Normal",
    }


def test_tracked_groups_filters_old_sets() -> None:
    groups = [
        Group(group_id=1, name="new", abbreviation="N", published_on=date(2025, 1, 1)),
        Group(group_id=2, name="old", abbreviation="O", published_on=date(2020, 1, 1)),
        Group(group_id=3, name="future", abbreviation="F", published_on=date(2030, 1, 1)),
    ]
    tracked = tracked_groups(groups, today=date(2025, 6, 1))
    assert [g.group_id for g in tracked] == [1]


def test_ingest_range_end_to_end(tmp_path: Path) -> None:
    d1, d2 = date(2025, 6, 1), date(2025, 6, 2)
    archives = {
        d1: make_archive(tmp_path / "fixtures1", d1, GROUP_ID, [price_row(10.0)]),
        d2: make_archive(tmp_path / "fixtures2", d2, GROUP_ID, [price_row(500.0)]),
    }

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/groups"):
            return httpx.Response(200, json=GROUPS_JSON)
        if path.endswith("/products"):
            return httpx.Response(200, json=PRODUCTS_JSON)
        for day, archive in archives.items():
            if path.endswith(f"prices-{day.isoformat()}.ppmd.7z"):
                return httpx.Response(200, content=archive.read_bytes())
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    root = tmp_path / "proj"
    stats = ingest_range(Paths(root=root), d1, d2, client=client)

    assert [s.day for s in stats] == [d1, d2]
    assert stats[0].rows_clean == 1
    # 10.0 -> 500.0 is a 50x jump: quarantined by the price_jump gate.
    assert stats[1].rows_clean == 0
    assert stats[1].rows_quarantined == 1

    warehouse = Warehouse(Paths(root=root))
    assert warehouse.stored_days() == [d1, d2]
    assert warehouse.load_products()["kind"].to_list() == ["sealed"]


def test_ingest_range_skips_already_stored(tmp_path: Path) -> None:
    d1 = date(2025, 6, 1)
    archive = make_archive(tmp_path / "fixtures", d1, GROUP_ID, [price_row(10.0)])
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path.endswith("/groups"):
            return httpx.Response(200, json=GROUPS_JSON)
        if request.url.path.endswith("/products"):
            return httpx.Response(200, json=PRODUCTS_JSON)
        return httpx.Response(200, content=archive.read_bytes())

    client = httpx.Client(transport=httpx.MockTransport(handler))
    root = tmp_path / "proj"
    first = ingest_range(Paths(root=root), d1, d1, client=client)
    second = ingest_range(Paths(root=root), d1, d1, client=client)
    assert len(first) == 1
    assert second == []
```

Also add a CLI smoke test at the bottom of the same file:

```python
def test_cli_help() -> None:
    from typer.testing import CliRunner

    from pkmn_quant.cli import app

    result = CliRunner().invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "ingest" in result.output
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_ingest.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement ingest orchestration**

`src/pkmn_quant/data/ingest.py`:

```python
"""Daily ingestion: download archives, transform, gate, and store."""

from __future__ import annotations

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


@dataclass(frozen=True)
class IngestStats:
    day: date
    rows_clean: int
    rows_quarantined: int


def tracked_groups(groups: list[Group], today: date) -> list[Group]:
    """The tradeable universe: sets released between MIN_SET_RELEASE and today."""
    return [g for g in groups if MIN_SET_RELEASE <= g.published_on <= today]


def refresh_products(client: httpx.Client, warehouse: Warehouse, groups: list[Group]) -> int:
    frames = [products_frame(tcgcsv.fetch_products(client, g.group_id)) for g in groups]
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
        client = httpx.Client(timeout=60.0, follow_redirects=True)
    warehouse = Warehouse(paths)
    stats: list[IngestStats] = []
    try:
        groups = tcgcsv.fetch_groups(client)
        tracked = tracked_groups(groups, today=end)
        group_ids = {g.group_id for g in tracked}
        if not paths.products.exists():
            refresh_products(client, warehouse, tracked)

        stored = warehouse.stored_days()
        previous = warehouse.load_day(stored[-1]) if stored else None
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
```

- [ ] **Step 4: Implement the CLI**

`src/pkmn_quant/cli.py`:

```python
"""Typer CLI entry points."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import typer

from pkmn_quant.config import Paths
from pkmn_quant.data.ingest import ingest_range

app = typer.Typer(no_args_is_help=True, help="Pokemon card quant toolkit.")


@app.command()
def ingest(
    start: str = typer.Option(..., help="First date to ingest (YYYY-MM-DD)."),
    end: str = typer.Option(..., help="Last date to ingest (YYYY-MM-DD)."),
    root: Path = typer.Option(Path("."), help="Project root holding the data/ directory."),
) -> None:
    """Download tcgcsv daily archives and load them into the warehouse."""
    stats = ingest_range(Paths(root=root), dt.date.fromisoformat(start), dt.date.fromisoformat(end))
    for s in stats:
        typer.echo(f"{s.day}: {s.rows_clean} clean rows, {s.rows_quarantined} quarantined")
    if not stats:
        typer.echo("Nothing to do - all days already ingested.")


if __name__ == "__main__":
    app()
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_ingest.py -v`
Expected: 4 PASSED

- [ ] **Step 6: Full suite, lint, typecheck**

Run: `uv run pytest && uv run ruff check . && uv run ruff format . && uv run mypy`
Expected: all pass (`tests/__init__.py` already exists from Task 1, so the `from tests.test_tcgcsv import make_archive` import resolves)

- [ ] **Step 7: Commit**

```bash
git add src/pkmn_quant/data/ingest.py src/pkmn_quant/cli.py tests/test_ingest.py
git commit -m "feat: ingest orchestration and pkmn ingest CLI"
```

---

### Task 9: Real-world smoke test (manual verification)

This task validates the one assumption tests can't: the actual archive layout. No new files except possible fixes.

- [ ] **Step 1: Ingest one real week**

Run: `uv run pkmn ingest --start 2025-06-01 --end 2025-06-07`
Expected: seven lines like `2025-06-01: ~15000-40000 clean rows, <small number> quarantined`. First run also downloads products for ~40-60 tracked sets (takes a minute or two).

**If every day reports 0 clean rows:** the archive layout assumption is wrong. Debug with:

```bash
uv run python -c "
import py7zr
with py7zr.SevenZipFile('data/raw/archives/prices-2025-06-01.ppmd.7z') as z:
    for n in z.getnames()[:10]:
        print(n)
"
```

Fix the path template in `extract_group_prices` (and the `make_archive` test fixture to match), re-run tests, then re-run the ingest after deleting `data/warehouse/prices/`.

- [ ] **Step 2: Sanity-query the warehouse**

```bash
uv run python -c "
from pathlib import Path
from pkmn_quant.config import Paths
from pkmn_quant.data.warehouse import Warehouse

w = Warehouse(Paths(root=Path('.')))
print(w.query('SELECT date, COUNT(*) AS rows FROM prices GROUP BY date ORDER BY date'))
print(w.query('SELECT kind, COUNT(*) AS n FROM products GROUP BY kind ORDER BY n DESC'))
print(w.query(\"\"\"
    SELECT p.name, pr.market FROM prices pr JOIN products p USING (product_id)
    WHERE p.kind = 'sealed' AND pr.date = DATE '2025-06-01'
    ORDER BY pr.market DESC LIMIT 10
\"\"\"))
"
```

Expected: 7 days of consistent row counts; kind counts where `single` dominates and `sealed` is a few hundred; the top-10 sealed list looks like booster boxes/ETB-type products (eyeball check that classification is sane).

- [ ] **Step 3: Verify quarantine behavior**

```bash
uv run python -c "
from pathlib import Path
import polars as pl
qdir = Path('data/warehouse/quarantine')
files = list(qdir.rglob('*.parquet')) if qdir.exists() else []
if files:
    df = pl.read_parquet(qdir / '**' / '*.parquet')
    print(df.group_by('reason').len())
else:
    print('no quarantined rows this week (fine)')
"
```

Expected: either empty or a small count of `null_market` rows (common for thinly traded products). Large `price_jump` counts would suggest the previous-day comparison is misfiring — investigate before proceeding.

- [ ] **Step 4: Commit any fixes**

```bash
git add -A src tests
git commit -m "fix: adjust archive extraction to real tcgcsv layout"  # only if changes were needed
```

---

## Done criteria (Plan 1)

- `uv run pytest` green, `uv run mypy` clean, `uv run ruff check .` clean, CI passing on GitHub.
- A real week of price data queryable through `Warehouse.query()` with sane kind classification.
- Plan 2 (backtest engine) can be written against `Warehouse.load_prices()` / `load_products()`.
