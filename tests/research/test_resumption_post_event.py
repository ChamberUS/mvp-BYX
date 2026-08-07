from pathlib import Path


def test_post_event_declaration_forbids_strategy_access() -> None:
    source = Path(
        "src/adaptive_trader/research/pullback_calibration_experiment.py"
    ).read_text(encoding="utf-8")
    strategy = Path(
        "src/adaptive_trader/strategy/pullback.py"
    ).read_text(encoding="utf-8")
    assert "POST_EVENT_ONLY_NO_STRATEGY_ACCESS" in source
    assert "return_after_" not in strategy
