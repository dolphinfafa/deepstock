#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json

from deepstock.web.database import SessionLocal
from deepstock.web.models import ResearchNote, utcnow


DECISIONS = {
    "rejected": "closed",
    "deferred": "deferred",
    "approved_for_test": "approved_for_test",
    "approved_for_method": "approved_for_method",
}


def main() -> None:
    parser = argparse.ArgumentParser(description="Record the user's research-note decision")
    parser.add_argument("note_id")
    parser.add_argument("--decision", choices=sorted(DECISIONS), required=True)
    args = parser.parse_args()

    with SessionLocal() as session:
        note = session.get(ResearchNote, args.note_id)
        if note is None:
            raise ValueError(f"research note not found: {args.note_id}")
        note.user_decision = args.decision
        note.status = DECISIONS[args.decision]
        note.decided_at = utcnow()
        session.commit()
        print(
            json.dumps(
                {"id": note.id, "decision": note.user_decision, "status": note.status},
                ensure_ascii=False,
            )
        )


if __name__ == "__main__":
    main()
