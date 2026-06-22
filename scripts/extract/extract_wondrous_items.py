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
from collections import Counter, defaultdict
from typing import Any

from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.extract.extract_classes import load_pages, normalize_key, normalize_ws, parse_tables

OUT_DIR = ROOT / "result" / "items"
OUT_PATH = OUT_DIR / "wondrous-items.json"
REPORT_PATH = OUT_DIR / "wondrous-items-report.json"


SLOT_PAGES = [
    ("page_223.html", "腰部", "Belt", "核心奇物"),
    ("page_224.html", "躯体", "Body", "核心奇物"),
    ("page_225.html", "胸部", "Chest", "核心奇物"),
    ("page_226.html", "眼部", "Eyes", "核心奇物"),
    ("page_227.html", "脚部", "Feet", "核心奇物"),
    ("page_228.html", "手部", "Hands", "核心奇物"),
    ("page_229.html", "头部", "Head", "核心奇物"),
    ("page_230.html", "头饰", "Headband", "核心奇物"),
    ("page_231.html", "颈部", "Neck", "核心奇物"),
    ("page_232.html", "肩部", "Shoulders", "核心奇物"),
    ("page_233.html", "腕部", "Wrists", "核心奇物"),
    ("page_234.html", "无位置", "Slotless", "核心奇物"),
    ("page_567.html", "无位置", "Slotless", "艾恩石"),
    ("page_568.html", "无位置", "Slotless", "寻路仪"),
    ("page_569.html", "无位置", "Slotless", "寻路仪附魔"),
    ("page_1254.html", "无位置", "Slotless", "艾恩石合集"),
    ("page_641.html", "未标明", "", "成长型奇物"),
    ("page_1128.html", "未标明", "", "异能冒险奇物"),
    ("UI\u5947\u7269.htm", "未标明", "", "极限诡道奇物"),
]

SLOT_ALIASES = {
    "belt": "腰部",
    "body": "躯体",
    "chest": "胸部",
    "eyes": "眼部",
    "feet": "脚部",
    "hands": "手部",
    "head": "头部",
    "headband": "头饰",
    "neck": "颈部",
    "shoulders": "肩部",
    "wrists": "腕部",
    "slotless": "无位置",
    "none": "无位置",
    "no slot": "无位置",
    "无": "无位置",
    "无位置": "无位置",
    "腰部": "腰部",
    "躯体": "躯体",
    "身体": "躯体",
    "胸部": "胸部",
    "眼部": "眼部",
    "脚部": "脚部",
    "足部": "脚部",
    "双脚": "脚部",
    "手部": "手部",
    "头部": "头部",
    "头饰": "头饰",
    "颈部": "颈部",
    "脖颈": "颈部",
    "肩部": "肩部",
    "腕部": "腕部",
    "刺青": "刺青",
}

FIELD_LABELS = [
    "栏位",
    "位置",
    "价格",
    "施法者等级",
    "重量",
    "灵光",
    "制造要求",
    "制造需求",
    "制造条件",
    "制造成本",
    "成本",
    "需求",
    "声望",
]


def clean_cell(text: str) -> str:
    return normalize_ws(text).replace("\u3000", " ").strip()


def looks_like_price(text: str) -> bool:
    value = clean_cell(text)
    if not value or value == "价格":
        return False
    lowered = value.lower().replace(",", "").replace(" ", "")
    if lowered in {"-", "—", "－", "特殊", "见正文", "不定"}:
        return True
    if "gp" in lowered or "金币" in lowered:
        return True
    if lowered.startswith("+") and lowered[1:2].isdigit():
        return True
    return bool(re.match(r"^\d+(?:\.\d+)?$", lowered))


def looks_like_source(text: str) -> bool:
    value = clean_cell(text)
    if not value or len(value) > 32:
        return False
    return bool(re.match(r"^[A-Z][A-Z0-9_:#&+\-/ ]{0,30}$", value))


def is_header_or_note(text: str) -> bool:
    value = clean_cell(text)
    if not value:
        return True
    if value in {"价格", "出处", "声望", "CL", "灵光", "效果", "共振", "制造需求", "制造要求", "颜色", "形状"}:
        return True
    return value.startswith(("表：", "表 :", "边栏", "译者", "http://"))


