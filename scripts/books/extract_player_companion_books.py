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
from typing import Dict, List, Optional, Tuple
from urllib.parse import unquote

from bs4 import BeautifulSoup

from scripts.books.extract_missing_books import (
    RESULT_DIR,
    ROOT,
    ascii_key,
    clean_text,
    cn_key,
    normalize_model,
    parse_block,
    read_html,
)
from scripts.books.extract_more_books import (
    FIELD_NORMALIZE_LABELS,
    dedupe_records,
    extract_source as extract_inline_source,
    quality_report,
    repair_more_record,
)


HTML_DIR = ROOT / "Pathfinder v2.14 SC"
MANIFEST_PATH = RESULT_DIR / "player-companion-source-manifest.json"
REPORT_PATH = RESULT_DIR / "player-companion-extraction-report.json"
REPORT_MD_PATH = RESULT_DIR / "player-companion-extraction-report.md"
SUPPLEMENT_PATH = ROOT / "data" / "player_companion_aon_supplements.json"
MERGE_FALLBACK_SOURCES = {"OO", "AOE", "QC", "HOTHC"}

EXTRA_LABELS = ["环级", "环阶", "动作", "施放时间", "距离", "抗力"]
ALL_LABELS = sorted(set(FIELD_NORMALIZE_LABELS + EXTRA_LABELS), key=len, reverse=True)
SECTION_INTRO_MARKERS = [
    "吟游诗人和牧师的法术",
    "游侠和德鲁伊法术",
]


def source_dir_name(source: str) -> str:
    return source.lower()


