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
from typing import Dict, List, Tuple


ROOT = Path(__file__).resolve().parents[2]
RESULT_DIR = ROOT / "result"

sys.path.append(str((ROOT / "scripts").resolve()))
from scripts.extract.extract_spells_apg import extract_apg_spells  # type: ignore  # noqa: E402
from scripts.books.extract_more_books import extract_source, resolve_source_path  # type: ignore  # noqa: E402


TARGET_SOURCES = ["APG", "AG", "MA", "MC", "UW", "VC"]

# Manual CHM-backed overrides for known parser gaps / naming variants.
# Keys are normalized English spell names.
MANUAL_CHM_OVERRIDES: Dict[str, Dict[str, str]] = {
    "APG": {
        "heroicfortune": "鸿运当头",
        "heroicfortunemass": "天降洪福",
        "malediction": "恶意中伤",
        "maledictionheropoints": "恶意中伤",
        "severedfate": "听天由命",
        "unraveldestiny": "天煞孤星",
    },
    "MA": {
        "blindnessdeafness": "目盲/耳聋术",
    },
    "MC": {
        "fleshyfacade": "鲜肉伪装",
        "fleshlyfacade": "鲜肉伪装",
        "giftofthedeep": "海渊之赠礼",
        "spellsteal": "偷窃魔法",
        "spellstealing": "偷窃魔法",
        "sunderedserpentcoil": "狂蟒之灾",
        "transferregeneration": "转移再生",
    },
    "UW": {
        "wingbounty": "绿翼馈赠",
    },
    "VC": {
        "foolsgold": "愚人之金",
        "foolsgoldvc": "愚人之金",
        "geomessage": "地讯术",
    },
}


def has_zh(text: str) -> bool:
    return bool(re.search(r"[\u4e00-\u9fff]", str(text or "")))


def normalize_key(text: str) -> str:
    text = str(text or "").strip().lower()
    text = text.replace("’", "'").replace("`", "'")
    text = re.sub(r"[^a-z0-9]+", "", text)
    return text


def extract_en_name(text: str) -> str:
    value = str(text or "").strip()
    if not value:
        return ""

    # For pure English names (including qualifiers in parentheses),
    # keep the full name instead of taking only inner-parenthesis text.
    if not has_zh(value) and re.search(r"[A-Za-z]", value):
        return value

    # Prefer English inside parentheses: 中文(English Name)
    m = re.search(r"[（(]\s*([A-Za-z][^（）()]{1,200})\s*[）)]", value)
    if m:
        return m.group(1).strip()

    # Trailing English phrase: 中文 English Name
    m = re.search(r"([A-Za-z][A-Za-z0-9'’`/\- ,]{2,})$", value)
    if m:
        return m.group(1).strip()

    # Pure English spell name
    if re.search(r"[A-Za-z]", value):
        return value

    return ""


def extract_zh_name(text: str) -> str:
    value = str(text or "").strip()
    if not value:
        return ""

    # 中文(English)
    m = re.match(r"^(.*?)\s*[（(][^（）()]+[）)]\s*$", value)
    if m and has_zh(m.group(1)):
        return m.group(1).strip()

    # 中文 English
    m = re.match(r"^(.*?[\u4e00-\u9fff].*?)\s+[A-Za-z][A-Za-z0-9'’`\- ,]{2,}$", value)
    if m and has_zh(m.group(1)):
        return m.group(1).strip()

    if has_zh(value):
        return value
    return ""


def derive_display_name(spell: Dict) -> str:
    for key in ("name_zh", "名称", "中文名", "译名"):
        value = str(spell.get(key, "")).strip()
        if value and has_zh(value):
            return value

    name = str(spell.get("name", "")).strip()
    zh = extract_zh_name(name)
    return zh or name


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, payload) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def build_map_from_index_located() -> Dict[str, Dict[str, str]]:
    path = RESULT_DIR / "index" / "spells-index-located.json"
    if not path.exists():
        return {}

    rows = load_json(path)
    mapping: Dict[str, Dict[str, str]] = defaultdict(dict)
    for row in rows:
        if not isinstance(row, dict):
            continue
        src = str(row.get("source_book", "")).upper()
        name = str(row.get("name", "")).strip()
        zh = extract_zh_name(name)
        en = extract_en_name(name)
        key = normalize_key(en)
        if src and zh and key:
            mapping[src][key] = zh
    return mapping


def build_map_from_current_result() -> Dict[str, str]:
    key_to_values: Dict[str, set] = defaultdict(set)
    for source_dir in RESULT_DIR.iterdir():
        if not source_dir.is_dir() or source_dir.name == "index":
            continue
        raw_path = source_dir / f"spells-{source_dir.name}.json"
        if not raw_path.exists():
            continue
        try:
            rows = load_json(raw_path)
        except Exception:
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            name = row.get("name", "")
            zh = extract_zh_name(name)
            en = extract_en_name(name)
            key = normalize_key(en)
            if zh and key:
                key_to_values[key].add(zh)

    unique_map: Dict[str, str] = {}
    for key, values in key_to_values.items():
        if len(values) == 1:
            unique_map[key] = next(iter(values))
    return unique_map


