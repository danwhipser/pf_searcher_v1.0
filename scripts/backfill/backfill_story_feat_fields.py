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

from scripts.extract.extract_feats_and_verify import (  # noqa: E402
    canonicalize_en_name,
    load_embedded_pages,
    normalize_key,
    normalize_ws,
)

IN_BOOK_FEATS = ROOT / "result" / "feats" / "feat-book-feats.json"
IN_VIEWER = ROOT / "result" / "Pathfinder-v2.14-SC-viewer-embedded.html"
OUT_REPORT = ROOT / "result" / "feats" / "story-feat-backfill-report.json"

# Use unicode escapes to avoid terminal/codepage mojibake.
LABEL_PREREQ = "\u5148\u51b3\u6761\u4ef6"  # 先决条件
LABEL_IMMEDIATE = "\u5373\u65f6\u6536\u76ca"  # 即时收益
LABEL_GOAL = "\u4e13\u957f\u76ee\u6807"  # 专长目标
LABEL_COMPLETION = "\u5b8c\u6210\u6536\u76ca"  # 完成收益
SECTION_LABELS = [LABEL_PREREQ, LABEL_IMMEDIATE, LABEL_GOAL, LABEL_COMPLETION]


def _match_story_start(lines: list[str], i: int) -> tuple[bool, str, int]:
    if i + 2 >= len(lines):
        return False, "", -1
    cn = normalize_ws(lines[i])
    if not cn.endswith("\uff08"):  # （
        return False, "", -1

    en_parts: list[str] = []
    for j in range(i + 1, min(i + 8, len(lines))):
        cur = normalize_ws(lines[j])
        has_story_tag = ("\u3014\u6545\u4e8b\u3015" in cur) or ("\u3010\u6545\u4e8b\u3011" in cur)
        if has_story_tag and ("\uff09" in cur):
            en_name = canonicalize_en_name(" ".join(en_parts))
            if normalize_key(en_name):
                return True, en_name, j
            return False, "", -1
        if re.fullmatch(r"[A-Za-z][A-Za-z0-9'`!/+&.,:\-\s]+", cur):
            en_parts.append(cur)
            continue
        break
    return False, "", -1


def _next_story_start(lines: list[str], begin: int) -> int:
    for i in range(begin, len(lines) - 2):
        ok, _, _ = _match_story_start(lines, i)
        if ok:
            return i
    return len(lines)


def _clean_colon_prefix(text: str) -> str:
    t = normalize_ws(text)
    t = re.sub(r"^[\uff1a:\s]+", "", t)  # ： or :
    return normalize_ws(t)


def _collect_label_value(lines: list[str], start_idx: int, label: str, stop_idx: int) -> tuple[str, int]:
    s = normalize_ws(lines[start_idx])
    rest = _clean_colon_prefix(s[len(label) :]) if s.startswith(label) else ""
    parts: list[str] = [rest] if rest else []
    i = start_idx + 1
    while i < stop_idx:
        cur = normalize_ws(lines[i])
        if cur in SECTION_LABELS:
            break
        if any(cur.startswith(x) for x in SECTION_LABELS):
            break
        parts.append(_clean_colon_prefix(cur))
        i += 1
    value = normalize_ws(" ".join(x for x in parts if x))
    return value, i


def parse_story_feats_from_page(html: str) -> dict[str, dict[str, str]]:
    lines = [normalize_ws(x) for x in BeautifulSoup(html, "html.parser").get_text("\n").splitlines() if normalize_ws(x)]
    out: dict[str, dict[str, str]] = {}
    i = 0
    while i < len(lines) - 2:
        ok, parsed_en_name, story_tag_idx = _match_story_start(lines, i)
        if not ok:
            i += 1
            continue
        name_cn = normalize_ws(lines[i][:-1])
        name_en = parsed_en_name
        key = normalize_key(name_en)
        block_end = _next_story_start(lines, story_tag_idx + 1)

        j = story_tag_idx + 1
        flavor_parts: list[str] = []
        while j < block_end:
            cur = normalize_ws(lines[j])
            if cur == LABEL_PREREQ or cur.startswith(LABEL_PREREQ):
                break
            flavor_parts.append(cur)
            j += 1
        flavor_text = normalize_ws(" ".join(flavor_parts))

        story_prerequisites = ""
        immediate_benefit = ""
        story_goal = ""
        completion_benefit = ""

        while j < block_end:
            cur = normalize_ws(lines[j])
            if cur == LABEL_PREREQ or cur.startswith(LABEL_PREREQ):
                story_prerequisites, j = _collect_label_value(lines, j, LABEL_PREREQ, block_end)
                continue
            if cur == LABEL_IMMEDIATE or cur.startswith(LABEL_IMMEDIATE):
                immediate_benefit, j = _collect_label_value(lines, j, LABEL_IMMEDIATE, block_end)
                continue
            if cur == LABEL_GOAL or cur.startswith(LABEL_GOAL):
                story_goal, j = _collect_label_value(lines, j, LABEL_GOAL, block_end)
                continue
            if cur == LABEL_COMPLETION or cur.startswith(LABEL_COMPLETION):
                completion_benefit, j = _collect_label_value(lines, j, LABEL_COMPLETION, block_end)
                continue
            j += 1

        if key:
            out[key] = {
                "name_en": name_en,
                "name_cn": name_cn,
                "flavor_text": flavor_text,
                "story_prerequisites": story_prerequisites,
                "immediate_benefit": immediate_benefit,
                "story_goal": story_goal,
                "completion_benefit": completion_benefit,
            }
        i = block_end
    return out


