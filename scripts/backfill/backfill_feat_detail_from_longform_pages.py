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
from collections import defaultdict
from typing import Any

from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.backfill.backfill_feat_detail_book_specific import _parse_uc_page_651
from scripts.extract.extract_feats_and_verify import load_embedded_pages, normalize_ws

IN_BOOK_FEATS = ROOT / "result" / "feats" / "feat-book-feats.json"
IN_VIEWER = ROOT / "result" / "Pathfinder-v2.14-SC-viewer-embedded.html"
OUT_REPORT = ROOT / "result" / "feats" / "longform_page_pool_backfill_report.json"

# Summary/index pages that mostly contain short roster entries.
SKIP_PAGES = {
    "page_195.html",
    "page_196.html",
    "page_197.html",
    "page_198.html",
    "page_199.html",
    "page_200.html",
    "page_201.html",
    "page_202.html",
    "page_203.html",
}

SKIP_NAME_PATTERNS = (
    "spell ",
    "专长一览",
)


def _is_short_detail(text: str) -> bool:
    d = normalize_ws(text)
    return (not d) or len(d) <= 20


def _clean_detail(text: str) -> str:
    d = normalize_ws(text)
    d = re.sub(r"^[：:\-\s]+", "", d)
    return normalize_ws(d)


def _pick_candidate_pages(pages: dict[str, str]) -> list[tuple[str, dict[str, dict[str, str]], float]]:
    """
    Return list of (page_key, block_map, median_benefit_len) usable as longform pools.
    """
    out: list[tuple[str, dict[str, dict[str, str]], float]] = []
    for pk, html in pages.items():
        pkl = pk.lower()
        if pkl in SKIP_PAGES:
            continue
        if any(x in pkl for x in SKIP_NAME_PATTERNS):
            continue
        lines = [normalize_ws(x) for x in BeautifulSoup(html, "html.parser").get_text("\n").splitlines() if normalize_ws(x)]
        blocks = _parse_uc_page_651(lines)
        if len(blocks) < 10:
            continue
        lens = sorted(len(normalize_ws(v.get("benefit", ""))) for v in blocks.values())
        med = lens[len(lens) // 2] if lens else 0
        # Keep pages with reasonably long body text.
        if med < 70:
            continue
        out.append((pk, blocks, float(med)))
    out.sort(key=lambda x: (x[2], len(x[1])), reverse=True)
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill short feat detail_text from longform CHM pages.")
    parser.add_argument("--book-feats", type=Path, default=IN_BOOK_FEATS)
    parser.add_argument("--viewer", type=Path, default=IN_VIEWER)
    parser.add_argument("--inplace", action="store_true")
    parser.add_argument("--output", type=Path, default=ROOT / "result" / "feats" / "feat-book-feats-longform-pages.json")
    parser.add_argument("--report", type=Path, default=OUT_REPORT)
    args = parser.parse_args()

    data: dict[str, list[dict[str, Any]]] = json.loads(args.book_feats.read_text(encoding="utf-8"))
    pages = load_embedded_pages(args.viewer)

    candidates = _pick_candidate_pages(pages)
    key_pool: dict[str, dict[str, Any]] = {}
    page_hit_counter: dict[str, int] = defaultdict(int)
    for pk, blocks, _med in candidates:
        for k, blk in blocks.items():
            ben = _clean_detail(blk.get("benefit", ""))
            if len(ben) < 40:
                continue
            old = key_pool.get(k)
            if old is None:
                key_pool[k] = {
                    "page": pk,
                    "benefit": ben,
                    "prereq": _clean_detail(blk.get("prereq", "")),
                    "flavor": _clean_detail(blk.get("flavor", "")),
                }
                continue
            if len(ben) > len(old.get("benefit", "")):
                key_pool[k] = {
                    "page": pk,
                    "benefit": ben,
                    "prereq": _clean_detail(blk.get("prereq", "")),
                    "flavor": _clean_detail(blk.get("flavor", "")),
                }

    updated_rows = 0
    by_book: dict[str, int] = defaultdict(int)
    samples: list[dict[str, Any]] = []
    for book, rows in data.items():
        for row in rows:
            key = normalize_ws(row.get("match_key", ""))
            if not key:
                continue
            old_detail = _clean_detail(row.get("detail_text", ""))
            if not _is_short_detail(old_detail):
                continue
            cand = key_pool.get(key)
            if not cand:
                continue
            new_detail = cand.get("benefit", "")
            if len(new_detail) < max(40, len(old_detail) + 10):
                continue

            changed = False
            row["detail_text"] = new_detail
            changed = True

            new_pr = cand.get("prereq", "")
            if new_pr and len(new_pr) > len(_clean_detail(row.get("prerequisites", ""))):
                row["prerequisites"] = new_pr

            if not _clean_detail(row.get("flavor_text", "")) and cand.get("flavor", ""):
                row["flavor_text"] = cand["flavor"]

            if changed:
                sp = row.get("source_pages") or []
                marker = {
                    "local": cand["page"],
                    "toc_path": "longform_page_pool_backfill",
                    "table_index": -99,
                    "row_index": -1,
                }
                if marker not in sp:
                    sp.append(marker)
                row["source_pages"] = sp

                updated_rows += 1
                by_book[book] += 1
                page_hit_counter[cand["page"]] += 1
                if len(samples) < 40:
                    samples.append(
                        {
                            "book": book,
                            "key": key,
                            "name_cn": row.get("name_cn", ""),
                            "old_len": len(old_detail),
                            "new_len": len(new_detail),
                            "page": cand["page"],
                        }
                    )

    out_path = args.book_feats if args.inplace else args.output
    out_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    report = {
        "candidate_pages": [
            {"page": pk, "blocks": len(blocks), "median_benefit_len": med}
            for pk, blocks, med in candidates
        ],
        "pool_keys": len(key_pool),
        "updated_rows": updated_rows,
        "by_book": dict(sorted(by_book.items(), key=lambda x: (-x[1], x[0]))),
        "by_page": dict(sorted(page_hit_counter.items(), key=lambda x: (-x[1], x[0]))),
        "samples": samples,
        "output": str(out_path),
    }
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print("Done.")
    print(f"Candidate pages: {len(candidates)}")
    print(f"Pool keys: {len(key_pool)}")
    print(f"Updated rows: {updated_rows}")
    for b, c in sorted(by_book.items(), key=lambda x: (-x[1], x[0])):
        print(f"{b}: +{c}")
    print(f"Output: {out_path}")
    print(f"Report: {args.report}")


if __name__ == "__main__":
    main()
