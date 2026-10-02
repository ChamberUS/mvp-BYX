import json
from unittest.mock import patch

from adaptive_trader.panel_status import capture_status, status


def test_missing_reports_are_null_and_partitions_stay_locked(tmp_path):
    with patch("adaptive_trader.panel_status.capture_status", return_value={}):
        result = status(tmp_path)
    assert result["system"]["online"] is True
    assert result["research"]["dataset_id"] is None
    assert result["research"]["validation_status"] == "LOCKED"
    assert result["research"]["final_holdout"] == "SEALED"
    assert result["warnings"]


def test_only_train_summaries_are_composed(tmp_path):
    reports = tmp_path / "reports/research"
    reports.mkdir(parents=True)
    (reports / "train-feature-aggregate.json").write_text(json.dumps({
        "partition": "TRAIN", "dataset_id": "train-id", "row_count": 42, "session_count": 2,
    }))
    (reports / "train-label-aggregate.json").write_text('{"partition":"VALIDATION"}')
    with patch("adaptive_trader.panel_status.capture_status", return_value={}):
        result = status(tmp_path)
    assert result["research"]["anchors"] == 42
    assert result["research"]["pure_mid_labels_status"] is None
    assert not (reports / "validation").exists()


def test_capture_running_and_stopped(tmp_path):
    from subprocess import CompletedProcess

    command = (f"123 python {tmp_path}/.venv/bin/adaptive-trader market microstructure "
               "campaign-record --market futures --symbol ETHUSDT --campaign-id active "
               "--streams aggTrade,depth")
    with patch("subprocess.run", return_value=CompletedProcess([], 0, command)):
        capture = capture_status(tmp_path, [])
    assert capture["running"] is True
    assert capture["symbol"] == "ETHUSDT"
    assert capture["market"] == "USD-M Futures"
    assert capture["recorder_health"] is None
    with patch("subprocess.run", return_value=CompletedProcess([], 0, "")):
        assert capture_status(tmp_path, [])["running"] is False


def test_process_failure_is_unknown_not_stopped(tmp_path):
    with patch("subprocess.run", side_effect=OSError):
        warnings = []
        assert capture_status(tmp_path, warnings)["running"] is None
        assert warnings


def test_health_poll_does_not_read_reports(tmp_path):
    with patch("adaptive_trader.panel_status.capture_status", return_value={}):
        with patch("adaptive_trader.panel_status.read_object", side_effect=AssertionError):
            assert "research" not in status(tmp_path, health_only=True)


def test_protected_symlink_is_not_opened(tmp_path):
    from adaptive_trader.panel_status import read_object

    target = tmp_path / "validation" / "private.json"
    link = tmp_path / "train-feature-aggregate.json"
    link.symlink_to(target)
    with patch("pathlib.Path.read_text", side_effect=AssertionError("must not open")):
        assert read_object(link, []) == {}
