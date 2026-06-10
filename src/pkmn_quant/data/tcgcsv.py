"""HTTP client helpers for tcgcsv.com (a daily mirror of TCGplayer data)."""

from __future__ import annotations

import json
import tempfile
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
    tmp = dest.with_name(dest.name + ".tmp")
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

    Note: py7zr 1.1.0 dropped the read() method; we use extract(path, targets)
    and read from the extracted files on disk instead.
    """
    wanted = {f"{day.isoformat()}/{POKEMON_CATEGORY_ID}/{gid}/prices": gid for gid in group_ids}
    out: dict[int, list[dict[str, Any]]] = {}

    with py7zr.SevenZipFile(archive, mode="r") as z:
        names = [n for n in z.getnames() if n in wanted]
        if not names:
            return out
        with tempfile.TemporaryDirectory() as tmp_dir:
            z.extract(path=tmp_dir, targets=names)
            for name in names:
                extracted_file = Path(tmp_dir) / name
                payload = json.loads(extracted_file.read_bytes())
                out[wanted[name]] = payload["results"]

    return out
