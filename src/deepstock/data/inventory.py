import json
from pathlib import Path

def metadata_for(path: Path) -> dict:
    norgate = "norgate" in str(path)
    cn = "auction" in str(path) or "short_term_forward" in str(path)
    result = {"market": "CN" if cn else "US", "provider": "Norgate Data" if norgate else "Tushare/FTshare legacy" if cn else "Massive", "restricted": norgate}
    # Stock exports have prices/responses/etc subdirectories. Inspect bounded
    # ancestor manifests, not only the immediate folder, and honor explicit market.
    for candidate in [path.with_suffix(".manifest.json"), *[parent / "manifest.json" for parent in list(path.parents)[:4]]]:
        if candidate.exists():
            evidence = json.loads(candidate.read_text(encoding="utf-8"))
            # Keep only a bounded, non-secret contract, not arbitrary API URLs.
            result.update({k: evidence[k] for k in ["market", "provider", "adjustment", "price_adjustment", "total_return_adjustment", "retrieved_at_utc", "volume_unit", "amount_unit", "timezone", "license_note", "origin", "upstream_versions", "restricted"] if k in evidence})
            if evidence.get("data_kind") == "market" and evidence.get("timezone") == "Asia/Shanghai":
                result["market"] = "CN"
            break
    if path.name == "calendar.csv":
        result["kind"] = "calendar"
    return result
