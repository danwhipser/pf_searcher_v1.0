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
from dataclasses import dataclass
from typing import Any

from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.backfill.backfill_feat_detail_book_specific import _parse_uc_page_651
from scripts.backfill.backfill_feat_detail_from_chm_structured_blocks import parse_page_blocks
from scripts.extract.extract_feats_and_verify import load_embedded_pages, normalize_key, normalize_ws

IN_BOOK_FEATS = ROOT / "result" / "feats" / "feat-book-feats.json"
IN_VIEWER = ROOT / "result" / "Pathfinder-v2.14-SC-viewer-embedded.html"
OUT_REPORT = ROOT / "result" / "feats" / "unified_detail_backfill_report.json"

SUMMARY_PAGES = {f"page_{i}.html" for i in range(195, 204)}


def has_cn(text: str) -> bool:
    return bool(re.search(r"[\u4e00-\u9fff]", text or ""))


def clean_text(text: str) -> str:
    t = normalize_ws(text or "")
    t = re.sub(r"^[：:\-\s]+", "", t)
    # Remove obvious header spillover
    t = re.sub(r"\s+Source\s+[A-Za-z].*$", "", t)
    return normalize_ws(t)


def cn_core(name_cn: str) -> str:
    s = normalize_ws(name_cn or "")
    s = re.sub(r"[（(].*?[）)]", "", s)
    s = re.sub(r"[\s·\-\[\]【】\"'`]+", "", s)
    return s


def mythic_flag(name_cn: str, name_en: str) -> bool:
    s = f"{normalize_ws(name_cn)} {normalize_ws(name_en)}".lower()
    return ("神话" in s) or ("mythic" in s)


def is_short_detail(text: str) -> bool:
    t = clean_text(text)
    return (not t) or len(t) <= 20


def text_quality(text: str) -> int:
    t = clean_text(text)
    if not t:
        return -10**9
    score = len(t)
    if has_cn(t):
        score += 40
    if re.search(r"[。；，：]", t):
        score += 20
    if "|" in t:
        score -= 120
    if any(x in t for x in ("Traceback", "UnicodeEncodeError")):
        score -= 500
    if "来源" in t and len(t) < 120:
        score -= 60
    return score


def overlap_score(benefit: str, cand_text: str) -> int:
    b = clean_text(benefit)
    c = clean_text(cand_text)
    if not b or not c:
        return 0
    chunks = re.findall(r"[\u4e00-\u9fff]{2,20}", b)
    if not chunks:
        return 0
    bgrams: set[str] = set()
    for ch in chunks:
        for i in range(len(ch) - 1):
            bgrams.add(ch[i : i + 2])
    if not bgrams:
        return 0
    hits = sum(1 for g in bgrams if g in c)
    if hits >= 5:
        return 45
    if hits >= 3:
        return 25
    if hits >= 1:
        return 8
    if len(c) >= 60:
        return -35
    return -10


@dataclass
class Candidate:
    key: str
    name_cn: str
    name_en: str
    detail: str
    prereq: str
    flavor: str
    page: str
    parser: str


PREREQ_LABEL = "\u5148\u51b3\u6761\u4ef6"
BENEFIT_LABEL = "\u4e13\u957f\u6548\u679c"
SPECIAL_LABEL = "\u7279\u6b8a\u8bf4\u660e"
NORMAL_LABEL = "\u901a\u5e38\u72b6\u51b5"
SOURCE_LABEL = "\u51fa\u81ea"
FIELD_LABELS = (PREREQ_LABEL, BENEFIT_LABEL, SPECIAL_LABEL, NORMAL_LABEL)


def _is_en_title_token(line: str) -> bool:
    s = normalize_ws(line)
    if not s or has_cn(s):
        return False
    if not re.search(r"[A-Za-z]", s):
        return False
    if len(s) > 32:
        return False
    return bool(re.fullmatch(r"[A-Za-z][A-Za-z'`\-/]*(?:\s+[A-Za-z][A-Za-z'`\-/]*){0,3}\.?", s))


