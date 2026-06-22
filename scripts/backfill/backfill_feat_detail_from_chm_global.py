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
from urllib.parse import unquote

from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.extract.extract_feats_and_verify import load_embedded_pages, normalize_ws
from scripts.backfill.backfill_feat_detail_from_chm_blocks import (
    PREREQ_PAT,
    BENEFIT_PAT,
    END_PAT,
    has_cn,
    is_probable_feat_en_line,
)

IN_BOOK_FEATS = ROOT / "result" / "feats" / "feat-book-feats.json"
IN_VIEWER = ROOT / "result" / "Pathfinder-v2.14-SC-viewer-embedded.html"
OUT_BOOK_FEATS = ROOT / "result" / "feats" / "feat-book-feats-chm-global.json"


def parse_block(lines: list[str], idx: int) -> tuple[str, str, str, bool]:
    detail_parts: list[str] = []
    prereq_parts: list[str] = []
    benefit_parts: list[str] = []
    mode = "detail"
    saw_label = False

    i = idx + 1
    while i < len(lines):
        line = normalize_ws(lines[i])
        if not line:
            i += 1
            continue

        if PREREQ_PAT.match(line):
            mode = "prereq"
            saw_label = True
            i += 1
            continue
        if BENEFIT_PAT.match(line):
            mode = "benefit"
            saw_label = True
            i += 1
            continue

        if mode == "benefit":
            if END_PAT.match(line):
                break
            if is_probable_feat_en_line(line):
                break
            if has_cn(line) and len(line) <= 12 and i + 1 < len(lines) and is_probable_feat_en_line(lines[i + 1]):
                break

        if mode == "detail":
            detail_parts.append(line)
        elif mode == "prereq":
            if END_PAT.match(line):
                break
            prereq_parts.append(line)
        else:
            benefit_parts.append(line)
        i += 1

    detail = normalize_ws(" ".join(detail_parts))
    prereq = normalize_ws(" ".join(prereq_parts))
    benefit = normalize_ws(" ".join(benefit_parts))
    return detail, prereq, benefit, saw_label


def find_indices(lines: list[str], name_en: str, name_cn: str, name_raw: str) -> list[int]:
    out: list[int] = []
    en = normalize_ws(name_en).lower()
    cn = normalize_ws(name_cn)
    raw = normalize_ws(name_raw)

    for i, line in enumerate(lines):
        s = normalize_ws(line)
        if not s:
            continue
        # Skip obvious table/header noise lines.
        if len(s) > 100:
            continue
        if "专长名称" in s or "先决条件" in s or "专长效果" in s:
            continue
        if "|" in s:
            continue
        low = s.lower()
        if en and (low == en or en in low):
            # English feat heading-like line should not carry too many tokens.
            words = [w for w in re.findall(r"[A-Za-z][A-Za-z'`-]*", s)]
            if len(words) > 10:
                continue
            out.append(i)
            continue
        if cn and cn in s:
            if len(s) > 40:
                continue
            out.append(i)
            continue
        if raw and raw in s:
            out.append(i)
            continue
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Global CHM page search for feat detail/prereq/benefit blocks.")
    parser.add_argument("--book-feats", type=Path, default=IN_BOOK_FEATS)
    parser.add_argument("--viewer", type=Path, default=IN_VIEWER)
    parser.add_argument("--output", type=Path, default=OUT_BOOK_FEATS)
    parser.add_argument("--inplace", action="store_true")
    args = parser.parse_args()

    data: dict[str, list[dict[str, Any]]] = json.loads(args.book_feats.read_text(encoding="utf-8"))
    pages = load_embedded_pages(args.viewer)
    page_keys = list(pages.keys())
    page_lows = {k: unquote(k).lower() for k in page_keys}
    line_cache: dict[str, list[str]] = {}

    # key -> rows
    key_rows: dict[str, list[dict[str, Any]]] = {}
    for rows in data.values():
        for row in rows:
            key = normalize_ws(row.get("match_key", ""))
            if not key:
                continue
            key_rows.setdefault(key, []).append(row)

    total_updated_rows = 0
    updated_keys = 0

    for key, rows in key_rows.items():
        # Skip keys already having CHM detail.
        if any(has_cn(normalize_ws(r.get("detail_text", ""))) for r in rows):
            continue

        rep = rows[0]
        name_en = normalize_ws(rep.get("name_en", ""))
        name_cn = normalize_ws(rep.get("name_cn", ""))
        name_raw = normalize_ws(rep.get("name_raw", ""))
        if not name_en and not name_cn and not name_raw:
            continue

        source_locals = {
            normalize_ws(sp.get("local", "")).lower()
            for r in rows
            for sp in (r.get("source_pages") or [])
            if normalize_ws(sp.get("local", ""))
        }

        best_payload: tuple[str, str, str, str] | None = None  # detail, prereq, benefit, page_key
        best_score = -10**9

        for pk in page_keys:
            html = pages[pk]
            # Fast prefilter.
            if name_en and name_en not in html and (not name_cn or name_cn not in html):
                if name_raw and name_raw not in html:
                    continue
            elif not name_en and name_cn and name_cn not in html and (not name_raw or name_raw not in html):
                continue

            if pk not in line_cache:
                soup = BeautifulSoup(html, "html.parser")
                text = soup.get_text("\n", strip=True)
                line_cache[pk] = [normalize_ws(x) for x in text.splitlines() if normalize_ws(x)]
            lines = line_cache[pk]

            idxs = find_indices(lines, name_en, name_cn, name_raw)
            if not idxs:
                continue

            for idx in idxs[:3]:
                detail, prereq, benefit, saw_label = parse_block(lines, idx)
                if not saw_label:
                    continue

                cn_count = int(has_cn(detail)) + int(has_cn(prereq)) + int(has_cn(benefit))
                if cn_count == 0:
                    continue
                score = cn_count * 10
                if detail and len(detail) <= 220:
                    score += 2
                if page_lows[pk] in source_locals:
                    score += 3
                if detail and len(detail) > 300:
                    score -= 5
                if "专长名称" in " ".join(lines[max(0, idx - 3) : min(len(lines), idx + 3)]):
                    score -= 3

                if score > best_score:
                    best_score = score
                    best_payload = (detail, prereq, benefit, pk)

        if not best_payload:
            continue

        detail, prereq, benefit, pk = best_payload
        changed_any = False
        for row in rows:
            changed = False
            if not normalize_ws(row.get("detail_text", "")) and detail and has_cn(detail) and len(detail) <= 220:
                row["detail_text"] = detail
                changed = True
            if not normalize_ws(row.get("prerequisites", "")) and prereq and has_cn(prereq):
                row["prerequisites"] = prereq
                changed = True
            if not normalize_ws(row.get("benefit_summary", "")) and benefit and has_cn(benefit):
                row["benefit_summary"] = benefit
                changed = True
            if changed:
                # Keep source trace for debugging.
                srcs = row.get("source_pages") or []
                srcs.append(
                    {
                        "local": pk,
                        "toc_path": "chm_detail_global_locator",
                        "table_index": -95,
                        "row_index": -1,
                    }
                )
                row["source_pages"] = srcs
                total_updated_rows += 1
                changed_any = True
        if changed_any:
            updated_keys += 1

    out_path = args.book_feats if args.inplace else args.output
    out_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    print("Done.")
    print(f"Updated keys: {updated_keys}")
    print(f"Updated rows: {total_updated_rows}")
    print(f"Output: {out_path}")


if __name__ == "__main__":
    main()