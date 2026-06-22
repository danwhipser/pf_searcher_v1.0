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

from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[2]

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.extract.extract_feats_and_verify import load_embedded_pages, normalize_key, normalize_ws

IN_BOOK_FEATS = ROOT / "result" / "feats" / "feat-book-feats.json"
IN_VIEWER = ROOT / "result" / "Pathfinder-v2.14-SC-viewer-embedded.html"
OUT_BOOK_FEATS = ROOT / "result" / "feats" / "feat-book-feats-rich-pages.json"

PR_LABELS = {"先决条件", "前置条件", "前置"}
BEN_LABELS = {"专长效果", "效果", "收益", "好处"}
STOP_LABELS = {
    "通常情况",
    "通常状况",
    "特殊",
    "特殊说明",
    "特殊情况",
    "战策", 
    "策略",
}


def has_cn(text: str) -> bool:
    return bool(re.search(r"[\u4e00-\u9fff]", text or ""))


def overlap_score(old_text: str, new_text: str) -> int:
    old = normalize_ws(old_text or "")
    new = normalize_ws(new_text or "")
    if not old or not new:
        return 0
    chunks = re.findall(r"[\u4e00-\u9fff]{2,20}", old)
    grams: set[str] = set()
    for ch in chunks:
        if len(ch) < 2:
            continue
        for i in range(len(ch) - 1):
            grams.add(ch[i : i + 2])
    if not grams:
        return 0
    return sum(1 for g in grams if g in new)


def is_separator(line: str) -> bool:
    s = normalize_ws(line or "")
    return bool(s) and all(ch in "_-—" for ch in s)


def is_heading_like(line: str) -> bool:
    s = normalize_ws(line or "")
    if not s or len(s) > 120:
        return False
    if "先决条件" in s or "专长效果" in s:
        return False
    # Typical: 中文（English）（来源） or 中文 English（来源）
    if "（" in s and ("）" in s or re.search(r"[A-Za-z]", s)):
        return True
    if re.search(r"[A-Za-z]{3,}", s) and has_cn(s):
        return True
    return False


def extract_en_from_heading(heading: str) -> str:
    h = normalize_ws(heading)
    if not h:
        return ""

    # Prefer English text inside first non-source parenthesized group.
    groups = re.findall(r"[（(]\s*([^（）()]+?)\s*[）)]", h)
    for g in groups:
        if not re.search(r"[A-Za-z]", g):
            continue
        gg = normalize_ws(g)
        # Skip short source tags like UC/APG/ACG/CRB.
        if re.fullmatch(r"[A-Za-z]{2,6}", gg):
            continue
        gg = re.sub(r"\s+", " ", gg).strip()
        if gg:
            return gg

    # Fallback to longest English phrase in heading.
    parts = re.findall(r"[A-Za-z][A-Za-z'`-]*(?:\s+[A-Za-z][A-Za-z'`-]*)*", h)
    parts = [normalize_ws(x) for x in parts if normalize_ws(x)]
    parts.sort(key=len, reverse=True)
    for p in parts:
        if re.fullmatch(r"[A-Za-z]{2,6}", p):
            continue
        return p
    return ""


