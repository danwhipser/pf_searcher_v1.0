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
from dataclasses import dataclass
from typing import Any

from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.extract.extract_feats_and_verify import load_embedded_pages, normalize_ws

IN_BOOK_FEATS = ROOT / "result" / "feats" / "feat-book-feats.json"
IN_VIEWER = ROOT / "result" / "Pathfinder-v2.14-SC-viewer-embedded.html"
OUT_REPORT = ROOT / "result" / "feats" / "uc_page_lookup_backfill_report.json"

SUMMARY_PAGES = {f"page_{i}.html" for i in range(195, 204)}
SKIP_PAGE_NAME_PARTS = ("spell ", "专长一览", "专长概述")
# Mythic feat pages; exclude for UC base feat backfill to avoid cross-system contamination.
SKIP_EXPLICIT_PAGES = {"page_623.html", "page_624.html"}

PREREQ_LABELS = ("先决条件", "前置条件", "Prerequisite", "Prerequisites")
BENEFIT_LABELS = ("专长效果", "效果", "好处", "Benefit")
STOP_LABELS = ("战策", "通常情况", "通常状况", "特殊说明", "特别说明")


def has_cn(text: str) -> bool:
    return bool(re.search(r"[\u4e00-\u9fff]", text or ""))


def is_short_detail(text: str) -> bool:
    t = normalize_ws(text)
    return (not t) or len(t) <= 20


def clean_detail(text: str) -> str:
    t = normalize_ws(text)
    t = re.sub(r"^[：:\-\s]+", "", t)
    return normalize_ws(t)


def match_label(line: str, labels: tuple[str, ...]) -> tuple[bool, str]:
    s = normalize_ws(line)
    if not s:
        return False, ""
    sl = s.lower()
    for lb in labels:
        ll = lb.lower()
        if sl == ll:
            return True, ""
        if sl.startswith(ll):
            rest = normalize_ws(s[len(lb) :])
            rest = re.sub(r"^[：:\s]+", "", rest)
            return True, rest
    return False, ""


def likely_next_title(line: str) -> bool:
    s = normalize_ws(line)
    if not s:
        return False
    # CN(EN) classic title
    if re.search(r"[\u4e00-\u9fff]{2,24}\s*[（(].{1,80}[）)]", s):
        return True
    # very short CN token title line
    if re.fullmatch(r"[\u4e00-\u9fff·]{2,14}", s):
        return True
    return False


def looks_like_title_context(line: str, name_cn: str) -> bool:
    s = normalize_ws(line)
    if not s:
        return False
    if any(x in s for x in ("来源", "引述", "通常情况", "战策", "替换为", "适用于", "如果你", "每当你")):
        return False
    if s.endswith(("。", "！", "？")):
        return False
    if len(s) > 70:
        return False
    if name_cn and (name_cn in s):
        if s == name_cn:
            return True
        if s.startswith(name_cn) and len(s) <= len(name_cn) + 14:
            return True
        if ("（" in s) or ("(" in s):
            return True
        # Long prose sentence mentioning feat name is usually not the title.
        if ("，" in s) or ("。" in s) or len(s) > 30:
            return False
        return True
    if re.search(r"[\u4e00-\u9fff]{2,24}\s*[（(].{1,80}[）)]", s):
        return True
    if re.fullmatch(r"[\u4e00-\u9fff·]{2,16}", s):
        return True
    return False


def en_title_regex(name_en: str) -> re.Pattern[str] | None:
    words = [w for w in re.findall(r"[A-Za-z][A-Za-z'`-]*", name_en or "") if w]
    if not words:
        return None
    pat = r"\b" + r"\s+".join(map(re.escape, words)) + r"\b"
    return re.compile(pat, re.I)


@dataclass
class CandidateBlock:
    page: str
    hit_index: int
    prereq: str
    benefit: str
    flavor: str
    score: int


