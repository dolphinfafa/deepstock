"""Publish only bounded licensed-node metadata, never market rows."""
import json
import argparse
from datetime import datetime
import httpx
from deepstock.data.store import DataStore
from deepstock.web.config import settings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--since", help="Publish only versions imported since this timezone-aware ISO UTC time")
    args = parser.parse_args()
    since = datetime.fromisoformat(args.since) if args.since else None
    if since is not None and since.tzinfo is None:
        raise ValueError("Metadata publishing start time must include a timezone")
    manifests = []
    for path in DataStore().manifests():
        value = json.loads(path.read_text(encoding="utf-8"))
        if since is not None and datetime.fromisoformat(value["imported_at_utc"]) < since:
            continue
        for field in ["source_path", "raw_file", "clean_file"]:
            value.pop(field, None)
        value["preview_allowed"] = False
        manifests.append(value)
    if not settings.node_token:
        raise RuntimeError("DEEPSTOCK_NODE_TOKEN missing")
    for start in range(0, len(manifests), 500):
        response = httpx.post(f"{settings.public_base_url}/api/agent/research/data-versions", headers={"Authorization": f"Bearer {settings.node_token}"},
                              json=manifests[start:start+500], timeout=60)
        response.raise_for_status()
    print(json.dumps({"published": len(manifests)}))


if __name__ == "__main__":
    main()
