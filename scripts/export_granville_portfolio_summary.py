"""Export aggregate research only; full per-entry sizing audits stay local."""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
import hashlib
import json
from pathlib import Path

from deepstock.data.store import digest, write_json
from scripts.rerun_clean_research import capture_code_provenance


def aggregate_summary(value):
    result = deepcopy(value)
    for diagnostics in [result.get("diagnostics", {}), *(c.get("diagnostics", {}) for c in result["cases"])]:
        rows = diagnostics.pop("sizing_audit", None)
        if rows is not None:
            diagnostics["sizing_audit_summary"] = {
                "rows": len(rows), "statuses": dict(Counter(r["status"] for r in rows)),
                "content_sha256": hashlib.sha256(json.dumps(rows, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest(),
                "policy": "Per-entry audit retained on licensed node; aggregate counts/hash only"}
    return result


def run(source, output):
    if output.exists():
        raise FileExistsError("Preserve prior exported summary")
    code = capture_code_provenance()
    if code["tracked_dirty"]:
        raise ValueError("Committed clean exporter source required")
    before = digest(source)
    value = aggregate_summary(json.loads(source.read_text(encoding="utf-8")))
    value.update(original_summary_sha256=before, summary_export_provenance=code)
    if digest(source) != before or capture_code_provenance()["source_sha256"] != code["source_sha256"]:
        raise ValueError("Frozen summary/exporter changed")
    write_json(output, value)
    print(json.dumps({"source_sha256": before, "export_sha256": digest(output), "export_bytes": output.stat().st_size}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    run(args.source, args.output)


if __name__ == "__main__":
    main()