def split_name(raw: str) -> tuple[str, str]:
    text = clean_cell(raw)
    text = re.sub(r"\s+", " ", text)
    m = re.match(r"^(?P<cn>.+?)\s*[（(]\s*(?P<en>[A-Za-z][^）)]*?)\s*[）)]", text)
    if m:
        return clean_cell(m.group("cn")), clean_cell(m.group("en"))
    m = re.match(r"^(?P<cn>.+?)\s*[（(]\s*(?P<en>[A-Za-z][A-Za-z0-9 ,.'’:\-]+)\s*$", text)
    if m:
        return clean_cell(m.group("cn")), clean_cell(m.group("en"))
    m = re.match(r"^(?P<cn>.*?[\u4e00-\u9fff][\u4e00-\u9fffA-Za-z0-9·、'’\- ]*?)\s+(?P<en>[A-Za-z][A-Za-z0-9 ,.'’:\-]+)$", text)
    if m:
        return clean_cell(m.group("cn")), clean_cell(m.group("en"))
    if re.search(r"[\u4e00-\u9fff]", text):
        return text, ""
    return "", text


def canonical_slot(raw_slot: str, fallback: str = "未标明") -> str:
    slot = clean_cell(raw_slot)
    slot = slot.strip("；;。,. ")
    if not slot:
        return fallback
    compact = slot.lower().replace(" ", "")
    for key, value in SLOT_ALIASES.items():
        if compact == key.lower().replace(" ", ""):
            return value
    for key, value in SLOT_ALIASES.items():
        if key.lower().replace(" ", "") in compact:
            return value
    return slot


def make_item_id(source_page: str, name_raw: str, index: int) -> str:
    key = normalize_key(name_raw) or f"item{index}"
    page_key = normalize_key(source_page) or "page"
    return f"wondrous-{page_key}-{key}-{index}"


def base_item(
    *,
    source_page: str,
    page_category: str,
    slot: str,
    slot_en: str,
    name_raw: str,
    price: str = "",
    source_book: str = "",
    index: int = 0,
) -> dict[str, Any]:
    name_cn, name_en = split_name(name_raw)
    return {
        "item_id": make_item_id(source_page, name_raw, index),
        "type": "wondrous_item",
        "slot": slot,
        "slot_en": slot_en,
        "category": page_category,
        "name_raw": clean_cell(name_raw),
        "name_cn": name_cn,
        "name_en": name_en,
        "price": clean_cell(price),
        "source_book": clean_cell(source_book),
        "source_page": source_page,
        "aura": "",
        "caster_level": "",
        "weight": "",
        "reputation": "",
        "requirements": "",
        "cost": "",
        "effect": "",
        "resonance": "",
        "shape": "",
        "detail_text": "",
        "extraction_method": "table",
    }


def header_map(row: list[str]) -> dict[str, int]:
    return {clean_cell(cell): idx for idx, cell in enumerate(row) if clean_cell(cell)}


def parse_ioun_overview_table(
    rows: list[list[str]],
    *,
    source_page: str,
    page_category: str,
    default_slot: str,
    default_slot_en: str,
    start_index: int,
) -> list[dict[str, Any]]:
    if not rows:
        return []
    headers = header_map(rows[0])
    if not {"颜色", "形状", "价格"}.issubset(headers):
        return []

    out = []
    for row in rows[1:]:
        if len(row) <= headers["颜色"]:
            continue
        name = clean_cell(row[headers["颜色"]])
        if is_header_or_note(name):
            continue
        item = base_item(
            source_page=source_page,
            page_category=page_category,
            slot=default_slot,
            slot_en=default_slot_en,
            name_raw=name,
            price=row[headers.get("价格", -1)] if headers.get("价格", -1) < len(row) else "",
            source_book="",
            index=start_index + len(out),
        )
        for field, key in [
            ("形状", "shape"),
            ("灵光", "aura"),
            ("CL", "caster_level"),
            ("效果", "effect"),
            ("共振", "resonance"),
            ("制造需求", "requirements"),
        ]:
            idx = headers.get(field)
            if idx is not None and idx < len(row):
                item[key] = clean_cell(row[idx])
        item["detail_text"] = clean_cell(" ".join(row))
        item["extraction_method"] = "ioun_overview_table"
        out.append(item)
    return out


def parse_reputation_table(
    rows: list[list[str]],
    *,
    source_page: str,
    page_category: str,
    default_slot: str,
    default_slot_en: str,
    start_index: int,
) -> list[dict[str, Any]]:
    if not rows or not any("声望" in cell for cell in rows[0]):
        return []
    out = []
    for row in rows[1:]:
        if len(row) < 2:
            continue
        name = clean_cell(row[0])
        if is_header_or_note(name):
            continue
        item = base_item(
            source_page=source_page,
            page_category=page_category,
            slot=default_slot,
            slot_en=default_slot_en,
            name_raw=name,
            price="",
            source_book=row[2] if len(row) > 2 else "",
            index=start_index + len(out),
        )
        item["reputation"] = clean_cell(row[1])
        item["extraction_method"] = "reputation_table"
        out.append(item)
    return out