def build_map_from_chm_extractors() -> Dict[str, Dict[str, str]]:
    mapping: Dict[str, Dict[str, str]] = defaultdict(dict)

    apg_path = ROOT / "spell" / "Spell APG.html"
    if apg_path.exists():
        spells, _issues = extract_apg_spells(apg_path)
        for row in spells:
            zh = extract_zh_name(row.get("name", ""))
            en = extract_en_name(row.get("name", ""))
            key = normalize_key(en)
            if zh and key:
                mapping["APG"][key] = zh

    for src in ["AG", "MA", "MC", "UW", "VC"]:
        try:
            path = resolve_source_path(src)
            spells = extract_source(src, path)
        except Exception:
            continue
        for row in spells:
            zh = extract_zh_name(row.get("name", ""))
            en = extract_en_name(row.get("name", ""))
            key = normalize_key(en)
            if zh and key:
                mapping[src][key] = zh

    return mapping


def candidate_keys(key: str) -> List[str]:
    cands = [key]
    if key.endswith("vc") and len(key) > 2:
        cands.append(key[:-2])
    if key == "spellsteal":
        cands.append("spellstealing")
    if key == "fleshyfacade":
        cands.append("fleshlyfacade")
    return cands


def fill_one_source(
    source: str,
    chm_map_by_source: Dict[str, Dict[str, str]],
    index_map_by_source: Dict[str, Dict[str, str]],
    global_map: Dict[str, str],
) -> Dict:
    slug = source.lower()
    raw_path = RESULT_DIR / slug / f"spells-{slug}.json"
    model_path = RESULT_DIR / slug / f"spells-{slug}-model.json"
    if not raw_path.exists() or not model_path.exists():
        return {"source": source, "status": "missing_files"}

    raw_rows: List[Dict] = load_json(raw_path)
    model_rows: List[Dict] = load_json(model_path)
    if not isinstance(raw_rows, list) or not isinstance(model_rows, list):
        return {"source": source, "status": "invalid_json"}

    source_map_chm = chm_map_by_source.get(source, {})
    source_map_manual = MANUAL_CHM_OVERRIDES.get(source, {})
    source_map_index = index_map_by_source.get(source, {})

    changed = 0
    still_missing = 0
    by_strategy = Counter()

    def fill_row(row: Dict) -> Tuple[bool, str]:
        if has_zh(derive_display_name(row)):
            return False, ""

        key = normalize_key(extract_en_name(row.get("name", "")))
        if not key:
            return False, ""

        for cand in candidate_keys(key):
            zh = source_map_chm.get(cand)
            if zh:
                row["name_zh"] = zh
                return True, "source_chm"

        for cand in candidate_keys(key):
            zh = source_map_manual.get(cand)
            if zh:
                row["name_zh"] = zh
                return True, "manual_chm"

        zh = source_map_index.get(key)
        if zh:
            row["name_zh"] = zh
            return True, "source_index"

        zh = global_map.get(key)
        if zh:
            row["name_zh"] = zh
            return True, "global_result"

        return False, ""

    for row in raw_rows:
        if not isinstance(row, dict):
            continue
        ok, strategy = fill_row(row)
        if ok:
            changed += 1
            by_strategy[strategy] += 1

    for row in model_rows:
        if not isinstance(row, dict):
            continue
        fill_row(row)
        if not has_zh(derive_display_name(row)):
            still_missing += 1

    if changed:
        save_json(raw_path, raw_rows)
        save_json(model_path, model_rows)

    return {
        "source": source,
        "status": "updated" if changed else "no_change",
        "changed": changed,
        "still_missing": still_missing,
        "strategy": dict(by_strategy),
        "total": len(model_rows),
    }


def main() -> None:
    chm_map_by_source = build_map_from_chm_extractors()
    index_map_by_source = build_map_from_index_located()
    global_map = build_map_from_current_result()

    report_rows = []
    for source in TARGET_SOURCES:
        report_rows.append(
            fill_one_source(
                source=source,
                chm_map_by_source=chm_map_by_source,
                index_map_by_source=index_map_by_source,
                global_map=global_map,
            )
        )

    report = {
        "target_sources": TARGET_SOURCES,
        "rows": report_rows,
        "chm_map_sizes": {k: len(v) for k, v in chm_map_by_source.items()},
        "index_map_sizes": {k: len(v) for k, v in index_map_by_source.items() if k in TARGET_SOURCES},
        "global_unique_map_size": len(global_map),
        "summary": {
            "changed_total": sum(int(r.get("changed", 0)) for r in report_rows),
            "still_missing_total": sum(int(r.get("still_missing", 0)) for r in report_rows),
        },
    }

    out = RESULT_DIR / "fill-cn-names-from-chm-report.json"
    save_json(out, report)
    print(f"wrote: {out}")
    for row in report_rows:
        print(
            f"{row.get('source')} status={row.get('status')} changed={row.get('changed', 0)} "
            f"still_missing={row.get('still_missing', 0)} strategy={row.get('strategy', {})}"
        )


if __name__ == "__main__":
    main()