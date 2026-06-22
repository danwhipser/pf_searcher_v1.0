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

from scripts.extract.extract_feats_and_verify import (
    canonicalize_en_name,
    load_embedded_pages,
    normalize_key,
    normalize_ws,
    split_feat_name,
)

IN_BOOK_FEATS = ROOT / "result" / "feats" / "feat-book-feats.json"
IN_VIEWER = ROOT / "result" / "Pathfinder-v2.14-SC-viewer-embedded.html"
OUT_BOOK_FEATS = ROOT / "result" / "feats" / "feat-book-feats-chm-structured.json"

PREREQ_LABELS = [
    "先决条件",
    "前置条件",
    "Prerequisite",
    "Prerequisites",
    "鍏堝喅鏉′欢",
    "鍓嶇疆鏉′欢",
]
BENEFIT_LABELS = [
    "专长效果",
    "效果",
    "好处",
    "Benefit",
    "涓撻暱鏁堟灉",
    "鏁堟灉",
]
STOP_PAT = re.compile(
    r"^(【|团队专长|战斗专长|一般专长|流派专长|专长名称|先决条件|专长效果|好处|专长详述|专长简表|"
    r"銆恷鍥㈤槦涓撻暱|鎴樻枟涓撻暱|涓€鑸笓闀縷娴佹淳涓撻暱|涓撻暱鍚嶇О|鍏堝喅鏉′欢|涓撻暱鏁堟灉)"
)
LABEL_SPLIT_PAT = re.compile(r"^\s*[：:]\s*")

# Book/page-specific entry points for future targeted tuning.
BOOK_PAGE_RULES: dict[str, str] = {
    "page_624.html": "mythic_detail_page",
}


def has_cn(text: str) -> bool:
    return bool(re.search(r"[\u4e00-\u9fff]", text or ""))


def is_probable_feat_en_line(line: str) -> bool:
    s = normalize_ws(line)
    if not s or len(s) > 80:
        return False
    if has_cn(s):
        return False
    if re.fullmatch(r"[A-Za-z][A-Za-z0-9'`/&,+\-\s]+", s):
        words = [w for w in s.split() if w]
        return 1 <= len(words) <= 8
    return False


def _match_labeled_line(line: str, labels: list[str]) -> tuple[bool, str]:
    s = normalize_ws(line)
    if not s:
        return False, ""
    sl = s.lower()
    for label in labels:
        ll = label.lower()
        if sl == ll:
            return True, ""
        if sl.startswith(ll):
            rest = normalize_ws(s[len(label) :])
            rest = LABEL_SPLIT_PAT.sub("", rest)
            return True, rest
    return False, ""


def _extract_heading_key(line: str) -> tuple[str, str]:
    s = normalize_ws(line)
    if not s:
        return "", ""

    en, _ = split_feat_name(s)
    if en:
        en = canonicalize_en_name(en)
        return normalize_key(en), en

    if is_probable_feat_en_line(s):
        en = canonicalize_en_name(s)
        return normalize_key(en), en

    return "", ""


def _looks_like_en_fragment(line: str) -> bool:
    s = normalize_ws(line)
    if not s or len(s) > 24:
        return False
    if has_cn(s):
        return False
    # Fragment tokens like "Ability", "Focus", "Quicken", "Spell-Like", etc.
    return bool(re.fullmatch(r"[A-Za-z][A-Za-z'`-]*", s))


def _find_heading(lines: list[str], prereq_idx: int) -> tuple[int, str, str]:
    for j in range(prereq_idx - 1, max(-1, prereq_idx - 14), -1):
        s = normalize_ws(lines[j])
        if not s:
            continue
        is_pr, _ = _match_labeled_line(s, PREREQ_LABELS)
        is_be, _ = _match_labeled_line(s, BENEFIT_LABELS)
        if is_pr or is_be:
            continue
        key, en_name = _extract_heading_key(s)
        # If we got only a single EN token, it is often the tail part of a split title.
        if key and en_name and len(en_name.split()) == 1 and _looks_like_en_fragment(en_name):
            parts = [en_name]
            p = j - 1
            while p >= max(-1, j - 5):
                sp = normalize_ws(lines[p])
                if not _looks_like_en_fragment(sp):
                    break
                parts.append(sp)
                p -= 1
            parts.reverse()
            if len(parts) >= 2:
                merged = normalize_ws(" ".join(parts))
                m_key, m_name = _extract_heading_key(merged)
                if m_key and len(m_key) > len(key):
                    key, en_name = m_key, m_name
        # Merge split EN title fragments like:
        # Ability / Focus
        # Quicken / Spell-Like / Ability
        if (not key) and _looks_like_en_fragment(s):
            parts = [s]
            p = j - 1
            while p >= max(-1, j - 5):
                sp = normalize_ws(lines[p])
                if not _looks_like_en_fragment(sp):
                    break
                parts.append(sp)
                p -= 1
            parts.reverse()
            merged = normalize_ws(" ".join(parts))
            key, en_name = _extract_heading_key(merged)
        if key:
            return j, key, en_name
    return -1, "", ""


