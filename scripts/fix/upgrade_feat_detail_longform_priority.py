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

from scripts.extract.extract_feats_and_verify import load_embedded_pages, normalize_ws
from scripts.backfill.backfill_feat_detail_from_chm_structured_blocks import parse_page_blocks

IN_BOOK_FEATS = ROOT / "result" / "feats" / "feat-book-feats.json"
IN_VIEWER = ROOT / "result" / "Pathfinder-v2.14-SC-viewer-embedded.html"
IN_SUSPECTS = ROOT / "result" / "feats" / "parse_error_suspects_high_confidence_latest.json"
OUT_REPORT = ROOT / "result" / "feats" / "detail_upgrade_longform_priority_report.json"


def has_cn(text: str) -> bool:
    return bool(re.search(r"[\u4e00-\u9fff]", text or ""))


def clean_text(text: str) -> str:
    t = normalize_ws(text or "")
    t = re.sub(r"^[：:\-)\]）\s]+", "", t)
    # Trim leaked AoN-like tail.
    t = re.sub(r"\s+Source\s+[A-Za-z].*$", "", t)
    # Trim leaked next feat header patterns like "骇人饰物(造物) Source ..."
    t = re.sub(r"\s+[\u4e00-\u9fff]{2,20}\([^)]{1,12}\)\s*$", "", t)
    # Trim leaked next feat headers like "蛇形拳（ Gorgon's Fist ）〔战斗〕 ..."
    t = re.sub(r"\s+[\u4e00-\u9fff]{2,20}（\s*[A-Za-z][^。]{0,120}?〔[^〕]{1,12}〕.*$", "", t)
    return t


def cn_core(name_cn: str) -> str:
    s = normalize_ws(name_cn or "")
    s = re.sub(r"[（(].*?$", "", s)
    s = re.sub(r"[\s【】\[\]<>《》“”\"'`·•]+", "", s)
    return s


def mythic_flag(name_cn: str, name_en: str) -> bool:
    s = f"{normalize_ws(name_cn)} {normalize_ws(name_en)}".lower()
    return ("神话" in s) or ("mythic" in s)


def is_noise_text(text: str) -> bool:
    t = normalize_ws(text or "")
    if not t:
        return True
    if "|" in t:
        return True
    if len(t) <= 8:
        return True
    if re.search(r"(战斗|团队|超魔|造物)\s*\|\s*", t):
        return True
    if re.search(r"^\+?\d+\s*\|\s*", t):
        return True
    if "专长一览" in t:
        return True
    return False


def text_quality(text: str) -> int:
    t = normalize_ws(text or "")
    if not t:
        return -10**9
    score = len(t)
    if has_cn(t):
        score += 30
    if re.search(r"[。；，：]", t):
        score += 20
    if "|" in t:
        score -= 80
    if len(t) > 480:
        score -= 60
    return score


def row_match_score(row: dict[str, Any], cand_name_cn: str, cand_name_en: str) -> int:
    score = 0
    r_core = cn_core(row.get("name_cn", ""))
    c_core = cn_core(cand_name_cn)
    r_m = mythic_flag(row.get("name_cn", ""), row.get("name_en", ""))
    c_m = mythic_flag(cand_name_cn, cand_name_en)

    if r_m == c_m:
        score += 30
    else:
        score -= 30

    if r_core and c_core:
        if r_core == c_core:
            score += 120
        elif (r_core in c_core) or (c_core in r_core):
            score += 60
        else:
            score -= 100
    elif r_core and not c_core:
        score -= 35

    if cand_name_en and normalize_ws(cand_name_en).lower() == normalize_ws(row.get("name_en", "")).lower():
        score += 25
    return score


def overlap_score(row: dict[str, Any], cand_text: str) -> int:
    benefit = normalize_ws(row.get("benefit_summary", ""))
    if not benefit:
        return 0
    chunks = re.findall(r"[\u4e00-\u9fff]{2,20}", benefit)
    if not chunks:
        return 0
    bgrams: set[str] = set()
    for ch in chunks:
        if len(ch) < 2:
            continue
        for i in range(len(ch) - 1):
            bgrams.add(ch[i : i + 2])
    if not bgrams:
        return 0
    hits = sum(1 for t in bgrams if t in cand_text)
    if hits >= 4:
        return 45
    if hits >= 2:
        return 20
    if len(cand_text) >= 40:
        return -45
    return -10


