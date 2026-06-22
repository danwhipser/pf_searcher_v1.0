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
IN_BOOK_FEATS = ROOT / "result" / "feats" / "feat-book-feats.json"
OUT_BOOK_FEATS = ROOT / "result" / "feats" / "feat-book-feats-summary-fallback.json"

SUMMARY_BOOK_PAGES: dict[str, set[str]] = {
    "CRB 核心规则手册": {"page_195.html"},
    "APG 进阶玩家手册": {"page_196.html"},
    "UM 极限魔法": {"page_198.html"},
    "UC 极限战斗": {"page_199.html"},
    "UCa 极限战役": {"page_200.html"},
    "ACG 进阶职业手册": {"page_203.html"},
}

# Some supplements are currently represented by compact feat list pages in CHM.
# For these books, missing detail_text can safely fall back to benefit_summary.
SUMMARY_ANY_PAGE_BOOKS: set[str] = {
    "冒险家手册",
    "B1 怪物图鉴",
    "远程战术工具箱",
    "内海世界指南",
    "内海战斗",
    "内海魔法",
    "近战战术工具箱",
    "DEP初探龙国",
    "进化职业起源（ACO）",
}


def _has_any_local(row: dict[str, Any], pages: set[str]) -> bool:
    for sp in row.get("source_pages") or []:
        local = sp.get("local")
        if local in pages:
            return True
    return False


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill detail_text from benefit_summary for known summary-only book pages.")
    parser.add_argument("--book-feats", type=Path, default=IN_BOOK_FEATS)
    parser.add_argument("--output", type=Path, default=OUT_BOOK_FEATS)
    parser.add_argument("--inplace", action="store_true")
    parser.add_argument(
        "--all-books",
        action="store_true",
        help="Apply fallback to every book (detail_text <- benefit_summary when detail is empty).",
    )
    args = parser.parse_args()

    data: dict[str, list[dict[str, Any]]] = json.loads(args.book_feats.read_text(encoding="utf-8"))

    updated_rows = 0
    by_book: dict[str, int] = {}

    for book, rows in data.items():
        allowed_pages = SUMMARY_BOOK_PAGES.get(book)
        allow_any_page = args.all_books or (book in SUMMARY_ANY_PAGE_BOOKS)
        if not allowed_pages and not allow_any_page:
            continue
        for row in rows:
            detail = (row.get("detail_text") or "").strip()
            benefit = (row.get("benefit_summary") or "").strip()
            if detail or not benefit:
                continue
            if (not allow_any_page) and (not _has_any_local(row, allowed_pages)):
                continue

            row["detail_text"] = benefit
            sp = row.get("source_pages") or []
            sp.append(
                {
                    "local": (sorted(allowed_pages)[0] if allowed_pages else ""),
                    "toc_path": "summary_page_detail_fallback",
                    "table_index": -95,
                    "row_index": -1,
                }
            )
            row["source_pages"] = sp
            updated_rows += 1
            by_book[book] = by_book.get(book, 0) + 1

    out_path = args.book_feats if args.inplace else args.output
    out_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    print("Done.")
    print(f"Updated rows: {updated_rows}")
    for b, c in sorted(by_book.items(), key=lambda x: (-x[1], x[0])):
        print(f"{b}: +{c}")
    print(f"Output: {out_path}")


if __name__ == "__main__":
    main()