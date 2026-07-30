from adaptive_trader.research.candidate_freeze import spot_to_futures_plan


def test_futures_transfer_plan_is_only_1x_and_not_executed() -> None:
    plan = spot_to_futures_plan("spot-candidate-v1")

    assert plan["leverages"] == ["1"]
    assert plan["executed"] is False
    assert plan["selection_allowed"] is False


def test_failed_spot_configuration_has_no_futures_transfer() -> None:
    plan = spot_to_futures_plan(None)

    assert plan["status"] == "NO_SPOT_CANDIDATE_FOR_FUTURES_TRANSFER"
    assert plan["executed"] is False
