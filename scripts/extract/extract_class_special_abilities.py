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
TOC_PATH = ROOT / "result" / "toc.json"
VIEWER_PATH = ROOT / "result" / "Pathfinder-v2.14-SC-viewer-embedded.html"
CLASSES_PATH = ROOT / "result" / "classes" / "classes-extracted.json"
REPORT_PATH = ROOT / "result" / "classes" / "class-special-abilities-report.json"

EXCLUDED_CHILD_TITLES = {
    "职业变体",
    "变体",
    "全变体未整合",
    "法术列表",
    "法术列表(完整)",
    "公式列表",
    "圣武士准则",
    "反圣武士准则",
    "武僧清规",
    "专长",
}

EXCLUDED_TITLE_PARTS = ("职业变体", "法术列表", "公式列表", "全变体")

TABLE_LIKE_SPECIAL_TITLES = {
    "神祇/领域",
    "神祇扩展",
    "自然纽带",
    "战斗流派",
    "游侠战斗流派",
    "技能解放",
    "战策全表",
}

SKIP_ENTRY_PREFIXES = (
    "http",
    "译者",
    "译注",
    "编注",
    "官方",
    "剧透",
    "含有",
    "Pathfinder",
    "【法术速查】",
    "【特性速查】",
    "【速查】",
)


