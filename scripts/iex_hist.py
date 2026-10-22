"""Download IEX HIST pcap.gz feed files for one trading day.

Data provided for free by IEX. By accessing or using IEX Historical Data,
you agree to the IEX Historical Data Terms of Use (see docs/iex-hist-terms.md).

Files are streamed straight to disk as `.pcap.gz` (never decompressed) via a
`.part` file that is renamed on completion, so an interrupted run never
leaves a truncated file under the final name.
"""

import argparse
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

import httpx

LISTING_URL = "https://iextrading.com/api/1.0/hist"
DEFAULT_RESERVE = 2 * 1024**3
CHUNK = 1024 * 1024


@dataclass(frozen=True)
class HistFile:
    date: str
    feed: str
    version: str
    protocol: str
    size: int
    link: str


def list_files(client: httpx.Client, day: str) -> list[HistFile]:
    resp = client.get(LISTING_URL, params={"date": day})
    resp.raise_for_status()
    return [
        HistFile(
            date=str(row["date"]),
            feed=str(row["feed"]),
            version=str(row["version"]),
            protocol=str(row["protocol"]),
            size=int(row["size"]),
            link=str(row["link"]),
        )
        for row in resp.json()
    ]


def select(files: list[HistFile], feeds: tuple[str, ...]) -> list[HistFile]:
    by_feed = {f.feed: f for f in files}
    missing = [feed for feed in feeds if feed not in by_feed]
    if missing:
        raise LookupError(
            f"feeds not listed: {', '.join(missing)} (available: {', '.join(sorted(by_feed))})"
        )
    return [by_feed[feed] for feed in feeds]


def ensure_space(dest_dir: Path, needed: int, reserve: int = DEFAULT_RESERVE) -> None:
    free = shutil.disk_usage(dest_dir).free
    if free - needed < reserve:
        raise OSError(
            f"refusing download: {free} bytes free, {needed} needed, {reserve} reserve required"
        )


def target_path(f: HistFile, dest_dir: Path) -> Path:
    return dest_dir / f"{f.date}_{f.protocol}_{f.feed}{f.version}.pcap.gz"


def download(client: httpx.Client, f: HistFile, dest_dir: Path) -> Path:
    final = target_path(f, dest_dir)
    if final.exists() and final.stat().st_size == f.size:
        return final
    part = final.with_name(final.name + ".part")
    with client.stream("GET", f.link) as resp:
        resp.raise_for_status()
        with part.open("wb") as out:
            for chunk in resp.iter_bytes(CHUNK):
                out.write(chunk)
    got = part.stat().st_size
    if got != f.size:
        raise OSError(f"{part.name}: got {got} bytes, listing says {f.size}")
    part.rename(final)
    return final


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--date", required=True, help="YYYYMMDD")
    p.add_argument("--feeds", nargs="+", required=True)
    p.add_argument("--dest", type=Path, required=True)
    args = p.parse_args(argv)

    args.dest.mkdir(parents=True, exist_ok=True)
    with httpx.Client(follow_redirects=True, timeout=None) as client:
        chosen = select(list_files(client, args.date), tuple(args.feeds))
        for f in chosen:
            if target_path(f, args.dest).exists() and (
                target_path(f, args.dest).stat().st_size == f.size
            ):
                print(f"have {target_path(f, args.dest).name}")
                continue
            ensure_space(args.dest, f.size)
            print(f"fetching {f.feed} ({f.size} bytes)", flush=True)
            print(download(client, f, args.dest))
    return 0


if __name__ == "__main__":
    sys.exit(main())
