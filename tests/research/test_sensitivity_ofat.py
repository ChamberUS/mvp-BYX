from adaptive_trader.research.sensitivity import ofat_variations


def test_ofat_varies_one_allowed_parameter_at_a_time(research_config) -> None:
    variations = ofat_variations(research_config)

    assert variations[0][0] == "BASE"
    assert len(variations) <= 60
    for _, _, config in variations:
        assert config.long_ema_period > config.short_ema_period
        assert config.taker_fee_bps == research_config.taker_fee_bps
