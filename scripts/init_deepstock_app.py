#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

from alembic import command
from alembic.config import Config

from deepstock.web.config import settings
from deepstock.web.database import SessionLocal
from deepstock.web.ingestion import ingest_all
from deepstock.web.security import ensure_bootstrap_user


def main() -> None:
    database_path = settings.database_url.removeprefix("sqlite:///")
    if settings.database_url.startswith("sqlite:///"):
        Path(database_path).parent.mkdir(parents=True, exist_ok=True)

    alembic_config = Config(str(settings.project_root / "alembic.ini"))
    alembic_config.set_main_option("sqlalchemy.url", settings.database_url)
    command.upgrade(alembic_config, "head")

    with SessionLocal() as session:
        user = ensure_bootstrap_user(session)
        result = ingest_all(session)

    print(
        json.dumps(
            {
                "status": "ok",
                "database_url": settings.database_url,
                "bootstrap_user": user.username,
                "ingestion": result,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
