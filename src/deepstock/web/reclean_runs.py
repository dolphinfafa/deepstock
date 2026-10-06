from __future__ import annotations
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from deepstock.web.models import ResearchRun, ResearchReport, Metric, Strategy


def ingest_reclean_runs(session, root: Path):
    count = 0
    for path in sorted((root / "artifacts/reclean").glob("*/publication.json")):
        value = json.loads(path.read_text(encoding="utf-8"))
        if session.get(Strategy, value["strategy_id"]) is None:
            continue
        run_id = value["id"]
        if session.get(ResearchRun, run_id):
            continue
        session.add(ResearchRun(id=run_id, strategy_id=value["strategy_id"], run_type="fixed_reclean_backtest", status=value["status"],
                                data_start=value.get("data_start"), data_end=value.get("data_end"), as_of_date=value["as_of_date"],
                                config_hash=value.get("config_hash"), code_version=value.get("code_version"), summary=value["summary"],
                                source_hash=hashlib.sha256(path.read_bytes()).hexdigest(), artifact_path=str(path.relative_to(root)),
                                details=value, finished_at=datetime.now(timezone.utc)))
        session.flush()
        for name, metric in value.get("metrics", {}).items():
            session.add(Metric(run_id=run_id, name=name, scope=value.get("scope", "full"), value=metric,
                               unit="ratio" if "return" in name or "drawdown" in name else "number"))
        session.add(ResearchReport(id=run_id + "-report", strategy_id=value["strategy_id"], run_id=run_id, report_type="reclean_comparison",
                                   title="清洗版本固定回测与历史对照", as_of_date=value["as_of_date"], format="markdown",
                                   content=value["report_md"], content_hash=hashlib.sha256(value["report_md"].encode()).hexdigest()))
        count += 1
    session.commit()
    return {"runs": count}
