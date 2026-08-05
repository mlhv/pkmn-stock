from datetime import date

import polars as pl

from pkmn_quant.engine.portfolio import Asset, Position
from pkmn_quant.engine.strategy import Context
from pkmn_quant.strategies.buy_and_hold import BuyAndHold

D1 = date(2025, 6, 1)
SEALED_A = Asset(product_id=1, sub_type="Normal")
SEALED_B = Asset(product_id=2, sub_type="Normal")
SINGLE_C = Asset(product_id=3, sub_type="Holofoil")

PRODUCTS = pl.DataFrame(
    {
        "product_id": [1, 2, 3],
        "group_id": [10, 10, 10],
        "name": ["Box A", "ETB B", "Card C"],
        "rarity": [None, None, "Rare"],
        "kind": ["sealed", "sealed", "single"],
        "released_on": [D1, D1, D1],
    }
)


def make_ctx(
    cash: float,
    positions: dict[Asset, Position] | None = None,
    marks: dict[Asset, float] | None = None,
    products: pl.DataFrame | None = None,
    history: pl.DataFrame | None = None,
) -> Context:
    return Context(
        today=D1,
        history=pl.DataFrame() if history is None else history,
        products=PRODUCTS if products is None else products,
        positions=positions or {},
        cash=cash,
        marks=marks if marks is not None else {SEALED_A: 100.0, SEALED_B: 50.0, SINGLE_C: 10.0},
    )


def _bh_ctx(n_products: int, cash: float, price: float) -> Context:
    """`n_products` sealed assets, all marked at `price`, one print each on D1.

    Every asset carries the same quote shape, so the screen's density and
    spread keys all tie and the order falls through to product_id — these
    tests exercise the allocation, not the ranking.
    """
    ids = list(range(1, n_products + 1))
    products = pl.DataFrame(
        {
            "product_id": ids,
            "group_id": [10] * n_products,
            "name": [f"Box {i}" for i in ids],
            "rarity": [None] * n_products,
            "kind": ["sealed"] * n_products,
            "released_on": [D1] * n_products,
        }
    )
    history = pl.DataFrame(
        {
            "date": [D1] * n_products,
            "product_id": ids,
            "sub_type": ["Normal"] * n_products,
            "low": [price * 0.9] * n_products,
            "mid": [price * 1.15] * n_products,
            "high": [price * 3.0] * n_products,
            "market": [price] * n_products,
        }
    )
    marks = {Asset(product_id=i, sub_type="Normal"): price for i in ids}
    return make_ctx(cash=cash, marks=marks, products=products, history=history)


def test_first_bar_equal_weights_sealed_universe() -> None:
    """Hand-derivation: cash 300, sealed universe A ($100) and B ($50).

    Pass 1: target 300/2 = 150 -> floor(150/100) = 1 unit of A ($100),
    floor(150/50) = 3 units of B ($150); $250 spent, $50 left.
    Pass 2: A costs $100 > $50 cash, unaffordable. B's deficit is
    150 - 3*50 = 0, which fails the strict `> 0` test. Nothing is bought,
    so the sweep leaves the old equal-dollar answer untouched.
    """
    strat = BuyAndHold(kind="sealed")
    orders = strat.on_bar(make_ctx(cash=300.0))
    by_asset = {o.asset: o.quantity for o in orders}
    assert by_asset == {SEALED_A: 1, SEALED_B: 3}


def test_never_orders_again_after_first_bar() -> None:
    strat = BuyAndHold(kind="sealed")
    strat.on_bar(make_ctx(cash=300.0))
    assert strat.on_bar(make_ctx(cash=300.0)) == []


