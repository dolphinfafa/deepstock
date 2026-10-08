"""Synthetic input/lineage tests, never historical performance evidence."""
from copy import deepcopy
import json

import pandas as pd
import pytest

from deepstock.data.store import DataStore, DataQualityError, digest, write_json
from scripts import prepare_granville_stock_data as collector
from scripts import prepare_granville_us_inventory as builder
from scripts.run_granville_portfolio import dataset_csv


@pytest.fixture
def inventory(tmp_path, monkeypatch):
    store = DataStore(tmp_path)
    folder = tmp_path / "inventory"
    cfg = {"source_start": "2024-01-03", "evaluation_start": "2024-01-05", "evaluation_end": "2024-01-08"}
    meta = {"symbol": "A", "assetid": 42, "first_quote": "2000-01-01", "last_quote": None}

    def evidence(name, value):
        path = tmp_path / (name + ".json")
        write_json(path, value)
        return store.import_file(path, {"provider": "Norgate", "origin": "provider_response", "restricted": True})["id"]

    metadata = evidence("meta", meta)
    upstream = [evidence("raw", {}), evidence("adjusted", {}), metadata]
    native = {"records": [{**meta, "metadata_version": metadata}]}
    native_version = evidence("native", native)
    days = pd.to_datetime(["2024-01-02", "2024-01-03", "2024-01-05", "2024-01-08"])
    prices = pd.DataFrame({"date": days, "symbol": "A", "open": 10., "high": 11., "low": 9., "close": 10.,
                           "adjusted_open": 20., "adjusted_high": 22., "adjusted_low": 18., "adjusted_close": 20.,
                           "volume": 1000, "turnover": 10000})
    path = folder / "prices/A.csv.gz"
    path.parent.mkdir(parents=True)
    prices.to_csv(path, index=False)
    contract = {"market": "US", "provider": "Norgate", "restricted": True, "kind": "daily_ohlc", "padding": "NONE",
                "timezone": "America/New_York", "adjustment": "raw_NONE_and_TOTALRETURN_analytical_units",
                "volume_unit": "shares", "amount_unit": "USD", "upstream_versions": upstream}
    registered = store.import_file(path, contract)
    record = {"symbol": "A", "status": "ready", "version": registered["id"],
              **dict(zip(["NONE_version", "TOTALRETURN_version", "metadata_version"], upstream))}
    value = {"status": "captured_inventory_not_backtest_input", "market": "US", "provider": "Norgate", "failures": [],
             "symbol_count": 1, "source_start": "2024-01-02", "source_end": "2024-01-08", "records": [record],
             "native_membership_capture_version": native_version}
    write_json(folder / "manifest.json", value)
    version = store.import_file(folder / "manifest.json", {"provider": "Norgate", "origin": "provider_response", "restricted": True})["id"]
    captured = {"records": deepcopy(native["records"]), "source_start": "2024-01-02", "source_end": "2024-01-08",
                "required_start": "2024-01-03", "interval_version": version, "membership_contract": {"id": "synthetic"}}
    member_dir = tmp_path / "members"
    write_json(member_dir / "manifest.json", captured)
    member_version = store.import_file(member_dir / "manifest.json", {"provider": "Norgate", "origin": "provider_response", "restricted": True})["id"]
    config_path = tmp_path / "config.json"
    write_json(config_path, cfg)
    mapping = {"A": [{"start": "2024-01-02", "end": "2024-01-08"}]}
    monkeypatch.setattr(builder, "DataStore", lambda: store)
    monkeypatch.setattr(collector, "DataStore", lambda: store)
    monkeypatch.setattr(builder, "load_capture", lambda path: (mapping, captured, member_version))
    monkeypatch.setattr(builder, "fixed_config", lambda path: json.loads(path.read_text()))
    monkeypatch.setattr(builder, "capture_code_provenance", lambda: {"tracked_dirty": False, "source_sha256": "a" * 64})
    return folder, version, captured, record, cfg, store, member_dir, config_path


