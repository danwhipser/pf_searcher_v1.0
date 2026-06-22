#!/usr/bin/env python3
from __future__ import annotations

# Allow direct execution from nested scripts/ folders.
import sys
from pathlib import Path
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import json
from collections import Counter, defaultdict
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.backfill.backfill_feat_detail_unified import (
    IN_VIEWER,
    SUMMARY_PAGES,
    clean_text,
    collect_candidates,
    is_short_detail,
    row_cand_score,
)

IN_BOOK_FEATS = ROOT / "result" / "feats" / "feat-book-feats.json"
OUT_REPORT = ROOT / "result" / "feats" / "short_detail_cause_analysis.json"


def local_pages(row: dict[str, Any]) -> list[str]:
    pages: list[str] = []
    for src in row.get("source_pages") or []:
        if isinstance(src, dict) and src.get("local"):
            pages.append(str(src["local"]))
        elif isinstance(src, str):
            pages.append(src)
    return pages


def classify(row: dict[str, Any], candidates: list[Any]) -> tuple[str, int | None, Any | None]:
    non_summary = [c for c in candidates if c.page.lower() not in SUMMARY_PAGES]
    pages = [p.lower() for p in local_pages(row)]
    summary_only_source = bool(pages) and all(p in SUMMARY_PAGES for p in pages)

    if not non_summary:
        if summary_only_source:
            return "original_summary_only", None, None
        return "no_candidate_in_chm", None, None

    best = max(non_summary, key=lambda c: row_cand_score(row, c))
    best_score = row_cand_score(row, best)
    if best_score >= 180:
        return "strategy_issue_high", best_score, best
    if best_score >= 95:
        return "strategy_issue_mid", best_score, best
    return "candidate_low_confidence", best_score, best


def main() -> None:
    data: dict[str, list[dict[str, Any]]] = json.loads(IN_BOOK_FEATS.read_text(encoding="utf-8"))
    cand_map = collect_candidates(IN_VIEWER)

    books: list[dict[str, Any]] = []
    global_classes: Counter[str] = Counter()
    global_short = 0
    global_total = 0

    for book, rows in data.items():
        if not isinstance(rows, list):
            continue
        class_counts: Counter[str] = Counter()
        examples: dict[str, list[dict[str, Any]]] = defaultdict(list)
        short_total = 0
        for row in rows:
            global_total += 1
            if not is_short_detail(row.get("detail_text", "")):
                continue
            short_total += 1
            global_short += 1
            key = clean_text(row.get("match_key", ""))
            cause, score, best = classify(row, cand_map.get(key, []))
            class_counts[cause] += 1
            global_classes[cause] += 1
            if len(examples[cause]) < 12:
                examples[cause].append(
                    {
                        "key": key,
                        "name_cn": row.get("name_cn", ""),
                        "name_en": row.get("name_en", ""),
                        "detail": row.get("detail_text", ""),
                        "source_pages": local_pages(row),
                        "best_score": score,
                        "best_page": best.page if best else "",
                        "best_parser": best.parser if best else "",
                        "best_detail_preview": (best.detail[:240] if best else ""),
                    }
                )

        if short_total:
            books.append(
                {
                    "book": book,
                    "total_feats": len(rows),
                    "short_total": short_total,
                    "short_ratio": round(short_total / max(len(rows), 1), 4),
                    "classes": dict(class_counts),
                    "examples": dict(examples),
                }
            )

    report = {
        "definition": {
            "short_detail": "detail_text empty or <=20 chars after normalization",
            "original_summary_only": "row only has known summary/list source pages and no non-summary same-key candidate was found",
            "no_candidate_in_chm": "non-summary source row, but current generic candidate extraction found no usable same-key body block",
            "strategy_issue_high": "high-scoring non-summary candidate exists; parser/selection policy should be improved or audited",
            "strategy_issue_mid": "candidate exists but score is borderline; needs page-specific validation or better parser",
            "candidate_low_confidence": "candidate exists but score is low; likely unrelated mention or weak evidence",
        },
        "total_feats": global_total,
        "short_total": global_short,
        "short_ratio": round(global_short / max(global_total, 1), 4),
        "classes": dict(global_classes),
        "books": sorted(books, key=lambda x: (-x["short_ratio"], -x["short_total"], x["book"])),
    }
    OUT_REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"Total short: {global_short}/{global_total} ({report['short_ratio']:.1%})")
    print("Classes:", dict(global_classes))
    for b in report["books"][:20]:
        print(f"{b['book']}: {b['short_total']}/{b['total_feats']} {b['short_ratio']:.1%} {b['classes']}")
    print(f"Report: {OUT_REPORT}")


if __name__ == "__main__":
    main()