def test_skips_assets_without_marks() -> None:
    """Hand-derivation: only A is marked, so the universe is [A] alone.

    Pass 1: target 300/1 = 300 -> floor(300/100) = 3 units, $300 spent,
    $0 left. Pass 2: $100 > $0 cash, nothing affordable.
    """
    strat = BuyAndHold(kind="sealed")
    orders = strat.on_bar(make_ctx(cash=300.0, marks={SEALED_A: 100.0}))  # B has no price yet
    assert [o.asset for o in orders] == [SEALED_A]
    assert [o.quantity for o in orders] == [3]


def test_name_includes_kind() -> None:
    assert BuyAndHold(kind="sealed").name == "buy-and-hold-sealed"


def test_reset_allows_reuse_across_runs() -> None:
    strat = BuyAndHold(kind="sealed")
    assert strat.on_bar(make_ctx(cash=300.0)) != []
    assert strat.on_bar(make_ctx(cash=300.0)) == []
    strat.reset()
    assert strat.on_bar(make_ctx(cash=300.0)) != []


def test_buy_and_hold_screens_to_n_assets() -> None:
    """Only the top n_assets by screen order are bought at all."""
    strat = BuyAndHold(kind="sealed", n_assets=2)
    ctx = _bh_ctx(n_products=5, cash=1000.0, price=10.0)
    orders = strat.on_bar(ctx)
    assert len(orders) == 2
    assert sorted(o.asset.product_id for o in orders) == [1, 2]


def test_buy_and_hold_deficit_sweep_deploys_the_remainder() -> None:
    """Equal-dollar alone leaves cash on the table when price does not divide
    the per-asset budget; the sweep must spend it down.

    Hand-derivation: cash 1000, n_assets 2, both priced 30.
    Pass 1: target 500 -> floor(500/30) = 16 units each = 960, cash 40 left.
    Pass 2: both held 480 vs target 500, deficit 20 each; buy 1 unit of the
    first in screen order (strict >, so earliest wins the tie) -> cash 10,
    nothing else affordable. Totals: 17 and 16 units, 990 spent.
    """
    strat = BuyAndHold(kind="sealed", n_assets=2)
    ctx = _bh_ctx(n_products=2, cash=1000.0, price=30.0)
    qty = sorted(o.quantity for o in strat.on_bar(ctx))
    assert qty == [16, 17]


def test_buy_and_hold_sweep_is_deterministic_under_ties() -> None:
    """Two identical assets must always produce the same split, never an
    order-dependent one — bit-for-bit C++ parity depends on it."""
    runs = []
    for _ in range(3):
        strat = BuyAndHold(kind="sealed", n_assets=2)
        ctx = _bh_ctx(n_products=2, cash=1000.0, price=30.0)
        runs.append([(o.asset.product_id, o.quantity) for o in strat.on_bar(ctx)])
    assert runs[0] == runs[1] == runs[2]
    assert runs[0] == [(1, 17), (2, 16)]


def test_screen_keeps_assets_with_no_history() -> None:
    """An asset marked but absent from history must stay buyable.

    It gets 0 prints and an infinite spread, so it ranks last — but dropping
    it would silently shrink the benchmark's universe to whatever printed
    inside the loaded window.
    """
    marked_only = Asset(product_id=9, sub_type="Normal")
    ctx = _bh_ctx(n_products=1, cash=200.0, price=10.0)
    products = pl.concat(
        [
            ctx.products,
            pl.DataFrame(
                {
                    "product_id": [9],
                    "group_id": [10],
                    "name": ["Box 9"],
                    "rarity": [None],
                    "kind": ["sealed"],
                    "released_on": [D1],
                },
                schema=ctx.products.schema,
            ),
        ]
    )
    marks = dict(ctx.marks) | {marked_only: 10.0}
    orders = BuyAndHold(kind="sealed").on_bar(
        make_ctx(cash=200.0, marks=marks, products=products, history=ctx.history)
    )
    # target 200/2 = 100 -> 10 units each, cash 0, sweep buys nothing.
    assert {o.asset: o.quantity for o in orders} == {
        Asset(product_id=1, sub_type="Normal"): 10,
        marked_only: 10,
    }
