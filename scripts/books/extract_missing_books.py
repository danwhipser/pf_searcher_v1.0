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
from bisect import bisect_right
from collections import Counter, defaultdict
from typing import Dict, List, Optional, Tuple

from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[2]
HTML_DIR = ROOT / "Pathfinder v2.14 SC"
RESULT_DIR = ROOT / "result"
LOCATED_INDEX = RESULT_DIR / "index" / "spells-index-located.json"

SOURCE_HTML = {
    "AARCH": "Spell AArch.html",
    "COTR": "Spell CotR.html",
    "FOB": "Spell FoP.html",
    "FOC": "Spell FoP.html",
    "FOP": "Spell FoP.html",
    "ISG": "Spell ISG.html",
    "ISM": "Spell ISM.html",
    "ISWG": "Spell ISWG.html",
    "MTT": "Spell MTT.html",
    "RTT": "Spell MTT.html",
    "TG": "Spell TG.html",
}

FIELD_LABELS: List[Tuple[str, str]] = [
    ("法术抗力", "法术抗力"),
    ("豁免检定", "豁免"),
    ("持续时间", "持续"),
    ("施法时间", "施法时间"),
    ("施放时间", "施法时间"),
    ("法术范围", "目标"),
    ("法术目标", "目标"),
    ("学派", "学派"),
    ("环位", "等级"),
    ("等级", "等级"),
    ("位阶", "等级"),
    ("成分", "成分"),
    ("距离", "范围"),
    ("射程", "范围"),
    ("范围", "范围"),
    ("区域", "区域"),
    ("目标", "目标"),
    ("效果", "目标"),
    ("持续", "持续"),
    ("豁免", "豁免"),
    ("抗力", "法术抗力"),
]

FIELD_MAP = dict(FIELD_LABELS)
LABEL_NAMES = [label for label, _ in sorted(FIELD_LABELS, key=lambda x: -len(x[0]))]
NO_COLON_LABELS = {"豁免", "抗力"}


def clean_text(text: str) -> str:
    return re.sub(r"[\s\xa0\u3000\ufeff]+", " ", text or "").strip()


def read_html(path: Path) -> str:
    raw = path.read_bytes()
    # Spell CotR.html has two literal corruption fragments inside Blood of the
    # Martyr that break GBK byte alignment. Repair only those known byte runs.
    raw = raw.replace(b"\xd7\xd4???\xf2\xce\xde\xd6\xfa", b"\xd7\xd4\xd4\xb8\xbb\xf2\xce\xde\xd6\xfa")
    raw = raw.replace(b"\xcb\xf0\xca?SPANlang=EN-US&gt;HP</SPAN>", b"\xcb\xf0\xca\xa7HP")
    try:
        return raw.decode("gb18030")
    except UnicodeDecodeError:
        return raw.decode("utf-8", errors="ignore")