def _should_replace_prereq(old: str, new: str) -> bool:
    old_n = normalize_ws(old)
    if not new:
        return False
    if not old_n:
        return True
    if old_n in {"\u89c1\u4e0b\u6587", "\u89c1\u4e13\u957f\u8be6\u8ff0"}:  # 见下文, 见专长详述
        return True
    return len(new) > len(old_n)


def _should_replace_detail(old: str, new: str) -> bool:
    old_n = normalize_ws(old)
    if not new:
        return False
    if not old_n:
        return True
    if old_n in {"\u6545\u4e8b | UCa", "\u83b7\u5f97\u6cd5\u672f\u6297\u529b"}:  # 故事 | UCa, 获得法术抗力
        return True
    if len(old_n) < 40:
        return True
    return len(new) > len(old_n) * 1.2


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill story-feat structured fields from UCa story page.")
    parser.add_argument("--book-feats", type=Path, default=IN_BOOK_FEATS)
    parser.add_argument("--viewer", type=Path, default=IN_VIEWER)
    parser.add_argument("--page", default="page_200.html")
    parser.add_argument("--inplace", action="store_true")
    parser.add_argument("--output", type=Path, default=ROOT / "result" / "feats" / "feat-book-feats-story-updated.json")
    parser.add_argument("--report", type=Path, default=OUT_REPORT)
    args = parser.parse_args()

    data: dict[str, list[dict[str, Any]]] = json.loads(args.book_feats.read_text(encoding="utf-8"))
    pages = load_embedded_pages(args.viewer)
    if args.page not in pages:
        raise ValueError(f"Page not found in embedded viewer: {args.page}")

    story_map = parse_story_feats_from_page(pages[args.page])

    updated_rows = 0
    updated_keys: set[str] = set()
    touched_samples: list[dict[str, Any]] = []
    for book, rows in data.items():
        if not isinstance(rows, list):
            continue
        for row in rows:
            key = normalize_ws(row.get("match_key", ""))
            if not key or key not in story_map:
                continue
            s = story_map[key]

            changed = False
            if s["name_cn"] and not normalize_ws(row.get("name_cn", "")):
                row["name_cn"] = s["name_cn"]
                changed = True

            if _should_replace_prereq(row.get("prerequisites", ""), s["story_prerequisites"]):
                row["prerequisites"] = s["story_prerequisites"]
                changed = True

            if _should_replace_detail(row.get("detail_text", ""), s["immediate_benefit"]):
                row["detail_text"] = s["immediate_benefit"]
                changed = True

            row["flavor_text"] = s["flavor_text"]
            row["story_prerequisites"] = s["story_prerequisites"]
            row["immediate_benefit"] = s["immediate_benefit"]
            row["story_goal"] = s["story_goal"]
            row["completion_benefit"] = s["completion_benefit"]
            changed = True

            if changed:
                updated_rows += 1
                updated_keys.add(key)
                if len(touched_samples) < 20:
                    touched_samples.append(
                        {
                            "book": book,
                            "match_key": key,
                            "name_en": row.get("name_en", ""),
                            "name_cn": row.get("name_cn", ""),
                            "detail_len": len(normalize_ws(row.get("detail_text", ""))),
                        }
                    )

    out_path = args.book_feats if args.inplace else args.output
    out_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    report = {
        "source_page": args.page,
        "story_feat_keys_parsed": len(story_map),
        "updated_keys": len(updated_keys),
        "updated_rows": updated_rows,
        "samples": touched_samples,
        "accursed_preview": story_map.get("accursed", {}),
        "output": str(out_path),
    }
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print("Done.")
    print(f"Story blocks parsed: {len(story_map)}")
    print(f"Updated keys: {len(updated_keys)}")
    print(f"Updated rows: {updated_rows}")
    print(f"Output: {out_path}")
    print(f"Report: {args.report}")


if __name__ == "__main__":
    main()