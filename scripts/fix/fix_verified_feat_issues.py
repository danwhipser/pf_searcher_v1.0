#!/usr/bin/env python3
from __future__ import annotations

# Allow direct execution from nested scripts/ folders.
import sys
from pathlib import Path
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import json
import re
from typing import Any

from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.backfill.backfill_feat_detail_unified import IN_VIEWER, clean_text, collect_candidates, row_cand_score
from scripts.extract.extract_feats_and_verify import load_embedded_pages, normalize_ws

IN_BOOK_FEATS = ROOT / "result" / "feats" / "feat-book-feats.json"
OUT_REPORT = ROOT / "result" / "feats" / "verified_feat_issue_fix_report.json"

PREREQ = "\u5148\u51b3\u6761\u4ef6"
BENEFIT = "\u597d\u5904"
SPECIAL = "\u7279\u6b8a"
NORMAL = "\u6b63\u5e38"

BAD_KEYS_BY_BOOK: dict[str, set[str]] = {
    "\u4e13\u957f\u6982\u8ff0": {"potion"},
    "\u6d41\u6d3e\u4e13\u957f\u4e00\u89c8": {"anything", "bab9", "monsterhuntershandbook"},
    "\u9020\u7269\u4e13\u957f\u4e00\u89c8": {"craftmagicarmsand", "reservoir", "tattoos"},
    "\u5185\u6d77\u8bf8\u795e": {"the"},
    "\u8428\u52a0\u74e6\uff0c\u5931\u843d\u7684\u6b96\u6c11\u5730": {"elephant"},
    "\u7687\u5ead\u82f1\u8c6a": {
        "afterward",
        "behind",
        "chosenskill",
        "family",
        "influencesystem",
        "monarch",
        "you",
        "yourtales",
    },
}

OA_KEYS = {
    "elongatedcranium",
    "emotionalconduit",
    "expandedphrenicpool",
    "fearsomespell",
    "interweavecompositeblast",
    "intuitivespell",
    "logicalspell",
    "spiritualistscall",
}

OA_PAGE_311_BOUNDS = {
    "elongatedcranium": (672, 727),
    "emotionalconduit": (727, 1084),
    "expandedphrenicpool": (1142, 1155),
    "fearsomespell": (1341, 1355),
    "interweavecompositeblast": (1494, 1545),
    "intuitivespell": (1596, 1607),
    "logicalspell": (1685, 1696),
    "spiritualistscall": (2235, 2249),
}

DIRECT_CANDIDATE_KEYS = {
    "horsemaster",
    "moonlightstalker",
    "moonlightstalkerfeint",
    "purefaith",
}


def compact_key(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (text or "").lower())


def join_parts(parts: list[str]) -> str:
    text = ""
    for part in parts:
        p = normalize_ws(part)
        if not p or p in {"\uff1a", ":"}:
            continue
        if not text:
            text = p
        elif re.fullmatch(r"[A-Za-z0-9+\-/]+", p):
            text += p
        elif re.fullmatch(r"[,.;:!?，。；：！？）】\]]+", p):
            text += p
        elif text.endswith(("（", "【", "[", "/", "+", "=")):
            text += p
        else:
            text += " " + p
    return clean_text(text)


def line_key(line: str) -> str:
    return compact_key(line.replace("\u2019", "'").replace("`", "'"))


def find_oa_heading(lines: list[str], key: str) -> int | None:
    # Earlier in page_311 there is a compact feat table. Start after that table
    # so we extract the prose feat entries, not the summary rows.
    for i in range(500, len(lines)):
        current = normalize_ws(lines[i])
        if (
            not re.search(r"[\u4e00-\u9fff]", current)
            or len(current) > 32
            or re.search(r"[，。；：、,.!?！？]", current)
        ):
            continue
        combined = " ".join(lines[i : min(len(lines), i + 4)])
        if line_key(combined).startswith(key):
            return i
    return None


