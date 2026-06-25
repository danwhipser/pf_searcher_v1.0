#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
RESULT_DIR = ROOT / "result"
SCHOOL_KEYS = ("学派", "school")
LEVEL_KEYS = ("等级", "level_raw", "等級")
NAME_KEYS = ("name", "name_en", "name_zh", "名称", "display_name")

SCHOOL_TRANSLATIONS = {
    "abjuration": "防护系",
    "conjuration": "咒法系",
    "divination": "预言系",
    "enchantment": "惑控系",
    "evocation": "塑能系",
    "illusion": "幻术系",
    "necromancy": "死灵系",
    "transmutation": "变化系",
    "universal": "通用系",
}

SCHOOL_OVERRIDES = {
    "Life Conduit, Improved": "咒法系 (医疗)",
    "进阶生命通道": "咒法系 (医疗)",
    "Mount, Communal": "咒法系 (召唤)",
    "共享召唤坐骑": "咒法系 (召唤)",
    "共用召唤坐骑": "咒法系 (召唤)",
}

ENTRY_RE = re.compile(r"\s*(?:[,，、;；]\s*)?((?:领域\s+)?[^,，、;；0-9]{1,30}?\s*[0-9])")
SCHOOL_WORD_RE = re.compile(
    r"防护|咒法|预言|惑控|塑能|幻术|死灵|变化|变形|通用|"
    r"abjuration|conjuration|divination|enchantment|evocation|illusion|necromancy|transmutation|universal",
    re.I,
)
CHINESE_SCHOOL_RE = re.compile(
    r"(防护系|咒法系|预言系|惑控系|塑能系|幻术系|死灵系|变化系|变形系|通用系)"
    r"(?:\s*[(（][^)）]{1,20}[)）])?"
    r"(?:\s*\[[^\]]{1,80}\])?"
)


def clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def is_level_like(text: str) -> bool:
    value = clean_text(text)
    if not value or SCHOOL_WORD_RE.search(value):
        return False
    pos = 0
    found = False
    while pos < len(value):
        match = ENTRY_RE.match(value, pos)
        if not match:
            return False
        found = True
        pos = match.end()
    return found


def append_level(row: dict, level_text: str) -> None:
    if not level_text:
        return
    key = next((item for item in LEVEL_KEYS if item in row), "等级")
    current = clean_text(str(row.get(key) or ""))
    if not current:
        row[key] = level_text
    elif level_text not in current:
        row[key] = f"{current}，{level_text}"


def name_tokens(row: dict) -> str:
    return " ".join(str(row.get(key) or "") for key in NAME_KEYS)


def override_school(row: dict) -> str:
    text = name_tokens(row)
    for token, school in SCHOOL_OVERRIDES.items():
        if token in text:
            return school
    return ""


def normalize_school(value: str) -> str:
    text = clean_text(value)
    lower = text.lower()
    if lower in SCHOOL_TRANSLATIONS:
        return SCHOOL_TRANSLATIONS[lower]

    match = CHINESE_SCHOOL_RE.match(text)
    if match and (len(text) > len(match.group(0)) + 30 or " 环位 " in text or " 等级 " in text):
        return clean_text(match.group(0))

    return text


def repair_container(container: dict, fallback_school: str) -> int:
    changes = 0
    for key in SCHOOL_KEYS:
        value = container.get(key)
        if not isinstance(value, str):
            continue

        original = value
        if not clean_text(value) and fallback_school:
            container[key] = fallback_school
        elif is_level_like(value):
            append_level(container, clean_text(value))
            container[key] = fallback_school
        else:
            container[key] = normalize_school(value)

        if container[key] != original:
            changes += 1
    return changes


def repair_row(row: dict) -> int:
    fallback_school = override_school(row)
    changes = repair_container(row, fallback_school)
    raw_fields = row.get("raw_fields")
    if isinstance(raw_fields, dict):
        changes += repair_container(raw_fields, fallback_school)
    return changes


def iter_spell_jsons() -> list[Path]:
    return sorted(
        path
        for path in RESULT_DIR.glob("**/spells*.json")
        if not any(token in path.name for token in ("check", "qa", "summary", "unparsed"))
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Repair spell school fields.")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    changed_files = 0
    changed_fields = 0
    for path in iter_spell_jsons():
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
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
