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


def _bh_ctx_prices(prices: list[float], cash: float) -> Context:
    """One sealed asset per entry in `prices`, product_ids 1..n, one print each.

    Same shape as `_bh_ctx` but with a per-asset price, so allocation across a
    MIXED price ladder can be exercised. Every quote is the same multiple of
    its own market price, so the screen's spread key ties to within float
    noise and no asset is deliberately ranked ahead of another — assertions
    below are therefore on multisets and totals, never on screen position.
    """
    n = len(prices)
    ids = list(range(1, n + 1))
    products = pl.DataFrame(
        {
            "product_id": ids,
            "group_id": [10] * n,
            "name": [f"Box {i}" for i in ids],
            "rarity": [None] * n,
            "kind": ["sealed"] * n,
            "released_on": [D1] * n,
        }
    )
    history = pl.DataFrame(
        {
            "date": [D1] * n,
            "product_id": ids,
            "sub_type": ["Normal"] * n,
            "low": [p * 0.9 for p in prices],
            "mid": [p * 1.15 for p in prices],
            "high": [p * 3.0 for p in prices],
            "market": list(prices),
        }
    )
    marks = {Asset(product_id=i, sub_type="Normal"): p for i, p in zip(ids, prices, strict=True)}
    return make_ctx(cash=cash, marks=marks, products=products, history=history)


def test_sweep_never_funds_one_asset_out_of_the_others_shares() -> None:
    """C1(a) regression: the deficit sweep used to concentrate the account.

    `deficit = target - qty*price` saturates at `target` for an asset too
    expensive to hold one unit of, and the only affordability test was against
    the whole remaining balance, so the most expensive product in the universe
    always won the sweep. On the published benchmark run that put 50.05% of a
    $10,000 account into a single box.

    Hand-derivation: cash 1000, ten assets, five at $10 and five at $150.
      Pre-fix: target 100. Cheap assets take floor(100/10) = 10 units ($100
        each, $500 total), the $150 ones take zero, leaving $500. Each $150
        asset then shows the maximum deficit of $100, so the sweep buys them
        one at a time until the cash runs out -- three units at $150 = $450,
        each a 15% position against a 10% target.
      Post-fix: `_affordable` keeps the largest k whose k cheapest members
        cost <= 1000/k. k=6 works (the 6th cheapest is $150 <= $166.67); k=7
        does not ($150 > $142.86). So five $10 assets plus ONE $150 asset,
        re-targeted at $166.67. Sizing: 16 units of each $10 asset ($160) and
        1 of the $150. That is $950; the sweep spends the last $50 as one more
        unit of each $10 asset ($170 each). Total $1,000 deployed at a largest
        weight of $170 -- above the $166.67 target by less than one unit,
        which is the bounded overshoot `_affordable` documents, against 1.5x
        target pre-fix here and 5x target on the real run.
    """
    prices = [10.0] * 5 + [150.0] * 5
    orders = BuyAndHold(kind="sealed", n_assets=10).on_bar(_bh_ctx_prices(prices, cash=1000.0))
    marks = {Asset(product_id=i, sub_type="Normal"): p for i, p in enumerate(prices, start=1)}
    notionals = sorted(o.quantity * marks[o.asset] for o in orders)
    assert notionals == [150.0, 170.0, 170.0, 170.0, 170.0, 170.0]
    assert sum(notionals) == 1000.0  # the whole account, as before the fix
    # Exactly one of the five $150 assets is funded, and nothing is bought
    # that the account could not have equal-weighted into.
    assert sum(1 for o in orders if marks[o.asset] == 150.0) == 1


def test_asset_priced_above_the_target_is_dropped_not_funded() -> None:
    """The narrow statement of the same rule, isolated from the sweep.

    cash 400 over four assets targets $100 each; a $250 asset cannot be held
    at that weight, so it leaves the universe entirely and the remaining three
    re-target at 400/3 = $133.33 -> 13 units each ($130), sweep spends $9 of
    the last $10 as one more unit. Pre-fix the $250 asset carried the maximum
    deficit and took one unit -- 62.5% of the account.
    """
    orders = BuyAndHold(kind="sealed", n_assets=4).on_bar(
        _bh_ctx_prices([10.0, 10.0, 10.0, 250.0], cash=400.0)
    )
    assert {o.asset.product_id for o in orders} == {1, 2, 3}
    assert sorted(o.quantity for o in orders) == [13, 13, 14]


def test_nothing_is_bought_when_no_asset_fits_the_account() -> None:
    """k == 0: the account cannot buy one unit of anything screened."""
    assert BuyAndHold(kind="sealed", n_assets=3).on_bar(_bh_ctx_prices([10.0] * 3, cash=5.0)) == []


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


def test_non_positive_n_assets_buys_nothing() -> None:
    """n_assets <= 0 means "hold nothing", matching the C++ guard.

    A bare `candidates[: self.n_assets]` reads -1 as Python's "all but the
    last", which would quietly trade 4 of the 5 assets here while the native
    engine traded none.
    """
    for n in (0, -1):
        orders = BuyAndHold(kind="sealed", n_assets=n).on_bar(
            _bh_ctx(n_products=5, cash=1000.0, price=10.0)
        )
        assert orders == [], f"n_assets={n}"


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
