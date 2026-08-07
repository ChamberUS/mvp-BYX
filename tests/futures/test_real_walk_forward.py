from decimal import Decimal


def test_real_walk_forward_is_rolling_fixed_and_period_separated(
    real_fixture_bundle,
) -> None:
    rows = real_fixture_bundle.walk_forward_rows
    assert rows
    assert {row["period"] for row in rows} == {"DEVELOPMENT", "VALIDATION"}
    assert {row["scenario"] for row in rows} == {"BASE_COST"}
    assert all(row["leverage"] == Decimal("1") for row in rows)
    configurations = {row["configuration"] for row in rows}
    assert len(configurations) == 6
    assert all(row["validation_start"] > row["train_end"] for row in rows)
    assert not any(
        row["validation_start"].year == 2026
        for row in rows
    )
