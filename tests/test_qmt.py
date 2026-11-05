from datetime import date
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import zipfile

import pandas as pd
import pytest

from deepstock.data.qmt import BOUNDARY, NORMALIZER, snapshot_window_activity, reconcile_snapshot_windows
from deepstock.data.qmt_runtime import WorkerJob, native_lock, supervise, verify_receipt
from deepstock.data.store import DataQualityError, DataStore, clean_frame, digest, read_clean_csv, write_json

DAY = date(2026, 9, 23)
META = {"provider": "QMT", "market": "CN", "origin": "provider_response", "restricted": True,
        "endpoint": "qmt_native_minutes", "normalizer": NORMALIZER, "boundary": BOUNDARY,
        "volume_unit": "lots_100_shares", "amount_unit": "CNY", "adjustment": "none", "fill_data": False,
        "strategy_input_approved": False}


def millis(stamp):
    return pd.Timestamp("2026-09-23 " + stamp, tz="Asia/Shanghai").value // 1_000_000


def minutes():
    return pd.DataFrame([{"symbol": "000001.SZ", "time": millis(stamp), "open": 10., "high": 11.,
                          "low": 9., "close": 10., "volume": 100., "amount": 100_000.}
                         for stamp in ["09:30:00", "09:31:00", "09:32:00"]])


def ticks():
    return pd.DataFrame({"time": [millis(s) for s in ["09:25:00", "09:30:00", "09:30:57", "09:31:00"]],
                         "volume": [10, 20, 40, 45], "pvolume": [1001, 2005, 4012, 4520],
                         "amount": [10_010, 20_050, 40_120, 45_200],
                         "bidPrice": [[10.] * 5] * 4})


def test_native_minutes_keep_explicit_boundaries_and_separate_auction():
    source = minutes()
    before = source.copy(deep=True)
    clean, rejected, quality, kind = clean_frame(source, META)
    pd.testing.assert_frame_equal(source, before)
    assert kind == "qmt_minute_bars" and rejected.empty and not quality["blocking"]
    assert clean.volume.tolist() == [10_000] * 3
    assert clean.provider_volume_lots.tolist() == [100] * 3
    assert clean.bar_kind.tolist() == ["opening_auction", "continuous", "continuous"]
    assert pd.isna(clean.bar_start.iloc[0]) and pd.isna(clean.bar_end.iloc[0])
    assert str(clean.bar_start.iloc[1]) == "2026-09-23 09:30:00+08:00"
    assert clean.boundary.tolist() == ["separate_auction_observation", BOUNDARY, BOUNDARY]
    assert quality["separate_auction_rows"] == 1 and quality["strategy_input_approved"] is False


@pytest.mark.parametrize("key,value", [("fill_data", True), ("adjustment", "front"),
    ("volume_unit", "shares"), ("boundary", "left_open_right_closed"), ("normalizer", "guess"), ("strategy_input_approved", True)])
def test_minutes_reject_undeclared_units_adjustments_and_boundaries(key, value):
    with pytest.raises(DataQualityError, match="contract"):
        clean_frame(minutes(), {**META, key: value})


@pytest.mark.parametrize("field,value", [("time", millis("09:31:00") + 1),
    ("time", millis("12:31:00")), ("symbol", "000001.BAD"), ("close", float("inf")),
    ("volume", -1), ("low", 12), ("amount", 999_999)])
def test_minute_invalid_rows_are_quarantined_not_repaired(field, value):
    raw = minutes()
    raw.loc[1, field] = value
    clean, rejected, quality, _ = clean_frame(raw, META)
    assert len(clean) == 2 and len(rejected) == 1 and quality["blocking"]
    assert quality["imputed_rows"] == 0


def test_minute_conflicts_block_and_exact_duplicates_are_audited():
    source = minutes()
    duplicate = pd.concat([source, source.iloc[[1]]], ignore_index=True)
    clean, _, quality, _ = clean_frame(duplicate, META)
    assert len(clean) == 3 and quality["duplicate_rows"] == 1
    duplicate.loc[3, "amount"] += 1
    clean, rejected, quality, _ = clean_frame(duplicate, META)
    assert len(clean) == 2 and len(rejected) == 2 and quality["conflict_rows"] == 2


