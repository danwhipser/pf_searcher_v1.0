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
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.extract.extract_feats_and_verify import extract_feats_from_page, normalize_key

IN_BOOK_FEATS = ROOT / "result" / "feats" / "feat-book-feats.json"
IN_COVERAGE = ROOT / "result" / "feats" / "feat-book-aon-coverage.json"
IN_VIEWER = ROOT / "result" / "Pathfinder-v2.14-SC-viewer-embedded.html"
OUT_BOOK_FEATS = ROOT / "result" / "feats" / "feat-book-feats-augmented-v2.json"

# High-confidence per-book extra pages verified to contain missing feats.
BOOK_EXTRA_PAGES: dict[str, list[str]] = {
    "护甲大师": ["page_1089.html", "page_555.html"],
    "魔法战术工具箱": ["page_343.html"],
}


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_pages(path: Path) -> dict[str, str]:
    text = path.read_text(encoding="utf-8", errors="ignore")
    m = re.search(r'<script id="pages-data" type="application/json">(.*?)</script>', text, re.S)
    if not m:
        raise ValueError("pages-data JSON block not found")
    return json.loads(m.group(1).replace("<\\/", "</"))


def strip_source_suffix(name: str) -> str:
    return re.sub(r"\s*\([A-Z]{2,6}\)\s*$", "", (name or "").strip())


def build_page_feat_index(pages: dict[str, str], locals_list: list[str]) -> dict[str, tuple[str, str]]:
    """match_key -> (display_name_en, local_page)"""
    out: dict[str, tuple[str, str]] = {}
    for local in locals_list:
        html = pages.get(local)
        if not html:
            continue
        try:
            rows = extract_feats_from_page(html, local, local)
        except Exception:
            continue
        for r in rows:
            name = (r.name_en or r.name_raw or "").strip()
            k = normalize_key(name)
            if not k:
                continue
            out.setdefault(k, (name, local))
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Augment per-book missing feats from high-confidence extra pages.")
    parser.add_argument("--book-feats", type=Path, default=IN_BOOK_FEATS)
    parser.add_argument("--coverage", type=Path, default=IN_COVERAGE)
    parser.add_argument("--viewer", type=Path, default=IN_VIEWER)
    parser.add_argument("--output", type=Path, default=OUT_BOOK_FEATS)
    parser.add_argument("--inplace", action="store_true", help="Overwrite --book-feats directly.")
    args = parser.parse_args()

    book_feats: dict[str, list[dict[str, Any]]] = load_json(args.book_feats)
    coverage = load_json(args.coverage)
    pages = load_pages(args.viewer)

    cov_by_book = {x.get("book", ""): x for x in coverage.get("books", [])}

    total_added = 0
    per_book: dict[str, int] = {}

    for book, extra_pages in BOOK_EXTRA_PAGES.items():
        cov = cov_by_book.get(book)
        if not cov or cov.get("status") != "ok":
            continue

        missing_names = [x.get("name_en", "").strip() for x in cov.get("missing_in_chm", []) if x.get("name_en")]
        if not missing_names:
            continue

        rows = book_feats.get(book, [])
        existing_keys = {r.get("match_key", "") for r in rows if r.get("match_key")}
        index = build_page_feat_index(pages, extra_pages)

        add_count = 0
        for miss in missing_names:
            miss_bare = strip_source_suffix(miss)
            k = normalize_key(miss_bare)
            if not k or k in existing_keys:
                continue
            hit = index.get(k)
            if not hit:
                continue
            _, local = hit
            rows.append(
                {
                    "match_key": k,
                    "name_en": miss_bare,
                    "name_cn": "",
                    "name_raw": miss_bare,
                    "prerequisites": "",
                    "benefit_summary": "",
                    "detail_text": "",
                    "source_pages": [
                        {
                            "local": local,
                            "toc_path": "book_extra_page_backfill",
                            "table_index": -97,
                            "row_index": -1,
                        }
                    ],
                }
            )
            existing_keys.add(k)
            add_count += 1

        if add_count:
            book_feats[book] = rows
            per_book[book] = add_count
            total_added += add_count

    out_path = args.book_feats if args.inplace else args.output
    out_path.write_text(json.dumps(book_feats, ensure_ascii=False, indent=2), encoding="utf-8")

    print("Done.")
    print(f"Added feats: {total_added}")
    for book, cnt in sorted(per_book.items(), key=lambda x: (-x[1], x[0])):
        print(f"{book}: +{cnt}")
    print(f"Output: {out_path}")


if __name__ == "__main__":
    main()