"""Append-only quality notices, separate from immutable performance ledgers."""
import hashlib
import json

from deepstock.web.models import ResearchReport, Strategy


def load_notices(root):
    path = root / "config/research_evidence_notices.json"
    return json.loads(path.read_text(encoding="utf-8"))["notices"] if path.exists() else []


def notices_for_run(root, strategy_id, details):
    refs = {r["version"] if isinstance(r, dict) else r for r in details.get("data_versions", [])}
    return [notice for notice in load_notices(root) if notice["strategy_id"] == strategy_id
            and refs.intersection(notice["affected_data_versions"])]


def ingest_evidence_notices(session, root):
    count = 0
    for value in load_notices(root):
        if not session.get(Strategy, value["strategy_id"]):
            continue
        report_id = value["id"] + "-report"
        content = value["report_md"]
        checksum = hashlib.sha256(content.encode()).hexdigest()
        existing = session.get(ResearchReport, report_id)
        if existing:
            if existing.content_hash != checksum:
                raise ValueError("Immutable evidence notice changed; add a new notice")
            continue
        session.add(ResearchReport(id=report_id, strategy_id=value["strategy_id"], run_id=None,
                                   report_type="data_readiness_correction", title=value["title"],
                                   as_of_date=value["as_of_date"], format="markdown", content=content,
                                   content_hash=checksum))
        count += 1
    session.commit()
    return {"reports": count}