def parse_rich_feat_blocks(lines: list[str]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    n = len(lines)
    i = 0
    while i < n:
        line = normalize_ws(lines[i])
        if not is_heading_like(line):
            i += 1
            continue

        heading = line
        heading_end = i
        # Merge split heading lines like:
        # 化敌为盾（Body
        # Shield）（UC）
        if i + 1 < n:
            nxt = normalize_ws(lines[i + 1])
            if ("（" in heading and "）" not in heading) or (re.search(r"[A-Za-z]$", heading) and ("）" in nxt or re.search(r"[A-Za-z]", nxt))):
                if len(nxt) <= 50 and (re.search(r"[A-Za-z]", nxt) or "）" in nxt):
                    heading = normalize_ws(f"{heading}{nxt}")
                    heading_end = i + 1

        # Locate prereq / benefit markers close by.
        pr_idx = -1
        be_idx = -1
        for j in range(heading_end + 1, min(n, heading_end + 18)):
            s = normalize_ws(lines[j])
            if s in PR_LABELS:
                pr_idx = j
                break
        if pr_idx < 0:
            i += 1
            continue
        for j in range(pr_idx + 1, min(n, pr_idx + 20)):
            s = normalize_ws(lines[j])
            if s in BEN_LABELS:
                be_idx = j
                break
        if be_idx < 0:
            i += 1
            continue

        # Flavor/detail between heading and prereq label, skipping separators.
        detail_lines = [normalize_ws(x) for x in lines[heading_end + 1 : pr_idx] if normalize_ws(x) and not is_separator(x)]
        detail = normalize_ws(" ".join(detail_lines))

        def collect_field(start: int, end_limit: int) -> str:
            vals: list[str] = []
            k = start
            while k < min(n, end_limit):
                s = normalize_ws(lines[k])
                if not s:
                    k += 1
                    continue
                if is_heading_like(s) and k > start:
                    break
                if s in PR_LABELS or s in BEN_LABELS or s in STOP_LABELS:
                    break
                if is_separator(s):
                    k += 1
                    continue
                vals.append(s)
                k += 1
            text = normalize_ws(" ".join(vals))
            text = re.sub(r"^[：:\s]+", "", text)
            return text

        prereq = collect_field(pr_idx + 1, be_idx)

        # benefit may span until stop label or next heading
        benefit_vals: list[str] = []
        k = be_idx + 1
        while k < n:
            s = normalize_ws(lines[k])
            if not s:
                k += 1
                continue
            if s in STOP_LABELS:
                break
            if s in PR_LABELS or s in BEN_LABELS:
                break
            if is_heading_like(s):
                break
            if is_separator(s):
                k += 1
                continue
            benefit_vals.append(s)
            k += 1
        benefit = normalize_ws(" ".join(benefit_vals))
        benefit = re.sub(r"^[：:\s]+", "", benefit)

        name_en = extract_en_from_heading(heading)
        key = normalize_key(name_en)
        if not key:
            i += 1
            continue
        if not has_cn(prereq + benefit + detail):
            i += 1
            continue

        out.append(
            {
                "key": key,
                "heading": heading,
                "name_en": name_en,
                "detail": detail,
                "prereq": prereq,
                "benefit": benefit,
            }
        )
        i = max(i + 1, k)
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill feat detail/prereq/benefit from rich CHM feat pages.")
    parser.add_argument("--book-feats", type=Path, default=IN_BOOK_FEATS)
    parser.add_argument("--viewer", type=Path, default=IN_VIEWER)
    parser.add_argument("--output", type=Path, default=OUT_BOOK_FEATS)
    parser.add_argument("--inplace", action="store_true")
    parser.add_argument(
        "--keys",
        type=str,
        default="",
        help="Comma-separated match_key filters. Empty means all.",
    )
    args = parser.parse_args()

    key_filter: set[str] = set()
    if args.keys.strip():
        key_filter = {normalize_ws(x) for x in args.keys.split(",") if normalize_ws(x)}

    data: dict[str, list[dict[str, Any]]] = json.loads(args.book_feats.read_text(encoding="utf-8"))
    pages = load_embedded_pages(args.viewer)

    block_map: dict[str, dict[str, str]] = {}
    parsed_pages = 0
    for pk, html in pages.items():
        soup = BeautifulSoup(html, "html.parser")
        lines = [normalize_ws(x) for x in soup.get_text("\n").splitlines() if normalize_ws(x)]
        if len(lines) < 80:
            continue
        blocks = parse_rich_feat_blocks(lines)
        if not blocks:
            continue
        parsed_pages += 1
        for b in blocks:
            k = b["key"]
            if key_filter and k not in key_filter:
                continue
            old = block_map.get(k)
            cand = dict(b)
            cand["page"] = pk
            cand_score = len(cand.get("benefit", "")) * 3 + len(cand.get("prereq", "")) * 2 + len(cand.get("detail", ""))
            if old is None:
                block_map[k] = cand
                continue
            old_score = len(old.get("benefit", "")) * 3 + len(old.get("prereq", "")) * 2 + len(old.get("detail", ""))
            if cand_score > old_score:
                block_map[k] = cand

    updated_rows = 0
    updated_keys: set[str] = set()
    for rows in data.values():
        for row in rows:
            k = normalize_ws(row.get("match_key", ""))
            if not k:
                continue
            if key_filter and k not in key_filter:
                continue
            blk = block_map.get(k)
            if not blk:
                continue

            changed = False
            prereq = normalize_ws(blk.get("prereq", ""))
            benefit = normalize_ws(blk.get("benefit", ""))
            detail = normalize_ws(blk.get("detail", ""))
            old_detail = normalize_ws(row.get("detail_text", ""))

            if prereq and (len(prereq) >= max(10, len(normalize_ws(row.get("prerequisites", ""))) + 6)):
                row["prerequisites"] = prereq
                changed = True
            old_benefit = normalize_ws(row.get("benefit_summary", ""))
            ov = overlap_score(old_benefit, benefit)
            benefit_ok = (not old_benefit) or (ov >= 2) or (len(benefit) <= len(old_benefit) + 8)
            if benefit and benefit_ok and (len(benefit) >= max(12, len(old_benefit) + 6)):
                row["benefit_summary"] = benefit
                changed = True
            # Long benefit text should override short summary-type detail.
            if benefit and benefit_ok and has_cn(benefit) and len(benefit) >= max(30, len(old_detail) + 10):
                row["detail_text"] = benefit
                changed = True
            elif (not old_detail) and detail and len(detail) >= 12:
                row["detail_text"] = detail
                changed = True

            if changed:
                sp = row.get("source_pages") or []
                sp.append(
                    {
                        "local": blk.get("page", ""),
                        "toc_path": "chm_rich_feat_pages",
                        "table_index": -99,
                        "row_index": -1,
                    }
                )
                row["source_pages"] = sp
                updated_rows += 1
                updated_keys.add(k)

    out_path = args.book_feats if args.inplace else args.output
    out_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    print("Done.")
    print(f"Rich parsed pages: {parsed_pages}")
    print(f"Parsed key blocks: {len(block_map)}")
    print(f"Updated keys: {len(updated_keys)}")
    print(f"Updated rows: {updated_rows}")
    print(f"Output: {out_path}")


if __name__ == "__main__":
    main()