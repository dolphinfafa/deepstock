import importlib
import json
from pathlib import Path

import pytest

from deepstock.research_control import require_active_research, research_pause


ROOT = Path(__file__).resolve().parents[1]


def test_tail_pause_is_persisted_and_does_not_pause_other_strategies():
    paused = research_pause("cn_etf_tail_momentum", ROOT)
    assert paused["status"] == "paused_missing_data"
    assert research_pause("adaptive_defensive_etf", ROOT) is None
    assert research_pause("ahl_global_futures_trend", ROOT)["status"] == "paused_frozen"
    with pytest.raises(ValueError, match="Unregistered"):
        research_pause("unknown", ROOT)


@pytest.mark.parametrize("name,operation", [
    ("scripts.cn.download_etf_tail_minutes", "download"),
    ("scripts.cn.run_etf_tail_momentum", "run_research"),
])
def test_paused_cli_does_not_download_or_backtest(monkeypatch, capsys, name, operation):
    module = importlib.import_module(name)
    def forbidden(*args, **kwargs):
        raise AssertionError("Paused research must not run")
    monkeypatch.setattr(module, operation, forbidden)
    if hasattr(module, "download_recent"):
        monkeypatch.setattr(module, "download_recent", forbidden)
    monkeypatch.setattr("sys.argv", [name])
    module.main()
    assert json.loads(capsys.readouterr().out)["status"] == "paused_missing_data"


def test_paused_ingestion_preserves_existing_evidence():
    from deepstock.web.ingestion import ingest_tail_momentum
    # No database interaction or artifact creation is needed while paused.
    assert ingest_tail_momentum(None)["status"] == "paused_missing_data"


def test_granville_pause_is_persisted_without_pausing_peer_strategies():
    paused = research_pause("granville_stock_portfolio", ROOT)
    assert paused["status"] == "paused_research_review"
    with pytest.raises(ValueError, match="Research paused.*paused_research_review"):
        require_active_research("granville_stock_portfolio", ROOT)
    for strategy in ["arc", "csi300_opening_auction", "adaptive_defensive_etf"]:
        assert research_pause(strategy, ROOT) is None
        require_active_research(strategy, ROOT)
    catalog = json.loads((ROOT / "config/strategy_catalog.json").read_text())
    entry = next(s for s in catalog["strategies"] if s["id"] == "granville_stock_portfolio")
    assert entry["research_status"] == paused["status"]
    assert entry["pause_reason"] == paused["reason"]
    assert entry["execution_status"] == "research_only_no_orders"


@pytest.mark.parametrize("name,operation,argument_count", [
    ("scripts.run_granville_portfolio", "run", 5),
    ("scripts.prepare_granville_stock_data", "collect_cn", 2),
    ("scripts.prepare_granville_stock_data", "collect_us", 2),
    ("scripts.prepare_granville_us_inventory", "run", 5),
    ("scripts.audit_granville_us_research", "run", 1),
    ("scripts.audit_granville_data_readiness", "run", 4),
    ("scripts.diagnose_granville_sources", "run", 1),
])
def test_granville_pause_blocks_before_input_reads_provider_imports_or_output(
    tmp_path, name, operation, argument_count
):
    module = importlib.import_module(name)
    # Even invalid/missing inputs must stop at the pause, before any research work.
    output = tmp_path / "must-not-exist"
    with pytest.raises(ValueError, match="Research paused.*paused_research_review"):
        getattr(module, operation)(*[output] * argument_count)
    assert not output.exists()


def test_explicit_resumption_requires_registry_change(tmp_path):
    registry = tmp_path / "config/strategy_registry.json"
    registry.parent.mkdir()
    entry = {"strategy_id": "granville_stock_portfolio", "research_status": "paused_research_review",
             "pause_reason": "synthetic explicit pause"}
    registry.write_text(json.dumps({"strategies": [entry]}))
    with pytest.raises(ValueError, match="synthetic explicit pause"):
        require_active_research(entry["strategy_id"], tmp_path)
    # Data arriving cannot clear a persisted pause.
    (tmp_path / "new-evidence.json").write_text("{}")
    with pytest.raises(ValueError, match="synthetic explicit pause"):
        require_active_research(entry["strategy_id"], tmp_path)
    entry.pop("research_status")
    registry.write_text(json.dumps({"strategies": [entry]}))
    require_active_research(entry["strategy_id"], tmp_path)
