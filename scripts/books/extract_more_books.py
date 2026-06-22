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
from typing import Dict, List, Optional, Tuple

from bs4 import BeautifulSoup

from scripts.books.extract_missing_books import (
    FIELD_LABELS,
    RESULT_DIR,
    ROOT,
    ascii_key,
    clean_text,
    cn_key,
    normalize_model,
    parse_block,
    parse_level_entries,
    read_html,
)

SPELL_DIR = ROOT / "spell"

SOURCE_HTML = {
    "AG": "AG-",
    "MC": "MC-",
    "MA": "page_625.html",
    "VC": "page_853.html",
    "HA": "page_854.html",
    "UW": "page_1219.html",
    "PA": "page_1474.html",
    "BOTD": "page_1494.html",
}

FIELD_START_LABELS = [
    "学派",
    "等级",
    "环位",
    "来源",
]

FIELD_NORMALIZE_LABELS = [
    "学派",
    "等级",
    "环位",
    "施法时间",
    "施放时间",
    "成分",
    "距离",
    "射程",
    "范围",
    "法术范围",
    "目标",
    "法术目标",
    "区域",
    "效果",
    "持续时间",
    "持续",
    "豁免检定",
    "豁免",
    "法术抗力",
    "抗力",
]

SOURCE_FIELD_RE = re.compile(r"来源\s*[:：]\s*(?P<source>[^。！？\n]{1,100}?[）)])\s*(?P<effect>.*)$", re.S)


def resolve_source_path(source: str) -> Path:
    marker = SOURCE_HTML[source]
    if marker.endswith((".html", ".htm")):
        return SPELL_DIR / marker
    matches = sorted(p for p in SPELL_DIR.iterdir() if p.name.startswith(marker))
    if not matches:
        raise FileNotFoundError(f"no HTML file for {source}: {marker}")
    return matches[0]