def _is_probable_cn_title(line: str) -> bool:
    s = normalize_ws(line).strip("\uff08(")
    if not has_cn(s) or _is_field_label(s):
        return False
    if len(s) > 28:
        return False
    if re.search(r"[，。；：、,.!?！？]", s):
        return False
    bad_fragments = (
        "\u5c0f\u4e8e",
        "\u7b49\u4e8e",
        "\u4f60\u7684",
        "\u8be5",
        "\u8fd9\u79cd",
        "\u76ee\u6807",
        "\u76df\u53cb",
        "\u52a0\u503c",
        "\u82e5",
        "\u5f53",
        "\u4ee5",
    )
    return not any(x in s for x in bad_fragments)


def _is_type_line(line: str) -> bool:
    s = normalize_ws(line)
    return s.startswith("\uff08") and s.endswith("\uff09") and len(s) <= 30


def _is_field_label(line: str) -> bool:
    return any(normalize_ws(line).startswith(x) for x in FIELD_LABELS)


def _strip_label(line: str, label: str) -> str:
    s = normalize_ws(line)
    s = re.sub(rf"^{re.escape(label)}\s*[\uff1a:]*\s*", "", s)
    return normalize_ws(s)


def _looks_like_next_heading(lines: list[str], idx: int) -> bool:
    if idx < 0 or idx >= len(lines):
        return False
    line = lines[idx]
    if not _is_probable_cn_title(line):
        return False

    if re.search(r"\([A-Za-z][^)]{1,80}\)", line):
        return True

    j = idx + 1
    token_count = 0
    while j < len(lines) and _is_en_title_token(lines[j]) and token_count < 8:
        token_count += 1
        j += 1
    if token_count == 0:
        return False
    if j < len(lines) and _is_type_line(lines[j]):
        j += 1
    window = lines[j : min(len(lines), j + 8)]
    return any(_is_field_label(x) for x in window)


def _looks_like_inline_heading(line: str) -> bool:
    s = normalize_ws(line)
    if len(s) > 90:
        return False
    cn = r"[\u4e00-\u9fff]{2,14}"
    en = r"[A-Z][A-Za-z'`\-/]+(?:\s+[A-Z]?[A-Za-z'`\-/]+){0,5}"
    return bool(
        re.match(rf"^{cn}\s*{en}\s*\uff08", s)
        or re.match(rf"^{cn}\uff08[^）]{{1,16}}\uff09\s*{en}", s)
    )


def _truncate_before_inline_heading(line: str) -> str:
    s = normalize_ws(line)
    cn = r"[\u4e00-\u9fff]{2,14}"
    en = r"[A-Z][A-Za-z'`\-/]+(?:\s+[A-Z]?[A-Za-z'`\-/]+){0,5}"
    patterns = (
        rf"\s{cn}\s*{en}\s*\uff08",
        rf"\s{cn}\uff08[^）]{{1,16}}\uff09\s*{en}",
    )
    cut = len(s)
    for pat in patterns:
        m = re.search(pat, s)
        if m and m.start() >= 20:
            cut = min(cut, m.start())
    return normalize_ws(s[:cut])


def _join_cn_fragments(parts: list[str]) -> str:
    text = ""
    for part in parts:
        p = normalize_ws(part)
        if not p or p == "\uff1a" or p.startswith(SOURCE_LABEL):
            continue
        if not text:
            text = p
            continue
        if re.fullmatch(r"[A-Za-z0-9+\-/]+", p):
            text += p
        elif re.fullmatch(r"[,.;:!?，。；：！？）】\]]+", p):
            text += p
        elif text.endswith(("（", "【", "[", "/", "+")):
            text += p
        else:
            text += " " + p
    return clean_text(text)


