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

from scripts.extract.extract_feats_and_verify import canonicalize_en_name, load_embedded_pages, normalize_key, normalize_ws

IN_BOOK_FEATS = ROOT / "result" / "feats" / "feat-book-feats.json"
IN_VIEWER = ROOT / "result" / "Pathfinder-v2.14-SC-viewer-embedded.html"
OUT_REPORT = ROOT / "result" / "feats" / "ma_page624_longform_report.json"


def _lines(html: str) -> list[str]:
    return [normalize_ws(x) for x in BeautifulSoup(html, "html.parser").get_text("\n").splitlines() if normalize_ws(x)]


def _is_en_token(s: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z][A-Za-z0-9'`!/+&.,:\-\s]{1,40}", s or ""))


def _is_cn_title_like(s: str) -> bool:
    t = normalize_ws(s)
    if len(t) > 32:
        return False
    if "（" not in t or "）" not in t:
        return False
    if "：" in t or ":" in t:
        return False
    return bool(re.search(r"[\u4e00-\u9fff]", t))


def parse_page624_blocks(lines: list[str]) -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    n = len(lines)
    i = 0
    while i < n - 8:
        cn_title = lines[i]
        if not _is_cn_title_like(cn_title):
            i += 1
            continue

        # EN title on one or more lines, ending at "(Mythic)".
        en_parts: list[str] = []
        j = i + 1
        mythic_hit = False
        while j < min(n, i + 8):
            cur = lines[j]
            if cur == "(Mythic)":
                mythic_hit = True
                j += 1
                break
            if "(Mythic)" in cur:
                left = normalize_ws(cur.replace("(Mythic)", ""))
                if left and _is_en_token(left):
                    en_parts.append(left)
                    mythic_hit = True
                    j += 1
                    break
            if _is_en_token(cur):
                en_parts.append(cur)
                j += 1
                continue
            break
        if not mythic_hit or not en_parts:
            i += 1
            continue

        name_en = canonicalize_en_name(" ".join(en_parts))
        key = normalize_key(name_en)
        if not key:
            i += 1
            continue

        # flavor text until prereq/benefit label
        flavor_parts: list[str] = []
        while j < n and lines[j] not in {"先决条件", "前置条件", "好处", "专长效果", "效果"}:
            if _is_cn_title_like(lines[j]):
                break
            flavor_parts.append(lines[j])
            j += 1
        if j >= n:
            i += 1
            continue
        has_prereq = lines[j] in {"先决条件", "前置条件"}
        if has_prereq:
            j += 1

        prereq_parts: list[str] = []
        if has_prereq:
            while j < n and lines[j] not in {"好处", "专长效果", "效果"}:
                if _is_cn_title_like(lines[j]):
                    break
                prereq_parts.append(lines[j])
                j += 1
        if j >= n or lines[j] not in {"好处", "专长效果", "效果"}:
            i += 1
            continue
        j += 1

        benefit_parts: list[str] = []
        while j < n:
            cur = lines[j]
            if _is_cn_title_like(cur):
                break
            benefit_parts.append(cur)
            j += 1

        flavor = normalize_ws(" ".join(flavor_parts))
        prereq = normalize_ws(" ".join(prereq_parts))
        benefit = normalize_ws(" ".join(benefit_parts))
        if not benefit or len(benefit) < 12:
            i += 1
            continue

        out[key] = {
            "name_en": name_en,
            "name_cn": re.sub(r"（[^）]+）$", "", cn_title),
            "flavor": flavor,
            "prereq": prereq,
            "benefit": benefit,
        }
        i = j
    return out


def _is_short_or_summary_like(detail: str, benefit: str) -> bool:
    d = normalize_ws(detail)
    b = normalize_ws(benefit)
    if not d:
        return True
    if len(d) <= 24:
        return True
    if d == b:
        return True
    if "待补全" in d:
        return True
    return False


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill MA mythic feat long detail from page_624 blocks.")
    parser.add_argument("--book-feats", type=Path, default=IN_BOOK_FEATS)
    parser.add_argument("--viewer", type=Path, default=IN_VIEWER)
    parser.add_argument("--inplace", action="store_true")
    parser.add_argument("--output", type=Path, default=ROOT / "result" / "feats" / "feat-book-feats-ma-page624-longform.json")
    parser.add_argument("--report", type=Path, default=OUT_REPORT)
    args = parser.parse_args()

    data: dict[str, list[dict[str, Any]]] = json.loads(args.book_feats.read_text(encoding="utf-8"))
    pages = load_embedded_pages(args.viewer)
    if "page_624.html" not in pages:
        raise ValueError("page_624.html not found in embedded viewer")

    blocks = parse_page624_blocks(_lines(pages["page_624.html"]))
    ma_book = next((k for k in data.keys() if k.startswith("MA ")), "")
    if not ma_book:
        raise ValueError("MA book key not found in feat-book-feats.json")

    updated = 0
    updated_keys: set[str] = set()
    samples: list[dict[str, Any]] = []
    for row in data.get(ma_book, []):
        k = normalize_ws(row.get("match_key", ""))
        if not k:
            continue
        b = blocks.get(k)
        if not b:
            continue

        old_detail = normalize_ws(row.get("detail_text", ""))
        old_benefit = normalize_ws(row.get("benefit_summary", ""))
        new_detail = normalize_ws(b.get("benefit", ""))
        if len(new_detail) < 20:
            continue

        changed = False
        if _is_short_or_summary_like(old_detail, old_benefit) or len(new_detail) > int(len(old_detail) * 1.2):
            row["detail_text"] = new_detail
            changed = True

        new_pr = normalize_ws(b.get("prereq", ""))
        if new_pr and (not normalize_ws(row.get("prerequisites", "")) or len(new_pr) > len(normalize_ws(row.get("prerequisites", "")))):
            row["prerequisites"] = new_pr
            changed = True

        fl = normalize_ws(b.get("flavor", ""))
        if fl and not normalize_ws(row.get("flavor_text", "")):
            row["flavor_text"] = fl
            changed = True

        if changed:
            sp = row.get("source_pages") or []
            marker = {
                "local": "page_624.html",
                "toc_path": "ma_page624_longform",
                "table_index": -92,
                "row_index": -1,
            }
            if marker not in sp:
                sp.append(marker)
            row["source_pages"] = sp
            updated += 1
            updated_keys.add(k)
            if len(samples) < 30:
                samples.append(
                    {
                        "key": k,
                        "name": row.get("name_cn") or row.get("name_en"),
                        "old_len": len(old_detail),
                        "new_len": len(new_detail),
                    }
                )

    out_path = args.book_feats if args.inplace else args.output
    out_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    report = {
        "ma_book": ma_book,
        "page624_blocks": len(blocks),
        "updated_rows": updated,
        "updated_keys": len(updated_keys),
        "samples": samples,
        "output": str(out_path),
    }
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print("Done.")
    print(f"MA page_624 parsed blocks: {len(blocks)}")
    print(f"Updated rows: {updated}")
    print(f"Updated keys: {len(updated_keys)}")
    print(f"Output: {out_path}")
    print(f"Report: {args.report}")


if __name__ == "__main__":
    main()