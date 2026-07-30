from pathlib import Path

from adaptive_trader.research.spot_hypotheses import (
    EXACT_VARIANT_IDS,
    load_spot_hypothesis_catalog,
)


def test_catalog_has_six_exact_unique_variants() -> None:
    catalog = load_spot_hypothesis_catalog()

    identifiers = tuple(item.variant_id for item in catalog.hypotheses)

    assert identifiers == EXACT_VARIANT_IDS
    assert len(set(identifiers)) == 6


def test_catalog_hash_is_stable_and_loading_does_not_modify_file(tmp_path: Path) -> None:
    source = Path("spot-hypotheses-v1.toml")
    copy = tmp_path / source.name
    copy.write_bytes(source.read_bytes())
    before = copy.read_bytes()

    first = load_spot_hypothesis_catalog(copy)
    second = load_spot_hypothesis_catalog(copy)

    assert first.content_hash == second.content_hash
    assert copy.read_bytes() == before


def test_catalog_rejects_an_unregistered_variant(tmp_path: Path) -> None:
    path = tmp_path / "catalog.toml"
    path.write_text(
        Path("spot-hypotheses-v1.toml").read_text(encoding="utf-8")
        + "\n[hypotheses.after_results]\ntarget_r_multiple = \"3\"\ntime_exit_candles = 5\n",
        encoding="utf-8",
    )

    try:
        load_spot_hypothesis_catalog(path)
    except ValueError as exc:
        assert "six exact" in str(exc)
    else:
        raise AssertionError("unregistered hypothesis was accepted")