def parse_price_tables(
    tables: list[dict[str, Any]],
    *,
    source_page: str,
    page_category: str,
    default_slot: str,
    default_slot_en: str,
) -> list[dict[str, Any]]:
    out = []
    for table in tables:
        rows = table.get("rows") or []
        out.extend(
            parse_ioun_overview_table(
                rows,
                source_page=source_page,
                page_category=page_category,
                default_slot=default_slot,
                default_slot_en=default_slot_en,
                start_index=len(out) + 1,
            )
        )
        if out and out[-1]["source_page"] == source_page and out[-1]["extraction_method"] == "ioun_overview_table":
            continue
        rep_items = parse_reputation_table(
            rows,
            source_page=source_page,
            page_category=page_category,
            default_slot=default_slot,
            default_slot_en=default_slot_en,
            start_index=len(out) + 1,
        )
        if rep_items:
            out.extend(rep_items)
            continue

        for row in rows:
            cells = [clean_cell(cell) for cell in row]
            if len(cells) <= 1:
                continue
            i = 0
            while i < len(cells) - 1:
                name = cells[i]
                price = cells[i + 1] if i + 1 < len(cells) else ""
                if is_header_or_note(name) or not looks_like_price(price):
                    i += 1
                    continue
                source = ""
                if i + 3 < len(cells) and looks_like_source(cells[i + 3]):
                    source = cells[i + 3]
                    i += 4
                elif i + 2 < len(cells) and looks_like_source(cells[i + 2]):
                    source = cells[i + 2]
                    i += 3
                else:
                    i += 2
                out.append(
                    base_item(
                        source_page=source_page,
                        page_category=page_category,
                        slot=default_slot,
                        slot_en=default_slot_en,
                        name_raw=name,
                        price=price,
                        source_book=source,
                        index=len(out) + 1,
                    )
                )
    return out


def soup_text_without_tables(soup: BeautifulSoup) -> str:
    clone = BeautifulSoup(str(soup), "html.parser")
    for table in clone.find_all("table"):
        table.decompose()
    return clean_cell(clone.get_text(" ", strip=True))


def extract_field(text: str, labels: list[str]) -> str:
    if not text:
        return ""
    label_alt = "|".join(re.escape(label) for label in labels)
    stop_alt = "|".join(re.escape(label) for label in FIELD_LABELS)
    pattern = rf"(?:{label_alt})\s*[:：]\s*(.*?)(?=\s*(?:{stop_alt})\s*[:：]|$)"
    m = re.search(pattern, text)
    if not m:
        return ""
    return clean_cell(m.group(1)).strip("；;。 ")


def update_fields_from_detail(item: dict[str, Any], detail: str) -> None:
    detail = clean_cell(detail)
    if not detail:
        return
    item["detail_text"] = detail
    slot = extract_field(detail, ["位置", "栏位"])
    if slot:
        item["slot"] = canonical_slot(slot, item.get("slot") or "未标明")
    for labels, key in [
        (["价格"], "price"),
        (["施法者等级"], "caster_level"),
        (["重量"], "weight"),
        (["灵光"], "aura"),
        (["制造要求", "制造需求", "制造条件", "需求"], "requirements"),
        (["制造成本", "成本"], "cost"),
        (["声望"], "reputation"),
    ]:
        value = extract_field(detail, labels)
        if value and not item.get(key):
            item[key] = value


def attach_details_from_named_text(items: list[dict[str, Any]], soup: BeautifulSoup) -> None:
    text = soup_text_without_tables(soup)
    if not text or not items:
        return

    positions = []
    used = set()
    for idx, item in enumerate(items):
        terms = detail_search_terms(item)
        best = -1
        for term in terms:
            term = clean_cell(term)
            if not term or len(term) < 2:
                continue
            pos = text.find(term)
            if pos >= 0 and (best < 0 or pos < best):
                best = pos
        if best >= 0 and best not in used:
            positions.append((best, idx))
            used.add(best)
    positions.sort()
    for order, (pos, idx) in enumerate(positions):
        end = positions[order + 1][0] if order + 1 < len(positions) else min(len(text), pos + 5000)
        update_fields_from_detail(items[idx], text[pos:end])


def detail_search_terms(item: dict[str, Any]) -> list[str]:
    terms = [item.get("name_raw", ""), item.get("name_cn", ""), item.get("name_en", "")]
    enhancement = enhancement_variant(item)
    if enhancement:
        _rank, base_cn, base_raw = enhancement
        terms.extend([base_cn, base_raw])
        cn, en = split_name(base_raw)
        terms.extend([cn, en])

    level_variant = spell_level_variant(item)
    if level_variant:
        _rank, _label, base_cn, base_raw = level_variant
        terms.extend([base_cn, base_raw])
        cn, en = split_name(base_raw)
        terms.extend([cn, en])

    seen = set()
    result = []
    for term in terms:
        value = clean_cell(term)
        key = normalize_key(value)
        if not value or key in seen:
            continue
        seen.add(key)
        result.append(value)
    return result


