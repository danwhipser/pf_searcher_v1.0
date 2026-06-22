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

from scripts.extract.extract_feats_and_verify import canonicalize_en_name, load_embedded_pages, normalize_key, normalize_ws
from scripts.locate.locate_feat_chapters_and_validate import load_toc, locate_chapters

TOC_PATH = ROOT / "result" / "toc.json"
VIEWER_PATH = ROOT / "result" / "Pathfinder-v2.14-SC-viewer-embedded.html"
BOOK_FEATS_PATH = ROOT / "result" / "feats" / "feat-book-feats.json"
OUT_REPORT = ROOT / "result" / "feats" / "chapter_detail_backfill_report.json"


PREREQ_LABELS = ["先决条件", "前置条件", "需求", "Prerequisite", "Prerequisites"]
BENEFIT_LABELS = ["专长效果", "效果", "好处", "Benefit"]
IMMEDIATE_LABELS = ["即时收益"]
GOAL_LABELS = ["专长目标"]
COMPLETION_LABELS = ["完成收益"]

PLACEHOLDER_FRAGMENTS = ["见下文", "见专长详述", "故事 | UCa", "待补全"]
HEADER_CN_EN_RE = re.compile(r"[\u4e00-\u9fff]{2,24}\s*[（(]\s*[^）)]{1,80}\s*[）)]\s*(?:〔[^〕]{1,12}〕)?")