def extract_body(path: Path) -> str:
    soup = BeautifulSoup(read_html(path), "html.parser")
    text = soup.get_text("\n")
    text = re.sub(r"[\xa0\u3000\ufeff]+", " ", text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n+", "\n", text)
    return text.strip()


def title_from_prefix(text: str, field_pos: int) -> Optional[Tuple[int, str]]:
    window_start = max(0, field_pos - 220)
    prefix = text[window_start:field_pos]
    stripped = prefix.rstrip()
    if not stripped:
        return None

    # School-tagged titles in VC/HA often start with 【...】 after translator notes.
    school_at = stripped.rfind("【")
    if school_at >= 0 and len(stripped) - school_at <= 130:
        title = stripped[school_at:].strip()
        if not any(label in title for label in FIELD_NORMALIZE_LABELS) and looks_like_title(title):
            return window_start + school_at, title

    # Otherwise use the final clause after sentence punctuation as the title zone.
    local_start = max(stripped.rfind(ch) for ch in "。！？")
    segment_start = local_start + 1 if local_start >= 0 else 0
    raw_segment = stripped[segment_start:]
    leading = len(raw_segment) - len(raw_segment.lstrip())
    segment = raw_segment.strip()
    segment_abs = window_start + segment_start + leading

    parenthesized = parenthesized_title_from_segment(segment)
    if parenthesized:
        rel_start, title = parenthesized
        start = segment_abs + rel_start
        if looks_like_title(title):
            return start, title

    # Drop short section labels before a real title.
    for pattern in [
        r".*?([^\s。！？；;（）()]{1,70}\s+[A-Z][A-Za-z][A-Za-z0-9 ,'’`\-.]+)$",
    ]:
        match = re.match(pattern, segment)
        if match:
            title = match.group(1).strip()
            start = segment_abs + match.start(1)
            if looks_like_title(title):
                return start, title
    return None


def parenthesized_title_from_segment(segment: str) -> Optional[Tuple[int, str]]:
    matches = list(re.finditer(r"[（(][^）)]*[A-Za-z][^）)]*[）)](?:\[[^\]]+\])?", segment))
    if not matches:
        return None
    match = matches[-1]
    if match.end() < len(segment.rstrip()) - 2:
        return None
    before = segment[: match.start()]
    # If this is a list item after a previous English parenthesized title, cut
    # after that previous title instead of swallowing the list tail.
    english_prev = list(re.finditer(r"[（(][^）)]*[A-Za-z][^）)]*[）)]\s+", before))
    if english_prev:
        start = english_prev[-1].end()
    else:
        separators = [before.rfind(ch) for ch in "。！？；;"]
        start = max(separators) + 1 if max(separators) >= 0 else 0
    prefix_title = before[start:]
    tokens = re.split(r"\s+", prefix_title.strip())
    if tokens and re.search(r"[\u4e00-\u9fff]", tokens[-1]):
        token_pos = prefix_title.rfind(tokens[-1])
        if token_pos >= 0:
            start += token_pos
    title = (before[start:] + match.group(0)).strip()
    title = re.sub(r"^\d+(?:\.\d+)?\s*", "", title)
    return start, title


def looks_like_title(title: str) -> bool:
    title = clean_text(title)
    if not title or len(title) > 150:
        return False
    if title[0] in "：:，,。；;、）)]":
        return False
    if not re.search(r"[\u4e00-\u9fff]", title):
        return False
    if re.match(r"^(防护|咒法|预言|惑控|塑能|幻术|死灵|变化|通用)系?[（(]", title):
        return False
    parens = re.findall(r"[（(]([^）)]*)[）)]", title)
    if parens and re.search(r"[\u4e00-\u9fff]", parens[-1]):
        return False
    if title.startswith("【") and not re.search(r"[）)]|[A-Za-z].*[（(]|[\u4e00-\u9fff]{2,}.*[A-Za-z]", title):
        return False
    if not (re.search(r"[A-Za-z]", title) or title.startswith("【")):
        return False
    if title.startswith(("等级", "施法时间", "施放时间", "成分", "范围", "目标", "持续", "豁免", "抗力")):
        return False
    return True


PROSE_TAIL_MARKERS = [
    " 该法术",
    " 此法术",
    " 这个法术",
    " 本法术",
    " 此幻象",
    " 这种法术",
    " 这是",
    " 你",
    " 你的",
    " 目标",
    " 动物",
    " 当",
    " 高举",
    " 曾经",
    " 如同",
    " 只要",
    " 致命",
    " 除了",
    " 当你",
    " 如果",
    " 若",
    " 被",
    " 通过",
    " 透过",
    " 作为",
]


def repair_more_record(record: Dict) -> Dict:
    for key, value in list(record.items()):
        if isinstance(value, str):
            record[key] = clean_text(re.sub(r"https?://\S+", "", value))
    effect_parts = []
    level_value = clean_text(record.get("等级", ""))
    if not record.get("学派") and level_value:
        school_match = re.match(r"^(.+?[\]】])\s+(.+\d.*)$", level_value)
        if school_match:
            record["学派"] = school_match.group(1).strip()
            record["等级"] = school_match.group(2).strip()
    for field in ["等级", "施法时间", "成分", "范围", "区域", "目标", "持续", "豁免", "法术抗力"]:
        value = clean_text(record.get(field, ""))
        if not value:
            continue
        best = None
        for marker in PROSE_TAIL_MARKERS:
            pos = value.find(marker)
            if pos > 0 and len(value[pos:].strip()) > 16:
                best = pos if best is None else min(best, pos)
        if best is None:
            continue
        head = value[:best].strip()
        tail = value[best:].strip()
        if field == "等级" and not re.search(r"\d\s*$", head):
            continue
        record[field] = head
        effect_parts.append(tail)
    if effect_parts:
        existing = record.get("效果", "").strip()
        record["效果"] = "\n".join([*effect_parts, existing]).strip()
    return record


def find_inline_starts(text: str) -> List[int]:
    starts = set()
    label_re = re.compile(r"(学派|等级|环位|来源)\s*[:：]?", re.S)
    for match in label_re.finditer(text):
        if match.group(1) in {"等级", "环位"} and match.start() > 0 and text[match.start() - 1] in "/／":
            continue
        found = title_from_prefix(text, match.start())
        if found:
            starts.add(found[0])
    filtered = []
    for start in sorted(starts):
        tail = text[start:].lstrip()
        if any(tail.startswith(label) for label in FIELD_NORMALIZE_LABELS):
            continue
        filtered.append(start)
    return filtered


def normalize_no_colon_fields(text: str) -> str:
    for label in sorted(FIELD_NORMALIZE_LABELS, key=len, reverse=True):
        text = re.sub(rf"(?<![\u4e00-\u9fffA-Za-z/／])({re.escape(label)})\s+(?![:：])", r"\1：", text)
    return text


def split_heading_and_rest(block_text: str) -> Tuple[str, str]:
    label_re = re.compile(r"(学派|等级|环位|来源)\s*[:：]?", re.S)
    match = label_re.search(block_text)
    if not match:
        return clean_text(block_text), ""
    return clean_text(block_text[: match.start()]), block_text[match.start() :].strip()


def parse_source_only_block(heading: str, rest: str, source: str) -> Optional[Dict]:
    match = SOURCE_FIELD_RE.search(rest)
    if not match:
        return None
    return {
        "name": clean_text(heading),
        "source_book": source.lower(),
        "法术类型": "mythic" if source == "MA" else "normal",
        "学派": "神话",
        "等级": "",
        "来源法术": clean_text(match.group("source")),
        "效果": clean_text(match.group("effect")),
    }


def parse_text_block(block_text: str, source: str) -> Optional[Dict]:
    block_text = clean_text(block_text)
    if not block_text:
        return None
    block_text = normalize_no_colon_fields(block_text)
    heading, rest = split_heading_and_rest(block_text)
    if not looks_like_title(heading):
        return None

    if rest.startswith("来源"):
        raw = parse_source_only_block(heading, rest, source)
        if raw:
            return raw

    expected = {
        "name": heading,
        "source_book": source,
        "english_key": ascii_key(heading),
        "cn_key": cn_key(heading),
    }
    raw = parse_block([heading, rest], expected)
    raw["source_book"] = source.lower()
    raw["法术类型"] = "mythic" if source == "MA" else "normal"
    return repair_more_record(raw)


def dedupe_records(records: List[Dict]) -> List[Dict]:
    by_key: Dict[str, Dict] = {}
    order: List[str] = []
    for record in records:
        name = record.get("name", "")
        key = ascii_key(name) or cn_key(name)
        if not key:
            key = name
        previous = by_key.get(key)
        if previous is None:
            by_key[key] = record
            order.append(key)
            continue
        prev_score = sum(1 for value in previous.values() if isinstance(value, str) and value.strip()) + len(previous.get("效果", ""))
        next_score = sum(1 for value in record.values() if isinstance(value, str) and value.strip()) + len(record.get("效果", ""))
        if next_score > prev_score:
            by_key[key] = record
    return [by_key[key] for key in order]


def extract_source(source: str, path: Path) -> List[Dict]:
    text = extract_body(path)
    starts = find_inline_starts(text)
    records = []
    for idx, start in enumerate(starts):
        end = starts[idx + 1] if idx + 1 < len(starts) else len(text)
        raw = parse_text_block(text[start:end], source)
        if raw:
            records.append(raw)
    return dedupe_records(records)


def normalize_more_model(raw: Dict, source: str, index: int) -> Dict:
    model = normalize_model(raw, source, index)
    spell_type = "mythic" if source == "MA" else "normal"
    model["spell_type"] = spell_type
    model["type_label"] = "神话法术" if spell_type == "mythic" else "普通法术"
    raw_fields = model.setdefault("raw_fields", {})
    raw_fields["法术类型"] = model["type_label"]
    if raw.get("来源法术"):
        model["base_spell"] = raw.get("来源法术", "")
    return model


def quality_report(source: str, raw: List[Dict], model: List[Dict]) -> Dict:
    missing_required = {}
    for field in ["name", "source_book", "school", "effect"]:
        missing = [item["spell_id"] for item in model if not item.get(field)]
        missing_required[field] = {"count": len(missing), "samples": missing[:20]}
    missing_level = [
        item["spell_id"]
        for item in model
        if source != "MA" and not item.get("level_raw")
    ]
    level_fail = [
        item["spell_id"]
        for item in model
        if item.get("level_raw") and not item.get("level_by_class")
    ]
    labels = ["学派", "等级", "环位", "施法时间", "施放时间", "成分", "范围", "区域", "目标", "持续", "豁免", "法术抗力", "抗力"]
    effect_label_hits = []
    for item in model:
        effect = item.get("effect") or ""
        for label in labels:
            if f"{label}：" in effect or f"{label}:" in effect:
                effect_label_hits.append({"spell_id": item["spell_id"], "label": label, "snippet": effect[:220]})
                break
    polluted = []
    for item in model:
        for field in ["level_raw", "cast_time", "components", "range", "area", "target", "duration", "save", "spell_resistance"]:
            value = item.get(field) or ""
            if any(f"{label}：" in value or f"{label}:" in value for label in labels):
                polluted.append({"spell_id": item["spell_id"], "field": field, "value": value[:180]})
    type_counts = Counter(item.get("spell_type", "") for item in model)
    return {
        "source_book": source,
        "extracted_spell_count": len(model),
        "spell_type_counts": dict(type_counts),
        "missing_required": missing_required,
        "missing_level_count": len(missing_level),
        "missing_level_samples": missing_level[:20],
        "level_parse_fail_count": len(level_fail),
        "level_parse_fail_samples": level_fail[:20],
        "polluted_field_count": len(polluted),
        "polluted_field_samples": polluted[:20],
        "effect_label_hit_count": len(effect_label_hits),
        "effect_label_hit_samples": effect_label_hits[:20],
        "sample_names": [item.get("name") for item in raw[:10]],
    }


def main() -> None:
    summary = []
    for source in SOURCE_HTML:
        path = resolve_source_path(source)
        raw = extract_source(source, path)
        model = [normalize_more_model(item, source, idx + 1) for idx, item in enumerate(raw)]
        out_dir = RESULT_DIR / source.lower()
        out_dir.mkdir(parents=True, exist_ok=True)
        raw_path = out_dir / f"spells-{source.lower()}.json"
        model_path = out_dir / f"spells-{source.lower()}-model.json"
        qa_path = out_dir / f"spells-{source.lower()}-qa.json"
        raw_path.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
        model_path.write_text(json.dumps(model, ensure_ascii=False, indent=2), encoding="utf-8")
        qa = quality_report(source, raw, model)
        qa_path.write_text(json.dumps(qa, ensure_ascii=False, indent=2), encoding="utf-8")
        row = {
            "source_book": source,
            "html": str(path.relative_to(ROOT)).replace("\\", "/"),
            "raw_json": str(raw_path.relative_to(ROOT)).replace("\\", "/"),
            "model_json": str(model_path.relative_to(ROOT)).replace("\\", "/"),
            "qa_json": str(qa_path.relative_to(ROOT)).replace("\\", "/"),
            **{k: qa[k] for k in ["extracted_spell_count", "missing_level_count", "level_parse_fail_count", "polluted_field_count", "effect_label_hit_count"]},
        }
        summary.append(row)
        print(f"{source}: {len(model)}")
    report = {"summary": summary, "total": sum(item["extracted_spell_count"] for item in summary)}
    report_path = RESULT_DIR / "more-books-extraction-report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"total: {report['total']}")


if __name__ == "__main__":
    main()