LONGFORM_FIELD_RE = re.compile(r"(?:位置|栏位)\s*[:：]", re.S)
TRAILING_NAME_RE = re.compile(r"([\u4e00-\u9fff][\u4e00-\u9fffA-Za-z0-9 ,.'’\-·&（）()]{1,115})$")


def name_before_slot_field(text: str, field_start: int) -> str:
    prefix = clean_cell(text[max(0, field_start - 180) : field_start])
    lower = prefix.lower()
    gp_pos = max(lower.rfind("gp"), prefix.rfind("金币"))
    if gp_pos >= 0:
        prefix = prefix[gp_pos + (2 if lower.rfind("gp") >= prefix.rfind("金币") else 2) :]
    else:
        boundary = max(prefix.rfind(mark) for mark in ["。", "；", ";"])
        if boundary >= 0:
            prefix = prefix[boundary + 1 :]
    prefix = re.sub(r"^(?:[A-Z][A-Z0-9_:#&+\-/]{1,8}\s+)+", "", prefix.strip())
    match = TRAILING_NAME_RE.search(prefix)
    if not match:
        return ""
    name = clean_cell(match.group(1)).strip("，,、 ")
    if len(name) > 120 or any(label in name for label in ["制造要求", "制造成本", "需求", "成本", "灵光", "重量"]):
        return ""
    return name


def parse_longform_items(
    soup: BeautifulSoup,
    *,
    source_page: str,
    page_category: str,
    default_slot: str,
    default_slot_en: str,
    start_index: int,
) -> list[dict[str, Any]]:
    text = clean_cell(soup.get_text(" ", strip=True))
    field_matches = list(LONGFORM_FIELD_RE.finditer(text))
    starts = []
    used_starts = set()
    for match in field_matches:
        raw_name = name_before_slot_field(text, match.start())
        if not raw_name or is_header_or_note(raw_name):
            continue
        start = match.start() - len(raw_name)
        actual_start = text.rfind(raw_name, max(0, match.start() - 200), match.start())
        if actual_start >= 0:
            start = actual_start
        if start in used_starts:
            continue
        starts.append((start, match.start(), raw_name))
        used_starts.add(start)
    starts.sort()
    out = []
    for idx, (start, _field_start, raw_name) in enumerate(starts):
        end = starts[idx + 1][0] if idx + 1 < len(starts) else min(len(text), start + 6000)
        detail = text[start:end]
        slot = canonical_slot(extract_field(detail, ["位置", "栏位"]), default_slot)
        item = base_item(
            source_page=source_page,
            page_category=page_category,
            slot=slot,
            slot_en=default_slot_en,
            name_raw=raw_name,
            price=extract_field(detail, ["价格"]),
            source_book="",
            index=start_index + len(out),
        )
        item["extraction_method"] = "longform"
        update_fields_from_detail(item, detail)
        out.append(item)
    return out


def dedupe_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen = {}
    result = []
    for item in items:
        key = (
            normalize_key(item.get("name_raw", "")),
            item.get("source_page", ""),
            item.get("slot", ""),
            canonical_price_key(item.get("price", "")),
        )
        if key in seen:
            old = seen[key]
            for field in ["aura", "caster_level", "weight", "requirements", "cost", "source_book"]:
                if not old.get(field) and item.get(field):
                    old[field] = item[field]
            if len(clean_cell(item.get("detail_text", ""))) > len(clean_cell(old.get("detail_text", ""))):
                old["detail_text"] = item["detail_text"]
            continue
        seen[key] = item
        result.append(item)
    for idx, item in enumerate(result, start=1):
        item["item_id"] = make_item_id(item["source_page"], item["name_raw"], idx)
    return result


def canonical_price_key(price: str) -> str:
    value = clean_cell(price).lower()
    digits = re.sub(r"[^0-9]", "", value)
    if digits:
        return digits
    return re.sub(r"[\s,，]+", "", value)


ENHANCEMENT_VARIANT_RE = re.compile(r"^\s*\+(?P<bonus>\d+)\s+(?P<name>.+?)\s*$")
SPELL_LEVEL_VARIANT_RE = re.compile(r"^\s*(?P<name>.+?)(?:\s*[-－]\s*|\s*)(?P<level>\d+)\s*(?P<label>环|级|个法术)\s*$")
SIZE_VARIANT_RE = re.compile(r"^\s*(?P<name>.+?)\s*[-－]\s*(?P<size>\d+x\d+)\s*$", re.I)


