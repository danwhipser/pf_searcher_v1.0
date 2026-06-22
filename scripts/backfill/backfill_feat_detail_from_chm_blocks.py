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

IN_BOOK_FEATS = ROOT / "result" / "feats" / "feat-book-feats.json"
IN_VIEWER = ROOT / "result" / "Pathfinder-v2.14-SC-viewer-embedded.html"
OUT_BOOK_FEATS = ROOT / "result" / "feats" / "feat-book-feats-chm-blocks.json"


PREREQ_PAT = re.compile(r"^(先决条件|前置条件|Prerequisites?)[:：]?\s*$", re.I)
BENEFIT_PAT = re.compile(r"^(专长效果|效果|Benefit)[:：]?\s*$", re.I)
END_PAT = re.compile(r"^(【|团队专长|战斗专长|一般专长|流派专长)")


def has_cn(text: str) -> bool:
    return bool(re.search(r"[\u4e00-\u9fff]", text or ""))


def is_probable_feat_en_line(line: str) -> bool:
    s = normalize_ws(line)
    if not s or len(s) > 90:
        return False
    if has_cn(s):
        return False
    # Mostly English feat-name-like token line.
    if re.fullmatch(r"[A-Za-z][A-Za-z0-9'`/&,+\-\s]+", s):
        words = [w for w in s.split() if w]
        return 1 <= len(words) <= 8
    return False


def parse_block_from_lines(lines: list[str], idx: int) -> tuple[str, str, str, bool]:
    # detail: between name line and prereq label
    detail_parts: list[str] = []
    prereq_parts: list[str] = []
    benefit_parts: list[str] = []

    mode = "detail"
    i = idx + 1
    saw_label = False
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

        # End heuristics for block.
        if mode == "benefit":
            if END_PAT.match(line):
                break
            if is_probable_feat_en_line(line):
                break
            # Chinese feat name often appears as a short line followed by English line.
            if has_cn(line) and len(line) <= 12 and i + 1 < len(lines) and is_probable_feat_en_line(lines[i + 1]):
                break

        if mode == "detail":
            detail_parts.append(line)
        elif mode == "prereq":
            # stop prereq when a new section accidentally starts
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


def find_line_index(lines: list[str], name_en: str, name_cn: str, name_raw: str) -> int:
    en = normalize_ws(name_en).lower()
    cn = normalize_ws(name_cn)
    raw = normalize_ws(name_raw)

    # Prefer English exact/contains matches.
    if en:
        for i, line in enumerate(lines):
            s = normalize_ws(line)
            if not s:
                continue
            low = s.lower()
            if low == en or en in low:
                return i

    # Fallback to Chinese name.
    if cn:
        for i, line in enumerate(lines):
            if cn in normalize_ws(line):
                return i

    # Last fallback: raw name.
    if raw:
        for i, line in enumerate(lines):
            if raw in normalize_ws(line):
                return i

    return -1


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill feat detail/prereq/benefit from CHM page text blocks.")
    parser.add_argument("--book-feats", type=Path, default=IN_BOOK_FEATS)
    parser.add_argument("--viewer", type=Path, default=IN_VIEWER)
    parser.add_argument("--output", type=Path, default=OUT_BOOK_FEATS)
    parser.add_argument("--inplace", action="store_true")
    parser.add_argument("--only-missing-detail", action="store_true", default=True)
    args = parser.parse_args()

    data: dict[str, list[dict[str, Any]]] = json.loads(args.book_feats.read_text(encoding="utf-8"))
    pages = load_embedded_pages(args.viewer)
    page_idx = {unquote(k).lower(): k for k in pages.keys()}
    line_cache: dict[str, list[str]] = {}

    updated_rows = 0
    touched_keys: set[str] = set()

    for book, rows in data.items():
        for row in rows:
            detail_now = normalize_ws(row.get("detail_text", ""))
            if args.only_missing_detail and detail_now:
                continue

            source_pages = row.get("source_pages") or []
            if not source_pages:
                continue

            best_local = None
            for sp in source_pages:
                local = normalize_ws(sp.get("local", ""))
                if not local:
                    continue
                key = page_idx.get(unquote(local).lower())
                if key and key in pages:
                    best_local = key
                    break
            if not best_local:
                continue

            if best_local not in line_cache:
                soup = BeautifulSoup(pages[best_local], "html.parser")
                text = soup.get_text("\n", strip=True)
                lines = [normalize_ws(x) for x in text.splitlines() if normalize_ws(x)]
                line_cache[best_local] = lines
            lines = line_cache[best_local]

            idx = find_line_index(
                lines,
                row.get("name_en", ""),
                row.get("name_cn", ""),
                row.get("name_raw", ""),
            )
            if idx < 0:
                continue

            detail, prereq, benefit, saw_label = parse_block_from_lines(lines, idx)

            # Strict guard: only trust blocks that explicitly contain feat field labels.
            if not saw_label:
                continue

            # Keep CHM quality guard: we only accept detail if it contains Chinese.
            changed = False
            if not detail_now and detail and has_cn(detail) and len(detail) <= 220:
                row["detail_text"] = detail
                changed = True
            if not normalize_ws(row.get("prerequisites", "")) and prereq and has_cn(prereq):
                row["prerequisites"] = prereq
                changed = True
            if not normalize_ws(row.get("benefit_summary", "")) and benefit and has_cn(benefit):
                row["benefit_summary"] = benefit
                changed = True

            if changed:
                updated_rows += 1
                mk = normalize_ws(row.get("match_key", ""))
                if mk:
                    touched_keys.add(mk)

    out_path = args.book_feats if args.inplace else args.output
    out_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    print("Done.")
    print(f"Updated rows: {updated_rows}")
    print(f"Touched feat keys: {len(touched_keys)}")
    print(f"Output: {out_path}")


if __name__ == "__main__":
    main()