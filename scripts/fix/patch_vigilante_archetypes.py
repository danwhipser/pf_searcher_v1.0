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
from collections import defaultdict
from typing import Any

from bs4 import BeautifulSoup


ROOT = Path(__file__).resolve().parents[2]
VIEWER_PATH = ROOT / "result" / "Pathfinder-v2.14-SC-viewer-embedded.html"
CLASSES_PATH = ROOT / "result" / "classes" / "classes-extracted.json"
REPORT_PATH = ROOT / "result" / "classes" / "classes-extraction-report.json"
SUMMARY_PATH = ROOT / "result" / "classes" / "classes-archetype-summary.md"

VIGILANTE_ARCHETYPE_PAGE = "page_346.html"
EXPECTED_ARCHETYPES = [
    "Brute",
    "Cabalist",
    "Gunmaster",
    "Magical Child",
    "Psychometrist",
    "Mounted Fury",
    "Warlock",
    "Wildsoul",
    "Zealot",
]


def normalize_ws(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").replace("\r", " ").replace("\n", " ")).strip()


def normalize_key(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (text or "").lower())


def has_cjk(text: str) -> bool:
    return bool(re.search(r"[\u4e00-\u9fff]", text or ""))


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


def english_tokens(name: str) -> list[str]:
    return name.split()


def match_english(lines: list[str], start: int, name: str) -> bool:
    tokens = english_tokens(name)
    return lines[start : start + len(tokens)] == tokens


def find_archetype_starts(lines: list[str]) -> list[tuple[int, str, str]]:
    starts: list[tuple[int, str, str]] = []
    cursor = 0
    for en_name in EXPECTED_ARCHETYPES:
        tokens = english_tokens(en_name)
        found: tuple[int, str, str] | None = None
        for idx in range(cursor, len(lines) - len(tokens)):
            cn_idx = idx - 1
            if cn_idx < 0 or not has_cjk(lines[cn_idx]):
                continue
            if match_english(lines, idx, en_name):
                found = (cn_idx, lines[cn_idx], en_name)
                cursor = idx + len(tokens)
                break
        if not found:
            raise RuntimeError(f"Vigilante archetype heading not found: {en_name}")
        starts.append(found)
    return starts


def is_heading_candidate(text: str) -> bool:
    text = normalize_ws(text)
    if not has_cjk(text):
        return False
    if text in {"职业变体", "引用"} or text.startswith("译者"):
        return False
    stripped = text.strip("（）() ")
    if len(stripped) < 2 or len(stripped) > 24:
        return False
    if re.search(r"[。；，、？！]", stripped):
        return False
    return True


def parse_feature_header(lines: list[str], idx: int) -> tuple[dict[str, str], int] | None:
    if not is_heading_candidate(lines[idx]):
        return None

    for end in range(idx + 1, min(len(lines), idx + 10)):
        token = lines[end]
        if not is_valid_heading_tail_token(token):
            return None
        if token == "：":
            return build_feature_header(lines[idx:end]), end + 1
        if token == "）":
            # Some talent headings in this page omit the colon after the ability tag.
            header_tokens = lines[idx : end + 1]
            if any(t in {"Ex", "Su", "Sp"} for t in header_tokens):
                next_idx = end + 1
                if next_idx < len(lines) and lines[next_idx] == "：":
                    next_idx += 1
                return build_feature_header(header_tokens), next_idx
    return None


def is_valid_heading_tail_token(token: str) -> bool:
    if token == "：":
        return True
    if token in {"（", "）", "(", ")", "，", ","}:
        return True
    if token in {"Ex", "Su", "Sp"}:
        return True
    # English headings can be split into tokens like "of Blood" or "and Focus Powers".
    return bool(re.match(r"^[A-Za-z][A-Za-z'`’/-]*(?: [A-Za-z][A-Za-z'`’/-]*)*$", token))


def build_feature_header(tokens: list[str]) -> dict[str, str]:
    tokens = [normalize_ws(t) for t in tokens if normalize_ws(t)]
    cn = tokens[0].strip(" （(")
    rest = tokens[1:]
    tag_parts = [t for t in rest if t in {"Ex", "Su", "Sp"}]
    en_parts = []
    for token in rest:
        if token in {"（", "）", "(", ")", "，", ","}:
            continue
        if token in {"Ex", "Su", "Sp"}:
            continue
        if re.match(r"^[A-Za-z][A-Za-z'`’/-]*(?: [A-Za-z][A-Za-z'`’/-]*)?$", token):
            en_parts.append(token)
    en = normalize_ws(" ".join(en_parts))
    name = f"{cn} {en}".strip() if en else cn
    if tag_parts:
        name = f"{name}（{', '.join(tag_parts)}）"
    return {
        "name": name,
        "name_cn": cn,
        "name_en": en,
        "ability_type": ", ".join(tag_parts),
    }


def join_body(lines: list[str]) -> str:
    lines = [x for x in lines if x and x != "引用"]
    text = normalize_ws(" ".join(lines))
    text = text.replace("（ ", "（").replace(" ）", "）")
    text = text.replace("[ ", "[").replace(" ]", "]")
    text = text.replace("DC =", "DC=")
    text = text.replace("+ ", "+").replace(" +", "+")
    text = text.replace("- ", "-").replace(" -", "-")
    return text


def extract_replaces(text: str) -> list[str]:
    result = []
    for pattern in [
        r"本能力取代([^。；;]+)",
        r"本能力调整了?([^。；;]+)",
        r"本天赋取代([^。；;]+)",
        r"本能力调整([^。；;]+)",
    ]:
        for match in re.findall(pattern, text):
            value = normalize_ws(match).strip(" 。；;")
            if value:
                result.append(value)
    return sorted(set(result))


def parse_features(block_lines: list[str], body_start: int) -> tuple[str, list[dict[str, Any]]]:
    features: list[dict[str, Any]] = []
    description_end = len(block_lines)
    parsed_headers: list[tuple[int, dict[str, str], int]] = []
    idx = body_start
    while idx < len(block_lines):
        parsed = parse_feature_header(block_lines, idx)
        if parsed:
            header, content_start = parsed
            parsed_headers.append((idx, header, content_start))
            if description_end == len(block_lines):
                description_end = idx
            idx = content_start
            continue
        idx += 1

    description = join_body(block_lines[body_start:description_end])
    for pos, (heading_idx, header, content_start) in enumerate(parsed_headers):
        next_heading = parsed_headers[pos + 1][0] if pos + 1 < len(parsed_headers) else len(block_lines)
        text = join_body(block_lines[content_start:next_heading])
        features.append(
            {
                "name": header["name"],
                "name_cn": header["name_cn"],
                "name_en": header["name_en"],
                "ability_type": header["ability_type"],
                "text": text,
                "replaces": extract_replaces(text),
            }
        )
    return description, features


def parse_vigilante_archetypes() -> list[dict[str, Any]]:
    pages = load_pages()
    if VIGILANTE_ARCHETYPE_PAGE not in pages:
        raise RuntimeError(f"Page not found: {VIGILANTE_ARCHETYPE_PAGE}")
    lines = page_lines(pages[VIGILANTE_ARCHETYPE_PAGE])
    starts = find_archetype_starts(lines)
    archetypes: list[dict[str, Any]] = []
    for idx, (start, cn, en) in enumerate(starts):
        end = starts[idx + 1][0] if idx + 1 < len(starts) else len(lines)
        block = lines[start:end]
        body_start = 1 + len(english_tokens(en))
        description, features = parse_features(block, body_start)
        archetypes.append(
            {
                "name_raw": f"{cn} {en}",
                "name_cn": cn,
                "name_en": en,
                "description": description,
                "features": features,
                "tables": [],
                "archetype_id": f"archetype-vigilante-page345html-{normalize_key(en)}",
                "parent_class": {
                    "class_id": "class-vigilante-page345html",
                    "name_cn": "侠客",
                    "name_en": "Vigilante",
                },
                "source_book": "极限诡道（Ultimate Intrigue）",
                "source_page": VIGILANTE_ARCHETYPE_PAGE,
                "summary_name_raw": f"{cn}（{en}）",
            }
        )
    return archetypes


def regenerate_report(data: dict[str, Any]) -> dict[str, Any]:
    classes = data.get("classes", [])
    archetype_pages = data.get("archetype_pages", [])
    archetypes = data.get("archetypes", [])
    return {
        "class_count": len(classes),
        "archetype_page_count": len(archetype_pages),
        "archetype_count": len(archetypes),
        "total_table_count": sum(len(c.get("tables", [])) for c in classes)
        + sum(len(p.get("summary_tables", [])) for p in archetype_pages)
        + sum(len(a.get("tables", [])) for a in archetypes),
        "classes_without_progression_table": [
            c.get("name_cn", "") for c in classes if not c.get("progression_table")
        ],
        "classes_without_features": [
            c.get("name_cn", "") for c in classes if not c.get("features")
        ],
        "archetypes_without_features": [
            {
                "parent_class": a.get("parent_class", {}).get("name_cn", ""),
                "name_raw": a.get("name_raw", ""),
                "source_page": a.get("source_page", ""),
                "description_preview": (a.get("description") or "")[:160],
            }
            for a in archetypes
            if not a.get("features")
        ],
        "archetypes_without_source_book": [
            {
                "parent_class": a.get("parent_class", {}).get("name_cn", ""),
                "name_raw": a.get("name_raw", ""),
                "source_page": a.get("source_page", ""),
            }
            for a in archetypes
            if not a.get("source_book")
        ],
        "archetype_pages": [
            {
                "parent_class": p.get("parent_class_cn", ""),
                "source_page": p.get("source_page", ""),
                "archetype_count": len(p.get("archetypes", [])),
                "summary_table_count": len(p.get("summary_tables", [])),
            }
            for p in archetype_pages
        ],
    }


def regenerate_summary(data: dict[str, Any], report: dict[str, Any]) -> None:
    classes = data.get("classes", [])
    archetypes = data.get("archetypes", [])
    by_class: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for archetype in archetypes:
        class_id = archetype.get("parent_class", {}).get("class_id", "")
        by_class[class_id].append(archetype)

    lines = [
        "# 职业与职业变体汇总",
        "",
        f"- 职业总数：{len(classes)}",
        f"- 职业变体总数：{len(archetypes)}",
        f"- 进度表缺失职业：{len(report.get('classes_without_progression_table', []))}",
        f"- 特性缺失职业：{len(report.get('classes_without_features', []))}",
        f"- 无正文变体：{len(report.get('archetypes_without_features', []))}",
        "",
        "## 职业索引",
        "",
        "| # | 职业 | 英文 | 分类 | 变体数 |",
        "|---:|---|---|---|---:|",
    ]
    for idx, class_data in enumerate(classes, 1):
        class_id = class_data.get("class_id", "")
        lines.append(
            f"| {idx} | {class_data.get('name_cn', '')} | {class_data.get('name_en', '')} | "
            f"{class_data.get('category', '')} | {len(by_class.get(class_id, []))} |"
        )

    lines += ["", "## 逐职业变体"]
    for idx, class_data in enumerate(classes, 1):
        class_id = class_data.get("class_id", "")
        class_archetypes = by_class.get(class_id, [])
        title_en = f" ({class_data.get('name_en')})" if class_data.get("name_en") else ""
        lines += ["", f"### {idx}. {class_data.get('name_cn', '')}{title_en} - {len(class_archetypes)} 个变体"]
        if not class_archetypes:
            lines.append("- 未抽取到变体")
            continue
        for archetype in class_archetypes:
            parts = [x for x in [archetype.get("name_en", ""), archetype.get("source_book", "")] if x]
            suffix = f"（{'，'.join(parts)}）" if parts else ""
            lines.append(f"- {archetype.get('name_cn') or archetype.get('name_raw', '')}{suffix}")

    lines += ["", "## 当前需要复核的抽取异常"]
    if report.get("archetypes_without_features"):
        for item in report.get("archetypes_without_features", []):
            lines.append(f"- 无特性正文：{item.get('parent_class')} / {item.get('name_raw')} / {item.get('source_page')}")
    else:
        lines.append("- 无")
    if report.get("archetypes_without_source_book"):
        for item in report.get("archetypes_without_source_book", []):
            lines.append(f"- 无来源书：{item.get('parent_class')} / {item.get('name_raw')} / {item.get('source_page')}")
    SUMMARY_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    data = json.loads(CLASSES_PATH.read_text(encoding="utf-8"))
    vigilante = next((c for c in data.get("classes", []) if c.get("class_id") == "class-vigilante-page345html"), None)
    if not vigilante:
        raise RuntimeError("Vigilante class not found in classes-extracted.json")

    parsed = parse_vigilante_archetypes()
    existing = data.get("archetypes", [])
    data["archetypes"] = [
        a
        for a in existing
        if a.get("parent_class", {}).get("class_id") != "class-vigilante-page345html"
    ] + parsed

    page_entry = {
        "parent_class_id": "class-vigilante-page345html",
        "parent_class_cn": "侠客",
        "source_page": VIGILANTE_ARCHETYPE_PAGE,
        "summary_tables": [],
        "summary_index": {},
        "archetypes": parsed,
    }
    data["archetype_pages"] = [
        p for p in data.get("archetype_pages", []) if p.get("source_page") != VIGILANTE_ARCHETYPE_PAGE
    ] + [page_entry]
    data.setdefault("meta", {})["archetype_count"] = len(data["archetypes"])
    data.setdefault("meta", {})["archetype_page_count"] = len(data["archetype_pages"])

    CLASSES_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    report = regenerate_report(data)
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    regenerate_summary(data, report)
    print(json.dumps({"patched": len(parsed), "archetype_count": len(data["archetypes"])}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()