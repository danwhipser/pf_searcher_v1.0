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

from scripts.extract.extract_feats_and_verify import extract_feats_from_page
IN_BOOK_FEATS = ROOT / "result" / "feats" / "feat-book-feats.json"
IN_COVERAGE = ROOT / "result" / "feats" / "feat-book-aon-coverage.json"
IN_VIEWER = ROOT / "result" / "Pathfinder-v2.14-SC-viewer-embedded.html"
OUT_BOOK_FEATS = ROOT / "result" / "feats" / "feat-book-feats-augmented.json"


LOW_COVERAGE_SOURCES = {
    "Inner Sea Races",
    "Heroes of the Wild",
    "Halflings of Golarion",
    "Armor Master's Handbook",
}

SOURCE_MARKERS = {
    "Inner Sea Races": ("Inner Sea Races", "ISR"),
    "Heroes of the Wild": ("Heroes of the Wild", "HotW"),
    "Halflings of Golarion": ("Halflings of Golarion", "HoG"),
    "Armor Master's Handbook": ("Armor Master's Handbook", "AMH"),
}

RELAXED_PAGE_WHITELIST = {
    "Inner Sea Races": {"page_1499.html", "page_1358.html", "page_46.html", "战役设定.htm", "page_1202.html"},
    "Heroes of the Wild": {"专长11.htm", "核心书籍.htm", "page_1563.html", "page_1564.html"},
    "Halflings of Golarion": {"page_1367.html"},
    "Armor Master's Handbook": {
        "page_1089.html",
        "page_1184.html",
        "玩家手册4.htm",
        "page_556.html",
        "page_49.html",
        "page_22.html",
        "page_62.html",
    },
}


def normalize_key(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (text or "").lower())


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_pages(path: Path) -> dict[str, str]:
    text = path.read_text(encoding="utf-8", errors="ignore")
    m = re.search(r'<script id="pages-data" type="application/json">(.*?)</script>', text, re.S)
    if not m:
        raise ValueError("pages-data JSON block not found")
    return json.loads(m.group(1).replace("<\\/", "</"))


def has_source_marker(html: str, source_names: list[str]) -> bool:
    for src in source_names:
        markers = SOURCE_MARKERS.get(src, (src,))
        for marker in markers:
            if marker and marker in html:
                return True
    return False


def main() -> None:
    parser = argparse.ArgumentParser(description="Augment low-coverage books with conservative CHM source scan.")
    parser.add_argument("--book-feats", type=Path, default=IN_BOOK_FEATS)
    parser.add_argument("--coverage", type=Path, default=IN_COVERAGE)
    parser.add_argument("--viewer", type=Path, default=IN_VIEWER)
    parser.add_argument("--output", type=Path, default=OUT_BOOK_FEATS)
    parser.add_argument("--inplace", action="store_true", help="Overwrite --book-feats directly.")
    args = parser.parse_args()

    book_feats: dict[str, list[dict[str, Any]]] = load_json(args.book_feats)
    coverage = load_json(args.coverage)
    pages = load_pages(args.viewer)
    parsed_key_cache: dict[str, set[str]] = {}

    total_added = 0
    per_book_added: dict[str, int] = {}

    for item in coverage.get("books", []):
        if item.get("status") != "ok":
            continue
        cov = item.get("coverage")
        if not isinstance(cov, (int, float)) or cov >= 0.6:
            continue

        source_names = [x.get("source_name", "") for x in item.get("sources", []) if x.get("source_name")]
        if not any(s in LOW_COVERAGE_SOURCES for s in source_names):
            continue

        book = item.get("book", "")
        rows = book_feats.get(book, [])
        existing = {r.get("match_key", "") for r in rows if r.get("match_key")}

        add_count = 0
        for miss in item.get("missing_in_chm", []):
            name_en = (miss.get("name_en") or "").strip()
            if not name_en:
                continue
            k = normalize_key(name_en)
            if not k or k in existing:
                continue

            candidates = []
            for local, html in pages.items():
                parsed_keys = parsed_key_cache.get(local)
                if parsed_keys is None:
                    parsed_keys = set()
                    try:
                        parsed_rows = extract_feats_from_page(html, local, local)
                        for r in parsed_rows:
                            kk = normalize_key(r.name_en or r.name_raw)
                            if kk:
                                parsed_keys.add(kk)
                    except Exception:
                        pass
                    parsed_key_cache[local] = parsed_keys

                if (name_en not in html) and (k not in parsed_keys):
                    continue
                score = 0
                if has_source_marker(html, source_names):
                    score += 3
                has_feat_marker = "专长" in html or "Feat" in html or "先决条件" in html or "Benefit" in html
                if has_feat_marker:
                    score += 1
                # Some books reference feat names from split pages lacking explicit source marker text.
                for src in source_names:
                    if local in RELAXED_PAGE_WHITELIST.get(src, set()) and has_feat_marker:
                        score += 2
                        break
                if score >= 2:
                    candidates.append((score, local))

            if not candidates:
                continue

            candidates.sort(reverse=True)
            best_local = candidates[0][1]
            rows.append(
                {
                    "match_key": k,
                    "name_en": name_en,
                    "name_cn": "",
                    "name_raw": name_en,
                    "prerequisites": "",
                    "benefit_summary": "",
                    "detail_text": "",
                    "source_pages": [
                        {
                            "local": best_local,
                            "toc_path": "supplemental_source_scan",
                            "table_index": -99,
                            "row_index": -1,
                        }
                    ],
                }
            )
            existing.add(k)
            add_count += 1

        if add_count:
            per_book_added[book] = add_count
            total_added += add_count
            book_feats[book] = rows

    out_path = args.book_feats if args.inplace else args.output
    out_path.write_text(json.dumps(book_feats, ensure_ascii=False, indent=2), encoding="utf-8")

    print("Done.")
    print(f"Added feats: {total_added}")
    for book, cnt in sorted(per_book_added.items(), key=lambda x: (-x[1], x[0])):
        print(f"{book}: +{cnt}")
    print(f"Output: {out_path}")


if __name__ == "__main__":
    main()