def find_hit_indexes(lines: list[str], name_cn: str, name_en: str) -> list[int]:
    idxs: list[int] = []
    cn = clean_detail(name_cn)
    en_re = en_title_regex(name_en)
    n = len(lines)

    for i, line in enumerate(lines):
        s = normalize_ws(line)
        if not s:
            continue
        hit = False
        if cn and cn in s:
            hit = True
        elif en_re:
            if en_re.search(s):
                hit = True
            elif i + 1 < n:
                combo = f"{s} {normalize_ws(lines[i+1])}"
                if en_re.search(combo):
                    hit = True
            elif i + 2 < n:
                combo = f"{s} {normalize_ws(lines[i+1])} {normalize_ws(lines[i+2])}"
                if en_re.search(combo):
                    hit = True
        if hit and looks_like_title_context(s, cn):
            idxs.append(i)
    # dedup close indexes
    out: list[int] = []
    for x in idxs:
        if not out or x - out[-1] > 3:
            out.append(x)
    return out


def parse_block_at(lines: list[str], hit_i: int) -> CandidateBlock | None:
    n = len(lines)
    # find prereq and benefit label after hit
    pr_i = -1
    be_i = -1
    pr_inline = ""
    be_inline = ""

    for j in range(hit_i, min(n, hit_i + 80)):
        line = lines[j]
        is_pr, pr_rest = match_label(line, PREREQ_LABELS)
        if is_pr and pr_i < 0:
            pr_i = j
            pr_inline = pr_rest
        is_be, be_rest = match_label(line, BENEFIT_LABELS)
        if is_be and be_i < 0:
            be_i = j
            be_inline = be_rest
        if pr_i >= 0 and be_i >= 0:
            break
    if be_i < 0:
        return None
    # Labels too far from title usually mean we matched a reference mention.
    if be_i - hit_i > 18:
        return None

    if pr_i >= 0 and pr_i < be_i:
        flavor = clean_detail(" ".join(lines[hit_i + 1 : pr_i]))
        pr_parts = [pr_inline] if pr_inline else []
        pr_parts.extend(lines[pr_i + 1 : be_i])
        prereq = clean_detail(" ".join(pr_parts))
    else:
        flavor = clean_detail(" ".join(lines[hit_i + 1 : be_i]))
        prereq = ""

    ben_parts = [be_inline] if be_inline else []
    for j in range(be_i + 1, min(n, be_i + 120)):
        s = normalize_ws(lines[j])
        if not s:
            continue
        if any(s.startswith(x) for x in STOP_LABELS):
            break
        is_pr2, _ = match_label(s, PREREQ_LABELS)
        is_be2, _ = match_label(s, BENEFIT_LABELS)
        if is_pr2 or is_be2:
            break
        if likely_next_title(s):
            # avoid crossing into next entry
            if j > be_i + 1:
                break
        ben_parts.append(s)

    benefit = clean_detail(" ".join(ben_parts))
    if len(benefit) < 30 or not has_cn(benefit):
        return None
    if any(x in benefit for x in ("来源", "引述")) and len(benefit) < 120:
        return None

    score = 0
    score += min(len(benefit), 500) // 5
    if prereq:
        score += 20
    if flavor:
        score += 5
    # prefer compact clean benefits
    if len(benefit) > 600:
        score -= 30

    return CandidateBlock(page="", hit_index=hit_i, prereq=prereq, benefit=benefit, flavor=flavor, score=score)


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill UC feat long detail by locating concrete pages and parsing blocks.")
    parser.add_argument("--book-feats", type=Path, default=IN_BOOK_FEATS)
    parser.add_argument("--viewer", type=Path, default=IN_VIEWER)
    parser.add_argument("--inplace", action="store_true")
    parser.add_argument("--output", type=Path, default=ROOT / "result" / "feats" / "feat-book-feats-uc-page-lookup.json")
    parser.add_argument("--report", type=Path, default=OUT_REPORT)
    args = parser.parse_args()

    data: dict[str, list[dict[str, Any]]] = json.loads(args.book_feats.read_text(encoding="utf-8"))
    uc_key = next((k for k in data.keys() if k.startswith("UC ")), "")
    if not uc_key:
        raise ValueError("UC book key not found.")

    pages = load_embedded_pages(args.viewer)
    page_lines: dict[str, list[str]] = {}
    page_blob: dict[str, str] = {}
    for pk, html in pages.items():
        pkl = pk.lower()
        if pkl in SUMMARY_PAGES:
            continue
        if pkl in SKIP_EXPLICIT_PAGES:
            continue
        if any(x in pkl for x in SKIP_PAGE_NAME_PARTS):
            continue
        lines = [normalize_ws(x) for x in BeautifulSoup(html, "html.parser").get_text("\n").splitlines() if normalize_ws(x)]
        if len(lines) < 20:
            continue
        page_lines[pk] = lines
        page_blob[pk] = " ".join(lines).lower()

    updated = 0
    untouched = 0
    no_candidate = 0
    report_rows: list[dict[str, Any]] = []

    for row in data[uc_key]:
        old_detail = clean_detail(row.get("detail_text", ""))
        if not is_short_detail(old_detail):
            continue
        k = normalize_ws(row.get("match_key", ""))
        name_cn = normalize_ws(row.get("name_cn", ""))
        name_en = normalize_ws(row.get("name_en", ""))
        if not (k or name_cn or name_en):
            continue

        best: CandidateBlock | None = None
        best_page = ""

        # Fast pre-filter to keep runtime bounded.
        candidates: list[str] = []
        en_words = [w.lower() for w in re.findall(r"[A-Za-z][A-Za-z'`-]*", name_en)]
        for pk, blob in page_blob.items():
            ok = False
            if name_cn and (name_cn in blob):
                ok = True
            if (not ok) and en_words:
                if en_words[0] in blob and (len(en_words) == 1 or en_words[1] in blob):
                    ok = True
            if ok:
                candidates.append(pk)
        if not candidates:
            no_candidate += 1
            continue
        if len(candidates) > 60:
            candidates = candidates[:60]

        for pk in candidates:
            lines = page_lines[pk]
            hits = find_hit_indexes(lines, name_cn, name_en)
            if not hits:
                continue
            for hi in hits:
                blk = parse_block_at(lines, hi)
                if not blk:
                    continue
                blk.page = pk
                # avoid using bare summary page row if parser accidentally finds it
                if blk.page in SUMMARY_PAGES:
                    continue
                if (best is None) or (blk.score > best.score):
                    best = blk
                    best_page = pk

        if best is None:
            no_candidate += 1
            continue

        changed = False
        if len(best.benefit) >= max(35, len(old_detail) + 8):
            row["detail_text"] = best.benefit
            changed = True
        if best.prereq and len(best.prereq) > len(clean_detail(row.get("prerequisites", ""))):
            row["prerequisites"] = best.prereq
            changed = True
        if (not clean_detail(row.get("flavor_text", ""))) and best.flavor:
            row["flavor_text"] = best.flavor
            changed = True

        if changed:
            sp = row.get("source_pages") or []
            marker = {
                "local": best_page,
                "toc_path": "uc_page_lookup_backfill",
                "table_index": -102,
                "row_index": best.hit_index,
            }
            if marker not in sp:
                sp.append(marker)
            row["source_pages"] = sp
            updated += 1
            report_rows.append(
                {
                    "key": k,
                    "name_cn": name_cn,
                    "page": best_page,
                    "old_len": len(old_detail),
                    "new_len": len(best.benefit),
                    "score": best.score,
                }
            )
        else:
            untouched += 1

    out_path = args.book_feats if args.inplace else args.output
    out_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    report = {
        "uc_book": uc_key,
        "updated_rows": updated,
        "untouched_rows": untouched,
        "no_candidate_rows": no_candidate,
        "scan_pages": len(page_lines),
        "samples": report_rows[:200],
        "output": str(out_path),
    }
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print("Done.")
    print(f"UC book: {uc_key}")
    print(f"Updated rows: {updated}")
    print(f"Untouched rows: {untouched}")
    print(f"No candidate rows: {no_candidate}")
    print(f"Scanned pages: {len(page_lines)}")
    print(f"Output: {out_path}")
    print(f"Report: {args.report}")


if __name__ == "__main__":
    main()