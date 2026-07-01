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
