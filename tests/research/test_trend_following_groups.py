import pytest

from adaptive_trader.research.trend_following_catalog import (
    EXACT_MARKET_GROUPS,
    FUTURES_LONG,
    FUTURES_LONG_SHORT,
    FUTURES_SHORT,
    SPOT_LONG,
    build_market_groups,
)


def test_all_pre_registered_market_groups_have_fixed_order() -> None:
    assert build_market_groups() == EXACT_MARKET_GROUPS
    assert EXACT_MARKET_GROUPS == (
        SPOT_LONG,
        FUTURES_LONG,
        FUTURES_SHORT,
        FUTURES_LONG_SHORT,
    )


def test_requested_market_subset_preserves_requested_futures_mode_order() -> None:
    assert build_market_groups(
        markets=("futures",),
        futures_modes=("short", "long"),
    ) == (FUTURES_SHORT, FUTURES_LONG)
    assert build_market_groups(markets=("spot",), futures_modes=()) == (SPOT_LONG,)


@pytest.mark.parametrize(
    ("markets", "modes"),
    [
        ((), ()),
        (("spot", "spot"), ()),
        (("unknown",), ()),
        (("futures",), ()),
        (("futures",), ("short", "short")),
        (("futures",), ("hedge",)),
    ],
)
def test_invalid_market_group_requests_are_rejected(
    markets: tuple[str, ...],
    modes: tuple[str, ...],
) -> None:
    with pytest.raises(ValueError):
        build_market_groups(markets=markets, futures_modes=modes)
