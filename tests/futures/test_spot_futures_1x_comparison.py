from adaptive_trader.futures.real_validation_report import (
    expected_artifact_names,
    write_real_validation_report,
)


def test_spot_futures_comparison_never_combines_markets(
    tmp_path,
    real_fixture_bundle,
) -> None:
    rows = real_fixture_bundle.comparison_rows
    assert rows[0]["configuration"] == "SPOT_BASELINE_V1"
    assert len(rows) == 7
    assert all(row["combined_with_other_market"] is False for row in rows)
    output = write_real_validation_report(
        real_fixture_bundle,
        tmp_path,
        git_commit="a889362effe0745ac06ce42ae82cadf16a91bdee",
        git_dirty=True,
    )
    assert set(expected_artifact_names()) <= {
        item.name for item in output.iterdir()
    }
    assert "nunca são somados" in (
        output / "spot_futures_1x_comparison.md"
    ).read_text(encoding="utf-8")
