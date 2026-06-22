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

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.backfill.backfill_feat_detail_unified import (
    Candidate,
    clean_text,
    collect_candidates,
    is_short_detail,
    mythic_flag,
    row_cand_score,
)

IN_BOOK_FEATS = ROOT / "result" / "feats" / "feat-book-feats.json"
IN_VIEWER = ROOT / "result" / "Pathfinder-v2.14-SC-viewer-embedded.html"
OUT_REPORT = ROOT / "result" / "feats" / "per_book_detail_backfill_report.json"


def book_code(book_name: str) -> str:
    m = re.match(r"^([A-Za-z0-9]+)\s+", (book_name or "").strip())
    return m.group(1).upper() if m else ""


def main() -> None:
    parser = argparse.ArgumentParser(description="Per-book batch extraction/backfill for feat details.")
    parser.add_argument("--book-feats", type=Path, default=IN_BOOK_FEATS)
    parser.add_argument("--viewer", type=Path, default=IN_VIEWER)
    parser.add_argument("--inplace", action="store_true")
    parser.add_argument("--min-score", type=int, default=115)
    parser.add_argument("--min-growth", type=int, default=8)
    parser.add_argument("--report", type=Path, default=OUT_REPORT)
    args = parser.parse_args()

    data: dict[str, list[dict[str, Any]]] = json.loads(args.book_feats.read_text(encoding="utf-8"))
    cand_map = collect_candidates(args.viewer)

    total_updated = 0
    by_book_updates: dict[str, int] = defaultdict(int)
    by_book_remaining_short: dict[str, int] = {}
    by_page: dict[str, int] = defaultdict(int)
    book_samples: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for book, rows in sorted(data.items(), key=lambda x: x[0]):
        if not isinstance(rows, list):
            continue
        bcode = book_code(book)
        for row in rows:
            key = clean_text(row.get("match_key", ""))
            if not key:
                continue
            old_detail = clean_text(row.get("detail_text", ""))
            if not is_short_detail(old_detail):
                continue

            cands: list[Candidate] = cand_map.get(key, [])
            if not cands:
                continue

            row_mythic = mythic_flag(row.get("name_cn", ""), row.get("name_en", ""))
            best = None
            best_score = -10**9
            for c in cands:
                s = row_cand_score(row, c)
                # book-level coarse prior: prefer pages whose detail text mentions same book code token.
                # (weak prior; keeps generic method while nudging per-book consistency)
                if bcode and (bcode in (c.detail or "").upper()):
                    s += 5
                # avoid non-mythic rows taking mythic-like candidates
                cand_mythic = mythic_flag(c.name_cn, c.name_en)
                if (not row_mythic) and cand_mythic:
                    s -= 120
                if s > best_score:
                    best_score = s
                    best = c

            if not best:
                continue
            new_detail = clean_text(best.detail)
            if len(new_detail) < max(35, len(old_detail) + args.min_growth):
                continue
            if best_score < args.min_score:
                continue

            changed = False
            row["detail_text"] = new_detail
            changed = True
            if best.prereq and len(best.prereq) > len(clean_text(row.get("prerequisites", ""))):
                row["prerequisites"] = best.prereq
            if (not clean_text(row.get("flavor_text", ""))) and best.flavor:
                row["flavor_text"] = best.flavor

            if changed:
                sp = row.get("source_pages") or []
                marker = {
                    "local": best.page,
                    "toc_path": "per_book_unified_backfill",
                    "table_index": -104,
                    "row_index": -1,
                }
                if marker not in sp:
                    sp.append(marker)
                row["source_pages"] = sp
                total_updated += 1
                by_book_updates[book] += 1
                by_page[best.page] += 1
                if len(book_samples[book]) < 12:
                    book_samples[book].append(
                        {
                            "key": key,
                            "name_cn": row.get("name_cn", ""),
                            "old_len": len(old_detail),
                            "new_len": len(new_detail),
                            "score": best_score,
                            "page": best.page,
                            "parser": best.parser,
                        }
                    )

        rem = sum(1 for r in rows if is_short_detail(r.get("detail_text", "")))
        by_book_remaining_short[book] = rem

    out_path = args.book_feats if args.inplace else args.book_feats.with_name("feat-book-feats-per-book.json")
    out_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    report_books = []
    for book in sorted(k for k in data.keys() if isinstance(data[k], list)):
        total = len(data[book])
        report_books.append(
            {
                "book": book,
                "total": total,
                "updated": by_book_updates.get(book, 0),
                "remaining_short": by_book_remaining_short.get(book, 0),
                "remaining_short_ratio": (by_book_remaining_short.get(book, 0) / total) if total else 0,
                "samples": book_samples.get(book, []),
            }
        )

    report = {
        "updated_rows": total_updated,
        "candidate_keys": len(cand_map),
        "min_score": args.min_score,
        "min_growth": args.min_growth,
        "books": report_books,
        "top_pages": dict(sorted(by_page.items(), key=lambda x: (-x[1], x[0]))),
        "output": str(out_path),
    }
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print("Done.")
    print(f"Updated rows: {total_updated}")
    for b, n in sorted(by_book_updates.items(), key=lambda x: (-x[1], x[0])):
        print(f"{b}: +{n}")
    print(f"Output: {out_path}")
    print(f"Report: {args.report}")


if __name__ == "__main__":
    main()
