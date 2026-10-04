#!/usr/bin/env python3
"""Configure the shared execution-node token without printing it."""

from __future__ import annotations

import argparse
import secrets
import sys
from pathlib import Path


def update_env(path: Path, values: dict[str, str]) -> None:
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    remaining = dict(values)
    output = []
    for line in lines:
        if "=" in line and not line.lstrip().startswith("#"):
            key = line.split("=", 1)[0].strip()
            if key in remaining:
                output.append(f"{key}={remaining.pop(key)}")
                continue
        output.append(line)
    if output and output[-1].strip():
        output.append("")
    output.extend(f"{key}={value}" for key, value in remaining.items())
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text("\n".join(output).rstrip() + "\n", encoding="utf-8")
    temporary.replace(path)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--generate", action="store_true")
    source.add_argument("--stdin", action="store_true")
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--token-output", type=Path)
    parser.add_argument(
        "--api-base-url",
        default="https://dev-cn-01.yios.cn/deepstock/api",
    )
    args = parser.parse_args()
    token = secrets.token_urlsafe(48) if args.generate else sys.stdin.read().strip()
    if len(token) < 32:
        raise ValueError("node token must contain at least 32 characters")
    update_env(
        args.env_file.resolve(),
        {
            "DEEPSTOCK_NODE_TOKEN": token,
            "DEEPSTOCK_API_BASE_URL": args.api_base_url.rstrip("/"),
        },
    )
    if args.token_output:
        args.token_output.write_text(token + "\n", encoding="utf-8")
    print("execution-node token configured (value not displayed)")


if __name__ == "__main__":
    main()
