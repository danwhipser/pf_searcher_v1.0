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
import urllib.parse
import urllib.request
from typing import Dict, List, Tuple

from bs4 import BeautifulSoup

from scripts.books.extract_missing_books import ROOT, RESULT_DIR, ascii_key, clean_text, normalize_model, read_html


SOURCES = {
    "ISG": {
        "title": "Inner Sea Gods",
        "local": ROOT / "Pathfinder v2.14 SC" / "Spell ISG.html",
        "aon_fixed": "Inner Sea Gods",
        "aon_count": 65,
    },
    "ISI": {
        "title": "Inner Sea Intrigue",
        "local": ROOT / "Pathfinder v2.14 SC" / "page_1104.html",
        "aon_fixed": "Inner Sea Intrigue",
        "aon_count": 26,
    },
}

LABEL_MAP = {
    "学派": "学派",
    "等级": "等级",
    "环位": "等级",
    "位阶": "等级",
    "施法时间": "施法时间",
    "施放时间": "施法时间",
    "成分": "成分",
    "距离": "范围",
    "范围": "范围",
    "射程": "范围",
    "法术目标": "目标",
    "目标": "目标",
    "区域": "区域",
    "效果": "目标",
    "持续时间": "持续",
    "持续": "持续",
    "豁免检定": "豁免",
    "豁免": "豁免",
    "法术抗力": "法术抗力",
    "抗力": "法术抗力",
}
LABEL_RE = re.compile(
    r"(法术抗力|豁免检定|持续时间|施法时间|施放时间|法术目标|学派|等级|环位|位阶|成分|距离|范围|射程|目标|区域|效果|持续|豁免|抗力)\s*[:：]"
)

LOCAL_NAME_OVERRIDES = {
    "Night of Blades": ["Night of Blade"],
    "Pick Your Poison": ["Pick your Posion", "Pick your Poison"],
}


def fetch_aon_names(fixed_source: str) -> List[str]:
    url = f"https://aonprd.com/SourceDisplay.aspx?FixedSource={urllib.parse.quote_plus(fixed_source)}"
    request = urllib.request.Request(url, headers={"User-Agent": "PF_RAG source checker"})
    html = urllib.request.urlopen(request, timeout=30).read().decode("utf-8", errors="replace")
    soup = BeautifulSoup(html, "html.parser")
    for heading in soup.find_all(["h2", "h3"]):
        if not heading.get_text(" ", strip=True).startswith("Spells ["):
            continue
        names: List[str] = []
        for sibling in heading.find_next_siblings():
            if sibling.name in {"h2", "h3"}:
                break
            if sibling.name == "a":
                text = sibling.get_text(" ", strip=True)
                if text:
                    names.append(text)
                continue
            for link in sibling.find_all("a"):
                text = link.get_text(" ", strip=True)
                if text:
                    names.append(text)
        return names
    raise RuntimeError(f"Could not find Spells section for {fixed_source}")


def extract_text(path: Path) -> str:
    soup = BeautifulSoup(read_html(path), "html.parser")
    return clean_text(soup.get_text(" "))


def name_variants(name: str) -> List[str]:
    variants = [name, name.upper(), name.title()]
    variants.extend(item.replace("'", "’") for item in list(variants))
    variants.extend(item.replace("'", "`") for item in list(variants))
    variants.extend(item.replace(",", "，") for item in list(variants))
    variants.extend(item.replace(", ", "，") for item in list(variants))
    variants.extend(LOCAL_NAME_OVERRIDES.get(name, []))
    return list(dict.fromkeys(variants))


def heading_start(text: str, english_pos: int) -> int:
    window_start = max(0, english_pos - 160)
    window = text[window_start:english_pos]
    rel = max(window.rfind(mark) for mark in ["。", "；", ";"])
    return window_start + rel + 1 if rel >= 0 else max(0, english_pos - 80)


def locate_spell_starts(text: str, names: List[str]) -> Tuple[List[Dict], List[str]]:
    starts: List[Dict] = []
    missing: List[str] = []
    search_from = 0
    lowered = text.lower()
    for name in names:
        best_pos = -1
        best_variant = ""
        for variant in name_variants(name):
            pos = lowered.find(variant.lower(), search_from)
            if pos >= 0 and (best_pos < 0 or pos < best_pos):
                best_pos = pos
                best_variant = variant
        if best_pos < 0:
            missing.append(name)
            continue
        starts.append(
            {
                "aon_name": name,
                "local_variant": best_variant,
                "start": heading_start(text, best_pos),
                "english_pos": best_pos,
            }
        )
        search_from = best_pos + max(1, len(best_variant))
    return starts, missing