def test_exact_0931_view_does_not_inherit_out_of_scope_auction_failures():
    source = minutes()
    source.loc[0, "amount"] *= 10
    source.loc[2, "amount"] *= 10
    full, _, quality, _ = clean_frame(source, META)
    assert quality["blocking"] and len(full) == 1
    selected, rejected, quality, _ = clean_frame(source, {**META, "minute_window": "exact_0931_v1", "trade_date": DAY.isoformat()})
    assert len(selected) == 1 and rejected.empty and not quality["blocking"]
    assert selected.provider_raw_row.tolist() == [1]
    assert quality["excluded_observations_outside_window"] == 2
    assert quality["strategy_input_approved"] is False


@pytest.mark.parametrize("problem", ["invalid_target", "fractional_second", "unknown_time", "wrong_session"])
def test_exact_view_keeps_target_and_unknown_errors_blocking(problem):
    source = minutes()
    if problem == "invalid_target": source.loc[1, "amount"] *= 10
    if problem == "fractional_second": source.loc[1, "time"] += 1
    if problem == "unknown_time": source.loc[0, "time"] = float("nan")
    if problem == "wrong_session": source.loc[0, "time"] -= 86_400_000
    _, rejected, quality, _ = clean_frame(source, {**META, "minute_window": "exact_0931_v1", "trade_date": DAY.isoformat()})
    assert quality["blocking"] and not rejected.empty


def test_registered_native_capture_is_private_pinned_and_not_strategy_input(tmp_path):
    raw = tmp_path / "raw.csv.gz"
    minutes().to_csv(raw, index=False, compression={"method": "gzip", "mtime": 0})
    original_hash = digest(raw)
    manifest = DataStore(tmp_path).import_file(raw, META)
    assert manifest["status"] == "ready" and manifest["kind"] == "qmt_minute_bars"
    assert manifest["preview_allowed"] is False
    assert manifest["contract"]["strategy_input_approved"] is False
    assert manifest["raw_sha256"] == original_hash
    frame = read_clean_csv(raw, version=manifest["id"])
    assert len(frame) == 3
    raw.write_bytes(b"changed")
    with pytest.raises(DataQualityError, match="Source changed"):
        read_clean_csv(raw, version=manifest["id"])