def resolve_html_path(local: str) -> Path:
    local = unquote(local).replace("/", "\\")
    candidates = [
        HTML_DIR / local,
        ROOT / "spell" / local,
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate

    name = Path(local).name
    for base in [HTML_DIR, ROOT / "spell"]:
        for path in base.iterdir():
            if path.name == name:
                return path
    raise FileNotFoundError(local)


def extract_body(path: Path) -> str:
    soup = BeautifulSoup(read_html(path), "html.parser")
    text = soup.get_text("\n")
    text = re.sub(r"[\xa0\u3000\ufeff]+", " ", text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n+", "\n", text)
    return text.strip()


def normalize_pc_labels(text: str) -> str:
    replacements = {
        "环级": "等级",
        "环阶": "等级",
        "位阶": "等级",
        "动作": "施法时间",
        "施放时间": "施法时间",
        "距离": "范围",
        "射程": "范围",
        "区域": "区域",
        "抗力": "法术抗力",
    }
    for src, dst in sorted(replacements.items(), key=lambda item: -len(item[0])):
        text = re.sub(rf"(?<![\u4e00-\u9fffA-Za-z/／]){re.escape(src)}\s*[:：]?", f"{dst}：", text)
    for label in ALL_LABELS:
        if label in replacements:
            continue
        text = re.sub(rf"(?<![\u4e00-\u9fffA-Za-z/／])({re.escape(label)})\s+(?![:：])", r"\1：", text)
    return text


def looks_like_pc_title(title: str) -> bool:
    title = clean_text(title)
    if not title or len(title) > 150:
        return False
    if not re.search(r"[\u4e00-\u9fff]", title):
        return False
    if not re.search(r"[A-Za-z]", title):
        return False
    if any(label in title for label in ALL_LABELS):
        return False
    if title.startswith(("译者", "原译者", "校正", "出自", "法术", "神话")):
        return False
    return True


def title_from_prefix(text: str, field_pos: int) -> Optional[Tuple[int, str]]:
    window_start = max(0, field_pos - 260)
    prefix = text[window_start:field_pos].rstrip()
    if not prefix:
        return None

    # Some Player Companion pages have malformed titles such as
    # "主观现实(Subjective Reality[影响心灵]" or "幽影耐力SHADOW ENDURANCE[阴影]".
    # Keep the Chinese title prefix narrow so prose before the title is not
    # swallowed as part of the spell name.
    precise_title_patterns = [
        r"[\u4e00-\u9fff]{2,40}\s*[\uff08(][A-Za-z][A-Za-z0-9\s,'`\-.]{2,90}(?:[\uff09)]|\[[^\]]+\])(?:\[[^\]]+\])?",
        r"[\u4e00-\u9fff]{2,40}\s+[A-Z][A-Z0-9\s,'`\-.]{3,90}(?:\[[^\]]+\])?",
        r"[\u4e00-\u9fff]{2,40}[A-Z][A-Z0-9\s,'`\-.]{3,90}(?:\[[^\]]+\])?",
    ]
    precise_candidates = []
    for pattern in precise_title_patterns:
        precise_candidates.extend(re.finditer(pattern, prefix))
    for match in sorted(precise_candidates, key=lambda item: item.start(), reverse=True):
        title = clean_text(match.group(0))
        if looks_like_pc_title(title):
            return window_start + match.start(), title
    ascii_title_patterns = [
        r"[\u4e00-\u9fff]{2,40}\s*[（(][A-Za-z][A-Za-z0-9\s,'’`\-.]{2,90}(?:[）)]|\[[^\]]+\])(?:\[[^\]]+\])?",
        r"[\u4e00-\u9fff]{2,40}\s+[A-Z][A-Z0-9\s,'’`\-.]{2,90}(?:\[[^\]]+\])?",
        r"[\u4e00-\u9fff]{2,40}[A-Z][A-Z0-9\s,'’`\-.]{2,90}(?:\[[^\]]+\])?",
    ]
    for pattern in ascii_title_patterns:
        matches = list(re.finditer(pattern, prefix))
        if not matches:
            continue
        match = matches[-1]
        title = clean_text(match.group(0))
        if looks_like_pc_title(title):
            return window_start + match.start(), title
    strict_title_patterns = [
        r"[\u4e00-\u9fff][\u4e00-\u9fff路銆?锛?·、]{1,40}\s*[锛?][A-Za-z][A-Za-z0-9 ,'鈥檂\-.]{2,90}(?:[锛?]|\[[^\]]+\])(?:\[[^\]]+\])?",
        r"[\u4e00-\u9fff][\u4e00-\u9fff路銆?锛?·、]{1,40}\s+[A-Z][A-Z0-9 ,'鈥檂\-.]{2,90}(?:\[[^\]]+\])?",
        r"[\u4e00-\u9fff][\u4e00-\u9fff路銆?锛?·、]{1,40}[A-Z][A-Z0-9 ,'鈥檂\-.]{2,90}(?:\[[^\]]+\])?",
    ]
    for pattern in strict_title_patterns:
        matches = list(re.finditer(pattern, prefix))
        if not matches:
            continue
        match = matches[-1]
        title = clean_text(match.group(0))
        if looks_like_pc_title(title):
            return window_start + match.start(), title

    # Prefer title with Chinese + parenthesized English.
    matches = list(re.finditer(r"[\u4e00-\u9fff][^\n。！？；;]{0,90}[（(][^）)]*[A-Za-z][^）)]*[）)](?:\[[^\]]+\])?", prefix))
    if matches:
        match = matches[-1]
        title = clean_text(match.group(0))
        if looks_like_pc_title(title):
            return window_start + match.start(), title

    # Fallback for titles like "预支未来 Borrowed Time".
    lines = [line.strip() for line in prefix.splitlines() if line.strip()]
    for line in reversed(lines[-5:]):
        line = re.sub(r"^.*?[。！？；;]\s*", "", line).strip()
        match = re.search(r"([\u4e00-\u9fff][\u4e00-\u9fff·、/／ -]{1,40}\s+[A-Z][A-Za-z0-9 ,'’`-]{2,80})$", line)
        if match:
            title = clean_text(match.group(1))
            if looks_like_pc_title(title):
                return window_start + prefix.rfind(match.group(1)), title
    return None


def find_starts(text: str) -> List[int]:
    normalized = normalize_pc_labels(text)
    starts = set()
    label_re = re.compile(r"(学派|等级)\s*[:：]", re.S)
    for match in label_re.finditer(normalized):
        found = title_from_prefix(normalized, match.start())
        if found:
            starts.add(found[0])
    return sorted(starts)


def split_heading_and_rest(block_text: str) -> Tuple[str, str]:
    match = re.search(r"(学派|等级)\s*[:：]", block_text, re.S)
    if not match:
        return clean_text(block_text), ""
    return clean_text(block_text[: match.start()]), block_text[match.start() :].strip()


def parse_block_text(block_text: str, source: str) -> Optional[Dict]:
    block_text = normalize_pc_labels(clean_text(block_text))
    heading, rest = split_heading_and_rest(block_text)
    if not looks_like_pc_title(heading):
        return None

    expected = {
        "name": heading,
        "source_book": source,
        "english_key": ascii_key(heading),
        "cn_key": cn_key(heading),
    }
    raw = parse_block([heading, rest], expected)
    raw["source_book"] = source.lower()
    raw["法术类型"] = "normal"
    return repair_more_record(raw)


def extract_fallback_source(source: str, path: Path) -> List[Dict]:
    text = extract_body(path)
    normalized = normalize_pc_labels(text)
    starts = find_starts(normalized)
    records = []
    for index, start in enumerate(starts):
        end = starts[index + 1] if index + 1 < len(starts) else len(normalized)
        raw = parse_block_text(normalized[start:end], source)
        if raw:
            records.append(raw)
    return dedupe_records(records)


def extract_source(source: str, path: Path) -> List[Dict]:
    records = extract_inline_source(source, path)
    if records:
        if source in MERGE_FALLBACK_SOURCES:
            fallback_records = extract_fallback_source(source, path)
            if fallback_records:
                return dedupe_records([*records, *fallback_records])
        return records
    return extract_fallback_source(source, path)


def load_aon_supplements(source: str) -> List[Dict]:
    if not SUPPLEMENT_PATH.exists():
        return []
    data = json.loads(SUPPLEMENT_PATH.read_text(encoding="utf-8"))
    records = []
    for record in data.get(source, []):
        item = dict(record)
        item["source_book"] = source.lower()
        item.setdefault("法术类型", "normal")
        item.setdefault("补充来源", "AoN")
        records.append(item)
    return records


def normalize_pc_model(raw: Dict, source: str, index: int, manifest_entry: Dict) -> Dict:
    raw = repair_pc_record(raw)
    model = normalize_model(raw, source, index)
    model["spell_type"] = "normal"
    model["type_label"] = "普通法术"
    model["source_display"] = manifest_entry.get("display_code") or source
    model["source_title"] = manifest_entry.get("title") or ""
    raw_fields = model.setdefault("raw_fields", {})
    raw_fields["法术类型"] = "普通法术"
    raw_fields["来源书名"] = model["source_title"]
    raw_fields["目录缩写"] = model["source_display"]
    return model


def repair_pc_record(raw: Dict) -> Dict:
    raw = dict(raw)
    if raw.get("name"):
        raw["name"] = re.sub(r"([（(][^\]）)]*[A-Za-z][^\]）)]*)\[", r"\1)[", raw["name"])
        raw["name"] = re.sub(r"\s*(?:出自|出处)\s*[:：].*$", "", raw["name"]).strip()
    level = clean_text(raw.get("等级", ""))
    cast_time = clean_text(raw.get("施法时间", ""))
    if re.search(r"^(?:标准动作|整轮|迅捷动作|直觉动作|即时动作|移动动作|自由动作|1\s*轮|1\s*分钟)", level) and re.search(r"\d", cast_time):
        raw["等级"], raw["施法时间"] = cast_time, level

    effect = raw.get("效果", "")
    if effect:
        cut = None
        for marker in SECTION_INTRO_MARKERS:
            pos = effect.find(marker)
            if pos > 40:
                cut = pos if cut is None else min(cut, pos)
        if cut is not None:
            raw["效果"] = effect[:cut].strip()
    return raw


def existing_model_files_for_entry(entry: Dict) -> List[str]:
    files = []
    for raw_code in entry.get("raw_codes") or []:
        code = re.sub(r"[^A-Za-z0-9_]+", "", raw_code).lower()
        path = RESULT_DIR / code / f"spells-{code}-model.json"
        if path.exists():
            files.append(str(path.relative_to(ROOT)).replace("\\", "/"))
    return files


def write_outputs(entry: Dict, raw: List[Dict], model: List[Dict], html_path: Path) -> Dict:
    source = entry["source_code"]
    out_dir = RESULT_DIR / source_dir_name(source)
    out_dir.mkdir(parents=True, exist_ok=True)

    raw_path = out_dir / f"spells-{source.lower()}.json"
    model_path = out_dir / f"spells-{source.lower()}-model.json"
    qa_path = out_dir / f"spells-{source.lower()}-qa.json"

    raw_path.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
    model_path.write_text(json.dumps(model, ensure_ascii=False, indent=2), encoding="utf-8")
    qa = quality_report(source, raw, model)
    qa["source_title"] = entry.get("title", "")
    qa["display_code"] = entry.get("display_code", "")
    qa["html"] = str(html_path.relative_to(ROOT)).replace("\\", "/")
    qa_path.write_text(json.dumps(qa, ensure_ascii=False, indent=2), encoding="utf-8")

    return {
        "source_book": source,
        "display_code": entry.get("display_code", ""),
        "title": entry.get("title", ""),
        "html": str(html_path.relative_to(ROOT)).replace("\\", "/"),
        "raw_json": str(raw_path.relative_to(ROOT)).replace("\\", "/"),
        "model_json": str(model_path.relative_to(ROOT)).replace("\\", "/"),
        "qa_json": str(qa_path.relative_to(ROOT)).replace("\\", "/"),
        "extracted_spell_count": len(model),
        "missing_level_count": qa["missing_level_count"],
        "level_parse_fail_count": qa["level_parse_fail_count"],
        "polluted_field_count": qa["polluted_field_count"],
        "effect_label_hit_count": qa["effect_label_hit_count"],
        "sample_names": [item.get("name", "") for item in raw[:5]],
    }


def write_markdown(report: Dict) -> None:
    lines = [
        "# Player Companion Extraction Report",
        "",
        f"- extracted total: {report['extracted_total']}",
        f"- reused existing total: {report['reused_existing_total']}",
        f"- skipped count: {len(report['skipped'])}",
        "",
        "| source | display | title | count | status | issues |",
        "|---|---|---|---:|---|---|",
    ]
    for row in report["summary"]:
        issues = []
        for key in ["missing_level_count", "level_parse_fail_count", "polluted_field_count", "effect_label_hit_count"]:
            if row.get(key):
                issues.append(f"{key}={row[key]}")
        lines.append(
            "| {source_book} | {display_code} | {title} | {count} | {status} | {issues} |".format(
                source_book=row.get("source_book", ""),
                display_code=row.get("display_code", ""),
                title=(row.get("title", "") or "").replace("|", "\\|"),
                count=row.get("extracted_spell_count", 0),
                status=row.get("status", "extracted"),
                issues=", ".join(issues),
            )
        )
    REPORT_MD_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    summary = []
    skipped = []
    reused_existing_total = 0

    for entry in manifest["entries"]:
        source = entry["source_code"]
        if entry.get("is_index_page"):
            skipped.append({"source_book": source, "title": entry.get("title"), "reason": "index_page"})
            continue
        if "/" in source:
            existing = existing_model_files_for_entry(entry)
            count = 0
            for file in existing:
                data = json.loads((ROOT / file).read_text(encoding="utf-8"))
                count += len(data)
            reused_existing_total += count
            summary.append(
                {
                    "source_book": source,
                    "display_code": entry.get("display_code", ""),
                    "title": entry.get("title", ""),
                    "html": entry.get("local", ""),
                    "model_json": existing,
                    "extracted_spell_count": count,
                    "status": "reused_existing_split_page",
                }
            )
            continue

        try:
            html_path = resolve_html_path(entry["local"])
            raw = extract_source(source, html_path)
            supplements = load_aon_supplements(source)
            if supplements:
                raw = dedupe_records([*raw, *supplements])
            model = [normalize_pc_model(item, source, index + 1, entry) for index, item in enumerate(raw)]
            row = write_outputs(entry, raw, model, html_path)
            row["status"] = "extracted"
            summary.append(row)
            print(f"{source}: {len(model)}")
        except Exception as exc:
            skipped.append({"source_book": source, "title": entry.get("title"), "reason": repr(exc)})
            print(f"{source}: ERROR {exc}")

    report = {
        "source": "result/player-companion-source-manifest.json",
        "summary": summary,
        "skipped": skipped,
        "extracted_total": sum(row.get("extracted_spell_count", 0) for row in summary if row.get("status") == "extracted"),
        "reused_existing_total": reused_existing_total,
        "total": sum(row.get("extracted_spell_count", 0) for row in summary),
    }
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    write_markdown(report)
    print(f"extracted total: {report['extracted_total']}")
    print(f"total including reused: {report['total']}")
    print(f"wrote {REPORT_PATH.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