def clean_heading(heading: str, aon_name: str, local_variant: str) -> str:
    heading = clean_text(heading)
    candidates = []
    for variant in [local_variant, *name_variants(aon_name)]:
        if not variant:
            continue
        precise_patterns = [
            rf"[\u4e00-\u9fff]{{1,40}}\s*[（(]\s*{re.escape(variant)}\s*[）)](?:\s*[（(][^）)]{{1,30}}[）)])?",
            rf"[\u4e00-\u9fff][\u4e00-\u9fff·、]{{0,40}}\s+{re.escape(variant)}(?:\s*[（(][^）)]{{1,30}}[）)])?",
        ]
        for pattern in precise_patterns:
            candidates.extend(re.finditer(pattern, heading, re.I))
    if candidates:
        return clean_text(candidates[-1].group(0))

    lowered = heading.lower()
    pos = lowered.rfind(local_variant.lower()) if local_variant else -1
    if pos < 0:
        pos = lowered.rfind(aon_name.lower())
    if pos >= 0:
        prefix = heading[max(0, pos - 60) : pos]
        rel = max(prefix.rfind(mark) for mark in ["。", "；", ";"])
        start = max(0, pos - 60) + rel + 1 if rel >= 0 else max(0, pos - 40)
        return clean_text(heading[start:])
    return heading


def split_last_field_tail(field: str, value: str) -> Tuple[str, str]:
    value = clean_text(value)
    if not value:
        return "", ""

    if field == "法术抗力":
        match = re.match(r"^(可|不可|无|有|否|见下文|特殊)([（(][^）)]*[）)])?\s+(.{8,})$", value)
        if match:
            return clean_text("".join(part or "" for part in match.groups()[:2])), match.group(3).strip()
        return value, ""

    if field == "豁免":
        match = re.match(r"^(.{1,45}?(?:无效|否定|减半|通过则[^，。；;]{1,20}|无|见下文|部分|特殊)(?:[（(][^）)]*[）)])?)\s+(.{12,})$", value)
        if match:
            return match.group(1).strip(), match.group(2).strip()
        return value, ""

    if field == "持续":
        match = re.match(
            r"^(.{1,60}?(?:立即|永久|专注|特殊|见正文|见下文|轮|分钟|小时|天|周|年|等级|释放|消解|解消)(?:[（(][^）)]*[）)])?(?:或[^，。；;]{1,30})?)\s+(.{12,})$",
            value,
        )
        if match:
            return match.group(1).strip(), match.group(2).strip()
        return value, ""

    if field in {"目标", "范围"}:
        match = re.match(r"^(.{1,80}?(?:你|自我|自己|生物|物体|区域|目标|文件|接触|个人|见正文|见下文|等级|尺|英尺|里|巢|黄蜂|文本|方格|路径|痕迹))\s+(.{20,})$", value)
        if match:
            return match.group(1).strip(), match.group(2).strip()
        return value, ""

    return value, ""


def parse_spell_block(block: str, aon_name: str, local_variant: str, source: str) -> Dict:
    block = clean_text(block)
    matches = list(LABEL_RE.finditer(block))
    if not matches:
        raise ValueError(f"No field labels found in block for {aon_name}")

    heading = clean_heading(block[: matches[0].start()], aon_name, local_variant)
    record: Dict[str, str] = {
        "name": heading,
        "source_book": source.lower(),
        "效果": "",
        "aon_name": aon_name,
    }
    effect_parts: List[str] = []

    for index, match in enumerate(matches):
        raw_label = match.group(1)
        field = LABEL_MAP[raw_label]
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(block)
        value = clean_text(block[start:end].strip(" ；;"))
        if not value:
            continue
        if index == len(matches) - 1:
            value, tail = split_last_field_tail(field, value)
            if tail:
                effect_parts.append(tail)
        if field == "目标" and record.get("目标"):
            record["目标"] = f"{record['目标']}；{value}".strip("；")
        else:
            record[field] = value

    if effect_parts:
        record["效果"] = "\n".join(effect_parts).strip()
    return repair_record(record)


def repair_record(record: Dict) -> Dict:
    # Normalize common local typos while preserving the translated title.
    replacements = {
        "Pick your Posion": "Pick Your Poison",
        "Night of Blade": "Night of Blades",
    }
    name = record.get("name", "")
    for old, new in replacements.items():
        name = re.sub(re.escape(old), new, name, flags=re.I)
    record["name"] = clean_text(name)

    if record.get("学派"):
        record["学派"] = record["学派"].rstrip("；;")
    if record.get("等级"):
        record["等级"] = record["等级"].rstrip("；;")
    return record


