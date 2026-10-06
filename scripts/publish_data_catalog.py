"""Publish only bounded licensed-node metadata, never market rows."""
import json
import httpx
from deepstock.data.store import DataStore
from deepstock.web.config import settings


def main():
    manifests = []
    for path in DataStore().manifests():
        value = json.loads(path.read_text(encoding="utf-8"))
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