def clean_block_text(text: str, max_len: int = 260) -> str:
    t = normalize_ws(text)
    if not t:
        return ""
    t = re.sub(r"^[）)\]:：\s]+", "", t)
    # Remove long table-like noise.
    if len(t) > max_len:
        return ""
    # Remove obvious roster lines.
    if t.count("*") >= 2:
        return ""
    return t


def parse_page_blocks(lines: list[str]) -> list[dict[str, str]]:
    blocks: list[dict[str, str]] = []
    n = len(lines)
    i = 0
    while i < n:
        is_pr, prereq_inline = _match_labeled_line(lines[i], PREREQ_LABELS)
        if not is_pr:
            i += 1
            continue

        # Find benefit label nearby.
        benefit_idx = -1
        benefit_inline = ""
        for j in range(i + 1, min(n, i + 28)):
            is_be, be_inline = _match_labeled_line(lines[j], BENEFIT_LABELS)
            if is_be:
                benefit_idx = j
                benefit_inline = be_inline
                break
        if benefit_idx < 0:
            i += 1
            continue

        # Find feat heading before prerequisite label.
        en_idx, key, en_name = _find_heading(lines, i)
        if en_idx < 0 or not key:
            i += 1
            continue

        cn_name = ""
        for j in range(en_idx - 1, max(-1, en_idx - 4), -1):
            s = normalize_ws(lines[j])
            if not s:
                continue
            is_pr2, _ = _match_labeled_line(s, PREREQ_LABELS)
            is_be2, _ = _match_labeled_line(s, BENEFIT_LABELS)
            if has_cn(s) and len(s) <= 24 and (not is_pr2) and (not is_be2):
                cn_name = s
                break

        detail = clean_block_text(" ".join(lines[en_idx + 1 : i]), max_len=600)

        prereq_parts: list[str] = []
        if prereq_inline:
            prereq_parts.append(prereq_inline)
        prereq_parts.extend(lines[i + 1 : benefit_idx])
        prereq = clean_block_text(" ".join(prereq_parts))

        # Benefit may span multiple lines until next block/header.
        benefit_parts: list[str] = [benefit_inline] if benefit_inline else []
        k = benefit_idx + 1
        while k < n:
            s = normalize_ws(lines[k])
            if not s:
                k += 1
                continue
            is_pr3, _ = _match_labeled_line(s, PREREQ_LABELS)
            if is_pr3:
                break
            if STOP_PAT.match(s) and benefit_parts:
                break
            # Split CN+EN title pairs for next feat entry (e.g. 可怖殴击（ / Awesome / Blow).
            if has_cn(s) and len(s) <= 24 and k + 1 < n:
                n1 = normalize_ws(lines[k + 1])
                n2 = normalize_ws(lines[k + 2]) if k + 2 < n else ""
                if _looks_like_en_fragment(n1) or is_probable_feat_en_line(n1) or _looks_like_en_fragment(n2):
                    for t in range(k + 1, min(n, k + 13)):
                        is_pr_next, _ = _match_labeled_line(normalize_ws(lines[t]), PREREQ_LABELS)
                        if is_pr_next:
                            break
                    else:
                        is_pr_next = False
                    if is_pr_next:
                        break
            # CN title + EN title pair likely indicates next feat block.
            if (
                has_cn(s)
                and len(s) <= 16
                and ("（" not in s and "(" not in s and "）" not in s and ")" not in s)
                and (not re.search(r"[，。；：,:;]", s))
                and k + 1 < n
                and is_probable_feat_en_line(normalize_ws(lines[k + 1]))
            ):
                for t in range(k + 1, min(n, k + 11)):
                    is_pr_next, _ = _match_labeled_line(normalize_ws(lines[t]), PREREQ_LABELS)
                    if is_pr_next:
                        break
                else:
                    is_pr_next = False
                if is_pr_next:
                    break
            if is_probable_feat_en_line(s) and k + 1 < n:
                is_pr4, _ = _match_labeled_line(normalize_ws(lines[k + 1]), PREREQ_LABELS)
                if is_pr4:
                    break
            benefit_parts.append(s)
            if len(" ".join(benefit_parts)) > 1200:
                break
            k += 1
        benefit = clean_block_text(" ".join(benefit_parts), max_len=1200)

        # Need at least one meaningful Chinese field.
        if not any(has_cn(x) for x in [detail, prereq, benefit]):
            i += 1
            continue

        blocks.append(
            {
                "key": key,
                "name_en": en_name,
                "name_cn": cn_name,
                "detail": detail,
                "prereq": prereq,
                "benefit": benefit,
            }
        )
        i = k if k > i else i + 1
    return blocks


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill feat detail/prereq/benefit from CHM structured blocks.")
    parser.add_argument("--book-feats", type=Path, default=IN_BOOK_FEATS)
    parser.add_argument("--viewer", type=Path, default=IN_VIEWER)
    parser.add_argument("--output", type=Path, default=OUT_BOOK_FEATS)
    parser.add_argument("--inplace", action="store_true")
    args = parser.parse_args()

    data: dict[str, list[dict[str, Any]]] = json.loads(args.book_feats.read_text(encoding="utf-8"))
    pages = load_embedded_pages(args.viewer)

    # Build global block map by feat key.
    block_map: dict[str, dict[str, str]] = {}
    for pk, html in pages.items():
        soup = BeautifulSoup(html, "html.parser")
        text = soup.get_text("\n", strip=True)
        lines = [normalize_ws(x) for x in text.splitlines() if normalize_ws(x)]
        if not lines:
            continue
        for block in parse_page_blocks(lines):
            key = block["key"]
            old = block_map.get(key)
            candidate = {**block, "page": pk, "page_rule": BOOK_PAGE_RULES.get(pk, "generic")}
            if old is None:
                block_map[key] = candidate
                continue
            # Prefer richer blocks.
            old_score = sum(len(old.get(f, "")) for f in ["detail", "prereq", "benefit"])
            new_score = sum(len(candidate.get(f, "")) for f in ["detail", "prereq", "benefit"])
            if new_score > old_score:
                block_map[key] = candidate

    updated_rows = 0
    updated_keys: set[str] = set()
    page_idx = {unquote(k).lower(): k for k in pages.keys()}

    for rows in data.values():
        for row in rows:
            key = normalize_ws(row.get("match_key", ""))
            if not key:
                continue
            block = block_map.get(key)
            if not block:
                continue

            changed = False
            if not normalize_ws(row.get("detail_text", "")) and block.get("detail"):
                row["detail_text"] = block["detail"]
                changed = True
            if not normalize_ws(row.get("prerequisites", "")) and block.get("prereq"):
                row["prerequisites"] = block["prereq"]
                changed = True
            if not normalize_ws(row.get("benefit_summary", "")) and block.get("benefit"):
                row["benefit_summary"] = block["benefit"]
                changed = True
            # Prefer long Chinese benefit text as detail when current detail is a short flavor sentence.
            old_detail = normalize_ws(row.get("detail_text", ""))
            block_benefit = normalize_ws(block.get("benefit", ""))
            if (
                block_benefit
                and has_cn(block_benefit)
                and len(block_benefit) >= 40
                and (not old_detail or len(old_detail) < 40 or old_detail == normalize_ws(block.get("detail", "")))
            ):
                if row.get("detail_text", "") != block_benefit:
                    row["detail_text"] = block_benefit
                    changed = True
            if changed:
                sp = row.get("source_pages") or []
                page_local = block.get("page", "")
                if page_local and page_local in page_idx.values():
                    sp.append(
                        {
                            "local": page_local,
                            "toc_path": f"chm_structured_block_locator/{block.get('page_rule','generic')}",
                            "table_index": -94,
                            "row_index": -1,
                        }
                    )
                row["source_pages"] = sp
                updated_rows += 1
                updated_keys.add(key)

    out_path = args.book_feats if args.inplace else args.output
    out_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    print("Done.")
    print(f"Parsed block keys: {len(block_map)}")
    print(f"Updated keys: {len(updated_keys)}")
    print(f"Updated rows: {updated_rows}")
    print(f"Output: {out_path}")


if __name__ == "__main__":
    main()