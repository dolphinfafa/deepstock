from datetime import datetime, timedelta, timezone
from pathlib import Path
import json
from types import SimpleNamespace

import pytest

from scripts.install_csi300_research_cron import BEGIN, END, backfill_block, replace_block
from scripts.run_csi300_minute_backfill import reserve_request, run_backfill


def test_provider_reservations_observe_daily_and_hourly_limits():
    now = datetime(2026, 10, 5, 3, 30, tzinfo=timezone.utc)
    ledger = {}
    assert reserve_request(ledger, now)
    assert not reserve_request(ledger, now + timedelta(minutes=60))
    assert reserve_request(ledger, now + timedelta(minutes=65))
    assert not reserve_request(ledger, now + timedelta(hours=3))
    # Shanghai's next day, while UTC is still on October 5.
    assert reserve_request(ledger, now + timedelta(hours=13))
    assert len(ledger["attempts"]) == 3


def test_cron_is_idempotent_preserves_other_jobs_and_has_correct_environment():
    original = "# unrelated\n30 7 * * * keep-this-job\n"
    block = backfill_block(Path("/srv/deepstock"), Path("/opt/envs/deepstock/bin/python"))
    first = replace_block(original, BEGIN, END, block)
    assert replace_block(first, BEGIN, END, block) == first
    assert "keep-this-job" in first
    assert first.count(BEGIN) == 1
    assert "30 11 * * *" in first
    assert "35 12 * * *" in first
    assert "/opt/envs/deepstock/bin/python -m scripts.run_csi300_minute_backfill" in first
    assert replace_block(first, BEGIN, END, None).strip() == original.strip()


def test_corrupt_cron_markers_are_not_silently_deleted():
    with pytest.raises(ValueError, match="unterminated"):
        replace_block("keep\n" + BEGIN + "\n", BEGIN, END, None)


def test_backfill_runs_only_one_window_and_never_fabricates_completion(tmp_path, monkeypatch):
    source = tmp_path / "darwen"
    root = source / "artifacts/auction_history_tushare"
    root.mkdir(parents=True)
    destination = tmp_path / "deepstock-artifacts"
    (root / "minute_fetch_report.json").write_text(json.dumps({"remaining_windows": 13, "remaining_dates": ["2026-09-04"]}))
    calls = []
    def fake_run(command, **kwargs):
        calls.append(command)
        if "fetch-minute-entries" in command:
            (root / "minute_manifest.json").write_text(json.dumps({"20260904_20260904": {"status": "ok"}}))
            (root / "minute_fetch_report.json").write_text(json.dumps({"remaining_windows": 12, "remaining_dates": ["2026-09-07"]}))
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr("scripts.run_csi300_minute_backfill.subprocess.run", fake_run)
    monkeypatch.setattr("scripts.run_csi300_minute_backfill.synchronize", lambda *args: {})
    result = run_backfill(source, Path("/darwen/python"), destination)
    assert result["completed_windows"] == 9
    assert result["frozen_report_ready"] is False
    assert len(calls) == 2
    assert calls[0][calls[0].index("--maximum-windows-per-run") + 1] == "1"
    assert calls[0][calls[0].index("--retries") + 1] == "0"
    assert calls[0][0] == "/darwen/python"
    result = run_backfill(source, Path("/darwen/python"), destination)
    assert result["status"] == "waiting_for_quota"
    assert len(calls) == 2