def _parse_heading_at(lines: list[str], idx: int) -> tuple[str, str, int] | None:
    line = normalize_ws(lines[idx])
    if idx > 0 and (normalize_ws(lines[idx - 1]) == "\uff1a" or _is_field_label(lines[idx - 1])):
        return None
    same_line = re.match(r"^(.{1,50}?)\s*\(([^()]*[A-Za-z][^()]*)\)\s*$", line)
    if same_line and _is_probable_cn_title(same_line.group(1)):
        name_cn = clean_text(same_line.group(1)).strip("\uff08(")
        name_en = clean_text(same_line.group(2))
        if not normalize_key(name_en).endswith("feats"):
            return name_cn, name_en, idx + 1

    cn_with_type = re.match(r"^(.{1,40}?)\s*\uff08[^）]{1,20}\uff09$", line)
    if cn_with_type and idx + 1 < len(lines) and _is_en_title_token(re.sub(r"\s*\([^)]*\)\s*$", "", lines[idx + 1]).lstrip("*")):
        name_cn = clean_text(cn_with_type.group(1))
        if _is_probable_cn_title(name_cn):
            name_en = clean_text(re.sub(r"\s*\([^)]*\)\s*$", "", lines[idx + 1]).lstrip("*"))
            if not normalize_key(name_en).endswith("feats"):
                return name_cn, name_en, idx + 2

    if not _is_probable_cn_title(line):
        return None
    j = idx + 1
    tokens: list[str] = []
    while j < len(lines) and _is_en_title_token(lines[j]) and len(tokens) < 8:
        tokens.append(lines[j])
        j += 1
    if not tokens:
        return None
    name_en = clean_text(" ".join(tokens))
    if not normalize_key(name_en):
        return None
    key = normalize_key(name_en)
    if len(key) < 4 or key in {"source", "effect", "benefit", "normal", "special"} or key.endswith("feats"):
        return None
    return clean_text(line).strip("\uff08("), name_en, j


def parse_split_heading_blocks(lines: list[str]) -> list[dict[str, str]]:
    blocks: list[dict[str, str]] = []
    headings: list[tuple[int, str, str, int]] = []
    for idx in range(len(lines)):
        parsed = _parse_heading_at(lines, idx)
        if not parsed:
            continue
        name_cn, name_en, body_start = parsed
        lookahead = lines[body_start : min(len(lines), body_start + 12)]
        if not any(_is_field_label(x) for x in lookahead):
            continue
        headings.append((idx, name_cn, name_en, body_start))

    for pos, (start, name_cn, name_en, body_start) in enumerate(headings):
        end = headings[pos + 1][0] if pos + 1 < len(headings) else len(lines)
        while body_start < end and _is_type_line(lines[body_start]):
            body_start += 1

        flavor_parts: list[str] = []
        sections: dict[str, list[str]] = {"prereq": [], "benefit": [], "special": [], "normal": []}
        current: str | None = None
        i = body_start
        while i < end:
            line = normalize_ws(lines[i])
            if not line:
                i += 1
                continue
            if line.startswith(SOURCE_LABEL):
                i += 1
                continue
            if current and _looks_like_inline_heading(line):
                break
            if current:
                line = _truncate_before_inline_heading(line)
                if not line:
                    break
            if line.startswith(PREREQ_LABEL):
                current = "prereq"
                rest = _strip_label(line, PREREQ_LABEL)
                if rest:
                    sections[current].append(rest)
                i += 1
                continue
            if line.startswith(BENEFIT_LABEL):
                current = "benefit"
                rest = _strip_label(line, BENEFIT_LABEL)
                if rest:
                    sections[current].append(rest)
                i += 1
                continue
            if line.startswith(SPECIAL_LABEL):
                current = "special"
                rest = _strip_label(line, SPECIAL_LABEL)
                if rest:
                    sections[current].append(rest)
                i += 1
                continue
            if line.startswith(NORMAL_LABEL):
                current = "normal"
                rest = _strip_label(line, NORMAL_LABEL)
                if rest:
                    sections[current].append(rest)
                i += 1
                continue
            if line == "\uff1a":
                i += 1
                continue
            if current:
                sections[current].append(line)
            else:
                flavor_parts.append(line)
            i += 1

        benefit = _join_cn_fragments(sections["benefit"])
        special = _join_cn_fragments(sections["special"])
        normal = _join_cn_fragments(sections["normal"])
        detail_parts = [benefit]
        if special:
            detail_parts.append(f"{SPECIAL_LABEL}\uff1a{special}")
        if normal:
            detail_parts.append(f"{NORMAL_LABEL}\uff1a{normal}")
        detail = clean_text(" ".join(x for x in detail_parts if x))
        if len(detail) < 25 or len(detail) > 1000:
            continue
        blocks.append(
            {
                "key": normalize_key(name_en),
                "name_cn": name_cn,
                "name_en": name_en,
                "detail": detail,
                "prereq": _join_cn_fragments(sections["prereq"]),
                "flavor": _join_cn_fragments(flavor_parts),
            }
        )
    return blocks


