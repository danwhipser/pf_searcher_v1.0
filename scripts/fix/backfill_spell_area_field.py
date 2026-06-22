#!/usr/bin/env python3
from __future__ import annotations

# Allow direct execution from nested scripts/ folders.
import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


ROOT = Path(__file__).resolve().parents[2]
RESULT_DIR = ROOT / "result"

SKIP_NAME_PARTS = {
    "-qa",
    "-check",
    "-summary",
    "-unparsed",
    "-tables",
    "-report",
    "-located",
    "-aligned",
}

AREA_LABEL_RE = re.compile(r"(?:^|[；;，,\s])(?:或)?区域\s*[:：]\s*(?P<area>.+)$")
AREA_TERMS_RE = re.compile(
    r"(锥形|锥状|圆锥|爆发|扩散|弥漫|弥散|半径|直线|线状|球形|球体|半球|圆柱|圆环)"
)
PURE_AREA_RE = re.compile(
    r"^(?:"
    r"(?:以.{1,24}为中心[，,]?)?"
    r"(?:\d+\s*尺|半径\s*\d+\s*尺|一?个?半径\s*\d+\s*尺|锥形|锥状|圆锥|直线|线形|"
    r"\d+\s*尺线状|一个半径|从.{1,24}向外)"
    r")"
)
TARGET_WORDS_RE = re.compile(r"(生物|盟友|敌人|物体|物品|目标|受术者|你自己|自己|自身|尸体)")
PROSE_MARKERS_RE = re.compile(r"(该法术|这个法术|本法术|当你|如果|若|受到|进行|造成|获得|可以|能够|不会|必须)")


def iter_spell_files(result_dir: Path) -> list[Path]:
    paths: list[Path] = []
    for path in sorted(result_dir.rglob("spells-*.json")):
        name = path.stem.lower()
        if any(part in name for part in SKIP_NAME_PARTS):
            continue
        if path.parent == result_dir:
            continue
        paths.append(path)
    return paths


def clean_text(value: Any) -> str:
    return re.sub(r"[\s\xa0\u3000\ufeff]+", " ", str(value or "")).strip()


def insert_after(data: dict[str, Any], after_key: str, key: str, value: Any) -> None:
    if key in data:
        data[key] = value
        return
    items = list(data.items())
    data.clear()
    inserted = False
    for item_key, item_value in items:
        data[item_key] = item_value
        if item_key == after_key:
            data[key] = value
            inserted = True
    if not inserted:
        data[key] = value


def is_area_like(text: str) -> bool:
    if not text:
        return False
    if AREA_LABEL_RE.search(text):
        return True
    if PROSE_MARKERS_RE.search(text):
        return False
    if "区域" in text and len(text) <= 90:
        return True
    return bool(AREA_TERMS_RE.search(text)) and len(text) <= 120


def is_pure_area(text: str) -> bool:
    if not text or len(text) > 90:
        return False
    if PROSE_MARKERS_RE.search(text):
        return False
    if TARGET_WORDS_RE.search(text):
        return False
    return bool(PURE_AREA_RE.search(text) and AREA_TERMS_RE.search(text))


def infer_area_from_target(target: str) -> tuple[str, bool, str]:
    target = clean_text(target)
    if not target:
        return "", False, ""

    label_match = AREA_LABEL_RE.search(target)
    if label_match:
        area = clean_text(label_match.group("area"))
        return area, False, "explicit_label"

    if not is_area_like(target):
        return "", False, ""

    return target, is_pure_area(target), "shape_heuristic"


def remove_key(data: dict[str, Any], key: str) -> None:
    if key in data:
        del data[key]


def get_model_area(record: dict[str, Any]) -> str:
    raw_fields = record.get("raw_fields") if isinstance(record.get("raw_fields"), dict) else {}
    return clean_text(record.get("area") or raw_fields.get("区域"))


def get_legacy_area(record: dict[str, Any]) -> str:
    return clean_text(record.get("区域"))


def patch_model_record(record: dict[str, Any]) -> tuple[bool, str]:
    area = get_model_area(record)
    if area:
        if not is_area_like(area):
            if not clean_text(record.get("target")):
                insert_after(record, "range", "target", area)
            remove_key(record, "area")
            raw_fields = record.get("raw_fields")
            if isinstance(raw_fields, dict):
                if not clean_text(raw_fields.get("目标")):
                    insert_after(raw_fields, "区域", "目标", area)
                remove_key(raw_fields, "区域")
            return True, "pruned_unsafe_area"
        if not clean_text(record.get("area")):
            insert_after(record, "range", "area", area)
            return True, "sync_existing"
        return False, ""

    target = clean_text(record.get("target"))
    inferred, clear_target, reason = infer_area_from_target(target)
    if not inferred:
        return False, ""

    insert_after(record, "range", "area", inferred)
    raw_fields = record.get("raw_fields")
    if isinstance(raw_fields, dict):
        insert_after(raw_fields, "范围", "区域", inferred)
        if clear_target and clean_text(raw_fields.get("目标")) == target:
            raw_fields["目标"] = ""
    if clear_target:
        record["target"] = ""
    return True, f"{reason}{'_moved' if clear_target else '_copied'}"


def patch_legacy_record(record: dict[str, Any]) -> tuple[bool, str]:
    area = get_legacy_area(record)
    if area:
        if not is_area_like(area):
            if not clean_text(record.get("目标")):
                insert_after(record, "区域", "目标", area)
            remove_key(record, "区域")
            return True, "pruned_unsafe_area"
        return False, ""

    target = clean_text(record.get("目标"))
    inferred, clear_target, reason = infer_area_from_target(target)
    if not inferred:
        return False, ""

    insert_after(record, "范围", "区域", inferred)
    if clear_target:
        record["目标"] = ""
    return True, f"{reason}{'_moved' if clear_target else '_copied'}"


def patch_file(path: Path) -> tuple[bool, dict[str, int]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        return False, {}

    counts: dict[str, int] = {}
    changed = False
    for record in data:
        if not isinstance(record, dict):
            continue
        if "spell_id" in record:
            item_changed, reason = patch_model_record(record)
        else:
            item_changed, reason = patch_legacy_record(record)
        if item_changed:
            changed = True
            counts[reason] = counts.get(reason, 0) + 1

    if changed:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return changed, counts


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill spell area/区域 fields from high-confidence target text.")
    parser.add_argument("--dry-run", action="store_true", help="Only report planned changes.")
    parser.add_argument("--result-dir", type=Path, default=RESULT_DIR)
    args = parser.parse_args()

    files = iter_spell_files(args.result_dir)
    changed_files: list[tuple[Path, dict[str, int]]] = []
    total_counts: dict[str, int] = {}

    for path in files:
        before = path.read_text(encoding="utf-8")
        changed, counts = patch_file(path)
        if args.dry_run and changed:
            path.write_text(before, encoding="utf-8")
        if changed:
            changed_files.append((path, counts))
            for key, value in counts.items():
                total_counts[key] = total_counts.get(key, 0) + value

    print(f"scanned_files={len(files)}")
    print(f"changed_files={len(changed_files)}")
    for key in sorted(total_counts):
        print(f"{key}={total_counts[key]}")
    for path, counts in changed_files[:50]:
        detail = ", ".join(f"{key}:{value}" for key, value in sorted(counts.items()))
        print(f"{path.relative_to(ROOT)}\t{detail}")
    if len(changed_files) > 50:
        print(f"... {len(changed_files) - 50} more files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
