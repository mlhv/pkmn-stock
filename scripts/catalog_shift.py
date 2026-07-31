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
