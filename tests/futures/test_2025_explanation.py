from adaptive_trader.futures.temporal_robustness import _explain_2025


def test_2025_explanation_uses_non_causal_language() -> None:
    yearly = (
        {"period": "2022", "net_return_percent": -1},
        {"period": "2023", "net_return_percent": -1},
        {"period": "2024", "net_return_percent": -1},
        {"period": "2025", "net_return_percent": 1},
    )
    explanation = _explain_2025(
        "TEST",
        (),
        (),
        (),
        (),
        (),
        (),
        yearly,
        (),
    )
    assert explanation["causal_claim"] is False
    assert explanation["historical_pattern"] == "NOT_OBSERVED_PREVIOUSLY"