def enhancement_variant(item: dict[str, Any]) -> tuple[int, str, str] | None:
    name_cn = clean_cell(item.get("name_cn", ""))
    raw = clean_cell(item.get("name_raw", ""))

    match_cn = ENHANCEMENT_VARIANT_RE.match(name_cn)
    match_raw = ENHANCEMENT_VARIANT_RE.match(raw)
    if not match_cn and not match_raw:
        return None

    match = match_cn or match_raw
    if not match:
        return None

    bonus = int(match.group("bonus"))
    base_cn = clean_cell(match_cn.group("name")) if match_cn else name_cn
    base_raw = clean_cell(match_raw.group("name")) if match_raw else raw
    return bonus, base_cn, base_raw


def spell_level_variant(item: dict[str, Any]) -> tuple[int, str, str, str] | None:
    name_cn = clean_cell(item.get("name_cn", ""))
    raw = clean_cell(item.get("name_raw", ""))

    match_raw = SPELL_LEVEL_VARIANT_RE.match(raw)
    match_cn = SPELL_LEVEL_VARIANT_RE.match(name_cn)
    if not match_raw and not match_cn:
        return None

    match = match_raw or match_cn
    if not match:
        return None

    level = int(match.group("level"))
    label = clean_cell(match.group("label"))
    base_raw = clean_cell(match_raw.group("name")) if match_raw else raw
    base_cn, _base_en = split_name(base_raw)
    if not base_cn and match_cn:
        base_cn = clean_cell(match_cn.group("name"))
    return level, label, base_cn, base_raw


def size_variant(item: dict[str, Any]) -> tuple[str, str, str] | None:
    raw = clean_cell(item.get("name_raw", ""))
    match = SIZE_VARIANT_RE.match(raw)
    if not match:
        return None
    size = clean_cell(match.group("size"))
    base_raw = clean_cell(match.group("name"))
    base_cn, _base_en = split_name(base_raw)
    return size, base_cn, base_raw


def variant_detail_score(item: dict[str, Any]) -> tuple[int, int]:
    detail_len = len(clean_cell(item.get("detail_text", "")))
    filled = sum(
        1
        for field in [
            "aura",
            "caster_level",
            "weight",
            "requirements",
            "cost",
            "effect",
            "resonance",
            "detail_text",
        ]
        if clean_cell(item.get(field, ""))
    )
    return detail_len, filled


def merge_enhancement_variants(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str, str, str, str], list[tuple[int, str, str, dict[str, Any]]]] = defaultdict(list)
    passthrough: list[dict[str, Any]] = []

    for item in items:
        variant = enhancement_variant(item)
        if not variant:
            passthrough.append(item)
            continue

        bonus, base_cn, base_raw = variant
        key = (
            item.get("source_page", ""),
            item.get("slot", ""),
            item.get("category", ""),
            normalize_key(base_cn or base_raw),
            normalize_key(item.get("name_en", "")),
        )
        groups[key].append((bonus, base_cn, base_raw, item))

    merged = list(passthrough)
    for _key, rows in groups.items():
        if len(rows) == 1:
            merged.append(rows[0][3])
            continue

        rows.sort(key=lambda row: row[0])
        representative = max((row[3] for row in rows), key=variant_detail_score)
        base_cn = next((row[1] for row in rows if row[1]), "")
        base_raw = next((row[2] for row in rows if row[2]), "")
        name_en = clean_cell(representative.get("name_en", ""))

        item = dict(representative)
        item["name_cn"] = base_cn
        item["name_raw"] = clean_cell(f"{base_cn} {name_en}") if base_cn and name_en else base_raw
        item["price"] = "；".join(
            f"+{bonus} {clean_cell(row_item.get('price', ''))}"
            for bonus, _base_cn, _base_raw, row_item in rows
            if clean_cell(row_item.get("price", ""))
        )
        item["variants"] = [
            {
                "bonus": bonus,
                "name_raw": row_item.get("name_raw", ""),
                "name_cn": row_item.get("name_cn", ""),
                "price": clean_cell(row_item.get("price", "")),
                "source_book": row_item.get("source_book", ""),
            }
            for bonus, _base_cn, _base_raw, row_item in rows
        ]
        item["variant_count"] = len(rows)
        method = clean_cell(item.get("extraction_method", ""))
        item["extraction_method"] = f"{method}+variant_merge" if method else "variant_merge"
        merged.append(item)

    for idx, item in enumerate(merged, start=1):
        item["item_id"] = make_item_id(item["source_page"], item["name_raw"], idx)
    return merged


