#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from deepstock.web.database import SessionLocal
from deepstock.web.models import ResearchNote, Strategy


REQUIRED_FIELDS = {
    "impact_scope",
    "causal_logic",
    "novelty",
    "point_in_time_data",
    "backtestability",
    "leakage_risk",
    "overfitting_risk",
    "evidence",
    "next_test",
}
RECOMMENDATIONS = {"reject", "observe", "test", "adopt"}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Register an assessed research note")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--text")
    source.add_argument("--text-file", type=Path)
    parser.add_argument("--strategy-id")
    parser.add_argument("--scope", choices=("global", "strategy"))
    parser.add_argument("--assessment", type=Path, required=True)
    parser.add_argument("--recommendation", choices=sorted(RECOMMENDATIONS), required=True)
    return parser


def _load_assessment(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("assessment must be a JSON object")
    missing = sorted(REQUIRED_FIELDS - value.keys())
    if missing:
        raise ValueError(f"assessment is missing fields: {', '.join(missing)}")
    return value


def main() -> None:
    args = _parser().parse_args()
    original_text = (
        args.text if args.text is not None else args.text_file.read_text(encoding="utf-8")
    ).strip()
    if not original_text:
        raise ValueError("research note text cannot be empty")
    assessment = _load_assessment(args.assessment)
    scope = args.scope or ("strategy" if args.strategy_id else "global")
    if scope == "strategy" and not args.strategy_id:
        raise ValueError("strategy scope requires --strategy-id")

    with SessionLocal() as session:
        if args.strategy_id and session.get(Strategy, args.strategy_id) is None:
            raise ValueError(f"unknown strategy: {args.strategy_id}")
        note = ResearchNote(
            original_text=original_text,
            source="chat",
            scope_type=scope,
            strategy_id=args.strategy_id,
            assessment=assessment,
            recommendation=args.recommendation,
            user_decision="pending",
            status="awaiting_user_decision",
        )
        session.add(note)
        session.commit()
        session.refresh(note)
        print(
            json.dumps(
                {
                    "id": note.id,
                    "status": note.status,
                    "recommendation": note.recommendation,
                },
                ensure_ascii=False,
            )
        )


if __name__ == "__main__":
    main()
