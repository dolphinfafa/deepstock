import json
from pathlib import Path

def metadata_for(path: Path) -> dict:
    norgate = "norgate" in str(path)
    cn = "auction" in str(path) or "short_term_forward" in str(path)
    result = {"market": "CN" if cn else "US", "provider": "Norgate Data" if norgate else "Tushare/FTshare legacy" if cn else "Massive", "restricted": norgate}
    for candidate in [path.with_suffix(".manifest.json"), path.parent / "manifest.json"]:
        if candidate.exists():
            evidence = json.loads(candidate.read_text(encoding="utf-8"))
            # Keep only a bounded, non-secret contract, not arbitrary API URLs.
            result.update({k: evidence[k] for k in ["provider", "adjustment", "price_adjustment", "total_return_adjustment", "retrieved_at_utc", "volume_unit", "amount_unit", "timezone", "license_note", "origin", "upstream_versions"] if k in evidence})
            if evidence.get("data_kind") == "market" and evidence.get("timezone") == "Asia/Shanghai":
                result["market"] = "CN"
            break
    if path.name == "calendar.csv":
        result["kind"] = "calendar"
    return result
