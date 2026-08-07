from decimal import Decimal

from adaptive_trader.futures.temporal_robustness import (
    volatility_bucket,
    volatility_quantile_boundaries,
)


def test_volatility_quantiles_are_fixed_before_2025_application() -> None:
    development = tuple(Decimal(value) for value in ("1", "2", "3", "4"))
    boundaries = volatility_quantile_boundaries(development)
    assert boundaries == (Decimal("1.75"), Decimal("2.5"), Decimal("3.25"))
    assert volatility_bucket(Decimal("100"), boundaries) == "EXTREME"
