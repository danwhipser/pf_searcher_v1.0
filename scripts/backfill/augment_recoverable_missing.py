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
import re
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
IN_BOOK_FEATS = ROOT / "result" / "feats" / "feat-book-feats.json"
IN_ANALYSIS = ROOT / "result" / "feats" / "missing-feats-cn-analysis-v2.json"


def normalize_key(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (text or "").lower())


def strip_source_suffix(name: str) -> str:
    return re.sub(r"\s*\(([A-Z]{2,6})\)\s*$", "", (name or "").strip())


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill recoverable missing feats into per-book feat list.")
    parser.add_argument("--book-feats", type=Path, default=IN_BOOK_FEATS)
    parser.add_argument("--analysis", type=Path, default=IN_ANALYSIS)
    parser.add_argument("--inplace", action="store_true")
    parser.add_argument("--output", type=Path, default=ROOT / "result" / "feats" / "feat-book-feats-recoverable.json")
    args = parser.parse_args()

    book_feats: dict[str, list[dict[str, Any]]] = load_json(args.book_feats)
    analysis = load_json(args.analysis).get("items", [])

    added = 0
    by_book: dict[str, int] = {}

    for item in analysis:
        reason = item.get("reason_code")
        # only backfill items that have concrete CHM page evidence
        if reason not in {"parser_miss_or_ocr", "source_tag_mismatch", "non_feat_page"}:
            continue

        book = item.get("book", "")
        feat_en_raw = item.get("feat_en", "")
        feat_en = strip_source_suffix(feat_en_raw)
        pages = item.get("found_pages") or []
        if not book or not feat_en or not pages:
            continue
        if book not in book_feats:
            continue

        key = normalize_key(feat_en)
        if not key:
            continue

        rows = book_feats[book]
        existing = {r.get("match_key", "") for r in rows if r.get("match_key")}
        if key in existing:
            continue

        cn_guess = item.get("feat_cn_guess", "") or ""
        rows.append(
            {
                "match_key": key,
                "name_en": feat_en,
                "name_cn": cn_guess,
                "name_raw": feat_en_raw,
                "prerequisites": "",
                "benefit_summary": "",
                "detail_text": "",
                "source_pages": [
                    {
                        "local": pages[0],
                        "toc_path": "recoverable_missing_backfill",
                        "table_index": -98,
                        "row_index": -1,
                    }
                ],
            }
        )
        added += 1
        by_book[book] = by_book.get(book, 0) + 1

    out = args.book_feats if args.inplace else args.output
    out.write_text(json.dumps(book_feats, ensure_ascii=False, indent=2), encoding="utf-8")

    print("Done.")
    print(f"Added: {added}")
    for k, v in sorted(by_book.items(), key=lambda x: (-x[1], x[0])):
        print(f"{k}: +{v}")
    print(f"Output: {out}")


if __name__ == "__main__":
    main()