def is_english_token_line(s: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z][A-Za-z0-9'`!/+&.,:\-\s]+", s or ""))


def has_cn(text: str) -> bool:
    return bool(re.search(r"[\u4e00-\u9fff]", text or ""))


def starts_with_any(s: str, labels: list[str]) -> tuple[bool, str]:
    t = normalize_ws(s)
    tl = t.lower()
    for lab in labels:
        ll = lab.lower()
        if tl == ll:
            return True, ""
        if tl.startswith(ll):
            rest = normalize_ws(t[len(lab) :])
            rest = re.sub(r"^[：:\s]+", "", rest)
            return True, normalize_ws(rest)
    return False, ""


def parse_title_inline(line: str) -> tuple[str, str, str, int]:
    """
    Return (key, name_en, name_cn, end_idx_delta).
    end_idx_delta is always 0 for inline parsing.
    """
    s = normalize_ws(line)
    # CN(EN) or CN（EN） with optional suffix tags
    m = re.match(r"^(.{1,30}?)[（(]\s*([A-Za-z][^)）]{1,80})\s*[)）](.*)$", s)
    if not m:
        return "", "", "", 0
    cn = normalize_ws(m.group(1))
    en = canonicalize_en_name(m.group(2))
    if (not en) or (not cn) or ("：" in cn) or (":" in cn):
        return "", "", "", 0
    k = normalize_key(en)
    return k, en, cn, 0


def parse_title_multiline(lines: list[str], i: int) -> tuple[str, str, str, int]:
    """
    Match:
      CN（
      EN token line(s)
      ）[...]  (same line contains full-width close paren)
    Return (key, name_en, name_cn, end_idx).
    """
    if i + 2 >= len(lines):
        return "", "", "", -1
    cn_line = normalize_ws(lines[i])
    if not cn_line.endswith("（"):
        return "", "", "", -1
    name_cn = normalize_ws(cn_line[:-1])
    if not name_cn or len(name_cn) > 30:
        return "", "", "", -1

    en_parts: list[str] = []
    for j in range(i + 1, min(i + 9, len(lines))):
        cur = normalize_ws(lines[j])
        if "）" in cur:
            if not en_parts:
                return "", "", "", -1
            en = canonicalize_en_name(" ".join(en_parts))
            k = normalize_key(en)
            return k, en, name_cn, j
        if is_english_token_line(cur):
            en_parts.append(cur)
            continue
        break
    return "", "", "", -1


def find_feat_starts(lines: list[str]) -> list[tuple[int, int, str, str, str]]:
    out: list[tuple[int, int, str, str, str]] = []
    i = 0
    while i < len(lines):
        key, en, cn, end_delta = parse_title_inline(lines[i])
        if key:
            out.append((i, i + end_delta, key, en, cn))
            i += 1
            continue
        key, en, cn, end_idx = parse_title_multiline(lines, i)
        if key:
            out.append((i, end_idx, key, en, cn))
            i = end_idx + 1
            continue
        i += 1
    # Dedup starts on same index by longest key
    dedup: dict[int, tuple[int, int, str, str, str]] = {}
    for item in out:
        st = item[0]
        old = dedup.get(st)
        if old is None or len(item[2]) > len(old[2]):
            dedup[st] = item
    return [dedup[k] for k in sorted(dedup.keys())]


def clean_parts(parts: list[str]) -> str:
    text = normalize_ws(" ".join(normalize_ws(p) for p in parts if normalize_ws(p)))
    text = re.sub(r"\s+", " ", text).strip()
    return text


def parse_block_fields(body_lines: list[str]) -> dict[str, str]:
    sections: dict[str, list[str]] = {
        "flavor_text": [],
        "prerequisites": [],
        "benefit_summary": [],
        "immediate_benefit": [],
        "story_goal": [],
        "completion_benefit": [],
    }
    state = "flavor_text"
    for raw in body_lines:
        line = normalize_ws(raw)
        if not line:
            continue
        matched, rest = starts_with_any(line, PREREQ_LABELS)
        if matched:
            state = "prerequisites"
            if rest:
                sections[state].append(rest)
            continue
        matched, rest = starts_with_any(line, IMMEDIATE_LABELS)
        if matched:
            state = "immediate_benefit"
            if rest:
                sections[state].append(rest)
            continue
        matched, rest = starts_with_any(line, GOAL_LABELS)
        if matched:
            state = "story_goal"
            if rest:
                sections[state].append(rest)
            continue
        matched, rest = starts_with_any(line, COMPLETION_LABELS)
        if matched:
            state = "completion_benefit"
            if rest:
                sections[state].append(rest)
            continue
        matched, rest = starts_with_any(line, BENEFIT_LABELS)
        if matched:
            state = "benefit_summary"
            if rest:
                sections[state].append(rest)
            continue

        # Keep appending to current section.
        sections[state].append(line)

    out = {k: clean_parts(v) for k, v in sections.items()}
    for k in list(out.keys()):
        out[k] = truncate_at_next_header(out[k])
    # Prefer story immediate benefit as detail source for story feats.
    detail = out["immediate_benefit"] or out["benefit_summary"]
    # If no labeled sections found, avoid noisy paragraph blocks.
    if not detail:
        return {}
    out["detail_candidate"] = detail
    return out


def truncate_at_next_header(detail: str) -> str:
    d = normalize_ws(detail)
    if not d:
        return d
    for m in HEADER_CN_EN_RE.finditer(d):
        if m.start() < 20:
            continue
        d = d[: m.start()].strip()
        break
    return normalize_ws(d)


def looks_contaminated(detail: str) -> bool:
    d = normalize_ws(detail)
    if not d:
        return False
    # feat header pattern appearing again inside detail usually means spillover
    return any(m.start() >= 20 for m in HEADER_CN_EN_RE.finditer(d))


def parse_page_feat_blocks(html: str) -> dict[str, dict[str, str]]:
    lines = [normalize_ws(x) for x in BeautifulSoup(html, "html.parser").get_text("\n").splitlines() if normalize_ws(x)]
    starts = find_feat_starts(lines)
    if not starts:
        return {}

    blocks: dict[str, dict[str, str]] = {}
    for idx, (start_i, end_i, key, en, cn) in enumerate(starts):
        next_start = starts[idx + 1][0] if idx + 1 < len(starts) else len(lines)
        body = lines[end_i + 1 : next_start]
        fields = parse_block_fields(body)
        if not fields:
            continue
        fields["name_en"] = en
        fields["name_cn"] = cn
        old = blocks.get(key)
        if old is None:
            blocks[key] = fields
            continue
        old_score = len(old.get("detail_candidate", "")) + len(old.get("prerequisites", "")) + len(old.get("completion_benefit", ""))
        new_score = len(fields.get("detail_candidate", "")) + len(fields.get("prerequisites", "")) + len(fields.get("completion_benefit", ""))
        if new_score > old_score:
            blocks[key] = fields
    return blocks


def is_short_or_placeholder(detail: str, benefit: str) -> bool:
    d = normalize_ws(detail)
    b = normalize_ws(benefit)
    if not d:
        return True
    if any(x in d for x in PLACEHOLDER_FRAGMENTS):
        return True
    if len(d) <= 20:
        return True
    if d == b:
        return True
    return False


def should_replace(old: str, new: str, *, allow_if_short: bool = True) -> bool:
    o = normalize_ws(old)
    n = normalize_ws(new)
    if not n:
        return False
    if not o:
        return True
    if allow_if_short and len(o) <= 12 and len(n) > len(o):
        return True
    if len(n) > len(o) * 1.25:
        return True
    return False


def build_book_page_map(toc_path: Path) -> dict[str, list[dict[str, str]]]:
    toc = load_toc(toc_path)
    chapters, _ = locate_chapters(toc)
    out: dict[str, list[dict[str, str]]] = {}
    for ch in chapters:
        if ch.get("chapter_type") != "book_chapter":
            continue
        book = normalize_ws(ch.get("chapter", ""))
        pages = ch.get("pages", []) or []
        out[book] = [{"local": normalize_ws(p.get("local", "")), "toc_path": normalize_ws(p.get("toc_path", ""))} for p in pages]
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill feat detail by each book's feat chapter pages.")
    parser.add_argument("--toc", type=Path, default=TOC_PATH)
    parser.add_argument("--viewer", type=Path, default=VIEWER_PATH)
    parser.add_argument("--book-feats", type=Path, default=BOOK_FEATS_PATH)
    parser.add_argument("--inplace", action="store_true")
    parser.add_argument("--output", type=Path, default=ROOT / "result" / "feats" / "feat-book-feats-chapter-targeted.json")
    parser.add_argument("--report", type=Path, default=OUT_REPORT)
    args = parser.parse_args()

    data: dict[str, list[dict[str, Any]]] = json.loads(args.book_feats.read_text(encoding="utf-8"))
    book_pages = build_book_page_map(args.toc)
    pages = load_embedded_pages(args.viewer)
    page_idx = {unquote(k).lower(): k for k in pages.keys()}

    report_books: list[dict[str, Any]] = []
    total_updated_rows = 0
    total_updated_keys: set[str] = set()

    for book, rows in data.items():
        if not isinstance(rows, list):
            continue
        chapter_pages = book_pages.get(book, [])
        page_blocks: dict[str, dict[str, str]] = {}
        parsed_pages = 0
        missing_pages: list[str] = []
        for p in chapter_pages:
            local = p.get("local", "")
            if not local:
                continue
            key = page_idx.get(unquote(local).lower())
            if not key:
                missing_pages.append(local)
                continue
            parsed_pages += 1
            blocks = parse_page_feat_blocks(pages[key])
            for bk, bv in blocks.items():
                old = page_blocks.get(bk)
                if old is None:
                    page_blocks[bk] = bv
                else:
                    old_score = len(old.get("detail_candidate", "")) + len(old.get("completion_benefit", ""))
                    new_score = len(bv.get("detail_candidate", "")) + len(bv.get("completion_benefit", ""))
                    if new_score > old_score:
                        page_blocks[bk] = bv

        updated_rows = 0
        updated_keys: set[str] = set()
        for row in rows:
            mk = normalize_ws(row.get("match_key", ""))
            if not mk:
                continue
            block = page_blocks.get(mk)
            if not block:
                continue

            old_detail = normalize_ws(row.get("detail_text", ""))
            old_benefit = normalize_ws(row.get("benefit_summary", ""))
            new_detail = normalize_ws(block.get("detail_candidate", ""))
            if (not has_cn(new_detail)) or len(new_detail) < 18:
                continue

            changed = False
            if is_short_or_placeholder(old_detail, old_benefit) or should_replace(old_detail, new_detail):
                row["detail_text"] = new_detail
                changed = True
            elif looks_contaminated(old_detail) and (not looks_contaminated(new_detail)) and has_cn(new_detail):
                row["detail_text"] = new_detail
                changed = True

            new_prereq = normalize_ws(block.get("prerequisites", ""))
            if has_cn(new_prereq) and should_replace(row.get("prerequisites", ""), new_prereq):
                row["prerequisites"] = new_prereq
                changed = True

            new_benefit = normalize_ws(block.get("benefit_summary", ""))
            if has_cn(new_benefit) and should_replace(row.get("benefit_summary", ""), new_benefit):
                row["benefit_summary"] = new_benefit
                changed = True

            # Structured story fields
            for field in ["flavor_text", "immediate_benefit", "story_goal", "completion_benefit"]:
                val = normalize_ws(block.get(field, ""))
                if val and has_cn(val):
                    if not normalize_ws(row.get(field, "")):
                        row[field] = val
                        changed = True

            if changed:
                sp = row.get("source_pages", []) or []
                marker = {
                    "local": chapter_pages[0]["local"] if chapter_pages else "",
                    "toc_path": f"chapter_targeted_backfill/{book}",
                    "table_index": -93,
                    "row_index": -1,
                }
                if marker not in sp:
                    sp.append(marker)
                row["source_pages"] = sp
                updated_rows += 1
                updated_keys.add(mk)
                total_updated_keys.add(mk)

        total_updated_rows += updated_rows
        report_books.append(
            {
                "book": book,
                "chapter_page_count": len(chapter_pages),
                "chapter_pages_parsed": parsed_pages,
                "chapter_pages_missing": missing_pages,
                "chapter_blocks_found": len(page_blocks),
                "updated_rows": updated_rows,
                "updated_keys": len(updated_keys),
            }
        )

    out_path = args.book_feats if args.inplace else args.output
    out_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    report = {
        "source_book_feats": str(args.book_feats),
        "source_toc": str(args.toc),
        "source_viewer": str(args.viewer),
        "updated_rows_total": total_updated_rows,
        "updated_keys_total": len(total_updated_keys),
        "books": sorted(report_books, key=lambda x: (x["updated_rows"], x["book"]), reverse=True),
        "output": str(out_path),
    }
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print("Done.")
    print(f"Updated rows total: {total_updated_rows}")
    print(f"Updated keys total: {len(total_updated_keys)}")
    print(f"Output: {out_path}")
    print(f"Report: {args.report}")


if __name__ == "__main__":
    main()