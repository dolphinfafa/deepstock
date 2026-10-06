"""Save provider-delivered evidence before normalising or deriving prices."""
from __future__ import annotations
import uuid
from datetime import datetime, timezone
from pathlib import Path
import pandas as pd
from .store import DataStore, ROOT, write_json, _local


def capture_response(provider: str, endpoint: str, value, market: str, restricted: bool = False) -> str:
    folder = ROOT / "artifacts/providers" / provider.lower()
    folder.mkdir(parents=True, exist_ok=True)
    name = uuid.uuid4().hex
    path = folder / (name + (".csv.gz" if isinstance(value, pd.DataFrame) else ".json"))
    if isinstance(value, pd.DataFrame):
        value.to_csv(path, index=False, compression="gzip")
    else:
        write_json(path, value)
    version = DataStore().import_file(path, {"provider": provider, "endpoint": endpoint, "market": market, "restricted": restricted,
                                           "origin": "provider_response", "retrieved_at_utc": datetime.now(timezone.utc).isoformat()})["id"]
    if not hasattr(_local, "provider_versions"):
        _local.provider_versions = []
    _local.provider_versions.append(version)
    return version


def provider_versions():
    return list(getattr(_local, "provider_versions", []))
