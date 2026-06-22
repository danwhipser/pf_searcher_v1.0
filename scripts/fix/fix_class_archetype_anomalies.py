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

sys.path.append(str(Path(__file__).resolve().parent))
from scripts.fix.patch_vigilante_archetypes import regenerate_report, regenerate_summary  # noqa: E402


ROOT = Path(__file__).resolve().parents[2]
VIEWER_PATH = ROOT / "result" / "Pathfinder-v2.14-SC-viewer-embedded.html"
CLASSES_PATH = ROOT / "result" / "classes" / "classes-extracted.json"
REPORT_PATH = ROOT / "result" / "classes" / "classes-extraction-report.json"


def normalize_ws(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").replace("\r", " ").replace("\n", " ")).strip()


def load_pages() -> dict[str, str]:
    text = VIEWER_PATH.read_text(encoding="utf-8")
    match = re.search(r'<script id="pages-data" type="application/json">(.*?)</script>', text, re.S)
    if not match:
        raise RuntimeError(f"pages-data not found: {VIEWER_PATH}")
    return json.loads(match.group(1))


def page_lines(page_html: str) -> list[str]:
    soup = BeautifulSoup(page_html, "html.parser")
    lines = [normalize_ws(x) for x in soup.get_text("\n", strip=True).split("\n")]
    return [x for x in lines if x]


def join_body(lines: list[str]) -> str:
    text = normalize_ws(" ".join(x for x in lines if x))
    text = text.replace("（ ", "（").replace(" ）", "）")
    text = text.replace("+ ", "+").replace(" +", "+")
    text = text.replace("- ", "-").replace(" -", "-")
    return text


def extract_replaces(text: str) -> list[str]:
    result = []
    for pattern in [
        r"本能力取代([^。；;]+)",
        r"本能力调整了?([^。；;]+)",
        r"替换([^。；;]+)",
    ]:
        for match in re.findall(pattern, text):
            value = normalize_ws(match).strip(" 。；;")
            if value:
                result.append(value)
    return sorted(set(result))


def find_line(lines: list[str], value: str, start: int = 0) -> int:
    for idx in range(start, len(lines)):
        if lines[idx] == value:
            return idx
    raise RuntimeError(f"Line not found: {value}")


def parse_inspired_chemist() -> dict[str, Any]:
    lines = page_lines(load_pages()["page_21.html"])
    start = find_line(lines, "启蒙化学家")
    if lines[start + 1] != "Inspired Chemist":
        raise RuntimeError("Inspired Chemist heading shape changed")
    end = find_line(lines, "古墓破坏者（Crypt Breaker）【炼金术士变体】【ISM】", start)

    headers = [
        {"name_cn": "灵感神药", "name_en": "Inspiring Cognatogen", "ability_type": "Su"},
        {"name_cn": "奖励专长", "name_en": "", "ability_type": ""},
        {"name_cn": "奖励调查员天赋", "name_en": "", "ability_type": ""},
        {"name_cn": "额外语言", "name_en": "", "ability_type": ""},
        {"name_cn": "炼金术士发现", "name_en": "", "ability_type": ""},
    ]
    header_positions = [(find_line(lines, h["name_cn"], start), h) for h in headers]
    description = join_body(lines[start + 2 : header_positions[0][0]])

    features = []
    for pos, (header_idx, header) in enumerate(header_positions):
        next_header_idx = header_positions[pos + 1][0] if pos + 1 < len(header_positions) else end
        if header["name_en"]:
            content_start = header_idx + 6
            name = f"{header['name_cn']} {header['name_en']}（{header['ability_type']}）"
        else:
            content_start = header_idx + 2
            name = header["name_cn"]
        text = join_body(lines[content_start:next_header_idx])
        features.append(
            {
                "name": name,
                "name_cn": header["name_cn"],
                "name_en": header["name_en"],
                "ability_type": header["ability_type"],
                "text": text,
                "replaces": extract_replaces(text),
            }
        )

    return {
        "name_raw": "启蒙化学家 Inspired Chemist",
        "name_cn": "启蒙化学家",
        "name_en": "Inspired Chemist",
        "description": description,
        "features": features,
        "tables": [],
        "archetype_id": "archetype-alchemist-page69html-inspiredchemist",
        "parent_class": {
            "class_id": "class-alchemist-page69html",
            "name_cn": "炼金术师",
            "name_en": "Alchemist",
        },
        "source_book": "进阶职业手册（ ACG ）",
        "source_page": "page_21.html",
        "summary_name_raw": "启蒙化学家（ Inspired Chemist ）",
    }


def parse_crypt_breaker() -> dict[str, Any]:
    lines = page_lines(load_pages()["page_21.html"])
    start = find_line(lines, "古墓破坏者（Crypt Breaker）【炼金术士变体】【ISM】")
    end = find_line(lines, "欧诺皮恩研究员（Oenopion", start)
    headers = [
        {
            "name_cn": "万能溶剂炸弹",
            "name_en": "Alkahest Bombs",
            "ability_type": "Su",
            "line": "万能溶剂炸弹（Alkahest",
            "skip": 2,
        },
        {
            "name_cn": "古墓破坏者药剂",
            "name_en": "Crypt Breaker's Draught",
            "ability_type": "Su",
            "line": "古墓破坏者药剂（Crypt Breaker's",
            "skip": 2,
        },
        {
            "name_cn": "寻找陷阱",
            "name_en": "Trapfinding",
            "ability_type": "",
            "line": "寻找陷阱（Trapfinding）：",
            "skip": 1,
        },
        {
            "name_cn": "科研发现",
            "name_en": "Discoveries",
            "ability_type": "",
            "line": "科研发现（Discoveries）：",
            "skip": 1,
        },
        {
            "name_cn": "强化万能溶剂",
            "name_en": "Enhanced Alkahest",
            "ability_type": "Su",
            "line": "强化万能溶剂（Enhanced",
            "skip": 2,
        },
    ]
    positions = [(find_line(lines, h["line"], start), h) for h in headers]
    description = join_body(lines[start + 1 : positions[0][0]])
    features = []
    for pos, (header_idx, header) in enumerate(positions):
        next_header_idx = positions[pos + 1][0] if pos + 1 < len(positions) else end
        content_start = header_idx + header["skip"]
        name = f"{header['name_cn']} {header['name_en']}".strip()
        if header["ability_type"]:
            name = f"{name}（{header['ability_type']}）"
        text = join_body(lines[content_start:next_header_idx])
        features.append(
            {
                "name": name,
                "name_cn": header["name_cn"],
                "name_en": header["name_en"],
                "ability_type": header["ability_type"],
                "text": text,
                "replaces": extract_replaces(text),
            }
        )
    return {
        "name_raw": "古墓破坏者（Crypt Breaker）【炼金术士变体】【ISM】",
        "name_cn": "古墓破坏者",
        "name_en": "Crypt Breaker",
        "description": description,
        "features": features,
        "tables": [],
        "archetype_id": "archetype-alchemist-page69html-cryptbreaker",
        "parent_class": {
            "class_id": "class-alchemist-page69html",
            "name_cn": "炼金术师",
            "name_en": "Alchemist",
        },
        "source_book": "内海战斗（ ISM ）",
        "source_page": "page_21.html",
        "summary_name_raw": "古墓破坏者（ Crypt Breaker ）",
    }


def is_inspired_chemist(archetype: dict[str, Any]) -> bool:
    parent_en = archetype.get("parent_class", {}).get("name_en", "")
    haystack = " ".join(
        [
            archetype.get("name_raw", ""),
            archetype.get("name_cn", ""),
            archetype.get("name_en", ""),
        ]
    )
    return parent_en == "Alchemist" and (
        "Inspired Chemist" in haystack or "启蒙化学家" in haystack
    )


def is_crypt_breaker(archetype: dict[str, Any]) -> bool:
    parent_en = archetype.get("parent_class", {}).get("name_en", "")
    haystack = " ".join(
        [
            archetype.get("name_raw", ""),
            archetype.get("name_cn", ""),
            archetype.get("name_en", ""),
        ]
    )
    return parent_en == "Alchemist" and (
        "Crypt Breaker" in haystack or "古墓破坏者" in haystack
    )


def fill_missing_source_book(archetype: dict[str, Any]) -> None:
    if archetype.get("source_book"):
        return
    parent_en = archetype.get("parent_class", {}).get("name_en", "")
    name_raw = archetype.get("name_raw", "")
    name_cn = archetype.get("name_cn", "")
    name_en = archetype.get("name_en", "")
    haystack = " ".join([name_raw, name_cn, name_en])

    source = ""
    if parent_en == "Gunslinger" and ("Bushwacker" in haystack or "游击队员" in haystack):
        source = "进阶种族手册（ APG ）"
    elif parent_en == "Magus" and ("Kapenia" in haystack or "卡潘妮亚舞者" in haystack):
        source = "瓦瑞西亚，传说诞生之地（ VBoL ）"
    elif parent_en == "Summoner" and ("First Worlder" in haystack or "原初之民" in haystack):
        source = "掉链子（Unchained）"
    elif parent_en == "Kineticist" and ("Elemental ascetic" in haystack or "元素行者" in haystack):
        source = "异能冒险（ OA ）"
    if source:
        archetype["source_book"] = source


def should_remove_archetype(archetype: dict[str, Any]) -> bool:
    parent = archetype.get("parent_class", {})
    parent_en = parent.get("name_en", "")
    parent_cn = parent.get("name_cn", "")
    name_cn = archetype.get("name_cn", "")
    name_en = archetype.get("name_en", "")

    if parent_en == "Fighter" and name_en == "Corsair" and not archetype.get("features") and not archetype.get("description"):
        return True
    if parent_en == "Oracle" and name_cn == "引用":
        return True
    if parent_en == "Oracle" and name_en == "Black Blood of Orv":
        return True
    # Some older runs may have only Chinese parent names.
    if parent_cn == "先知" and name_cn == "引用":
        return True
    if parent_cn == "先知" and name_en == "Black Blood of Orv":
        return True
    return False


def place_crypt_breaker_after_inspired(archetypes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    crypt_items = [item for item in archetypes if is_crypt_breaker(item)]
    if not crypt_items:
        return archetypes
    rest = [item for item in archetypes if not is_crypt_breaker(item)]
    insert_idx = next(
        (idx + 1 for idx, item in enumerate(rest) if is_inspired_chemist(item)),
        len(rest),
    )
    return rest[:insert_idx] + crypt_items[:1] + rest[insert_idx:]


def main() -> None:
    data = json.loads(CLASSES_PATH.read_text(encoding="utf-8"))
    inspired = parse_inspired_chemist()
    crypt_breaker = parse_crypt_breaker()
    removed = []
    replaced = False
    crypt_exists = False
    new_archetypes = []
    inspired_added = False

    for archetype in data.get("archetypes", []):
        if should_remove_archetype(archetype):
            removed.append(archetype.get("name_raw", ""))
            continue
        if is_inspired_chemist(archetype):
            if not inspired_added:
                new_archetypes.append(inspired)
                inspired_added = True
            replaced = True
            continue
        if is_crypt_breaker(archetype):
            new_archetypes.append(crypt_breaker)
            crypt_exists = True
            continue
        fill_missing_source_book(archetype)
        new_archetypes.append(archetype)

    if not replaced:
        new_archetypes.append(inspired)
    if not crypt_exists:
        insert_idx = next(
            (idx + 1 for idx, item in enumerate(new_archetypes) if is_inspired_chemist(item)),
            len(new_archetypes),
        )
        new_archetypes.insert(insert_idx, crypt_breaker)
    data["archetypes"] = place_crypt_breaker_after_inspired(new_archetypes)

    for page in data.get("archetype_pages", []):
        archetypes = page.get("archetypes")
        if not isinstance(archetypes, list):
            continue
        patched = []
        page_replaced = False
        page_inspired_added = False
        page_crypt_exists = False
        for archetype in archetypes:
            if should_remove_archetype(archetype):
                continue
            if is_inspired_chemist(archetype):
                if not page_inspired_added:
                    patched.append(inspired)
                    page_inspired_added = True
                page_replaced = True
                continue
            if is_crypt_breaker(archetype):
                patched.append(crypt_breaker)
                page_crypt_exists = True
                continue
            fill_missing_source_book(archetype)
            patched.append(archetype)
        if page.get("source_page") == "page_21.html" and not page_replaced:
            patched.append(inspired)
        if page.get("source_page") == "page_21.html" and not page_crypt_exists:
            insert_idx = next(
                (idx + 1 for idx, item in enumerate(patched) if is_inspired_chemist(item)),
                len(patched),
            )
            patched.insert(insert_idx, crypt_breaker)
        page["archetypes"] = place_crypt_breaker_after_inspired(patched)

    data.setdefault("meta", {})["archetype_count"] = len(data["archetypes"])
    data.setdefault("meta", {})["archetype_page_count"] = len(data.get("archetype_pages", []))
    CLASSES_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    report = regenerate_report(data)
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    regenerate_summary(data, report)
    print(
        json.dumps(
            {
                "removed": removed,
                "inspired_chemist_features": len(inspired["features"]),
                "crypt_breaker_features": len(crypt_breaker["features"]),
                "archetype_count": len(data["archetypes"]),
                "archetypes_without_features": len(report["archetypes_without_features"]),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()