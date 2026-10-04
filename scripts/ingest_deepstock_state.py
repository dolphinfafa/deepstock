#!/usr/bin/env python3
from __future__ import annotations

import json

from deepstock.web.database import SessionLocal
from deepstock.web.ingestion import ingest_all


def main() -> None:
    with SessionLocal() as session:
        result = ingest_all(session)
    print(json.dumps({"status": "ok", **result}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