def merge_spell_level_variants(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str, str, str, str], list[tuple[int, str, str, str, dict[str, Any]]]] = defaultdict(list)
    passthrough: list[dict[str, Any]] = []

    for item in items:
        variant = spell_level_variant(item)
        if not variant:
            passthrough.append(item)
            continue

        level, label, base_cn, base_raw = variant
        _cn, base_en = split_name(base_raw)
        key = (
            item.get("source_page", ""),
            item.get("slot", ""),
            item.get("category", ""),
            normalize_key(base_cn or base_raw),
            normalize_key(base_en or item.get("name_en", "")),
        )
        groups[key].append((level, label, base_cn, base_raw, item))

    remaining: list[dict[str, Any]] = []
    for item in passthrough:
        plain_key = spell_level_plain_key(item)
        if plain_key not in groups:
            remaining.append(item)
            continue
        label = most_common_level_label(groups[plain_key])
        inferred_level = infer_level_from_price(item)
        if inferred_level is None:
            remaining.append(item)
            continue
        if any(level == inferred_level and row_label == label for level, row_label, *_rest in groups[plain_key]):
            remaining.append(item)
            continue
        base_cn, base_en = split_name(item.get("name_raw", ""))
        base_raw = clean_cell(f"{base_cn} {base_en}") if base_cn and base_en else clean_cell(item.get("name_raw", ""))
        groups[plain_key].append((inferred_level, label, base_cn, base_raw, item))
    passthrough = remaining

    merged = list(passthrough)
    for _key, rows in groups.items():
        if len(rows) == 1:
            merged.append(rows[0][4])
            continue

        rows.sort(key=lambda row: row[0])
        representative = max((row[4] for row in rows), key=variant_detail_score)
        base_cn = next((row[2] for row in rows if row[2]), "")
        base_raw = next((row[3] for row in rows if row[3]), "")
        _cn, name_en = split_name(base_raw)

        item = dict(representative)
        item["name_cn"] = base_cn
        item["name_en"] = name_en or clean_cell(representative.get("name_en", ""))
        item["name_raw"] = clean_cell(f"{base_cn} {item['name_en']}") if base_cn and item["name_en"] else base_raw
        item["price"] = "；".join(
            f"{level}{label} {clean_cell(row_item.get('price', ''))}"
            for level, label, _base_cn, _base_raw, row_item in rows
            if clean_cell(row_item.get("price", ""))
        )
        item["variants"] = [
            {
                "level": level,
                "label": label,
                "name_raw": row_item.get("name_raw", ""),
                "name_cn": row_item.get("name_cn", ""),
                "price": clean_cell(row_item.get("price", "")),
                "source_book": row_item.get("source_book", ""),
            }
            for level, label, _base_cn, _base_raw, row_item in rows
        ]
        item["variant_count"] = len(rows)
        method = clean_cell(item.get("extraction_method", ""))
        item["extraction_method"] = f"{method}+level_variant_merge" if method else "level_variant_merge"
        merged.append(item)

    for idx, item in enumerate(merged, start=1):
        item["item_id"] = make_item_id(item["source_page"], item["name_raw"], idx)
    return merged


def spell_level_plain_key(item: dict[str, Any]) -> tuple[str, str, str, str, str]:
    name_raw = clean_cell(item.get("name_raw", ""))
    base_cn, base_en = split_name(name_raw)
    return (
        item.get("source_page", ""),
        item.get("slot", ""),
        item.get("category", ""),
        normalize_key(base_cn or name_raw),
        normalize_key(base_en or item.get("name_en", "")),
    )


def most_common_level_label(rows: list[tuple[int, str, str, str, dict[str, Any]]]) -> str:
    counts = Counter(label for _level, label, _base_cn, _base_raw, _item in rows)
    return counts.most_common(1)[0][0] if counts else "级"


def price_number(value: str) -> int | None:
    digits = re.sub(r"[^0-9]", "", clean_cell(value))
    if not digits:
        return None
    return int(digits)


