#!/usr/bin/env python3
"""Create a repeatable workspace layout for internal slider CAPTCHA evaluations."""

from __future__ import annotations

import argparse
from pathlib import Path

DIRECTORIES = [
    "samples/raw/backgrounds",
    "samples/raw/pieces",
    "samples/rendered",
    "labels",
    "manifests",
    "runs",
    "reports",
]


def create_tree(root: Path) -> tuple[list[Path], list[Path]]:
    created: list[Path] = []
    existing: list[Path] = []

    root.mkdir(parents=True, exist_ok=True)

    for relative_path in DIRECTORIES:
        target = root / relative_path
        if target.exists():
            existing.append(target)
            continue
        target.mkdir(parents=True, exist_ok=True)
        created.append(target)

    return created, existing


def format_tree(root: Path) -> str:
    lines = [str(root.resolve())]
    for relative_path in DIRECTORIES:
        lines.append(f"  - {relative_path}")
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Bootstrap a repeatable folder structure for slider CAPTCHA evaluation."
    )
    parser.add_argument("target_dir", help="Directory to create or update")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = Path(args.target_dir)
    created, existing = create_tree(root)

    print("Workspace ready.")
    print(format_tree(root))
    print()
    print(f"Created: {len(created)}")
    for path in created:
        print(f"  + {path}")

    print(f"Already existed: {len(existing)}")
    for path in existing:
        print(f"  = {path}")

    print()
    print("Next steps:")
    print("  1. Place immutable raw assets under samples/raw/")
    print("  2. Define labels and manifests using references/data-contract.md")
    print("  3. Write run outputs as JSONL under runs/")
    print("  4. Summarize a run with scripts/summarize_runs.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