def collect_structured_candidates(viewer_path: Path, key_set: set[str]) -> dict[str, list[dict[str, str]]]:
    pages = load_embedded_pages(viewer_path)
    out: dict[str, list[dict[str, str]]] = {}
    for local, html in pages.items():
        soup = BeautifulSoup(html, "html.parser")
        lines = [normalize_ws(x) for x in soup.get_text("\n", strip=True).splitlines() if normalize_ws(x)]
        if not lines:
            continue
        for b in parse_page_blocks(lines):
            k = normalize_ws(b.get("key", ""))
            if not k or k not in key_set:
                continue
            out.setdefault(k, []).append(
                {
                    "name_cn": normalize_ws(b.get("name_cn", "")),
                    "name_en": normalize_ws(b.get("name_en", "")),
                    "text": clean_text(b.get("benefit", "")) if len(clean_text(b.get("benefit", ""))) >= len(clean_text(b.get("detail", ""))) else clean_text(b.get("detail", "")),
                    "source": f"structured:{local}",
                }
            )
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Upgrade summary-like feat details to long-form detail when available.")
    parser.add_argument("--book-feats", type=Path, default=IN_BOOK_FEATS)
    parser.add_argument("--viewer", type=Path, default=IN_VIEWER)
    parser.add_argument("--suspects", type=Path, default=IN_SUSPECTS)
    parser.add_argument("--report", type=Path, default=OUT_REPORT)
    parser.add_argument("--inplace", action="store_true")
    args = parser.parse_args()

    data: dict[str, list[dict[str, Any]]] = json.loads(args.book_feats.read_text(encoding="utf-8"))
    suspects = json.loads(args.suspects.read_text(encoding="utf-8"))
    target_keys = {
        it.get("key", "")
        for it in suspects.get("items", [])
        if "too_short_vs_benefit" in (it.get("reasons") or [])
    }
    target_keys = {k for k in target_keys if k}

    # Collect current rows by key and row-level candidates.
    key_rows: dict[str, list[dict[str, Any]]] = {}
    row_candidates: dict[str, list[dict[str, str]]] = {}
    for book, rows in data.items():
        for r in rows:
            k = normalize_ws(r.get("match_key", ""))
            if k not in target_keys:
                continue
            key_rows.setdefault(k, []).append(r)
            for field in ("detail_text", "benefit_summary"):
                txt = clean_text(r.get(field, ""))
                if is_noise_text(txt):
                    continue
                row_candidates.setdefault(k, []).append(
                    {
                        "name_cn": normalize_ws(r.get("name_cn", "")),
                        "name_en": normalize_ws(r.get("name_en", "")),
                        "text": txt,
                        "source": f"row:{book}:{field}",
                    }
                )

    structured_candidates = collect_structured_candidates(args.viewer, target_keys)

    updated = 0
    report_items: list[dict[str, Any]] = []

    for key in sorted(target_keys):
        rows = key_rows.get(key, [])
        cands = (row_candidates.get(key, []) or []) + (structured_candidates.get(key, []) or [])
        # De-duplicate candidate text.
        uniq = {}
        for c in cands:
            t = normalize_ws(c.get("text", ""))
            if not t:
                continue
            if t not in uniq:
                uniq[t] = c
        cands = list(uniq.values())

        key_updates = 0
        row_reports: list[dict[str, Any]] = []
        for r in rows:
            old_detail = clean_text(r.get("detail_text", ""))
            best = None
            best_score = -10**9
            for c in cands:
                txt = clean_text(c.get("text", ""))
                if is_noise_text(txt):
                    continue
                s = text_quality(txt) + row_match_score(r, c.get("name_cn", ""), c.get("name_en", ""))
                s += overlap_score(r, txt)
                if s > best_score:
                    best_score = s
                    best = c

            changed = False
            if best:
                new_detail = clean_text(best.get("text", ""))
                current_score = text_quality(old_detail) + overlap_score(r, old_detail) + row_match_score(
                    r,
                    normalize_ws(r.get("name_cn", "")),
                    normalize_ws(r.get("name_en", "")),
                )
                force_repair = any(
                    (sp.get("toc_path") == "detail_longform_priority_upgrade")
                    for sp in (r.get("source_pages") or [])
                )
                longer_enough = len(new_detail) >= len(old_detail) + 12
                much_better = best_score >= current_score + 20
                if (
                    has_cn(new_detail)
                    and best_score >= 80
                    and new_detail != old_detail
                    and (longer_enough or (force_repair and much_better))
                ):
                    r["detail_text"] = new_detail
                    sp = r.get("source_pages") or []
                    source = best.get("source", "")
                    local = source.split("structured:", 1)[1] if source.startswith("structured:") else ""
                    sp.append(
                        {
                            "local": local,
                            "toc_path": "detail_longform_priority_upgrade",
                            "table_index": -98,
                            "row_index": -1,
                        }
                    )
                    r["source_pages"] = sp
                    changed = True
                    key_updates += 1
                    updated += 1

            row_reports.append(
                {
                    "book": r.get("book_source", ""),
                    "name_cn": r.get("name_cn", ""),
                    "name_en": r.get("name_en", ""),
                    "old_len": len(old_detail),
                    "new_len": len(clean_text(r.get("detail_text", ""))),
                    "changed": changed,
                }
            )

        # Post-fix: cleanup and rollback long text that clearly mismatches this row's own benefit semantics.
        for r in rows:
            cur = clean_text(r.get("detail_text", ""))
            ben = clean_text(r.get("benefit_summary", ""))
            if cur != normalize_ws(r.get("detail_text", "")):
                r["detail_text"] = cur
            if not cur or not ben:
                continue
            if len(cur) < 40:
                continue
            if overlap_score(r, cur) <= -40 and has_cn(ben):
                r["detail_text"] = ben

        report_items.append(
            {
                "key": key,
                "rows": len(rows),
                "updates": key_updates,
                "candidates": len(cands),
                "status": "upgraded" if key_updates > 0 else "no_longer_source_found",
                "row_reports": row_reports,
            }
        )

    out_path = args.book_feats if args.inplace else args.book_feats.with_name("feat-book-feats-longform-priority.json")
    out_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    report = {
        "target_keys": len(target_keys),
        "updated_rows": updated,
        "output": str(out_path),
        "items": report_items,
    }
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print("Done.")
    print(f"Target keys: {len(target_keys)}")
    print(f"Updated rows: {updated}")
    print(f"Output: {out_path}")
    print(f"Report: {args.report}")


if __name__ == "__main__":
    main()