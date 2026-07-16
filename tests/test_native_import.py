"""The native extension builds and imports; version pinned to the project."""


def test_engine_imports_with_version() -> None:
    from pkmn_quant import _engine

    assert _engine.__version__ == "0.1.0"