def collect_candidates(viewer_path: Path) -> dict[str, list[Candidate]]:
    pages = load_embedded_pages(viewer_path)
    out: dict[str, list[Candidate]] = defaultdict(list)

    for page, html in pages.items():
        lines = [normalize_ws(x) for x in BeautifulSoup(html, "html.parser").get_text("\n").splitlines() if normalize_ws(x)]
        if not lines:
            continue

        # Many CHM pages split the English title across multiple text nodes,
        # e.g. "Stick" / "Together". Parse those prose blocks before tables.
        for b in parse_split_heading_blocks(lines):
            key = normalize_ws(b.get("key", ""))
            if not key:
                continue
            detail = clean_text(b.get("detail", ""))
            if len(detail) < 30:
                continue
            out[key].append(
                Candidate(
                    key=key,
                    name_cn=clean_text(b.get("name_cn", "")),
                    name_en=clean_text(b.get("name_en", "")),
                    detail=detail,
                    prereq=clean_text(b.get("prereq", "")),
                    flavor=clean_text(b.get("flavor", "")),
                    page=page,
                    parser="split_heading",
                )
            )

        # Structured parser (generic across most pages)
        for b in parse_page_blocks(lines):
            key = normalize_ws(b.get("key", ""))
            if not key:
                continue
            detail_a = clean_text(b.get("benefit", ""))
            detail_b = clean_text(b.get("detail", ""))
            detail = detail_a if len(detail_a) >= len(detail_b) else detail_b
            if len(detail) < 30:
                continue
            out[key].append(
                Candidate(
                    key=key,
                    name_cn=clean_text(b.get("name_cn", "")),
                    name_en=clean_text(b.get("name_en", "")),
                    detail=detail,
                    prereq=clean_text(b.get("prereq", "")),
                    flavor="",
                    page=page,
                    parser="structured",
                )
            )

        # Longform parser (works well for stamina/feat prose pages)
        parsed = _parse_uc_page_651(lines)
        for key, b in parsed.items():
            detail = clean_text(b.get("benefit", ""))
            if len(detail) < 30:
                continue
            out[key].append(
                Candidate(
                    key=key,
                    name_cn=clean_text(b.get("name_cn", "")),
                    name_en=clean_text(b.get("name_en", "")),
                    detail=detail,
                    prereq=clean_text(b.get("prereq", "")),
                    flavor=clean_text(b.get("flavor", "")),
                    page=page,
                    parser="longform",
                )
            )

    # De-dup by (page, detail) per key
    dedup: dict[str, list[Candidate]] = {}
    for k, arr in out.items():
        seen: set[tuple[str, str]] = set()
        keep: list[Candidate] = []
        for c in arr:
            sig = (c.page, c.detail)
            if sig in seen:
                continue
            seen.add(sig)
            keep.append(c)
        dedup[k] = keep
    return dedup


def row_cand_score(row: dict[str, Any], cand: Candidate) -> int:
    score = text_quality(cand.detail)
    row_cn = cn_core(row.get("name_cn", ""))
    cand_cn = cn_core(cand.name_cn)
    row_m = mythic_flag(row.get("name_cn", ""), row.get("name_en", ""))
    cand_m = mythic_flag(cand.name_cn, cand.name_en)

    if row_m == cand_m:
        score += 30
    else:
        score -= 120

    if row_cn and cand_cn:
        if row_cn == cand_cn:
            score += 120
        elif (row_cn in cand_cn) or (cand_cn in row_cn):
            score += 55
        else:
            score -= 140

    row_en = normalize_ws(row.get("name_en", "")).lower()
    cand_en = normalize_ws(cand.name_en).lower()
    if row_en and cand_en:
        if row_en == cand_en:
            score += 35
        elif (row_en in cand_en) or (cand_en in row_en):
            score += 12

    # Page priors
    if cand.page.lower() in SUMMARY_PAGES:
        score -= 60
    if cand.page.lower() in {"page_623.html", "page_624.html"} and (not row_m):
        score -= 200

    score += overlap_score(row.get("benefit_summary", ""), cand.detail)
    return score


