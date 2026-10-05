import importlib
import json
from pathlib import Path

import pytest

from deepstock.research_control import research_pause


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