def infer_level_from_price(item: dict[str, Any]) -> int | None:
    price = price_number(item.get("price", ""))
    if not price:
        return None
    if price % 1000 != 0:
        return None
    root = int((price // 1000) ** 0.5)
    if 1 <= root <= 9 and root * root * 1000 == price:
        return root
    return None


def merge_size_variants(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str, str, str, str], list[tuple[str, str, str, dict[str, Any]]]] = defaultdict(list)
    passthrough: list[dict[str, Any]] = []

    for item in items:
        variant = size_variant(item)
        if not variant:
            passthrough.append(item)
            continue
        size, base_cn, base_raw = variant
        _cn, base_en = split_name(base_raw)
        key = (
            item.get("source_page", ""),
            item.get("slot", ""),
            item.get("category", ""),
            normalize_key(base_cn or base_raw),
            normalize_key(base_en or item.get("name_en", "")),
        )
        groups[key].append((size, base_cn, base_raw, item))

    merged = list(passthrough)
    for _key, rows in groups.items():
        if len(rows) == 1:
            merged.append(rows[0][3])
            continue

        representative = max((row[3] for row in rows), key=variant_detail_score)
        base_cn = next((row[1] for row in rows if row[1]), "")
        base_raw = next((row[2] for row in rows if row[2]), "")
        _cn, name_en = split_name(base_raw)

        item = dict(representative)
        item["name_cn"] = base_cn
        item["name_en"] = name_en or clean_cell(representative.get("name_en", ""))
        item["name_raw"] = clean_cell(f"{base_cn} {item['name_en']}") if base_cn and item["name_en"] else base_raw
        item["price"] = "；".join(
            f"{size} {clean_cell(row_item.get('price', ''))}"
            for size, _base_cn, _base_raw, row_item in rows
            if clean_cell(row_item.get("price", ""))
        )
        item["variants"] = [
            {
                "size": size,
                "name_raw": row_item.get("name_raw", ""),
                "name_cn": row_item.get("name_cn", ""),
                "price": clean_cell(row_item.get("price", "")),
                "source_book": row_item.get("source_book", ""),
            }
            for size, _base_cn, _base_raw, row_item in rows
        ]
        item["variant_count"] = len(rows)
        method = clean_cell(item.get("extraction_method", ""))
        item["extraction_method"] = f"{method}+size_variant_merge" if method else "size_variant_merge"
        merged.append(item)

    for idx, item in enumerate(merged, start=1):
        item["item_id"] = make_item_id(item["source_page"], item["name_raw"], idx)
    return merged


def text_until_any(text: str, markers: list[str]) -> str:
    text = clean_cell(text)
    ends = [text.find(marker) for marker in markers if marker and text.find(marker) > 0]
    if not ends:
        return text
    return clean_cell(text[: min(ends)])


def text_from_marker(text: str, marker: str) -> str:
    text = clean_cell(text)
    pos = text.find(marker)
    return clean_cell(text[pos:]) if pos >= 0 else text


def fixed_wondrous_item(
    *,
    source_page: str,
    page_category: str,
    slot: str,
    slot_en: str = "",
    name_cn: str,
    name_en: str,
    price: str,
    caster_level: str,
    weight: str,
    aura: str,
    requirements: str,
    cost: str,
    detail_text: str,
    index: int = 0,
) -> dict[str, Any]:
    item = base_item(
        source_page=source_page,
        page_category=page_category,
        slot=slot,
        slot_en=slot_en,
        name_raw=clean_cell(f"{name_cn} {name_en}") if name_en else name_cn,
        price=price,
        source_book="",
        index=index,
    )
    item.update(
        {
            "name_cn": name_cn,
            "name_en": name_en,
            "caster_level": caster_level,
            "weight": weight,
            "aura": aura,
            "requirements": requirements,
            "cost": cost,
            "detail_text": clean_cell(detail_text),
            "extraction_method": "known_item_repair",
        }
    )
    return item


def repair_known_item_extraction(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Repair known longform boundary leaks in mixed item pages.

    Some source pages place "requirements" text immediately before the next
    item title. The generic longform parser can include that trailing spell
    name as part of the next item name. These repairs keep the generated
    corpus stable while preserving the useful detail text.
    """
    repaired: list[dict[str, Any]] = []

    def has_any(item: dict[str, Any], terms: list[str]) -> bool:
        blob = " ".join(
            clean_cell(str(item.get(field, "")))
            for field in ["name_raw", "name_cn", "name_en", "detail_text"]
        )
        return any(term in blob for term in terms)

    wayfinder_source = next(
        (
            item
            for item in items
            if item.get("source_page") == "page_234.html"
            and has_any(item, ["出自内海世界指南）", "魔法指南针"])
            and clean_cell(item.get("price", "")) == "500 gp"
        ),
        None,
    )
    fan_source = next(
        (
            item
            for item in items
            if item.get("source_page") == "UI奇物.htm"
            and "阿谀手扇Fan of Flirting" in clean_cell(item.get("detail_text", ""))
        ),
        None,
    )
    codex_source = next(
        (
            item
            for item in items
            if item.get("source_page") == "UI奇物.htm"
            and "言谈录Codex of Conversations" in clean_cell(item.get("detail_text", ""))
        ),
        None,
    )

    for item in items:
        if item is wayfinder_source or item is fan_source or item is codex_source:
            continue
        if item.get("source_page") == "page_234.html" and has_any(item, ["出自内海世界指南）"]):
            continue
        if item.get("source_page") == "UI奇物.htm" and has_any(item, ["阿谀手扇", "Fan of Flirting"]):
            continue
        if item.get("source_page") == "UI奇物.htm" and has_any(item, ["言谈录", "Codex of Conversations"]):
            continue
        repaired.append(item)

    if wayfinder_source:
        detail_tail = clean_cell(wayfinder_source.get("detail_text", ""))
        if "位置" in detail_tail:
            detail_tail = detail_tail[detail_tail.find("位置") :]
        detail = text_until_any(
            f"魔法指南针（Wayfinder；出自内海世界指南） {detail_tail}",
            ["破裂的刺球形艾恩石", "有裂痕的刺球形艾恩石", "第一戒律之碑"],
        )
        repaired.append(
            fixed_wondrous_item(
                source_page="page_234.html",
                page_category="核心奇物",
                slot="无位置",
                slot_en="Slotless",
                name_cn="魔法指南针",
                name_en="Wayfinder",
                price="500 gp",
                caster_level="5 级",
                weight="1 磅",
                aura="微弱塑能系",
                requirements="制造奇物（Craft Wondrous Item），光亮术（light）",
                cost="250 gp",
                detail_text=detail,
            )
        )

    if fan_source:
        detail = text_from_marker(fan_source.get("detail_text", ""), "阿谀手扇Fan of Flirting")
        detail = text_until_any(detail, ["幽冥刺针Ghost Needle", "面纱琉璃镜Glass of Veils"])
        repaired.append(
            fixed_wondrous_item(
                source_page="UI奇物.htm",
                page_category="极限诡道奇物",
                slot="无位置",
                name_cn="阿谀手扇",
                name_en="Fan of Flirting",
                price="1700 GP",
                caster_level="1",
                weight="-",
                aura="昏暗惑控系",
                requirements="制造奇物，魅惑人类，催眠术，强迫凝视 UC",
                cost="850 GP",
                detail_text=detail,
            )
        )

    if codex_source:
        detail = text_from_marker(codex_source.get("detail_text", ""), "言谈录Codex of Conversations")
        detail = text_until_any(detail, ["百纳衣柜Costume Bureau", "信使安全袋Courier's Secure Pouch"])
        repaired.append(
            fixed_wondrous_item(
                source_page="UI奇物.htm",
                page_category="极限诡道奇物",
                slot="无位置",
                name_cn="言谈录",
                name_en="Codex of Conversations",
                price="10000 GP",
                caster_level="5",
                weight="3磅",
                aura="昏暗预言系",
                requirements="制造奇物，锐耳术/鹰眼术，通晓语言，幻象手稿",
                cost="5000 GP",
                detail_text=detail,
            )
        )

    for idx, item in enumerate(repaired, start=1):
        item["item_id"] = make_item_id(item["source_page"], item["name_raw"], idx)
    return repaired


def extract_page(local: str, slot: str, slot_en: str, category: str, html: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    tables = parse_tables(soup)
    items = parse_price_tables(
        tables,
        source_page=local,
        page_category=category,
        default_slot=slot,
        default_slot_en=slot_en,
    )
    attach_details_from_named_text(items, soup)

    longform = parse_longform_items(
        soup,
        source_page=local,
        page_category=category,
        default_slot=slot,
        default_slot_en=slot_en,
        start_index=len(items) + 1,
    )
    items.extend(longform)
    return dedupe_items(items)


def main() -> None:
    pages = load_pages()
    all_items: list[dict[str, Any]] = []
    page_report = []

    for local, slot, slot_en, category in SLOT_PAGES:
        html = pages.get(local)
        if not html:
            page_report.append({"source_page": local, "category": category, "status": "missing", "item_count": 0})
            continue
        items = extract_page(local, slot, slot_en, category, html)
        all_items.extend(items)
        page_report.append(
            {
                "source_page": local,
                "category": category,
                "default_slot": slot,
                "item_count": len(items),
                "by_method": dict(Counter(item["extraction_method"] for item in items)),
            }
        )

    all_items = repair_known_item_extraction(
        merge_size_variants(
            merge_spell_level_variants(
                merge_enhancement_variants(dedupe_items(all_items))
            )
        )
    )
    by_slot = Counter(item["slot"] or "未标明" for item in all_items)
    by_source_page = Counter(item["source_page"] for item in all_items)
    grouped: dict[str, list[str]] = defaultdict(list)
    for item in all_items:
        grouped[item["slot"] or "未标明"].append(item["item_id"])

    slots = [
        {
            "slot": slot,
            "item_count": count,
            "item_ids": grouped.get(slot, []),
        }
        for slot, count in sorted(by_slot.items(), key=lambda pair: (-pair[1], pair[0]))
    ]

    output = {
        "meta": {
            "schema": "pf1-wondrous-items-v1",
            "item_count": len(all_items),
            "slot_count": len(slots),
            "source": "Pathfinder v2.14 SC CHM embedded pages",
        },
        "slots": slots,
        "items": all_items,
    }

    report = {
        "item_count": len(all_items),
        "slot_counts": dict(by_slot),
        "source_page_counts": dict(by_source_page),
        "pages": page_report,
        "missing_detail_count": sum(1 for item in all_items if not item.get("detail_text")),
    }

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {OUT_PATH} ({len(all_items)} items, {len(slots)} slots)")
    print(f"wrote {REPORT_PATH}")


if __name__ == "__main__":
    main()