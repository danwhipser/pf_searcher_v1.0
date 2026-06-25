#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
RESULT_DIR = ROOT / "result"
LEVEL_KEYS = ("等级", "level_raw", "等級")
EFFECT_KEYS = ("效果", "effect", "法术效果")
PROSE_MARKERS = (
    "该法术",
    "此法术",
    "这个法术",
    "该能力",
    "不过",
    "但是",
    "如果",
    "若",
    "当你",
    "目标",
    "受术者",
    "你可以",
    "你能够",
    "获得",
    "拥有",
    "提高",
    "造成",
    "每施法者",
    "施法者等级",
    "可创造",
    "类似于",
    "功能",
    "方式如同",
)
ENTRY_RE = re.compile(r"\s*(?:[,，、;；]\s*)?((?:领域\s+)?[^,，、;；0-9]{1,30}?\s*[0-9])")


def is_plausible_level_entry(entry: str) -> bool:
    text = re.sub(r"\s+", " ", entry or "").strip()
    class_name = re.sub(r"\s*[0-9]\s*$", "", text).strip()
    if not class_name or len(class_name) > 40:
        return False
    if re.search(r"[。！？：:]", class_name):
        return False
    return not any(marker in class_name for marker in PROSE_MARKERS)


def split_level_text(value: str) -> tuple[str, str]:
    text = re.sub(r"\s+", " ", value or "").strip()
    if not text:
        return "", ""

    pos = 0
    last_end = 0
    entries: list[str] = []
    while pos < len(text):
        match = ENTRY_RE.match(text, pos)
        if not match:
            break
        entry = re.sub(r"\s+", " ", match.group(1)).strip()
        if not is_plausible_level_entry(entry):
            break
        entries.append(entry)
        last_end = match.end()
        pos = match.end()

    if not entries:
        return text, ""

    remainder = text[last_end:].strip()
    if len(remainder) < 20:
        return text, ""
    return "，".join(entries).strip(" ,，、;；"), remainder


def effect_key_for(row: dict) -> str:
    for key in EFFECT_KEYS:
        if key in row:
            return key
    return "效果"


def append_effect(row: dict, remainder: str) -> None:
    key = effect_key_for(row)
    current = str(row.get(key) or "").strip()
    if not current:
        row[key] = remainder
    elif remainder not in current:
        row[key] = f"{current}\n{remainder}"


def repair_row(row: dict) -> int:
    changes = 0
    for key in LEVEL_KEYS:
        if key not in row or not isinstance(row.get(key), str):
            continue
        cleaned, remainder = split_level_text(row[key])
        if not remainder or cleaned == row[key]:
            continue
        row[key] = cleaned
        append_effect(row, remainder)
        changes += 1

    raw_fields = row.get("raw_fields")
    if isinstance(raw_fields, dict):
        for key in LEVEL_KEYS:
            if key not in raw_fields or not isinstance(raw_fields.get(key), str):
                continue
            cleaned, remainder = split_level_text(raw_fields[key])
            if not remainder or cleaned == raw_fields[key]:
                continue
            raw_fields[key] = cleaned
            append_effect(raw_fields, remainder)
            changes += 1
    return changes


def iter_spell_jsons() -> list[Path]:
    return sorted(
        path
        for path in RESULT_DIR.glob("*/spells-*.json")
        if not any(token in path.name for token in ("check", "qa", "summary", "unparsed"))
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Move leaked spell descriptions out of level fields.")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    changed_files = 0
    changed_fields = 0
    for path in iter_spell_jsons():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(data, list):
            continue

        file_changes = 0
        for row in data:
            if isinstance(row, dict):
                file_changes += repair_row(row)

        if file_changes:
            changed_files += 1
            changed_fields += file_changes
            print(f"{path.relative_to(ROOT)}: {file_changes}")
            if not args.dry_run:
                path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"changed_files={changed_files} changed_fields={changed_fields} dry_run={args.dry_run}")


if __name__ == "__main__":
    main()
