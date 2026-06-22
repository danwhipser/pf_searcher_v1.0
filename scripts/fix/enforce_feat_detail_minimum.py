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
DEFAULT_BOOK_FEATS = ROOT / "result" / "feats" / "feat-book-feats.json"
DEFAULT_FRONTEND = ROOT / "result" / "feats" / "feats-frontend.json"
DEFAULT_REPORT = ROOT / "result" / "feats" / "feat-detail-minimum-report.json"


def normalize_ws(text: Any) -> str:
    return " ".join(str(text or "").replace("\u3000", " ").split()).strip()


def pick_name(row: dict[str, Any]) -> str:
    for key in ("name_cn", "name_en", "name_raw", "match_key"):
        value = normalize_ws(row.get(key, ""))
        if value:
            return value
    return "未命名专长"


def ensure_string_field(row: dict[str, Any], key: str) -> None:
    value = row.get(key, "")
    if value is None:
        row[key] = ""
    elif not isinstance(value, str):
        row[key] = str(value)


def fill_detail_text(row: dict[str, Any]) -> tuple[bool, str]:
    """
    Ensure detail_text exists and is non-empty.
    Returns (changed, fill_source).
    """
    for key in ("prerequisites", "benefit_summary", "detail_text"):
        ensure_string_field(row, key)

    detail = normalize_ws(row.get("detail_text", ""))
    if detail:
        # Preserve extracted detail as-is.
        return False, "kept"

    benefit = normalize_ws(row.get("benefit_summary", ""))
    if benefit:
        row["detail_text"] = benefit
        return True, "benefit_summary"

    prereq = normalize_ws(row.get("prerequisites", ""))
    if prereq:
        row["detail_text"] = f"先决条件：{prereq}"
        return True, "prerequisites"

    row["detail_text"] = f"【待补全】{pick_name(row)}"
    return True, "placeholder"


def process_book_feats(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path} 顶层不是对象")

    stats = {
        "total_rows": 0,
        "changed_rows": 0,
        "fill_by_benefit_summary": 0,
        "fill_by_prerequisites": 0,
        "fill_by_placeholder": 0,
        "kept_existing_detail": 0,
        "books_changed": 0,
    }

    for book, rows in data.items():
        if not isinstance(rows, list):
            continue
        book_changed = False
        for row in rows:
            if not isinstance(row, dict):
                continue
            stats["total_rows"] += 1
            changed, source = fill_detail_text(row)
            if source == "benefit_summary":
                stats["fill_by_benefit_summary"] += 1
            elif source == "prerequisites":
                stats["fill_by_prerequisites"] += 1
            elif source == "placeholder":
                stats["fill_by_placeholder"] += 1
            else:
                stats["kept_existing_detail"] += 1
            if changed:
                stats["changed_rows"] += 1
                book_changed = True
        if book_changed:
            stats["books_changed"] += 1

    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return stats


def process_frontend(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path} 顶层不是对象")

    feats = data.get("feats")
    if not isinstance(feats, list):
        raise ValueError(f"{path} 缺少 feats 列表")

    stats = {
        "total_rows": 0,
        "changed_rows": 0,
        "fill_by_benefit_summary": 0,
        "fill_by_prerequisites": 0,
        "fill_by_placeholder": 0,
        "kept_existing_detail": 0,
    }

    for row in feats:
        if not isinstance(row, dict):
            continue
        stats["total_rows"] += 1
        changed, source = fill_detail_text(row)
        if source == "benefit_summary":
            stats["fill_by_benefit_summary"] += 1
        elif source == "prerequisites":
            stats["fill_by_prerequisites"] += 1
        elif source == "placeholder":
            stats["fill_by_placeholder"] += 1
        else:
            stats["kept_existing_detail"] += 1
        if changed:
            stats["changed_rows"] += 1

    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Ensure each feat has non-empty detail_text, preserving extracted fields."
    )
    parser.add_argument("--book-feats", type=Path, default=DEFAULT_BOOK_FEATS)
    parser.add_argument("--frontend", type=Path, default=DEFAULT_FRONTEND)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args()

    book_stats = process_book_feats(args.book_feats)
    frontend_stats = process_frontend(args.frontend)

    report = {
        "book_feats_path": str(args.book_feats),
        "frontend_path": str(args.frontend),
        "book_feats": book_stats,
        "frontend": frontend_stats,
    }
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print("book_feats:", book_stats)
    print("frontend:", frontend_stats)
    print("report:", args.report)


if __name__ == "__main__":
    main()