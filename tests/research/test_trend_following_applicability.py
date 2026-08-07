from adaptive_trader.research.trend_following_catalog import (
    FUTURES_LONG,
    FUTURES_LONG_SHORT,
    FUTURES_SHORT,
    SPOT_LONG,
    load_trend_following_catalog,
)


def test_variant_applicability_matches_the_pre_registered_market_matrix() -> None:
    catalog = load_trend_following_catalog()

    assert tuple(item.variant_id for item in catalog.applicable_to(SPOT_LONG)) == (
        "TF_DONCHIAN_20_FIXED_RISK",
        "TF_DONCHIAN_10_FIXED_RISK",
        "TF_DONCHIAN_20_DEFENSIVE_RISK",
        "TF_DONCHIAN_10_DEFENSIVE_RISK",
        "TF_LONG_ONLY_DONCHIAN_20",
    )
    assert tuple(item.variant_id for item in catalog.applicable_to(FUTURES_LONG)) == (
        "TF_DONCHIAN_20_FIXED_RISK",
        "TF_DONCHIAN_10_FIXED_RISK",
        "TF_DONCHIAN_20_DEFENSIVE_RISK",
        "TF_DONCHIAN_10_DEFENSIVE_RISK",
        "TF_LONG_ONLY_DONCHIAN_20",
    )
    assert tuple(item.variant_id for item in catalog.applicable_to(FUTURES_SHORT)) == (
        "TF_DONCHIAN_20_FIXED_RISK",
        "TF_DONCHIAN_10_FIXED_RISK",
        "TF_DONCHIAN_20_DEFENSIVE_RISK",
        "TF_DONCHIAN_10_DEFENSIVE_RISK",
        "TF_SHORT_ONLY_DONCHIAN_20",
    )
    assert tuple(item.variant_id for item in catalog.applicable_to(FUTURES_LONG_SHORT)) == (
        "TF_DONCHIAN_20_FIXED_RISK",
        "TF_DONCHIAN_10_FIXED_RISK",
        "TF_DONCHIAN_20_DEFENSIVE_RISK",
        "TF_DONCHIAN_10_DEFENSIVE_RISK",
    )