def test_normalization_preserves_blocked_full_capture_and_old_report(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from scripts.capture_qmt_minutes import register_capture
    run = tmp_path / "artifacts/qmt-vip/capture-test"
    run.mkdir(parents=True)
    raw = minutes()
    raw.loc[0, "amount"] *= 10
    raw.to_csv(run / "minute.csv.gz", index=False, compression={"method": "gzip", "mtime": 0})
    write_json(run / "1m-download.json", {})
    write_json(run / "request.json", {"schema": "qmt-readonly-v1", "date": DAY.isoformat(),
                                      "code_commit": "a" * 40, "tick_symbols": [], "watch_seconds": 0})
    receipt = {"status": "complete", "request_sha256": digest(run / "request.json"),
               "finished_at_utc": "2026-11-05T05:00:00+00:00", "operations": [],
               "files": [{"path": n, "bytes": (run/n).stat().st_size, "sha256": digest(run/n)}
                         for n in ["minute.csv.gz", "1m-download.json"]]}
    write_json(run / "receipt.json", receipt)
    write_json(run / "report.json", {"old_evidence": True})
    original = digest(run / "minute.csv.gz")
    monkeypatch.setattr("scripts.capture_qmt_minutes.subprocess.run", lambda *a, **k: SimpleNamespace(stdout="b"*40))
    report = register_capture(tmp_path, run, {"status": "complete", "local_port_released": True,
        "receipt": receipt, "cleanup": "parent_supervised_termination", "worker_returncode": 1})
    assert report["status"] == "captured_diagnostic_only" and report["target_quality"] == "ready"
    assert report["all_observations_quality"] == "blocked" and report["all_observations_quarantined_rows"] == 1
    assert report["exact_0931_stocks"] == 1 and report["strategy_input_approved"] is False
    store = DataStore(tmp_path)
    target = store.get(report["target_minute_version"])
    assert target["contract"]["code_commit"] == "a"*40
    assert target["contract"]["normalizer_code_commit"] == "b"*40
    assert store.get(report["minute_version"])["status"] == "blocked"
    assert digest(run / "minute.csv.gz") == original
    assert json.loads((run / "report.json").read_text()) == {"old_evidence": True}


def test_tick_endpoint_conventions_and_odd_lot_shares_are_not_fitted():
    source = ticks()
    left = snapshot_window_activity(source, DAY, right_closed=False)
    right = snapshot_window_activity(source, DAY, right_closed=True)
    assert left["volume_shares"] == 3011
    assert right["volume_shares"] == 2515
    assert right["volume_rounded_lots_as_shares"] == 2500
    assert right["vwap"] == 10
    assert right["boundary"] == "left_open_right_closed"
    assert right["snapshot_diagnostic_only"] and right["strategy_input_approved"] is False
    assert source.bidPrice.iloc[0] == [10.] * 5


def test_window_reconciliation_preserves_disagreement_and_missing_references():
    ref = pd.DataFrame([{"ts_code": "000001.SZ", "vol": 2515, "amount": 25150}])
    result, private = reconcile_snapshot_windows({"000001.SZ": ticks(), "600519.SH": ticks()}, ref, DAY)
    assert result["right_closed"]["volume_matches"] == result["right_closed"]["amount_matches"] == 1
    assert result["left_closed"]["volume_matches"] == 0
    assert result["right_closed"]["blocked"] == 1
    assert result["ohlc_equivalence_verified"] is False and result["strategy_input_approved"] is False
    assert private["000001.SZ"]["right_closed"]["volume_rounded_lots_as_shares"] == 2500


@pytest.mark.parametrize("missing", [True, False])
def test_unpopulated_share_field_uses_declared_lots_with_visible_rounding(missing):
    source = ticks()
    if missing:
        source = source.drop(columns="pvolume")
    else:
        source["pvolume"] = 0
    result = snapshot_window_activity(source, DAY, right_closed=True)
    assert result["volume_shares"] == 2500 and result["share_count_exact"] is False
    assert result["volume_source"] == "volume_lots_x100"
    assert result["lot_rounding_error_bound_shares"] == 100
    ref = pd.DataFrame([{"ts_code": "000001.SZ", "vol": 2515, "amount": 25150}])
    summary, _ = reconcile_snapshot_windows({"000001.SZ": source}, ref, DAY)
    assert summary["right_closed"]["volume_matches"] == 0
    assert summary["right_closed"]["volume_within_rounding_bound"] == 1


@pytest.mark.parametrize("problem", ["reset", "wrong_day", "unit", "conflict", "missing_end"])
def test_tick_diagnostics_fail_on_reset_wrong_day_units_conflicts_and_gaps(problem):
    source = ticks()
    if problem == "reset": source.loc[2, "amount"] = 0
    if problem == "wrong_day": source.loc[0, "time"] -= 86_400_000
    if problem == "unit": source.loc[2, "pvolume"] *= 100
    if problem == "conflict":
        source = pd.concat([source, source.iloc[[1]]], ignore_index=True)
        source.loc[4, "amount"] += 1
    if problem == "missing_end": source = source.iloc[:2]
    with pytest.raises(DataQualityError):
        snapshot_window_activity(source, DAY, right_closed=True)


def fake_worker(run, mode="good"):
    write_json(run / "request.json", {"nonce": "unique-local-request"})
    code = '''import sys,json,time,hashlib,os
from pathlib import Path
assert sys.stdin.readline().strip()=="start"
run=Path(sys.argv[1]);mode=sys.argv[2]
if mode=="crash":os._exit(7)
if mode=="timeout":time.sleep(60)
p=run/"payload.json";p.write_text("{}")
item={"path":"payload.json","bytes":p.stat().st_size,"sha256":hashlib.sha256(p.read_bytes()).hexdigest()}
if mode=="bad_hash":item["sha256"]="0"*64
if mode=="bad_path":item["path"]="../payload.json"
receipt={"status":"complete","request_sha256":hashlib.sha256((run/"request.json").read_bytes()).hexdigest(),"files":[item]}
if mode=="wrong_request":receipt["request_sha256"]="0"*64
tmp=run/"receipt.tmp";tmp.write_text(json.dumps(receipt));tmp.replace(run/"receipt.json")
time.sleep(60)
'''
    return [sys.executable, "-c", code, str(run), mode]


def test_supervisor_verifies_receipt_then_reaps_worker_without_native_teardown(tmp_path):
    result = supervise(fake_worker(tmp_path), tmp_path, tmp_path, timeout=5)
    assert result["status"] == "complete"
    assert result["cleanup"] == "parent_supervised_termination"
    assert result["worker_returncode"] == (1 if os.name == "nt" else -15)
    assert result["native_shutdown_fixed"] is False
    verify_receipt(tmp_path, digest(tmp_path / "request.json"))


def test_requested_outputs_cannot_be_omitted_from_a_valid_hash_receipt(tmp_path):
    command = fake_worker(tmp_path)
    write_json(tmp_path / "request.json", {"schema": "qmt-readonly-v1", "tick_symbols": ["000001.SZ"], "watch_seconds": 1})
    result = supervise(command, tmp_path, tmp_path, timeout=5)
    assert result["status"] == "failed" and "omits" in result["error"]


@pytest.mark.parametrize("mode", ["crash", "timeout", "wrong_request", "bad_hash", "bad_path"])
def test_supervisor_fails_closed_and_reaps_bad_workers_without_retry(tmp_path, mode):
    result = supervise(fake_worker(tmp_path, mode), tmp_path, tmp_path, timeout=.2 if mode == "timeout" else 5)
    assert result["status"] == "failed"
    assert result["worker_returncode"] is not None
    assert result["elapsed_seconds"] < 10
    assert result["error_type"] == ("TimeoutError" if mode == "timeout" else "DataQualityError")


def test_os_owned_lock_blocks_concurrency_and_releases_after_scope(tmp_path):
    path = tmp_path / "native.lock"
    with native_lock(path):
        with pytest.raises(DataQualityError, match="token lock"):
            with native_lock(path):
                pytest.fail("concurrent token login")
    with native_lock(path):
        pass


@pytest.mark.parametrize("change", ["none", "modified", "extra"])
def test_sdk_code_must_match_pinned_archive_without_extra_executables(tmp_path, monkeypatch, change):
    from deepstock.data.qmt_runtime import verify_sdk
    wheel = tmp_path / "sdk.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("xtquant/__init__.py", "# original")
    monkeypatch.setattr("deepstock.data.qmt_runtime.WHEEL_SHA256", digest(wheel))
    sdk = tmp_path / "sdk"
    (sdk / "xtquant").mkdir(parents=True)
    (sdk / "xtquant/__init__.py").write_text("# altered" if change == "modified" else "# original")
    if change == "extra":
        (sdk / "xtquant/injected.py").write_text("# unreviewed")
    if change == "none":
        verify_sdk(sdk, wheel)
    else:
        with pytest.raises(DataQualityError):
            verify_sdk(sdk, wheel)


def test_failed_job_assignment_kills_worker_before_start_gate(tmp_path, monkeypatch):
    class FailedJob:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def assign(self, process): raise OSError("Job assignment denied")
    monkeypatch.setattr("deepstock.data.qmt_runtime.WorkerJob", FailedJob)
    result = supervise(fake_worker(tmp_path), tmp_path, tmp_path, timeout=5)
    assert result["status"] == "failed" and result["error_type"] == "OSError"
    assert result["worker_returncode"] is not None and not (tmp_path / "payload.json").exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows kernel Job Object containment")
def test_windows_job_kills_worker_when_parent_dies(tmp_path):
    marker = tmp_path / "worker-pid.txt"
    script = '''from deepstock.data.qmt_runtime import WorkerJob
import subprocess,sys,os
from pathlib import Path
job=WorkerJob().__enter__()
p=subprocess.Popen([sys.executable,"-c","import time;time.sleep(60)"])
job.assign(p)
Path(sys.argv[1]).write_text(str(p.pid))
os._exit(0)
'''
    subprocess.run([sys.executable, "-c", script, str(marker)], check=True, timeout=15)
    import ctypes as c
    from ctypes import wintypes as w
    kernel = c.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
    kernel.OpenProcess.restype = w.HANDLE
    kernel.WaitForSingleObject.argtypes = [w.HANDLE, w.DWORD]
    kernel.CloseHandle.argtypes = [w.HANDLE]
    handle = kernel.OpenProcess(0x100000, False, int(marker.read_text()))
    if handle:
        try:
            assert kernel.WaitForSingleObject(handle, 5000) == 0
        finally:
            kernel.CloseHandle(handle)
