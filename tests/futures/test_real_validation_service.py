from decimal import Decimal

from adaptive_trader.futures.integrity import ReadinessStatus


def test_real_validation_service_runs_only_six_fixed_1x_variants(
    real_fixture_bundle,
) -> None:
    assert real_fixture_bundle.integrity.readiness is ReadinessStatus.READY
    assert len(real_fixture_bundle.variants) == 6
    assert {item.leverage for item in real_fixture_bundle.variants} == {
        Decimal("1")
    }
    assert len(real_fixture_bundle.segment_rows) == 12
    assert len(real_fixture_bundle.assessments) == 6
    benchmark_keys = {
        (item["period"], item["benchmark"])
        for item in real_fixture_bundle.benchmark_rows
    }
    assert len(real_fixture_bundle.benchmark_rows) == len(benchmark_keys)
    assert benchmark_keys == {
        (period, benchmark)
        for period in ("DEVELOPMENT", "VALIDATION")
        for benchmark in ("CASH", "FUTURES_LONG_1X", "FUTURES_SHORT_1X")
    }
    assert all(item["candidate_frozen"] is False for item in real_fixture_bundle.assessments)
    assert all(
        item["status"]
        in {
            "PROMISING_FOR_FURTHER_VALIDATION",
            "NOT_PROMISING",
            "INCONCLUSIVE",
        }
        for item in real_fixture_bundle.assessments
    )
    assert real_fixture_bundle.periods.validation_end.year < 2026
