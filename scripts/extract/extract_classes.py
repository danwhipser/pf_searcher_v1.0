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
OUT_DIR = ROOT / "result" / "classes"
OUT_PATH = OUT_DIR / "classes-extracted.json"
REPORT_PATH = OUT_DIR / "classes-extraction-report.json"


MAIN_CLASS_CATEGORY_TITLES = {
    "核心职业",
    "基础职业",
    "混合职业",
    "掉链子（Unchained）",
    "异能冒险（Occult Adventures）",
    "极限诡道",
    "极限荒野（Ultimate Wilderness）",
    "其他职业",
}

PRESTIGE_CLASS_ROOT_TITLE = "进阶职业"
PRESTIGE_NON_CLASS_TITLES = {
    "专长、庇护主、法术、奇物",
}
FEATURE_SECTION_MARKERS = ("职业特性", "职业能力")
PRESTIGE_OVERVIEW_PAGE = "page_128.html"
MYTHIC_ROOT_TITLE = "神话冒险"
MYTHIC_PATH_TITLES = {
    "大法师",
    "斗士",
    "守护者",
    "圣者",
    "统帅",
    "诡术大师",
}


FIELD_LABELS = {
    "role": ["角色定位"],
    "alignment": ["阵营"],
    "hit_die": ["生命骰"],
    "parent_classes": ["源职业"],
    "starting_wealth": ["起始资金"],
    "class_skills": ["本职技能"],
    "skill_ranks_per_level": ["升级技能点数", "每级技能点数"],
}

KNOWN_CLASS_EN = {
    "反圣武士": "Antipaladin",
    "铳手": "Gunslinger",
    "拳师": "Brawler",
    "调查员": "Investigator",
    "萨满": "Shaman",
    "歌者": "Skald",
    "战斗祭司": "Warpriest",
    "通灵者": "Medium",
    "唤魂师": "Spiritualist",
    "侠客": "Vigilante",
}


FEATURE_SKIP_PREFIXES = (
    "译者",
    "http://",
    "https://",
    "表：",
    "职业特性",
    "以下为",
)


