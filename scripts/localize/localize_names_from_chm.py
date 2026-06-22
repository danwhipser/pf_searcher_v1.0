#!/usr/bin/env python3
from __future__ import annotations

# Allow direct execution from nested scripts/ folders.
import sys
from pathlib import Path
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import html
import json
import re
import difflib
from collections import Counter, defaultdict
from typing import Any

from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[2]
RESULT_DIR = ROOT / "result"
FEAT_DIR = RESULT_DIR / "feats"

sys.path.append(str((ROOT / "scripts").resolve()))


HAN_RE = re.compile(r"[\u4e00-\u9fff]")
CN_EN_RE = re.compile(
    r"(?P<cn>[\u4e00-\u9fff][^（()\n\r]{0,90}?)\s*[（(]\s*(?P<en>[A-Za-z][^）)\n\r]{1,150})\s*[）)]"
)


def has_cn(value: Any) -> bool:
    return bool(HAN_RE.search(str(value or "")))


def normalize_key(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = (
        text.replace("’", "'")
        .replace("`", "'")
        .replace("，", ",")
        .replace("（", "(")
        .replace("）", ")")
    )
    return re.sub(r"[^a-z0-9]+", "", text)


def normalize_ws(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def clean_cn_name(value: str) -> str:
    text = normalize_ws(html.unescape(value))
    text = re.sub(r"^[：:、，,。\s]+", "", text)
    text = re.sub(r"[：:、，,。\s]+$", "", text)
    text = re.sub(r"^[\d.]+\s*", "", text)
    text = re.sub(r"\s*[（(]\s*(?:战斗|神话|团队|超魔|造物|故事|格斗|表现|种族|风格)专?长?\s*[）)]\s*$", "", text)
    text = re.sub(r"\s*[（(]\s*(?:Combat|Mythic|Teamwork|Metamagic|Item Creation|Story|Style|Performance|Racial)\s*[）)]\s*$", "", text, flags=re.I)
    text = re.sub(r"\s+", "", text)
    return text.strip()


def extract_en_name(name: Any) -> str:
    text = normalize_ws(name)
    if not text:
        return ""
    if not has_cn(text) and re.search(r"[A-Za-z]", text):
        return text
    matches = list(re.finditer(r"[（(]\s*([A-Za-z][^）)]{0,180})\s*[）)]", text))
    if matches:
        return normalize_ws(matches[-1].group(1))
    m = re.search(r"([A-Za-z][A-Za-z0-9'’/\- ,:]+)$", text)
    return normalize_ws(m.group(1)) if m else ""


def extract_cn_name(name: Any) -> str:
    text = normalize_ws(name)
    if not text or not has_cn(text):
        return ""
    m = CN_EN_RE.search(text)
    if m:
        return clean_cn_name(m.group("cn"))
    if re.search(r"[A-Za-z]", text):
        text = re.sub(r"[（(]\s*[A-Za-z][^）)]{0,180}\s*[）)]", "", text)
        text = re.sub(r"\s+[A-Za-z][A-Za-z0-9'’/\- ,:]+$", "", text)
    return clean_cn_name(text)


def formatted_name(cn: str, en: str) -> str:
    cn = clean_cn_name(cn)
    en = normalize_ws(en)
    return f"{cn}（{en}）" if cn and en else cn or en


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def active_spell_paths() -> list[Path]:
    out: list[Path] = []
    seen: set[Path] = set()
    result_dir = ROOT / "result"
    for source_dir in sorted(path for path in result_dir.iterdir() if path.is_dir()):
        code = source_dir.name
        if code.lower() == "index":
            continue
        for path in (
            source_dir / f"spells-{code}.json",
            source_dir / f"spells-{code}-model.json",
        ):
            if path.exists() and path not in seen:
                out.append(path)
                seen.add(path)
    return out


def source_code_from_spell_path(path: Path) -> str:
    return path.parent.name.upper()


def add_spell_mapping(mapping: dict[str, dict[str, str]], source: str, name: Any) -> None:
    en = extract_en_name(name)
    cn = extract_cn_name(name)
    key = normalize_key(en)
    if source and key and cn:
        mapping[source].setdefault(key, cn)
        mapping["*"].setdefault(key, cn)


def build_spell_name_map() -> dict[str, dict[str, str]]:
    mapping: dict[str, dict[str, str]] = defaultdict(dict)

    # Existing localized spell JSONs are CHM-derived and give a reliable global map.
    for path in RESULT_DIR.glob("*/spells-*.json"):
        if not path.is_file() or path.parent.name == "index":
            continue
        try:
            rows = read_json(path)
        except Exception:
            continue
        source = path.parent.name.upper()
        if not isinstance(rows, list):
            continue
        for row in rows:
            if isinstance(row, dict):
                add_spell_mapping(mapping, source, row.get("name", ""))
                raw = row.get("raw_fields") or {}
                if isinstance(raw, dict):
                    add_spell_mapping(mapping, source, raw.get("name", ""))

    # The CHM index includes Chinese-English pairs for many spells even when the
    # per-source extraction still has an English-only title.
    for path in [
        RESULT_DIR / "index" / "spells-index-located.json",
        RESULT_DIR / "index" / "spells-index-model.json",
        RESULT_DIR / "spells-summary.json",
    ]:
        if not path.exists():
            continue
        try:
            payload = read_json(path)
        except Exception:
            continue
        collect_spell_mappings_from_any(payload, mapping)

    # Dedicated CHM extractors recover source-local names for sources that were
    # previously generated from English reference data.
    try:
        from scripts.extract.extract_spells_apg import extract_apg_spells  # type: ignore

        path = ROOT / "spell" / "Spell APG.html"
        if path.exists():
            rows, _issues = extract_apg_spells(path)
            for row in rows:
                add_spell_mapping(mapping, "APG", row.get("name", ""))
    except Exception:
        pass

    try:
        from scripts.books.extract_more_books import extract_source, resolve_source_path  # type: ignore

        for source in ["AG", "MA", "MC", "UW", "VC"]:
            try:
                rows = extract_source(source, resolve_source_path(source))
            except Exception:
                continue
            for row in rows:
                add_spell_mapping(mapping, source, row.get("name", ""))
    except Exception:
        pass

    return mapping


def collect_spell_mappings_from_any(value: Any, mapping: dict[str, dict[str, str]], source: str = "") -> None:
    if isinstance(value, str):
        for m in CN_EN_RE.finditer(value):
            cn = clean_cn_name(m.group("cn"))
            en = normalize_ws(m.group("en")).replace("，", ",")
            key = normalize_key(en)
            if cn and key:
                if source:
                    mapping[source].setdefault(key, cn)
                mapping["*"].setdefault(key, cn)
        return
    if isinstance(value, list):
        for item in value:
            collect_spell_mappings_from_any(item, mapping, source)
        return
    if isinstance(value, dict):
        row_source = str(value.get("source_book") or value.get("来源") or source or "").upper()
        name = value.get("name")
        if name:
            add_spell_mapping(mapping, row_source, name)
        for item in value.values():
            collect_spell_mappings_from_any(item, mapping, row_source)


def localize_spell_file(path: Path, mapping: dict[str, dict[str, str]]) -> dict[str, Any]:
    rows = read_json(path)
    if not isinstance(rows, list):
        return {"path": str(path.relative_to(ROOT)), "status": "not_list"}
    source = source_code_from_spell_path(path)
    source_map = mapping.get(source, {})
    global_map = mapping.get("*", {})
    changed = 0
    missing: list[str] = []
    strategies = Counter()
    for row in rows:
        if not isinstance(row, dict):
            continue
        name = normalize_ws(row.get("name", ""))
        if not name or has_cn(name):
            continue
        key = normalize_key(name)
        cn = clean_cn_name(row.get("name_zh", "") or row.get("name_cn", ""))
        strategy = "row_chm_name_field"
        if not cn:
            cn = source_map.get(key)
            strategy = "source_chm"
        if not cn:
            cn = global_map.get(key)
            strategy = "global_chm"
        if not cn:
            source_match = fuzzy_lookup(key, source_map)
            if source_match:
                cn = source_match
                strategy = "source_chm_fuzzy"
        if not cn:
            global_match = fuzzy_lookup(key, global_map)
            if global_match:
                cn = global_match
                strategy = "global_chm_fuzzy"
        if not cn:
            missing.append(name)
            continue
        row["name"] = formatted_name(cn, name)
        row["name_zh"] = clean_cn_name(cn)
        row["name_en"] = name
        raw = row.get("raw_fields")
        if isinstance(raw, dict):
            raw["name"] = row["name"]
        changed += 1
        strategies[strategy] += 1
    if changed:
        write_json(path, rows)
    return {
        "path": str(path.relative_to(ROOT)),
        "source": source,
        "total": len(rows),
        "changed": changed,
        "remaining_english_only": len(missing),
        "missing_sample": missing[:50],
        "strategies": dict(strategies),
    }


def fuzzy_lookup(key: str, mapping: dict[str, str]) -> str:
    if not key or not mapping:
        return ""
    best_key = ""
    best_score = 0.0
    for candidate in mapping:
        score = difflib.SequenceMatcher(None, key, candidate).ratio()
        if key in candidate or candidate in key:
            score += 0.08
        if score > best_score:
            best_score = score
            best_key = candidate
    if best_key and best_score >= 0.86:
        return mapping[best_key]
    return ""


def localize_spells() -> dict[str, Any]:
    mapping = build_spell_name_map()
    paths = set(active_spell_paths())
    # Keep raw/model pairs consistent even when the frontend only lists one side.
    for path in list(paths):
        code = path.parent.name
        paths.add(path.parent / f"spells-{code}.json")
        paths.add(path.parent / f"spells-{code}-model.json")
    reports = []
    for path in sorted(p for p in paths if p.exists()):
        reports.append(localize_spell_file(path, mapping))
    return {
        "spell_mapping_sources": {k: len(v) for k, v in sorted(mapping.items())},
        "files": reports,
    }


def build_feat_map_from_rows() -> dict[str, str]:
    mapping: dict[str, str] = {}
    sources = [
        FEAT_DIR / "feat-book-feats.json",
        FEAT_DIR / "chapter_extract" / "feat-book-feats.json",
        FEAT_DIR / "feats-chm-extracted.json",
        FEAT_DIR / "feats-frontend.json",
    ]
    for path in sources:
        if not path.exists():
            continue
        try:
            payload = read_json(path)
        except Exception:
            continue
        for row in iter_feat_rows(payload):
            key = normalize_key(row.get("match_key") or row.get("name_en") or row.get("name_raw"))
            cn = row.get("name_cn") or extract_cn_name(row.get("name_raw", ""))
            cn = clean_cn_name(cn)
            if key and cn:
                mapping.setdefault(key, cn)
    return mapping


def iter_feat_rows(payload: Any):
    if isinstance(payload, list):
        for item in payload:
            if isinstance(item, dict):
                if isinstance(item.get("feats"), list):
                    yield from iter_feat_rows(item["feats"])
                else:
                    yield item
    elif isinstance(payload, dict):
        if isinstance(payload.get("feats"), list):
            yield from iter_feat_rows(payload["feats"])
        for value in payload.values():
            if isinstance(value, list):
                yield from iter_feat_rows(value)


def suspicious_frontend_feats() -> list[dict[str, Any]]:
    path = FEAT_DIR / "feats-frontend.json"
    if not path.exists():
        return []
    payload = read_json(path)
    out = []
    for row in payload.get("feats", []):
        if not isinstance(row, dict):
            continue
        if row.get("name_en") and not has_cn(row.get("name_cn", "")):
            out.append(row)
    return out


def best_cn_before_english(text: str, english: str) -> str:
    idx = text.lower().find(english.lower())
    if idx < 0:
        return ""
    before = text[:idx]
    before = re.sub(r"\([^)]{0,80}$", "", before)
    chunks = re.split(r"[\n\r\t ]+", before)
    for chunk in reversed(chunks[-20:]):
        chunk = clean_cn_name(chunk)
        if 1 <= len(chunk) <= 24 and has_cn(chunk):
            if chunk in {"专长", "效果", "先决条件", "战斗专长", "神话专长"}:
                continue
            return chunk
    m = re.search(r"([\u4e00-\u9fff][\u4e00-\u9fff·・、，（）()A-Za-z0-9]{0,40})$", before)
    return clean_cn_name(m.group(1)) if m else ""


def extract_feat_map_from_embedded(feats: list[dict[str, Any]]) -> dict[str, str]:
    path = RESULT_DIR / "Pathfinder-v2.14-SC-viewer-embedded.html"
    if not path.exists() or not feats:
        return {}
    text = path.read_text(encoding="utf-8", errors="ignore")
    lower = text.lower()
    mapping: dict[str, str] = {}
    for row in feats:
        en = normalize_ws(row.get("name_en", "") or row.get("name_raw", ""))
        key = normalize_key(row.get("match_key") or en)
        if not en or not key or key in mapping:
            continue
        idx = lower.find(en.lower())
        if idx < 0:
            continue
        window = text[max(0, idx - 1800) : idx + len(en) + 500]
        window = html.unescape(window.replace("\\/", "/").replace("\\r", "\n").replace("\\n", "\n"))
        plain = BeautifulSoup(window, "html.parser").get_text(" ")
        cn = best_cn_before_english(plain, en)
        if cn:
            mapping[key] = cn
    return mapping


def apply_feat_name_map_to_payload(payload: Any, mapping: dict[str, str]) -> dict[str, int]:
    stats = Counter()
    for row in iter_feat_rows(payload):
        en = normalize_ws(row.get("name_en", ""))
        if not en:
            continue
        key = normalize_key(row.get("match_key") or en)
        cn = clean_cn_name(row.get("name_cn", ""))
        if not cn:
            cn = mapping.get(key, "")
            if cn:
                row["name_cn"] = cn
                stats["filled_name_cn"] += 1
        if cn and row.get("name_raw") != formatted_name(cn, en):
            row["name_raw"] = formatted_name(cn, en)
            stats["normalized_name_raw"] += 1
    return dict(stats)


def localize_feats() -> dict[str, Any]:
    existing_map = build_feat_map_from_rows()
    embedded_map = extract_feat_map_from_embedded(suspicious_frontend_feats())
    merged_map = dict(existing_map)
    merged_map.update({k: v for k, v in embedded_map.items() if v})

    reports = []
    for path in [
        FEAT_DIR / "feat-book-feats.json",
        FEAT_DIR / "chapter_extract" / "feat-book-feats.json",
        FEAT_DIR / "feats-chm-extracted.json",
        FEAT_DIR / "feats-frontend.json",
    ]:
        if not path.exists():
            continue
        payload = read_json(path)
        stats = apply_feat_name_map_to_payload(payload, merged_map)
        if stats:
            write_json(path, payload)
        reports.append({"path": str(path.relative_to(ROOT)), "stats": stats})

    return {
        "existing_feat_name_map": len(existing_map),
        "embedded_feat_name_map": len(embedded_map),
        "files": reports,
    }


def main() -> None:
    report = {
        "spells": localize_spells(),
        "feats": localize_feats(),
    }
    out = RESULT_DIR / "name-localization-from-chm-report.json"
    write_json(out, report)
    print(f"wrote {out.relative_to(ROOT)}")
    print(json.dumps(report, ensure_ascii=False, indent=2)[:6000])


if __name__ == "__main__":
    main()