def main() -> None:
    parser = argparse.ArgumentParser(description="Unified feat detail extraction/backfill from CHM pages.")
    parser.add_argument("--book-feats", type=Path, default=IN_BOOK_FEATS)
    parser.add_argument("--viewer", type=Path, default=IN_VIEWER)
    parser.add_argument("--inplace", action="store_true")
    parser.add_argument("--min-score", type=int, default=120)
    parser.add_argument("--min-growth", type=int, default=10)
    parser.add_argument("--report", type=Path, default=OUT_REPORT)
    args = parser.parse_args()

    data: dict[str, list[dict[str, Any]]] = json.loads(args.book_feats.read_text(encoding="utf-8"))
    cand_map = collect_candidates(args.viewer)

    updated = 0
    by_book: dict[str, int] = defaultdict(int)
    by_page: dict[str, int] = defaultdict(int)
    samples: list[dict[str, Any]] = []

    for book, rows in data.items():
        if not isinstance(rows, list):
            continue
        for row in rows:
            key = normalize_ws(row.get("match_key", ""))
            if not key:
                continue
            old = clean_text(row.get("detail_text", ""))
            if not is_short_detail(old):
                continue

            cands = cand_map.get(key, [])
            if not cands:
                continue

            best = None
            best_score = -10**9
            for c in cands:
                s = row_cand_score(row, c)
                if s > best_score:
                    best_score = s
                    best = c
            if not best:
                continue

            new_detail = clean_text(best.detail)
            if len(new_detail) < max(35, len(old) + args.min_growth):
                continue
            if best_score < args.min_score:
                continue

            changed = False
            row["detail_text"] = new_detail
            changed = True
            if (not clean_text(row.get("name_cn", ""))) and best.name_cn:
                row["name_cn"] = best.name_cn
                if not clean_text(row.get("name_raw", "")) or clean_text(row.get("name_raw", "")) == clean_text(row.get("name_en", "")):
                    row["name_raw"] = f"{best.name_cn} ({row.get('name_en') or best.name_en})"
            if best.prereq and len(best.prereq) > len(clean_text(row.get("prerequisites", ""))):
                row["prerequisites"] = best.prereq
            if (not clean_text(row.get("flavor_text", ""))) and best.flavor:
                row["flavor_text"] = best.flavor

            if changed:
                sp = row.get("source_pages") or []
                marker = {
                    "local": best.page,
                    "toc_path": "unified_detail_backfill",
                    "table_index": -103,
                    "row_index": -1,
                }
                if marker not in sp:
                    sp.append(marker)
                row["source_pages"] = sp
                updated += 1
                by_book[book] += 1
                by_page[best.page] += 1
                if len(samples) < 120:
                    samples.append(
                        {
                            "book": book,
                            "key": key,
                            "name_cn": row.get("name_cn", ""),
                            "old_len": len(old),
                            "new_len": len(new_detail),
                            "score": best_score,
                            "page": best.page,
                            "parser": best.parser,
                        }
                    )

    out_path = args.book_feats if args.inplace else args.book_feats.with_name("feat-book-feats-unified-backfill.json")
    out_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    report = {
        "updated_rows": updated,
        "candidate_key_count": len(cand_map),
        "min_score": args.min_score,
        "min_growth": args.min_growth,
        "by_book": dict(sorted(by_book.items(), key=lambda x: (-x[1], x[0]))),
        "by_page": dict(sorted(by_page.items(), key=lambda x: (-x[1], x[0]))),
        "samples": samples,
        "output": str(out_path),
    }
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print("Done.")
    print(f"Candidate keys: {len(cand_map)}")
    print(f"Updated rows: {updated}")
    for b, n in sorted(by_book.items(), key=lambda x: (-x[1], x[0])):
        print(f"{b}: +{n}")
    print(f"Output: {out_path}")
    print(f"Report: {args.report}")


if __name__ == "__main__":
    main()