def normalize_ws(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").replace("\r", " ").replace("\n", " ")).strip()


def normalize_key(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (text or "").lower())


def split_cn_en(text: str) -> tuple[str, str]:
    text = normalize_ws(text).strip("：: ")
    m = re.search(r"^(.+?)\s*[（(]\s*([A-Za-z][^）)]*?)\s*[）)]", text)
    if m:
        return normalize_ws(m.group(1)), normalize_ws(m.group(2))
    if re.search(r"[\u4e00-\u9fff]", text):
        return text, ""
    return "", text


def split_overview_class_name(text: str) -> tuple[str, str]:
    text = normalize_ws(text)
    m = re.search(r"^(?P<cn>.*?)\s*[\uff08(]\s*(?P<en>[A-Za-z][^\uff09)]*?)\s*[\uff09)]", text)
    if not m:
        return split_cn_en(text)
    cn = normalize_ws(m.group("cn"))
    en = normalize_ws(m.group("en"))
    en = re.sub(r",\s*[A-Z][A-Za-z0-9#:&\s]+$", "", en).strip()
    return cn, en


def clean_title_line(text: str) -> str:
    text = normalize_ws(text)
    m = re.match(r"^(.+?[（(]\s*[A-Za-z][^）)]*?\s*[）)])", text)
    return normalize_ws(m.group(1)) if m else text


def load_pages() -> dict[str, str]:
    text = VIEWER_PATH.read_text(encoding="utf-8")
    m = re.search(r'<script id="pages-data" type="application/json">(.*?)</script>', text, re.S)
    if not m:
        raise RuntimeError(f"pages-data not found: {VIEWER_PATH}")
    return json.loads(m.group(1))


def find_toc_node(toc: list[dict[str, Any]], title: str) -> dict[str, Any]:
    for node in toc:
        if node.get("title") == title:
            return node
    raise RuntimeError(f"TOC node not found: {title}")


def iter_main_class_nodes(class_root: dict[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for cat in class_root.get("children", []):
        cat_title = cat.get("title", "")
        if cat_title not in MAIN_CLASS_CATEGORY_TITLES:
            continue
        for node in cat.get("children", []):
            local = node.get("local") or ""
            if not local:
                continue
            result.append(
                {
                    "category": cat_title,
                    "title": node.get("title", ""),
                    "local": local,
                    "children": node.get("children", []),
                }
            )
    return result


def iter_prestige_class_nodes(class_root: dict[str, Any]) -> list[dict[str, Any]]:
    prestige_root = next(
        (node for node in class_root.get("children", []) if node.get("title") == PRESTIGE_CLASS_ROOT_TITLE),
        None,
    )
    if not prestige_root:
        return []

    result: list[dict[str, Any]] = []
    for source_group in prestige_root.get("children", []):
        source_title = source_group.get("title", "")
        category = f"{PRESTIGE_CLASS_ROOT_TITLE} / {source_title}" if source_title else PRESTIGE_CLASS_ROOT_TITLE
        children = source_group.get("children", [])
        if children:
            for node in children:
                title = node.get("title", "")
                local = node.get("local") or ""
                if not local or title in PRESTIGE_NON_CLASS_TITLES:
                    continue
                result.append(
                    {
                        "type": "prestige_class",
                        "category": category,
                        "prestige_source": source_title,
                        "title": title,
                        "local": local,
                        "children": node.get("children", []),
                    }
                )
        else:
            local = source_group.get("local") or ""
            if not local:
                continue
            result.append(
                {
                    "type": "prestige_class",
                    "category": category,
                    "prestige_source": source_title,
                    "title": source_title,
                    "local": local,
                    "children": [],
                    "title_from_page": True,
                }
            )
    return result


def iter_class_nodes(class_root: dict[str, Any]) -> list[dict[str, Any]]:
    return iter_main_class_nodes(class_root) + iter_prestige_class_nodes(class_root) + iter_mythic_path_nodes(class_root)


def iter_mythic_path_nodes(class_root: dict[str, Any]) -> list[dict[str, Any]]:
    mythic_root = next(
        (node for node in class_root.get("children", []) if node.get("title") == MYTHIC_ROOT_TITLE),
        None,
    )
    if not mythic_root:
        return []
    result = []
    for node in mythic_root.get("children", []):
        title = node.get("title", "")
        local = node.get("local") or ""
        if title not in MYTHIC_PATH_TITLES or not local:
            continue
        result.append(
            {
                "type": "mythic_path",
                "category": "神话道途",
                "mythic_source": MYTHIC_ROOT_TITLE,
                "title": title,
                "local": local,
                "children": [],
                "title_from_page": True,
            }
        )
    return result


def extract_prestige_overview_entries(pages: dict[str, str]) -> list[dict[str, Any]]:
    html = pages.get(PRESTIGE_OVERVIEW_PAGE)
    if not html:
        return []
    tables = parse_tables(BeautifulSoup(html, "html.parser"))
    overview_table = next(
        (
            table
            for table in tables
            if table.get("rows")
            and table["rows"][0][:2] == ["出处", "进阶职业名"]
        ),
        None,
    )
    if not overview_table:
        return []

    entries: list[dict[str, Any]] = []
    for idx, row in enumerate(overview_table.get("rows", [])[1:], start=1):
        if len(row) < 2:
            continue
        source = normalize_ws(row[0])
        raw_name = normalize_ws(row[1])
        if not source or not raw_name:
            continue
        name_cn, name_en = split_overview_class_name(raw_name)
        key = normalize_key(name_en or name_cn or raw_name)
        if not key:
            key = f"overview{idx}"
        entries.append(
            {
                "overview_index": idx,
                "source": source,
                "name_raw": raw_name,
                "name_cn": name_cn,
                "name_en": name_en,
                "key": key,
            }
        )
    return entries


def score_prestige_detail_page(local: str, html: str, entry: dict[str, Any]) -> int:
    if local == PRESTIGE_OVERVIEW_PAGE:
        return -1000
    name_cn = entry.get("name_cn") or ""
    name_en = entry.get("name_en") or ""
    if not name_cn and not name_en:
        return -1000

    soup = BeautifulSoup(html, "html.parser")
    page_title = normalize_ws(soup.title.get_text(" ", strip=True) if soup.title else "")
    texts = paragraph_texts(soup)[:12]
    joined = "\n".join(texts[:6])

    score = 0
    if name_cn and page_title == name_cn:
        score += 140
    elif name_cn and name_cn in page_title:
        score += 90
    if name_en and name_en.lower() in page_title.lower():
        score += 90
    if name_cn and any(text.startswith(name_cn) for text in texts[:6]):
        score += 80
    if name_en and any(name_en.lower() in text.lower() for text in texts[:6]):
        score += 60
    if name_cn and name_cn in joined:
        score += 25
    if name_en and name_en.lower() in joined.lower():
        score += 25
    if any(marker in joined for marker in ("进阶职业", "进阶条件", "进阶要求", "职业能力", "职业特性")):
        score += 20
    if len(html) > 250000:
        score -= 25
    return score


def locate_prestige_detail_page(entry: dict[str, Any], pages: dict[str, str]) -> str:
    terms = [term for term in (entry.get("name_cn"), entry.get("name_en")) if term]
    if not terms:
        return ""
    best_score = 0
    best_local = ""
    for local, html in pages.items():
        if local == PRESTIGE_OVERVIEW_PAGE:
            continue
        if not any(term in html for term in terms):
            continue
        score = score_prestige_detail_page(local, html, entry)
        if score > best_score:
            best_score = score
            best_local = local
    return best_local if best_score >= 120 else ""


def make_prestige_overview_stub(entry: dict[str, Any]) -> dict[str, Any]:
    page_key = f"overview{entry.get('overview_index')}"
    class_key = normalize_key(entry.get("name_en") or entry.get("name_cn") or entry.get("name_raw")) or page_key
    intro = f"该进阶职业列于全进阶职业一览，出处：{entry.get('source', '')}。当前数据包未定位到独立详情页。"
    return {
        "class_id": f"class-{class_key}-{page_key}",
        "type": "prestige_class",
        "category": f"{PRESTIGE_CLASS_ROOT_TITLE} / 全进阶职业一览",
        "prestige_source": entry.get("source", ""),
        "prestige_overview": {
            "index": entry.get("overview_index"),
            "source": entry.get("source", ""),
            "name_raw": entry.get("name_raw", ""),
        },
        "overview_only": True,
        "name_cn": entry.get("name_cn", ""),
        "name_en": entry.get("name_en", ""),
        "name_raw": entry.get("name_raw", ""),
        "source_page": PRESTIGE_OVERVIEW_PAGE,
        "intro": intro,
        "metadata": {
            "role": "",
            "alignment": "",
            "hit_die": "",
            "parent_classes": "",
            "starting_wealth": "",
            "class_skills": "",
            "skill_ranks_per_level": "",
        },
        "progression_table": None,
        "features": [],
        "tables": [],
        "archetype_pages": [],
    }


def table_to_matrix(table) -> list[list[str]]:
    rows = table.find_all("tr")
    matrix: list[list[str]] = []
    spans: dict[tuple[int, int], tuple[str, int]] = {}
    for r_idx, tr in enumerate(rows):
        out: list[str] = []
        c_idx = 0
        while (r_idx, c_idx) in spans:
            value, remaining = spans.pop((r_idx, c_idx))
            out.append(value)
            if remaining > 1:
                spans[(r_idx + 1, c_idx)] = (value, remaining - 1)
            c_idx += 1

        for cell in tr.find_all(["td", "th"], recursive=False):
            while (r_idx, c_idx) in spans:
                value, remaining = spans.pop((r_idx, c_idx))
                out.append(value)
                if remaining > 1:
                    spans[(r_idx + 1, c_idx)] = (value, remaining - 1)
                c_idx += 1

            value = normalize_ws(cell.get_text(" ", strip=True))
            colspan = int(cell.get("colspan") or 1)
            rowspan = int(cell.get("rowspan") or 1)
            for offset in range(colspan):
                out.append(value)
                if rowspan > 1:
                    spans[(r_idx + 1, c_idx + offset)] = (value, rowspan - 1)
            c_idx += colspan

        while (r_idx, c_idx) in spans:
            value, remaining = spans.pop((r_idx, c_idx))
            out.append(value)
            if remaining > 1:
                spans[(r_idx + 1, c_idx)] = (value, remaining - 1)
            c_idx += 1
        if any(out):
            matrix.append(out)
    return matrix


def parse_tables(soup: BeautifulSoup) -> list[dict[str, Any]]:
    tables = []
    for idx, table in enumerate(soup.find_all("table")):
        rows = table_to_matrix(table)
        title = ""
        if rows and rows[0] and len(set(rows[0])) == 1:
            title = rows[0][0]
        elif rows and rows[0]:
            joined = " ".join(rows[0])
            if joined.startswith("表："):
                title = joined
        tables.append({"index": idx, "title": title, "rows": rows})
    return tables


def paragraph_nodes(soup: BeautifulSoup) -> list[Any]:
    body = soup.body or soup
    nodes = []
    for node in body.find_all(["h1", "h2", "h3", "p", "table"], recursive=True):
        if node.name == "table":
            nodes.append(node)
            continue
        if node.find_parent("table"):
            continue
        text = normalize_ws(node.get_text(" ", strip=True))
        if text:
            nodes.append(node)
    return nodes


def paragraph_texts(soup: BeautifulSoup, skip_tables: bool = True) -> list[str]:
    result = []
    for node in paragraph_nodes(soup):
        if skip_tables and getattr(node, "name", "") == "table":
            continue
        text = normalize_ws(node.get_text(" ", strip=True))
        if text:
            result.append(text)
    return result


def extract_labeled_field(texts: list[str], labels: list[str]) -> str:
    for text in texts:
        for label in labels:
            m = re.match(rf"^{re.escape(label)}\s*[：:]\s*(.+)$", text)
            if m:
                return normalize_ws(m.group(1))
    return ""


def is_feature_start(text: str) -> bool:
    if not text or any(text.startswith(prefix) for prefix in FEATURE_SKIP_PREFIXES):
        return False
    if len(text) > 5000:
        return False
    if re.match(r"^.+?[（(]\s*[A-Za-z][^）)]{1,80}[）)]\s*[：:]", text):
        return True
    if re.match(r"^[\u4e00-\u9fffA-Za-z0-9 /＋+\-]{2,40}\s*[：:]", text):
        return True
    return False


FEATURE_HEADING_RE = re.compile(
    r"(?P<head>(?:[\u4e00-\u9fffA-Za-z0-9/＋+\-、 ]{2,36}|[\u4e00-\u9fffA-Za-z0-9/＋+\-、 ]{1,42}[（(]\s*[A-Za-z][^）)]{1,90}[）)]))\s*[：:]"
)


def split_feature_chunks(text: str) -> list[str]:
    text = normalize_ws(text)
    text = re.sub(r"^职业特性\s+", "", text)
    matches = list(FEATURE_HEADING_RE.finditer(text))
    if not matches:
        return [text]
    chunks = []
    if matches[0].start() > 0:
        prefix = normalize_ws(text[: matches[0].start()])
        if prefix:
            chunks.append(prefix)
    for idx, match in enumerate(matches):
        start = match.start()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        chunk = normalize_ws(text[start:end])
        if chunk:
            chunks.append(chunk)
    return chunks or [text]


def split_feature_line(text: str) -> tuple[str, str, str, str]:
    text = normalize_ws(text)
    text = re.sub(r"^职业特性\s+", "", text)
    name_part = text
    body = ""
    m = re.match(r"^(.+?)\s*[：:]\s*(.*)$", text)
    if m:
        name_part = normalize_ws(m.group(1))
        body = normalize_ws(m.group(2))
    cn, en = split_cn_en(name_part)
    return name_part, cn, en, body


def extract_replaces(text: str) -> list[str]:
    matches = re.findall(r"(?:该能力|此能力|这个能力|这项能力)?\s*取代\s*([^。；;]+)", text)
    return [normalize_ws(m).strip(" 。；;") for m in matches if normalize_ws(m)]


def extract_features_from_texts(texts: list[str], start_after: str | None = None) -> list[dict[str, Any]]:
    features: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    active = start_after is None
    for text in texts:
        if start_after and not active:
            marker_idx = text.rfind(start_after)
            if marker_idx < 0:
                continue
            active = True
            current = None
            text = normalize_ws(text[marker_idx + len(start_after) :])
            if not text:
                continue
        chunks = split_feature_chunks(text)
        if len(chunks) > 1:
            for chunk in chunks:
                if not is_feature_start(chunk):
                    if current is not None:
                        current["text"] = normalize_ws((current.get("text") or "") + " " + chunk)
                        current["replaces"] = sorted(set(current.get("replaces", []) + extract_replaces(chunk)))
                    continue
                name_raw, name_cn, name_en, body = split_feature_line(chunk)
                current = {
                    "name": name_raw,
                    "name_cn": name_cn,
                    "name_en": name_en,
                    "text": body,
                    "replaces": extract_replaces(body),
                }
                features.append(current)
            continue

        if is_feature_start(text):
            name_raw, name_cn, name_en, body = split_feature_line(text)
            current = {
                "name": name_raw,
                "name_cn": name_cn,
                "name_en": name_en,
                "text": body,
                "replaces": extract_replaces(body),
            }
            features.append(current)
        elif current is not None:
            current["text"] = normalize_ws((current.get("text") or "") + " " + text)
            current["replaces"] = sorted(set(current.get("replaces", []) + extract_replaces(text)))
    return features


def summarize_progression_table(tables: list[dict[str, Any]]) -> dict[str, Any] | None:
    for table in tables:
        title = table.get("title") or ""
        rows = table.get("rows") or []
        flat = " ".join(" ".join(row) for row in rows[:4])
        looks_like_progression = (
            ("等级" in flat and "基本攻击加值" in flat and "强韧" in flat)
            or ("等级" in flat and "BAB" in flat and "特殊" in flat)
            or ("等级" in flat and "特殊" in flat and "每日" in flat)
            or ("等级" in flat and "职业能力" in flat and ("BAB" in flat or "基本攻击加值" in flat))
        )
        if title.startswith("表：") or (rows and rows[0] and rows[0][0].startswith("表：")) or looks_like_progression:
            header_idx = 1 if rows and len(set(rows[0])) == 1 else 0
            headers = rows[header_idx] if len(rows) > header_idx else []
            data_rows = rows[header_idx + 1 :] if len(rows) > header_idx + 1 else []
            return {"title": title or rows[0][0], "headers": headers, "rows": data_rows}
    return None


def split_progression_abilities(value: str) -> list[str]:
    value = normalize_ws(value).strip(" －-—")
    if not value:
        return []
    parts = re.split(r"\s*[，,、]\s*", value)
    result = []
    for part in parts:
        part = normalize_ws(part).strip(" ；;。")
        if not part or part in {"-", "－", "—"}:
            continue
        result.append(part)
    return result


def features_from_progression_table(progression: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not progression:
        return []
    headers = progression.get("headers") or []
    special_idx = next(
        (
            idx
            for idx, header in enumerate(headers)
            if any(marker in (header or "") for marker in ("特殊", "职业能力"))
        ),
        -1,
    )
    if special_idx < 0:
        return []

    by_name: dict[str, list[str]] = {}
    for row in progression.get("rows") or []:
        if len(row) <= special_idx:
            continue
        level = row[0] if row else ""
        for name in split_progression_abilities(row[special_idx]):
            by_name.setdefault(name, []).append(level)

    features = []
    for name, levels in by_name.items():
        levels_text = "、".join(level for level in levels if level)
        features.append(
            {
                "name": name,
                "name_cn": name,
                "name_en": "",
                "text": f"进阶表列出该能力出现于：{levels_text}。" if levels_text else "进阶表列出该能力。",
                "replaces": [],
                "source": "progression_table",
            }
        )
    return features


def parse_class_page(node: dict[str, Any], html: str) -> dict[str, Any]:
    soup = BeautifulSoup(html, "html.parser")
    texts = paragraph_texts(soup)
    title_line = ""
    title_idx = 0
    title_re = re.compile(rf"^{re.escape(node.get('title', ''))}\s*[（(]\s*[A-Za-z]", re.I)
    for idx, text in enumerate(texts[:12]):
        if title_re.search(text):
            title_line = text
            title_idx = idx
            break
    if not title_line and node.get("title_from_page"):
        for idx, text in enumerate(texts[:12]):
            if text.startswith("http") or text.startswith("译者"):
                continue
            if re.match(r"^.{1,90}?[\uff08(]\s*[A-Za-z]", text):
                title_line = text
                title_idx = idx
                break
    title_line = clean_title_line(title_line or node.get("title", "") or (texts[0] if texts else ""))
    class_texts = texts[title_idx:] if texts else []
    name_cn, name_en = split_cn_en(title_line)
    if not name_cn:
        name_cn = node.get("title", "")
    if not name_en:
        name_en = KNOWN_CLASS_EN.get(name_cn, "")
    if node.get("overview_name_cn"):
        name_cn = node.get("overview_name_cn", name_cn)
    if node.get("overview_name_en"):
        name_en = node.get("overview_name_en", name_en)
    title_line = node.get("overview_name_raw") or title_line
    page_key = normalize_key(node.get("local", ""))
    class_key = normalize_key(name_en or name_cn) or page_key

    tables = parse_tables(soup)
    progression_table = summarize_progression_table(tables)
    metadata = {key: extract_labeled_field(class_texts, labels) for key, labels in FIELD_LABELS.items()}
    intro = []
    for text in class_texts:
        if any(text.startswith(label) for labels in FIELD_LABELS.values() for label in labels):
            break
        if text.startswith("http") or text.startswith("译者"):
            continue
        if text == node.get("title") or text.startswith(f"{name_cn}（"):
            continue
        intro.append(text)
    features: list[dict[str, Any]] = []
    for marker in FEATURE_SECTION_MARKERS:
        features = extract_features_from_texts(class_texts, start_after=marker)
        if features:
            break
    if not features:
        features = extract_features_from_texts(class_texts)
    if not features:
        features = features_from_progression_table(progression_table)

    return {
        "class_id": f"class-{class_key}-{page_key}",
        "type": node.get("type", "class"),
        "category": node.get("category", ""),
        "prestige_source": node.get("prestige_source", ""),
        "mythic_source": node.get("mythic_source", ""),
        "prestige_overview": node.get("prestige_overview", {}),
        "overview_only": False,
        "name_cn": name_cn,
        "name_en": name_en,
        "name_raw": title_line,
        "source_page": node.get("local", ""),
        "intro": normalize_ws(" ".join(intro)),
        "metadata": metadata,
        "progression_table": progression_table,
        "features": features,
        "tables": tables,
        "archetype_pages": [
            {"title": c.get("title", ""), "local": c.get("local", "")}
            for c in node.get("children", [])
            if (c.get("title") or "") in {"职业变体", "变体"} and c.get("local")
        ],
    }


def parse_archetype_summary_tables(tables: list[dict[str, Any]]) -> dict[str, dict[str, str]]:
    summary: dict[str, dict[str, str]] = {}
    for table in tables[:2]:
        for row in table.get("rows", []):
            cells = [normalize_ws(x) for x in row if normalize_ws(x)]
            if len(cells) < 2:
                continue
            source = cells[0]
            for cell in cells[1:]:
                cn, en = split_cn_en(cell)
                key = normalize_key(en or cn)
                if key:
                    summary[key] = {"source": source, "name_cn": cn, "name_en": en, "name_raw": cell}
    return summary


def is_archetype_heading(text: str, summary_index: dict[str, dict[str, str]]) -> bool:
    text = normalize_ws(text)
    if not text or "：" in text or ":" in text:
        return False
    if len(text) > 180:
        return False
    cn, en = split_cn_en(re.sub(r"【[^】]+】", "", text))
    key = normalize_key(en or cn)
    if key and key in summary_index:
        return True
    return bool("【" in text and re.match(r"^.{1,80}[（(]\s*[A-Za-z][^）)]{1,90}[）)](?:【[^】]+】)?$", text))


def split_heading_and_remainder(text: str) -> tuple[str, str]:
    text = normalize_ws(text)
    m = re.match(r"^(.{1,90}?[（(]\s*[A-Za-z][^）)]{1,120}[）)])\s*(.+)$", text)
    if m and (len(text) > 180 or "有以下职业特性" in text):
        return normalize_ws(m.group(1)), normalize_ws(m.group(2))
    return text, ""


def parse_archetype_blocks(soup: BeautifulSoup, summary_index: dict[str, dict[str, str]]) -> list[dict[str, Any]]:
    nodes = paragraph_nodes(soup)
    blocks: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    
    def add_text_to_current(text: str) -> None:
        nonlocal current
        if current is None:
            return
        text = normalize_ws(text)
        if not text:
            return
        chunks = split_feature_chunks(text)
        if len(chunks) > 1:
            for chunk in chunks:
                if is_feature_start(chunk):
                    name_raw, name_cn, name_en, body = split_feature_line(chunk)
                    current["features"].append(
                        {
                            "name": name_raw,
                            "name_cn": name_cn,
                            "name_en": name_en,
                            "text": body,
                            "replaces": extract_replaces(body),
                        }
                    )
                elif current["features"]:
                    feat = current["features"][-1]
                    feat["text"] = normalize_ws((feat.get("text") or "") + " " + chunk)
                    feat["replaces"] = sorted(set(feat.get("replaces", []) + extract_replaces(chunk)))
                else:
                    current["description"] = normalize_ws((current.get("description") or "") + " " + chunk)
            return
        if is_feature_start(text):
            name_raw, name_cn, name_en, body = split_feature_line(text)
            current["features"].append(
                {
                    "name": name_raw,
                    "name_cn": name_cn,
                    "name_en": name_en,
                    "text": body,
                    "replaces": extract_replaces(body),
                }
            )
        elif current["features"]:
            feat = current["features"][-1]
            feat["text"] = normalize_ws((feat.get("text") or "") + " " + text)
            feat["replaces"] = sorted(set(feat.get("replaces", []) + extract_replaces(text)))
        else:
            current["description"] = normalize_ws((current.get("description") or "") + " " + text)

    for node in nodes:
        name = getattr(node, "name", "")
        if name == "h2" or (name in {"p", "h1", "h3"} and is_archetype_heading(normalize_ws(node.get_text(" ", strip=True)), summary_index)):
            title, remainder = split_heading_and_remainder(normalize_ws(node.get_text(" ", strip=True)))
            title = re.sub(r"【[^】]+】", "", title).strip()
            cn, en = split_cn_en(title)
            current = {
                "name_raw": title,
                "name_cn": cn,
                "name_en": en,
                "description": "",
                "features": [],
                "tables": [],
            }
            blocks.append(current)
            if remainder:
                add_text_to_current(remainder)
            continue
        if current is None:
            continue
        if name == "table":
            current["tables"].append({"rows": table_to_matrix(node)})
            continue
        text = normalize_ws(node.get_text(" ", strip=True))
        add_text_to_current(text)
    return blocks


def parse_archetype_page(parent_class: dict[str, Any], page: dict[str, str], html: str) -> dict[str, Any]:
    soup = BeautifulSoup(html, "html.parser")
    tables = parse_tables(soup)
    summary = parse_archetype_summary_tables(tables)
    archetypes = []
    for block in parse_archetype_blocks(soup, summary):
        key = normalize_key(block.get("name_en") or block.get("name_cn"))
        meta = summary.get(key, {})
        block["archetype_id"] = f"archetype-{parent_class['class_id'].replace('class-', '')}-{key}"
        block["parent_class"] = {
            "class_id": parent_class["class_id"],
            "name_cn": parent_class["name_cn"],
            "name_en": parent_class["name_en"],
        }
        block["source_book"] = meta.get("source", "")
        block["source_page"] = page.get("local", "")
        block["summary_name_raw"] = meta.get("name_raw", "")
        archetypes.append(block)
    return {
        "parent_class_id": parent_class["class_id"],
        "parent_class_cn": parent_class["name_cn"],
        "source_page": page.get("local", ""),
        "summary_tables": tables,
        "summary_index": summary,
        "archetypes": archetypes,
    }


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    toc = json.loads(TOC_PATH.read_text(encoding="utf-8"))
    pages = load_pages()
    class_root = find_toc_node(toc, "职业")
    prestige_overview_entries = extract_prestige_overview_entries(pages)
    class_nodes = iter_class_nodes(class_root)

    classes = []
    archetype_pages = []
    for node in class_nodes:
        html = pages.get(node["local"])
        if not html:
            continue
        class_data = parse_class_page(node, html)
        classes.append(class_data)
        for page in class_data["archetype_pages"]:
            page_html = pages.get(page["local"])
            if page_html:
                archetype_pages.append(parse_archetype_page(class_data, page, page_html))

    prestige_by_key = {}
    prestige_by_cn = {}
    for cls in classes:
        if cls.get("type") != "prestige_class":
            continue
        en_key = normalize_key(cls.get("name_en") or "")
        if en_key:
            prestige_by_key[en_key] = cls
        cn_key = normalize_ws(cls.get("name_cn") or "")
        if cn_key:
            prestige_by_cn[cn_key] = cls
    for entry in prestige_overview_entries:
        key = entry.get("key") or normalize_key(entry.get("name_en") or entry.get("name_cn"))
        existing = prestige_by_key.get(key) or prestige_by_cn.get(normalize_ws(entry.get("name_cn") or ""))
        if existing:
            existing["prestige_overview"] = {
                "index": entry.get("overview_index"),
                "source": entry.get("source", ""),
                "name_raw": entry.get("name_raw", ""),
            }
            if entry.get("source") and not existing.get("prestige_source"):
                existing["prestige_source"] = entry["source"]
            continue

        local = locate_prestige_detail_page(entry, pages)
        if local:
            node = {
                "type": "prestige_class",
                "category": f"{PRESTIGE_CLASS_ROOT_TITLE} / 全进阶职业一览",
                "prestige_source": entry.get("source", ""),
                "prestige_overview": {
                    "index": entry.get("overview_index"),
                    "source": entry.get("source", ""),
                    "name_raw": entry.get("name_raw", ""),
                },
                "overview_name_cn": entry.get("name_cn", ""),
                "overview_name_en": entry.get("name_en", ""),
                "overview_name_raw": entry.get("name_raw", ""),
                "title": entry.get("name_cn") or entry.get("name_en") or entry.get("name_raw", ""),
                "local": local,
                "children": [],
                "title_from_page": True,
            }
            class_data = parse_class_page(node, pages[local])
        else:
            class_data = make_prestige_overview_stub(entry)
        classes.append(class_data)
        if key:
            prestige_by_key[key] = class_data
        if class_data.get("name_cn"):
            prestige_by_cn[normalize_ws(class_data["name_cn"])] = class_data

    archetypes = [a for page in archetype_pages for a in page.get("archetypes", [])]
    result = {
        "meta": {
            "source": "Pathfinder v2.14 SC CHM embedded pages",
            "toc_root": "职业",
            "class_count": len(classes),
            "prestige_overview_count": len(prestige_overview_entries),
            "prestige_class_count": sum(1 for c in classes if c.get("type") == "prestige_class"),
            "prestige_overview_only_count": sum(1 for c in classes if c.get("overview_only")),
            "mythic_path_count": sum(1 for c in classes if c.get("type") == "mythic_path"),
            "archetype_page_count": len(archetype_pages),
            "archetype_count": len(archetypes),
        },
        "classes": classes,
        "archetype_pages": archetype_pages,
        "archetypes": archetypes,
    }
    OUT_PATH.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    report = {
        "class_count": len(classes),
        "archetype_page_count": len(archetype_pages),
        "archetype_count": len(archetypes),
        "total_table_count": sum(len(c.get("tables", [])) for c in classes)
        + sum(len(p.get("summary_tables", [])) for p in archetype_pages)
        + sum(len(a.get("tables", [])) for a in archetypes),
        "classes_without_progression_table": [
            c["name_cn"] for c in classes if not c.get("progression_table")
        ],
        "classes_without_features": [
            c["name_cn"] for c in classes if not c.get("features")
        ],
        "prestige_overview_count": len(prestige_overview_entries),
        "prestige_class_count": sum(1 for c in classes if c.get("type") == "prestige_class"),
        "prestige_overview_only_count": sum(1 for c in classes if c.get("overview_only")),
        "mythic_path_count": sum(1 for c in classes if c.get("type") == "mythic_path"),
        "prestige_overview_only": [
            {
                "source": c.get("prestige_source", ""),
                "name_cn": c.get("name_cn", ""),
                "name_en": c.get("name_en", ""),
            }
            for c in classes
            if c.get("overview_only")
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
                "parent_class": p["parent_class_cn"],
                "source_page": p["source_page"],
                "archetype_count": len(p.get("archetypes", [])),
                "summary_table_count": len(p.get("summary_tables", [])),
            }
            for p in archetype_pages
        ],
    }
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()