#!/usr/bin/env python3
from __future__ import annotations

# Allow direct execution from nested scripts/ folders.
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import argparse
import json
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
RESULT_DIR = ROOT / "result"
WRONG_CLASS = "巫师"
CORRECT_CLASS = "法师"
LEVEL_TEXT_KEYS = {"等级", "level_raw"}
CLASS_LIST_KEYS = {"level_by_class", "class_levels"}


def fix_class_text(value: str) -> str:
    return value.replace(WRONG_CLASS, CORRECT_CLASS)


def fix_spell_object(obj: Any) -> int:
    changes = 0
    if isinstance(obj, list):
        for item in obj:
            changes += fix_spell_object(item)
        return changes

    if not isinstance(obj, dict):
        return 0

    for key, value in list(obj.items()):
        if key in LEVEL_TEXT_KEYS and isinstance(value, str):
            fixed = fix_class_text(value)
            if fixed != value:
                obj[key] = fixed
                changes += 1
            continue

        if key in CLASS_LIST_KEYS and isinstance(value, list):
            for entry in value:
                if not isinstance(entry, dict):
                    continue
                class_name = entry.get("class")
                if isinstance(class_name, str):
                    fixed = fix_class_text(class_name)
                    if fixed != class_name:
                        entry["class"] = fixed
                        changes += 1
            continue

        changes += fix_spell_object(value)

    return changes


def fix_file(path: Path, dry_run: bool = False) -> int:
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    changes = fix_spell_object(data)
    if changes and not dry_run:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return changes


def iter_spell_files() -> list[Path]:
    return sorted(RESULT_DIR.glob("*/spells-*.json"))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="修正法术职业翻译：将等级字段里的“巫师”归入“法师”。"
    )
    parser.add_argument("--dry-run", action="store_true", help="只统计，不写入文件")
    args = parser.parse_args()

    touched = []
    for path in iter_spell_files():
        try:
            changes = fix_file(path, dry_run=args.dry_run)
        except Exception as exc:
            print(f"SKIP {path}: {exc}")
            continue
        if changes:
            touched.append((path, changes))

    for path, changes in touched:
        print(f"{path.relative_to(ROOT)}: {changes} changes")
    print(f"Updated files: {len(touched)}")


if __name__ == "__main__":
    main()
