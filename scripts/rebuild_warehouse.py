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
