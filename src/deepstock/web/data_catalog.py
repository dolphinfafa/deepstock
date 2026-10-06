from __future__ import annotations
import json
from pathlib import Path
from sqlalchemy import select
from deepstock.data.store import DataStore
from deepstock.web.models import DataVersion, ResearchDataInput, ResearchRun


def register_manifest(session, manifest: dict):
    row = session.get(DataVersion, manifest["id"])
    values = {"node": manifest["node"], "market": manifest["market"], "provider": manifest["provider"],
              "source_name": manifest["source_name"], "status": manifest["status"], "data_start": manifest.get("data_start"),
              "data_end": manifest.get("data_end"), "row_count": manifest.get("rows", 0),
              "preview_allowed": bool(manifest.get("preview_allowed")), "details": manifest}
    if row is None:
        row = DataVersion(id=manifest["id"], **values)
        session.add(row)
    elif row.details != manifest:
        raise ValueError("Immutable dataset metadata changed")
    return row


def ingest_datasets(session, root: Path):
    count = 0
    for path in DataStore(root).manifests():
        register_manifest(session, json.loads(path.read_text(encoding="utf-8")))
        count += 1
    session.flush()
    for run in session.scalars(select(ResearchRun)):
        for ref in run.details.get("data_versions", []):
            version = ref["version"] if isinstance(ref, dict) else ref
            if session.get(DataVersion, version) and not session.get(ResearchDataInput, (run.id, version)):
                session.add(ResearchDataInput(run_id=run.id, version_id=version))
    session.commit()
    return {"local_versions": count}


def payload(row):
    m = row.details
    return {"id": row.id, "node": row.node, "market": row.market, "provider": row.provider, "source_name": row.source_name,
            "status": row.status, "data_start": row.data_start, "data_end": row.data_end, "rows": row.row_count,
            "preview_allowed": row.preview_allowed, "origin": m.get("origin"), "rules": m.get("rules"),
            "raw_sha256": m.get("raw_sha256"), "clean_sha256": m.get("clean_sha256"),
            "quality": m.get("quality", {}), "contract": m.get("contract", {}), "kind": m.get("kind"),
            "imported_at_utc": m.get("imported_at_utc")}