def test_offline_slice_preserves_gaps_raw_adjusted_and_original_versions(inventory, tmp_path):
    folder, version, captured, record, cfg, store, member_dir, config_path = inventory
    source = folder / "prices/A.csv.gz"
    before = digest(source)
    # A later generic alias must not overrule the original price contract pin.
    store.import_file(source, {"provider": "Generic inventory"})
    summary = builder.run(member_dir, folder, version, tmp_path / "output", config_path)
    manifest = json.loads((tmp_path / "output/manifest.json").read_text())
    sliced = dataset_csv(tmp_path / "output", manifest, "prices/A.csv.gz")
    assert list(sliced.date) == ["2024-01-03", "2024-01-05", "2024-01-08"]
    assert sliced.close.eq(10).all() and sliced.adjusted_close.eq(20).all()
    assert summary["new_price_count"] == 0 and summary["rows"] == 3 and not summary["backtest_admitted"]
    assert manifest["slice_records"][0]["upstream_version"] == record["version"]
    assert manifest["slice_records"][0]["excluded_outside_declared_interval"] == 1
    assert digest(source) == before
    with pytest.raises(FileExistsError):
        builder.run(member_dir, folder, version, tmp_path / "output", config_path)


@pytest.mark.parametrize("mutation", ["assetid", "first_quote", "missing_security", "duplicate_security"])
def test_entire_native_effective_identity_must_match(inventory, mutation):
    folder, version, captured, _, _, store, *_ = inventory
    current = deepcopy(captured)
    if mutation in {"assetid", "first_quote"}:
        current["records"][0][mutation] = 999 if mutation == "assetid" else "2001-01-01"
    elif mutation == "missing_security":
        current["records"] = []
    else:
        current["records"] *= 2
    with pytest.raises(ValueError, match="identity|lifetime"):
        builder.load_inventory(folder, version, current, store)


def test_altered_inventory_and_altered_price_source_reject(inventory):
    folder, version, captured, record, cfg, store, *_ = inventory
    with (folder / "manifest.json").open("a") as f:
        f.write(" ")
    with pytest.raises(ValueError, match="manifest changed"):
        builder.load_inventory(folder, version, captured, store)
    (folder / "prices/A.csv.gz").write_bytes(b"changed source")
    with pytest.raises(DataQualityError, match="Source changed"):
        builder.inventory_slice(folder, record, cfg, store)


def test_padded_contract_or_unknown_required_prices_never_fallback(inventory):
    folder, _, _, record, cfg, store, *_ = inventory
    changed = deepcopy(record)
    changed["status"] = "outside_verified_quote_lifetime"
    with pytest.raises(ValueError, match="never download/drop"):
        builder.inventory_slice(folder, changed, cfg, store)
    source = folder / "prices/A.csv.gz"
    contract = {**store.get(record["version"])["contract"], "padding": "ALLMARKETDAYS"}
    changed.update(status="ready", version=store.import_file(source, contract)["id"])
    with pytest.raises(ValueError, match="no-padding"):
        builder.inventory_slice(folder, changed, cfg, store)


def test_required_price_failure_remains_in_full_pool(inventory, tmp_path, monkeypatch):
    folder, version, _, _, _, _, member_dir, config_path = inventory
    monkeypatch.setattr(builder, "inventory_slice", lambda *args: (_ for _ in ()).throw(ValueError("synthetic unknown prices")))
    with pytest.raises(ValueError, match="all failed securities retained"):
        builder.run(member_dir, folder, version, tmp_path / "blocked", config_path)
    result = json.loads((tmp_path / "blocked/manifest.json").read_text())
    assert result["symbols"] == ["A"] and result["symbol_count"] == 1
    assert result["failures"] == [{"symbol": "A", "error": "synthetic unknown prices"}]


def test_manifest_pin_cannot_be_missing_or_empty(inventory):
    folder, _, _, record, _, _, *_ = inventory
    with pytest.raises(ValueError, match="pin missing"):
        dataset_csv(folder, {"input_versions": {}}, "prices/A.csv.gz")
    with pytest.raises(DataQualityError, match="Invalid pinned"):
        dataset_csv(folder, {"input_versions": {"prices/A.csv.gz": ""}}, "prices/A.csv.gz")
