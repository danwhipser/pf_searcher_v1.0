#!/usr/bin/env python3
from __future__ import annotations

# Allow direct execution from nested scripts/ folders.
import sys
from pathlib import Path
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import json
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.backfill.backfill_feat_detail_unified import IN_VIEWER, clean_text, collect_candidates, row_cand_score

IN_BOOK_FEATS = ROOT / "result" / "feats" / "feat-book-feats.json"
OUT_REPORT = ROOT / "result" / "feats" / "validated_feat_field_fixes_report.json"

VALIDATED_KEYS = {
    "magicalepiphany": {
        "book": "\u5185\u6d77\u8bf8\u795e",
        "reason": "current detail is flavor text; CHM page has a separate effect field",
    },
    "druidicdecoder": {
        "book": "\u4fe1\u4ef0\u4e0e\u54f2\u5b66",
        "reason": "current row is polluted by following entries; CHM single-feat page is clean",
    },
    "aquaticspell": {
        "book": "\u8d85\u9b54\u4e13\u957f\u4e00\u89c8",
        "reason": "score is just below generic threshold, but CHM page has an exact metamagic feat block",
    },
}


def source_marker(page: str) -> dict[str, Any]:
    return {
        "local": page,
        "toc_path": "validated_field_fix",
        "table_index": -104,
        "row_index": -1,
    }


def pick_candidate(key: str, row: dict[str, Any], cands: list[Any]) -> Any:
    if key == "druidicdecoder":
        split = [c for c in cands if c.parser == "split_heading"]
        if split:
            return max(split, key=lambda c: row_cand_score(row, c))
    return max(cands, key=lambda c: row_cand_score(row, c))


def main() -> None:
    data: dict[str, list[dict[str, Any]]] = json.loads(IN_BOOK_FEATS.read_text(encoding="utf-8"))
    cand_map = collect_candidates(IN_VIEWER)
    changes: list[dict[str, Any]] = []

    for book, rows in data.items():
        if not isinstance(rows, list):
            continue
        for row in rows:
            key = clean_text(row.get("match_key", ""))
            spec = VALIDATED_KEYS.get(key)
            if not spec or book != spec["book"]:
                continue
            cands = cand_map.get(key, [])
            if not cands:
                continue
            best = pick_candidate(key, row, cands)
            old = {
                "name_cn": row.get("name_cn", ""),
                "prerequisites": row.get("prerequisites", ""),
                "benefit_summary": row.get("benefit_summary", ""),
                "detail_text": row.get("detail_text", ""),
                "flavor_text": row.get("flavor_text", ""),
            }

            old_detail = clean_text(row.get("detail_text", ""))
            new_detail = clean_text(best.detail)
            if not new_detail:
                continue

            if best.name_cn:
                row["name_cn"] = best.name_cn
                row["name_raw"] = f"{best.name_cn} ({row.get('name_en') or best.name_en})"
            if best.prereq:
                row["prerequisites"] = best.prereq
            row["benefit_summary"] = new_detail
            row["detail_text"] = new_detail
            if best.flavor:
                row["flavor_text"] = best.flavor
            elif old_detail and old_detail != new_detail and not clean_text(row.get("flavor_text", "")):
                row["flavor_text"] = old_detail

            sp = row.get("source_pages") or []
            marker = source_marker(best.page)
            if marker not in sp:
                sp.append(marker)
            row["source_pages"] = sp

            changes.append(
                {
                    "book": book,
                    "key": key,
                    "page": best.page,
                    "parser": best.parser,
                    "reason": spec["reason"],
                    "old": old,
                    "new": {
                        "name_cn": row.get("name_cn", ""),
                        "prerequisites": row.get("prerequisites", ""),
                        "benefit_summary": row.get("benefit_summary", ""),
                        "detail_text": row.get("detail_text", ""),
                        "flavor_text": row.get("flavor_text", ""),
                    },
                }
            )

    IN_BOOK_FEATS.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    OUT_REPORT.write_text(json.dumps({"updated_rows": len(changes), "changes": changes}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Updated rows: {len(changes)}")
    for c in changes:
        print(f"{c['book']} {c['key']} <- {c['page']} ({c['parser']})")
    print(f"Report: {OUT_REPORT}")


if __name__ == "__main__":
    main()