def ascii_key(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", text.lower())


def cn_key(text: str) -> str:
    text = text.split("※", 1)[0]
    text = re.split(r"[A-Za-z（(]", text, 1)[0]
    return re.sub(r"[^\u4e00-\u9fffⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ一二三四五六七八九十]+", "", text)


def english_key(name: str) -> str:
    name = name.split("※", 1)[0]
    paren = re.search(r"[（(]([^）)]*[A-Za-z][^）)]*)[）)]", name)
    if paren:
        return ascii_key(paren.group(1))
    ascii_part = "".join(re.findall(r"[A-Za-z][A-Za-z0-9`'’\-\s,]+", name))
    # Drop deity/source annotations that may be written after the English name.
    ascii_part = re.sub(r"\s{2,}.*$", "", ascii_part).strip()
    return ascii_key(ascii_part)


def line_matches_expected(line: str, expected: Dict) -> bool:
    en = expected["english_key"]
    cn = expected["cn_key"]
    line_ascii = ascii_key(line)
    if en and en in line_ascii:
        return True
    if cn and cn in cn_key(line):
        return True
    return False


def is_heading_candidate(line: Dict) -> bool:
    text = line["text"]
    if any(text.startswith(label) for label, _ in FIELD_LABELS):
        return False
    if "【" in text and "】" in text:
        return True
    inline_field = re.search(r"^(.{2,120}?)(?:学派|等级|环位)[:：]", text)
    if line.get("bold") and inline_field and re.search(r"[A-Za-z]", inline_field.group(1)):
        return True
    if line.get("bold") and len(text) <= 140:
        if text.endswith(("。", "；", ";")):
            return False
        if text.startswith(("该法术", "这个法术", "本法术", "你", "目标", "如果", "受术者", "生物")):
            return False
        if re.search(r"^.{1,70}[（(][^）)]*[A-Za-z][^）)]*[）)]", text):
            return True
        if re.search(r"^.{1,70}\s+[A-Z][A-Za-z]", text):
            return True
    return False


def strip_heading_noise(line: str, expected: Dict) -> str:
    line = clean_text(line)
    line = re.sub(r"^.*?(?=【[^】]+】)", "", line)
    line = re.sub(r"^(?:【[^】]+】|\[[^\]]+\]|[（(][^）)]*[）)])+", "", line).strip()

    en = expected["english_key"]
    if en:
        # Keep only the final clause that contains the expected English name.
        candidates = re.split(r"[。；;]", line)
        matching = [part.strip() for part in candidates if en in ascii_key(part)]
        if matching:
            line = matching[-1]

    # Remove trailing deity/book note after an English parenthesized name.
    line = re.sub(r"([）)])\s*/\s*[\u4e00-\u9fff·．A-Za-z\s]+$", r"\1", line)
    return line


def school_from_heading(line: str) -> str:
    line = clean_text(line)
    if "【" in line:
        line = line[line.rfind("【") :]
    parts = []
    while True:
        match = re.match(r"^(?:【([^】]+)】|\[([^\]]+)\]|[（(]([^）)]*)[）)])", line)
        if not match:
            break
        value = next((group for group in match.groups() if group), "")
        if value:
            parts.append(value)
        line = line[match.end() :].strip()
    return " ".join(parts)


def extract_lines(path: Path) -> List[Dict]:
    soup = BeautifulSoup(read_html(path), "html.parser")
    lines = []
    for index, p in enumerate(soup.find_all("p")):
        text = clean_text(p.get_text())
        if not text:
            continue
        lines.append(
            {
                "index": index,
                "text": text,
                "bold": p.find(["b", "strong"]) is not None,
            }
        )
    return lines


def load_expected() -> List[Dict]:
    located = json.loads(LOCATED_INDEX.read_text(encoding="utf-8"))
    expected = [
        {
            "index_id": item["index_id"],
            "name": item["name"],
            "source_book": item["source_book"],
            "english_key": english_key(item["name"]),
            "cn_key": cn_key(item["name"]),
        }
        for item in located
        if item.get("status") == "source_not_loaded"
    ]
    return expected


def find_starts(lines: List[Dict], expected_items: List[Dict]) -> Tuple[List[Dict], List[Dict]]:
    starts = []
    missing = []
    used_line_indexes = set()
    cursor = 0
    for expected in expected_items:
        found = None
        # Prefer monotonic matching because the index and HTML are in the same order.
        for pos in range(cursor, len(lines)):
            if pos in used_line_indexes:
                continue
            if is_heading_candidate(lines[pos]) and line_matches_expected(lines[pos]["text"], expected):
                found = pos
                break
        if found is None:
            for pos, line in enumerate(lines):
                if pos in used_line_indexes:
                    continue
                if is_heading_candidate(line) and line_matches_expected(line["text"], expected):
                    found = pos
                    break
        if found is None:
            missing.append(expected)
            continue
        used_line_indexes.add(found)
        cursor = found + 1
        starts.append({"line_pos": found, "expected": expected})
    return starts, missing


def split_field_segments(text: str) -> List[Tuple[str, str]]:
    matches = []
    for pos in range(len(text)):
        for label in LABEL_NAMES:
            if not text.startswith(label, pos):
                continue
            after = pos + len(label)
            suffix = text[after : after + 3]
            if re.match(r"\s*[:：]", suffix):
                matches.append((pos, after, label))
                break
            if label in NO_COLON_LABELS and (pos == 0 or text[pos - 1] in "；;。 \t") and re.match(r"\s+", suffix):
                matches.append((pos, after, label))
                break
    # De-duplicate overlaps caused by short labels inside longer labels.
    deduped = []
    occupied = set()
    for start, end, label in sorted(matches, key=lambda item: (item[0], -(item[1] - item[0]))):
        if any(index in occupied for index in range(start, end)):
            continue
        deduped.append((start, end, label))
        occupied.update(range(start, end))
    matches = deduped
    if not matches or matches[0][0] > 2:
        return []
    segments: List[Tuple[str, str]] = []
    for i, match in enumerate(matches):
        label = match[2]
        start = match[1]
        end = matches[i + 1][0] if i + 1 < len(matches) else len(text)
        value = text[start:end]
        value = re.sub(r"^[：:\s；;，,、]+", "", value).strip()
        value = re.sub(r"[；;，,、\s]+$", "", value).strip()
        if value or FIELD_MAP[label] in {"豁免", "法术抗力"}:
            segments.append((FIELD_MAP[label], value))
    return segments


def append_field(record: Dict, field: str, value: str) -> None:
    value = clean_text(value)
    if not value:
        return
    if field in record and record[field]:
        record[field] = f"{record[field]} {value}".strip()
    else:
        record[field] = value


def parse_block(block: List[str], expected: Dict) -> Dict:
    heading = block[0]
    record: Dict[str, str] = {
        "name": strip_heading_noise(heading, expected) or expected["name"],
        "source_book": expected["source_book"].lower(),
        "效果": "",
    }
    heading_school = school_from_heading(heading)
    if heading_school:
        record["学派"] = heading_school

    effect_parts: List[str] = []
    current_field: Optional[str] = None

    for offset, line in enumerate(block[1:], start=1):
        if not line:
            continue
        if line.startswith(("http://", "https://", "译者", "原译者", "校正")):
            continue
        if re.fullmatch(r"[A-Z]", line):
            continue
        if line.startswith("※"):
            continue

        segments = split_field_segments(line)
        if segments:
            for field, value in segments:
                if field == "范围" and record.get("范围") and not record.get("目标"):
                    field = "目标"
                append_field(record, field, value)
                current_field = field
            continue

        # A line immediately after the heading can be a section preface in compressed pages.
        if offset == 1 and len(line) < 20 and not re.search(r"[\u4e00-\u9fff]", line):
            continue

        # Short continuations after an incomplete field line are still field values.
        if current_field and current_field != "效果" and len(line) < 80 and not effect_parts:
            append_field(record, current_field, line)
            continue

        effect_parts.append(line)
        current_field = "效果"

    record["效果"] = "\n".join(effect_parts).strip()

    if not record.get("学派"):
        record["学派"] = ""
    return record


def parse_level_entries(level_text: str) -> Tuple[List[Dict], List[str]]:
    if not level_text:
        return [], []
    normalized = level_text.translate(str.maketrans("０１２３４５６７８９", "0123456789"))
    normalized = normalized.replace("、", "，").replace(",", "，").replace("；", "，").replace(";", "，")
    parts = [part.strip() for part in normalized.split("，") if part.strip()]
    entries, unparsed = [], []
    for part in parts:
        match = re.match(r"^(.+?)\s*([0-9]+)(?:\s|$)", part)
        if not match:
            unparsed.append(part)
            continue
        cls = match.group(1).strip()
        if cls.startswith("领域") or cls.endswith(("领域", "子域")):
            continue
        entries.append({"class": cls, "level": int(match.group(2))})
    return entries, unparsed


PROSE_MARKERS = [
    " 该法术",
    " 这个法术",
    " 本法术",
    " 你",
    " 你的",
    " 当你",
    " 如果",
    " 若",
    " 被",
    " 通过",
]


def split_prose_tail(field: str, value: str) -> Tuple[str, str]:
    value = clean_text(value)
    if not value:
        return "", ""

    if field == "法术抗力":
        match = re.match(r"^(可|无|有|不可|否|见下文|见后文|特殊(?:，见下文)?(?:[（(][^）)]{1,40}[）)])?)\s+(.+)$", value)
        if match and len(match.group(2)) > 12:
            return match.group(1).strip(), match.group(2).strip()

    for marker in PROSE_MARKERS:
        pos = value.find(marker)
        if pos > 0:
            head = value[:pos].strip()
            tail = value[pos:].strip()
            if len(tail) > 12:
                return head, tail

    if field == "等级":
        match = re.match(r"^(.+?\d)\s+((?:该|这个|本)法术.+)$", value)
        if match:
            return match.group(1).strip(), match.group(2).strip()

    if field == "持续":
        match = re.match(r"^(.+?(?:等级|轮|分钟|小时|天|瞬间|立即|永久|见下文|特殊)(?:[）)]|（D）|\([Dd]\))?)\s+(.{12,})$", value)
        if match:
            return match.group(1).strip(), match.group(2).strip()

    return value, ""


def repair_record_fields(record: Dict) -> Dict:
    effect_parts = []
    for field in ["等级", "施法时间", "成分", "范围", "区域", "目标", "持续", "豁免", "法术抗力"]:
        cleaned, tail = split_prose_tail(field, record.get(field, ""))
        if cleaned != record.get(field, ""):
            record[field] = cleaned
        if tail:
            effect_parts.append(tail)
    if effect_parts:
        existing = record.get("效果", "").strip()
        record["效果"] = "\n".join([*effect_parts, existing]).strip()
    return record


def normalize_model(raw: Dict, source: str, index: int) -> Dict:
    raw = repair_record_fields(dict(raw))
    level_raw = raw.get("等级", "").strip()
    level_by_class, level_unparsed = parse_level_entries(level_raw)
    return {
        "spell_id": f"{source.lower()}-{index:04d}",
        "name": raw.get("name", "").strip(),
        "source_book": source.lower(),
        "school": raw.get("学派", "").strip(),
        "level_raw": level_raw,
        "level_by_class": level_by_class,
        "cast_time": raw.get("施法时间", "").strip(),
        "components": raw.get("成分", "").strip(),
        "range": raw.get("范围", "").strip(),
        "area": raw.get("区域", "").strip(),
        "target": raw.get("目标", "").strip(),
        "duration": raw.get("持续", "").strip(),
        "save": raw.get("豁免", "").strip(),
        "spell_resistance": raw.get("法术抗力", "").strip(),
        "effect": raw.get("效果", "").strip(),
        "level_unparsed": level_unparsed,
        "raw_fields": raw,
    }


def parse_all() -> Tuple[Dict[str, List[Dict]], Dict]:
    expected = load_expected()
    by_html: Dict[str, List[Dict]] = defaultdict(list)
    for item in expected:
        by_html[SOURCE_HTML[item["source_book"]]].append(item)

    parsed_by_source: Dict[str, List[Dict]] = defaultdict(list)
    report = {
        "expected_total": len(expected),
        "books": {},
        "missing_starts": [],
    }

    for html_name, items in by_html.items():
        path = HTML_DIR / html_name
        lines = extract_lines(path)
        boundary_positions = sorted(
            index for index, line in enumerate(lines) if is_heading_candidate(line)
        )
        starts, missing = find_starts(lines, items)
        starts = sorted(starts, key=lambda item: item["line_pos"])
        for idx, start in enumerate(starts):
            next_boundary = bisect_right(boundary_positions, start["line_pos"])
            end = boundary_positions[next_boundary] if next_boundary < len(boundary_positions) else len(lines)
            block = [line["text"] for line in lines[start["line_pos"] : end]]
            raw = parse_block(block, start["expected"])
            parsed_by_source[start["expected"]["source_book"]].append(raw)
        report["books"][html_name] = {
            "expected": len(items),
            "located": len(starts),
            "missing": [item["name"] for item in missing],
        }
        report["missing_starts"].extend(
            {"source_book": item["source_book"], "name": item["name"]} for item in missing
        )

    return parsed_by_source, report


def quality_report(source: str, raw: List[Dict], model: List[Dict], expected_count: int) -> Dict:
    missing_required = {}
    for field in ["name", "source_book", "school", "level_raw", "effect"]:
        missing = [item["spell_id"] for item in model if not item.get(field)]
        missing_required[field] = {"count": len(missing), "samples": missing[:20]}
    level_fail = [
        item["spell_id"]
        for item in model
        if item.get("level_raw") and not item.get("level_by_class")
    ]
    pollution_markers = [" 学派", " 等级", " 施法时间", " 成分", " 范围", " 区域", " 持续", " 豁免", " 法术抗力"]
    polluted = []
    for item in model:
        for field in ["level_raw", "cast_time", "components", "range", "area", "target", "duration", "save", "spell_resistance"]:
            value = item.get(field) or ""
            if any(marker in value for marker in pollution_markers):
                polluted.append({"spell_id": item["spell_id"], "field": field, "value": value[:180]})
    return {
        "source_book": source,
        "expected_spell_count": expected_count,
        "extracted_spell_count": len(model),
        "coverage_ratio": round(len(model) / expected_count, 4) if expected_count else 0,
        "missing_required": missing_required,
        "level_parse_fail_count": len(level_fail),
        "level_parse_fail_samples": level_fail[:20],
        "polluted_field_count": len(polluted),
        "polluted_field_samples": polluted[:20],
        "sample_names": [item.get("name") for item in raw[:10]],
    }


def write_outputs(parsed_by_source: Dict[str, List[Dict]], report: Dict) -> None:
    expected_counts = Counter(item["source_book"] for item in load_expected())
    summary = []
    for source in sorted(parsed_by_source):
        raw = parsed_by_source[source]
        model = [normalize_model(item, source, index + 1) for index, item in enumerate(raw)]
        source_dir = RESULT_DIR / source.lower()
        source_dir.mkdir(parents=True, exist_ok=True)
        raw_path = source_dir / f"spells-{source.lower()}.json"
        model_path = source_dir / f"spells-{source.lower()}-model.json"
        qa_path = source_dir / f"spells-{source.lower()}-qa.json"
        raw_path.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
        model_path.write_text(json.dumps(model, ensure_ascii=False, indent=2), encoding="utf-8")
        qa = quality_report(source, raw, model, expected_counts[source])
        qa_path.write_text(json.dumps(qa, ensure_ascii=False, indent=2), encoding="utf-8")
        summary.append(
            {
                "source_book": source,
                "raw_json": str(raw_path.relative_to(ROOT)).replace("\\", "/"),
                "model_json": str(model_path.relative_to(ROOT)).replace("\\", "/"),
                "qa_json": str(qa_path.relative_to(ROOT)).replace("\\", "/"),
                "expected_spell_count": expected_counts[source],
                "extracted_spell_count": len(model),
                "coverage_ratio": qa["coverage_ratio"],
                "missing_required": qa["missing_required"],
                "level_parse_fail_count": qa["level_parse_fail_count"],
                "polluted_field_count": qa["polluted_field_count"],
            }
        )

    report["summary"] = summary
    report_path = RESULT_DIR / "missing-books-extraction-report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    parsed_by_source, report = parse_all()
    write_outputs(parsed_by_source, report)
    total = sum(len(items) for items in parsed_by_source.values())
    print(f"extracted {total}/{report['expected_total']} spells")
    for source in sorted(parsed_by_source):
        print(f"{source}: {len(parsed_by_source[source])}")
    if report["missing_starts"]:
        print("missing starts:")
        for item in report["missing_starts"]:
            print(f"- {item['source_book']} {item['name']}")


if __name__ == "__main__":
    main()
