def test_funding_disabled_is_diagnostic_only(real_fixture_bundle) -> None:
    rows = real_fixture_bundle.funding_rows
    assert len(rows) == 12
    assert all(row["diagnostic_only"] is True for row in rows)
    assert all(row["candidate_assessment_eligible"] is False for row in rows)
    assert all(
        row["warning"] == "FUNDING_DISABLED_DIAGNOSTIC_ONLY" for row in rows
    )
    assert all("with_funding_net_pnl" in row for row in rows)
    assert all("without_funding_net_pnl" in row for row in rows)


def test_cost_scenarios_do_not_disable_funding(real_fixture_bundle) -> None:
    consolidated = [
        row for row in real_fixture_bundle.cost_rows if row["fold"] == "CONSOLIDATED"
    ]
    assert {row["scenario"] for row in consolidated} == {
        "LOW_COST",
        "BASE_COST",
        "HIGH_COST",
        "STRESS_COST",
    }
    assert all(row["funding_unchanged_by_cost_scenario"] is True for row in consolidated)