def normalize_ws(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").replace("\r", " ").replace("\n", " ")).strip()


def normalize_key(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (text or "").lower())


def load_pages() -> dict[str, str]:
    text = VIEWER_PATH.read_text(encoding="utf-8")
    m = re.search(r'<script id="pages-data" type="application/json">(.*?)</script>', text, re.S)
    if not m:
        raise RuntimeError(f"pages-data not found: {VIEWER_PATH}")
    return json.loads(m.group(1))


def walk_toc(nodes: list[dict[str, Any]]):
    for node in nodes:
        yield node
        yield from walk_toc(node.get("children", []))


def split_cn_en(title: str) -> tuple[str, str, str]:
    text = normalize_ws(title)
    m2 = re.search(
        r"^(?P<cn>[\u4e00-\u9fff·・'’`\-/+＋、\s]{1,40})\s+"
        r"(?P<en>[A-Za-z][A-Za-z0-9'’`\-/+＋,\s]{1,90})"
        r"[（(]\s*(?P<type>Ex|Su|Sp|EX|SU|SP)\s*[）)]",
        text,
    )
    if m2:
        return normalize_ws(m2.group("cn")).strip(" ：:【】[]"), normalize_ws(m2.group("en")), normalize_ws(m2.group("type"))
    m = re.search(r"^(?P<cn>.+?)[（(]\s*(?P<en>[A-Za-z][^）)]*?)\s*[）)]", text)
    if not m:
        return text, "", ""
    cn = normalize_ws(m.group("cn")).strip(" ：:【】[]")
    cn = re.sub(r"^[，,、和及\s]+", "", cn)
    if " " in cn:
        cn = cn.split()[-1]
    en_full = normalize_ws(m.group("en"))
    ability_type = ""
    if en_full in {"Ex", "Su", "Sp", "EX", "SU", "SP"}:
        return cn, "", en_full
    if "," in en_full:
        en, ability_type = [normalize_ws(x) for x in en_full.split(",", 1)]
    else:
        en = en_full
    return cn, en, ability_type


def paragraph_texts(html: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    texts: list[str] = []
    for node in (soup.body or soup).find_all(["h1", "h2", "h3", "p"], recursive=True):
        if node.find_parent("table"):
            continue
        text = normalize_ws(node.get_text(" ", strip=True))
        if not text:
            continue
        if text.startswith(("http", "译者", "*")):
            continue
        if "书目列表" in text or "翻译链接" in text:
            continue
        if text:
            texts.append(text)
    return texts


ENTRY_RE = re.compile(
    r"(?P<head>"
    r"(?:【[^】]{1,20}】\s*)?"
    r"(?:"
    r"[\u4e00-\u9fffA-Za-z0-9·・'’`\-/+＋、\s]{1,70}?[（(]\s*[A-Za-z][^）)]{1,120}\s*[）)]"
    r"|"
    r"[\u4e00-\u9fff·・'’`\-/+＋、\s]{1,40}\s+[A-Za-z][A-Za-z0-9'’`\-/+＋,\s]{1,90}[（(]\s*(?:Ex|Su|Sp|EX|SU|SP)\s*[）)]"
    r")"
    r"(?:\s*【[^】]{1,40}】)?"
    r")\s*[：:]?"
)


def looks_like_entry_head(head: str, group_title: str) -> bool:
    head = normalize_ws(head)
    if not head or len(head) > 150:
        return False
    if any(head.startswith(prefix) for prefix in SKIP_ENTRY_PREFIXES):
        return False
    cn, en, _ = split_cn_en(head)
    cn = cn.strip()
    if not cn or not en:
        return False
    if en in {"Ex", "Su", "Sp", "EX", "SU", "SP"}:
        return False
    if re.search(r"\b(Blur|Ferocity|Trample|Punching Dagger|DC)\b", en):
        return False
    if cn.startswith(("和", "及", "、", "，", ",")):
        return False
    if cn.startswith(("获得", "使用", "如果", "当", "在", "他", "她", "野蛮人", "角色", "目标", "敌人", "每当", "每次")):
        return False
    if any(bad in cn for bad in ("获得法术", "特殊能力", "特殊攻击", "使用法术", "轮中获得")):
        return False
    if len(cn) > 42:
        return False
    if cn == group_title or cn.endswith(group_title):
        return False
    if "速查" in cn or "书目列表" in cn or "翻译链接" in cn:
        return False
    return True


def extract_field(body: str, labels: tuple[str, ...], stop_labels: tuple[str, ...]) -> str:
    label_re = "|".join(re.escape(label) for label in labels)
    stop_re = "|".join(re.escape(label) for label in stop_labels)
    m = re.search(rf"(?:{label_re})\s*[：:]\s*(.*?)(?=(?:{stop_re})\s*[：:]|$)", body)
    return normalize_ws(m.group(1)) if m else ""


def clean_detail(body: str) -> str:
    body = normalize_ws(body)
    body = re.sub(r"^(前提|先决条件|效果|好处|特殊)\s*[：:]\s*", "", body)
    return body


def option_quality(option: dict[str, Any]) -> int:
    score = len(option.get("detail_text") or "")
    if option.get("effect"):
        score += 200
    if option.get("prerequisites"):
        score += 50
    return score


def parse_options(texts: list[str], group_title: str) -> tuple[str, list[dict[str, Any]]]:
    joined = "\n".join(texts)
    matches = [m for m in ENTRY_RE.finditer(joined) if looks_like_entry_head(m.group("head"), group_title)]
    intro_end = matches[0].start() if matches else len(joined)
    intro_parts = [
        normalize_ws(x)
        for x in joined[:intro_end].split("\n")
        if normalize_ws(x) and not normalize_ws(x).startswith(("http", "译者"))
    ]
    intro = normalize_ws(" ".join(intro_parts[:8]))

    options: list[dict[str, Any]] = []
    seen: set[str] = set()
    stop_labels = ("前提", "先决条件", "效果", "好处", "收益", "特殊")
    for idx, match in enumerate(matches):
        head = normalize_ws(match.group("head"))
        start = match.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(joined)
        body = normalize_ws(joined[start:end])
        cn, en, ability_type = split_cn_en(head)
        key = normalize_key(en or cn)
        if not key:
            continue
        source_book = ""
        source_match = re.search(r"【([^】]{1,40})】", head)
        if source_match:
            source_book = normalize_ws(source_match.group(1))
        prereq = extract_field(body, ("前提", "先决条件"), stop_labels)
        effect = extract_field(body, ("效果", "好处", "收益"), stop_labels)
        special = extract_field(body, ("特殊",), stop_labels)
        option = {
            "name": head,
            "name_cn": cn,
            "name_en": en,
            "ability_type": ability_type,
            "source_book": source_book,
            "prerequisites": prereq,
            "effect": effect,
            "special": special,
            "detail_text": clean_detail(body),
        }
        if key in seen:
            existing_idx = next((i for i, item in enumerate(options) if normalize_key(item.get("name_en") or item.get("name_cn")) == key), -1)
            if existing_idx >= 0 and option_quality(option) > option_quality(options[existing_idx]):
                options[existing_idx] = option
            continue
        seen.add(key)
        options.append(option)
    return intro, options


def should_parse_child(title: str) -> bool:
    if title in EXCLUDED_CHILD_TITLES:
        return False
    if title in TABLE_LIKE_SPECIAL_TITLES:
        return False
    return not any(part in title for part in EXCLUDED_TITLE_PARTS)


def canonical_child_title(title: str) -> str:
    title = normalize_ws(title)
    title = re.sub(r"^【整理】", "", title)
    title = re.sub(r"(汇总|汇整|全扩展|未整理)$", "", title)
    return re.sub(r"[\s/、（）()]+", "", title)


def select_special_children(children: list[dict[str, Any]], pages: dict[str, str]) -> list[dict[str, Any]]:
    valid = [
        child
        for child in children
        if child.get("local") and child.get("local") in pages and should_parse_child(child.get("title") or "")
    ]
    selected: list[dict[str, Any]] = []
    for child in valid:
        title = child.get("title") or ""
        canon = canonical_child_title(title)
        is_summary = any(mark in title for mark in ("汇总", "汇整", "全扩展"))
        duplicate_summary = None
        for other in valid:
            if other is child:
                continue
            other_title = other.get("title") or ""
            other_canon = canonical_child_title(other_title)
            other_summary = any(mark in other_title for mark in ("汇总", "汇整", "全扩展"))
            if other_summary and (canon in other_canon or other_canon in canon):
                duplicate_summary = other
                break
        if duplicate_summary is not None and not is_summary:
            continue
        selected.append(child)
    return selected


def main() -> None:
    toc = json.loads(TOC_PATH.read_text(encoding="utf-8"))
    pages = load_pages()
    data = json.loads(CLASSES_PATH.read_text(encoding="utf-8"))
    nodes_by_local = {node.get("local"): node for node in walk_toc(toc) if node.get("local")}

    report_rows: list[dict[str, Any]] = []
    total_groups = 0
    total_options = 0
    for cls in data.get("classes", []):
        node = nodes_by_local.get(cls.get("source_page"))
        groups: list[dict[str, Any]] = []
        if cls.get("overview_only"):
            node = None
        if node:
            for child in select_special_children(node.get("children", []), pages):
                title = child.get("title") or ""
                local = child.get("local") or ""
                intro, options = parse_options(paragraph_texts(pages[local]), title)
                if not options:
                    continue
                group_key = normalize_key(f"{cls.get('class_id')} {local} {title}") or normalize_key(title)
                groups.append(
                    {
                        "group_id": f"special-{group_key}",
                        "title": title,
                        "source_page": local,
                        "intro": intro,
                        "option_count": len(options),
                        "options": options,
                    }
                )
                total_groups += 1
                total_options += len(options)
        cls["special_ability_groups"] = groups
        report_rows.append(
            {
                "class": cls.get("name_cn") or cls.get("name_en"),
                "source_page": cls.get("source_page"),
                "group_count": len(groups),
                "option_count": sum(len(g.get("options", [])) for g in groups),
                "groups": [
                    {"title": g["title"], "source_page": g["source_page"], "option_count": g["option_count"]}
                    for g in groups
                ],
            }
        )

    data.setdefault("metadata", {})["special_ability_group_count"] = total_groups
    data.setdefault("metadata", {})["special_ability_option_count"] = total_options
    CLASSES_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    REPORT_PATH.write_text(
        json.dumps(
            {
                "class_count": len(data.get("classes", [])),
                "special_ability_group_count": total_groups,
                "special_ability_option_count": total_options,
                "classes": report_rows,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"updated {CLASSES_PATH}")
    print(f"groups={total_groups} options={total_options}")
    print(f"report={REPORT_PATH}")


if __name__ == "__main__":
    main()