def looks_heading(lines: list[str], idx: int) -> bool:
    if idx <= 0 or idx >= len(lines):
        return False
    line = normalize_ws(lines[idx])
    if not re.search(r"[\u4e00-\u9fff]", line) or len(line) > 32:
        return False
    if line.startswith(("\uff1a", ":", "\uff09", ")")):
        return False
    if idx > 0 and (lines[idx - 1].startswith(PREREQ) or lines[idx - 1].startswith(BENEFIT)):
        return False
    if idx + 1 >= len(lines) or not re.search(r"[A-Za-z]", lines[idx + 1]):
        return False
    if lines[idx + 1].startswith(PREREQ) or lines[idx + 1].startswith(BENEFIT):
        return False
    combined = " ".join(lines[idx + 1 : min(len(lines), idx + 4)])
    if not re.search(r"[A-Za-z]", combined):
        return False
    window = lines[idx + 2 : min(len(lines), idx + 14)]
    return any(x.startswith(PREREQ) or x.startswith(BENEFIT) for x in window)


def next_heading(lines: list[str], start: int) -> int:
    for i in range(start + 1, len(lines)):
        if looks_heading(lines, i):
            return i
    return len(lines)


def extract_oa_block(lines: list[str], key: str) -> dict[str, str] | None:
    if key in OA_PAGE_311_BOUNDS:
        start, end = OA_PAGE_311_BOUNDS[key]
    else:
        start = find_oa_heading(lines, key)
        if start is None:
            return None
        end = next_heading(lines, start)
    name_cn = clean_text(lines[start])

    # English title may span one or more lines immediately after the Chinese title.
    i = start + 1
    en_parts: list[str] = []
    while i < end and re.search(r"[A-Za-z]", lines[i]) and not lines[i].startswith(PREREQ) and not lines[i].startswith(BENEFIT):
        en_parts.append(lines[i])
        i += 1
        if "(" in " ".join(en_parts) or len(en_parts) >= 4:
            break
    name_en = clean_text(" ".join(en_parts))
    name_en = re.sub(r"\s*\([^)]*\)\s*$", "", name_en).replace("\u2019", "'")

    prereq_parts: list[str] = []
    benefit_parts: list[str] = []
    flavor_parts: list[str] = []
    current = "flavor"
    while i < end:
        line = normalize_ws(lines[i])
        if not line:
            i += 1
            continue
        if line.startswith(PREREQ):
            current = "prereq"
            rest = re.sub(rf"^{PREREQ}\s*[\uff1a:]*\s*", "", line)
            if rest:
                prereq_parts.append(rest)
            i += 1
            continue
        if line.startswith(BENEFIT):
            current = "benefit"
            rest = re.sub(rf"^{BENEFIT}\s*[\uff1a:]*\s*", "", line)
            if rest:
                benefit_parts.append(rest)
            i += 1
            continue
        if line.startswith(SPECIAL) or line.startswith(NORMAL):
            current = "benefit"
        if line in {"\uff1a", ":"}:
            i += 1
            continue
        if current == "prereq":
            prereq_parts.append(line)
        elif current == "benefit":
            benefit_parts.append(line)
        else:
            flavor_parts.append(line)
        i += 1

    detail = join_parts(benefit_parts)
    if not detail:
        return None
    return {
        "name_cn": name_cn,
        "name_en": name_en,
        "prerequisites": join_parts(prereq_parts),
        "detail_text": detail,
        "benefit_summary": detail,
        "flavor_text": join_parts(flavor_parts),
        "page": "page_311.html",
    }


def append_marker(row: dict[str, Any], page: str, path: str) -> None:
    marker = {"local": page, "toc_path": path, "table_index": -106, "row_index": -1}
    sp = row.get("source_pages") or []
    if marker not in sp:
        sp.append(marker)
    row["source_pages"] = sp


