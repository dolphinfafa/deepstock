import json
from deepstock.data.inventory import metadata_for


def test_nested_stock_exports_honor_explicit_market_and_provider_manifest(tmp_path):
    folder = tmp_path / "artifacts/data/stock-export"
    (folder / "prices").mkdir(parents=True)
    (folder / "manifest.json").write_text(json.dumps({"market": "CN", "provider": "Tushare", "timezone": "Asia/Shanghai"}))
    metadata = metadata_for(folder / "prices/000001.SZ.csv.gz")
    assert metadata["market"] == "CN" and metadata["provider"] == "Tushare"


def test_flat_explicit_cn_market_does_not_need_legacy_data_kind(tmp_path):
    (tmp_path / "manifest.json").write_text(json.dumps({"market": "CN", "provider": "Tushare"}))
    assert metadata_for(tmp_path / "daily.csv")["market"] == "CN"
