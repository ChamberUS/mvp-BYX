from decimal import Decimal

import pytest

from adaptive_trader.config.settings import ConfigError, TradingConfig, load_config


def test_default_configuration_is_safe() -> None:
    config = TradingConfig()

    assert config.symbol == "ETHUSDT"
    assert config.initial_balance == Decimal("10000")
    assert config.trading_enabled is False
    assert config.is_research_only() is True


@pytest.mark.parametrize("field", ["allow_leverage", "allow_futures"])
def test_forbidden_capabilities_are_rejected(field: str) -> None:
    with pytest.raises(ConfigError):
        TradingConfig(**{field: True})


def test_environment_configuration_is_validated() -> None:
    config = load_config({"ADAPTIVE_TRADER_INITIAL_BALANCE": "10000.25"})

    assert config.initial_balance == Decimal("10000.25")


def test_invalid_boolean_is_rejected() -> None:
    with pytest.raises(ConfigError):
        load_config({"ADAPTIVE_TRADER_TRADING_ENABLED": "yes"})