def dedupe_records(records: List[Dict]) -> List[Dict]:
    seen = set()
    out = []
    for record in records:
        key = ascii_key(record.get("aon_name") or record.get("name", ""))
        if key in seen:
            continue
        seen.add(key)
        out.append(record)
    return out


def quality_report(source: str, raw: List[Dict], model: List[Dict], aon_count: int, missing_starts: List[str]) -> Dict:
    missing_required = {}
    for field in ["name", "source_book", "school", "level_raw", "effect"]:
        missing = [item["spell_id"] for item in model if not item.get(field)]
        missing_required[field] = {"count": len(missing), "samples": missing[:20]}
    level_fail = [item["spell_id"] for item in model if item.get("level_raw") and not item.get("level_by_class")]
    labels = ["学派", "等级", "环位", "施法时间", "施放时间", "成分", "距离", "范围", "区域", "目标", "持续", "豁免", "法术抗力", "抗力"]
    polluted = []
    for item in model:
        for field in ["level_raw", "cast_time", "components", "range", "area", "target", "duration", "save", "spell_resistance"]:
            value = item.get(field) or ""
            if any(f"{label}：" in value or f"{label}:" in value for label in labels):
                polluted.append({"spell_id": item["spell_id"], "field": field, "value": value[:180]})
    return {
        "source_book": source,
        "aon_expected_count": aon_count,
        "extracted_spell_count": len(model),
        "missing_starts": missing_starts,
        "missing_required": missing_required,
        "level_parse_fail_count": len(level_fail),
        "level_parse_fail_samples": level_fail[:20],
        "polluted_field_count": len(polluted),
        "polluted_field_samples": polluted[:20],
        "sample_names": [item.get("name", "") for item in raw[:10]],
    }


def write_source(source: str, config: Dict) -> Dict:
    names = fetch_aon_names(config["aon_fixed"])
    text = extract_text(config["local"])
    starts, missing = locate_spell_starts(text, names)
    records: List[Dict] = []
    for index, start in enumerate(starts):
        end = starts[index + 1]["start"] if index + 1 < len(starts) else len(text)
        block = text[start["start"] : end]
        records.append(parse_spell_block(block, start["aon_name"], start["local_variant"], source))
    records = dedupe_records(records)
    model = [normalize_model(record, source, index + 1) for index, record in enumerate(records)]
    for item in model:
        item["spell_type"] = "normal"
        item["type_label"] = "普通法术"
        item["source_display"] = source
        item["source_title"] = config["title"]
        item["raw_fields"]["来源书名"] = config["title"]
        item["raw_fields"]["目录缩写"] = source

    out_dir = RESULT_DIR / source.lower()
    out_dir.mkdir(parents=True, exist_ok=True)
    raw_path = out_dir / f"spells-{source.lower()}.json"
    model_path = out_dir / f"spells-{source.lower()}-model.json"
    qa_path = out_dir / f"spells-{source.lower()}-qa.json"
    raw_path.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    model_path.write_text(json.dumps(model, ensure_ascii=False, indent=2), encoding="utf-8")
    qa = quality_report(source, records, model, config["aon_count"], missing)
    qa["html"] = str(config["local"].relative_to(ROOT)).replace("\\", "/")
    qa_path.write_text(json.dumps(qa, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "source_book": source,
        "title": config["title"],
        "html": qa["html"],
        "raw_json": str(raw_path.relative_to(ROOT)).replace("\\", "/"),
        "model_json": str(model_path.relative_to(ROOT)).replace("\\", "/"),
        "qa_json": str(qa_path.relative_to(ROOT)).replace("\\", "/"),
        "aon_expected_count": config["aon_count"],
        "extracted_spell_count": len(model),
        "missing_start_count": len(missing),
        "level_parse_fail_count": qa["level_parse_fail_count"],
        "polluted_field_count": qa["polluted_field_count"],
    }


def main() -> None:
    summary = [write_source(source, config) for source, config in SOURCES.items()]
    report = {"summary": summary, "total": sum(item["extracted_spell_count"] for item in summary)}
    report_path = RESULT_DIR / "isg-isi-extraction-report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    for row in summary:
        print(f"{row['source_book']}: {row['extracted_spell_count']}/{row['aon_expected_count']}")
    print(f"wrote {report_path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