def apply_fields(row: dict[str, Any], fields: dict[str, str], page: str, path: str) -> dict[str, Any]:
    before = {
        "name_cn": row.get("name_cn", ""),
        "prerequisites": row.get("prerequisites", ""),
        "benefit_summary": row.get("benefit_summary", ""),
        "detail_text": row.get("detail_text", ""),
        "flavor_text": row.get("flavor_text", ""),
    }
    for field in ("name_cn", "name_en", "prerequisites", "benefit_summary", "detail_text", "flavor_text"):
        if fields.get(field):
            row[field] = fields[field]
    if fields.get("name_cn") and fields.get("name_en"):
        row["name_raw"] = f"{fields['name_cn']} ({fields['name_en']})"
    append_marker(row, page, path)
    after = {
        "name_cn": row.get("name_cn", ""),
        "prerequisites": row.get("prerequisites", ""),
        "benefit_summary": row.get("benefit_summary", ""),
        "detail_text": row.get("detail_text", ""),
        "flavor_text": row.get("flavor_text", ""),
    }
    return {"before": before, "after": after}


def main() -> None:
    data: dict[str, list[dict[str, Any]]] = json.loads(IN_BOOK_FEATS.read_text(encoding="utf-8"))
    pages = load_embedded_pages(IN_VIEWER)
    page_311_lines = [
        normalize_ws(x)
        for x in BeautifulSoup(pages["page_311.html"], "html.parser").get_text("\n").splitlines()
        if normalize_ws(x)
    ]
    oa_blocks = {key: extract_oa_block(page_311_lines, key) for key in OA_KEYS}
    cand_map = collect_candidates(IN_VIEWER)

    updated: list[dict[str, Any]] = []
    removed: list[dict[str, Any]] = []
    missing: list[str] = []

    for book, rows in list(data.items()):
        if not isinstance(rows, list):
            continue
        bad_keys = BAD_KEYS_BY_BOOK.get(book, set())
        kept: list[dict[str, Any]] = []
        for row in rows:
            key = normalize_ws(row.get("match_key", ""))
            if key in bad_keys:
                removed.append(
                    {
                        "book": book,
                        "key": key,
                        "name_cn": row.get("name_cn", ""),
                        "name_en": row.get("name_en", ""),
                        "reason": "confirmed parse fragment / non-feat row from correctness audit",
                    }
                )
                continue

            if book == "\u004f\u0041\u5f02\u80fd\u5192\u9669" and key in OA_KEYS:
                fields = oa_blocks.get(key)
                if fields:
                    change = apply_fields(row, fields, fields["page"], "verified_oa_page_311_fix")
                    updated.append({"book": book, "key": key, "method": "oa_page_311", **change})
                else:
                    missing.append(key)

            if key in DIRECT_CANDIDATE_KEYS and (
                book in {"\u0055\u0043 \u6781\u9650\u6218\u6597", "\u5168\u4e13\u957f\u5217\u8868"}
            ):
                cands = cand_map.get(key, [])
                if cands:
                    best = max(cands, key=lambda c: row_cand_score(row, c))
                    if row_cand_score(row, best) >= 120:
                        fields = {
                            "name_cn": best.name_cn.strip("\uff08("),
                            "name_en": best.name_en,
                            "prerequisites": best.prereq,
                            "benefit_summary": best.detail,
                            "detail_text": best.detail,
                            "flavor_text": best.flavor,
                        }
                        change = apply_fields(row, fields, best.page, "verified_candidate_backfill")
                        updated.append({"book": book, "key": key, "method": "candidate", "page": best.page, **change})
            kept.append(row)
        data[book] = kept

    IN_BOOK_FEATS.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    OUT_REPORT.write_text(
        json.dumps(
            {
                "updated_rows": len(updated),
                "removed_rows": len(removed),
                "missing_oa_blocks": missing,
                "updated": updated,
                "removed": removed,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"Updated rows: {len(updated)}")
    print(f"Removed rows: {len(removed)}")
    if missing:
        print("Missing OA blocks:", ", ".join(missing))
    print(f"Report: {OUT_REPORT}")


if __name__ == "__main__":
    main()