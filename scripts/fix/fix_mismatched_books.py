#!/usr/bin/env python3
from __future__ import annotations

# Allow direct execution from nested scripts/ folders.
import sys
from pathlib import Path
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import json
import argparse
import re
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date
from typing import Dict, List, Optional, Tuple

from bs4 import BeautifulSoup

from scripts.books.extract_missing_books import ROOT, RESULT_DIR, clean_text, normalize_model


USER_AGENT = "Mozilla/5.0 (PF_RAG fixer)"


@dataclass(frozen=True)
class SourceConfig:
    code: str
    fixed_source: str
    section: str


SOURCES: Dict[str, SourceConfig] = {
    "AG": SourceConfig("AG", "Adventurer's Guide", "Spells"),
    "APG": SourceConfig("APG", "Advanced Player's Guide", "Spells"),
    "MA": SourceConfig("MA", "Mythic Adventures", "Mythic Spells"),
    "MC": SourceConfig("MC", "Monster Codex", "Spells"),
    "UW": SourceConfig("UW", "Ultimate Wilderness", "Spells"),
    "VC": SourceConfig("VC", "Villain Codex", "Spells"),
}

SPELL_TYPE_BY_SOURCE = {
    "MA": "mythic",
}

# Normalize known local spelling variants to canonical AoN spell keys.
LOCAL_KEY_ALIASES: Dict[str, Dict[str, str]] = {
    "MA": {
        "gustofwinds": "gustofwind",
    },
    "MC": {
        "fleshlyfacade": "fleshyfacade",
        "spellstealing": "spellsteal",
        "sunderingserpentcoil": "sunderedserpentcoil",
        "transferregenration": "transferregeneration",
    },
}


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, obj) -> None:
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def request_text(url: str) -> str:
    last_err: Optional[Exception] = None
    for attempt in range(4):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(request, timeout=40) as response:
                return response.read().decode("utf-8", errors="replace")
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"failed to fetch url after retries: {url}") from last_err


def normalize_en_key(text: str) -> str:
    text = (text or "").strip()
    text = text.replace("’", "'").replace("`", "'")
    text = text.replace("，", ",")
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", "", text)
    return text


def normalize_match_key(text: str) -> str:
    """Key for cross-source matching; strips source disambiguation suffixes like '(VC)'."""
    t = clean_text(text)
    # Remove trailing short all-caps disambiguator in parentheses.
    t = re.sub(r"\s*[（(]\s*[A-Z]{2,6}\s*[）)]\s*$", "", t)
    return normalize_en_key(t)


def extract_en_name(name: str) -> str:
    name = clean_text(name)
    if not name:
        return ""
    # English-only title: keep full title; only trim trailing disambiguator like "(VC)".
    if not re.search(r"[\u4e00-\u9fff]", name):
        return clean_text(re.sub(r"\s*[（(]\s*[A-Z]{2,6}\s*[）)]\s*$", "", name))
    # Prefer English inside parentheses.
    m = re.search(r"[（(]\s*([A-Za-z][^）)]{1,200})\s*[）)]", name)
    if m:
        return clean_text(m.group(1))
    # Fallback to trailing English run.
    chunks = re.findall(r"[A-Za-z][A-Za-z0-9'`,.\- ]*", name)
    if chunks:
        return clean_text(chunks[-1])
    return name


def spell_key_from_name(name: str, source: str) -> str:
    key = normalize_match_key(extract_en_name(name))
    alias = LOCAL_KEY_ALIASES.get(source, {}).get(key)
    return alias or key


def spell_type_for_source(source: str) -> str:
    return SPELL_TYPE_BY_SOURCE.get(source, "normal")


def source_display_url(fixed_source: str) -> str:
    return f"https://aonprd.com/SourceDisplay.aspx?FixedSource={urllib.parse.quote_plus(fixed_source)}"


def parse_source_spell_catalog(fixed_source: str, section: str) -> Tuple[List[str], Dict[str, str]]:
    html = request_text(source_display_url(fixed_source))
    soup = BeautifulSoup(html, "html.parser")

    section_head = None
    for heading in soup.find_all(["h2", "h3"]):
        title = heading.get_text(" ", strip=True)
        if title.startswith(f"{section} ["):
            section_head = heading
            break
    if section_head is None:
        raise RuntimeError(f"Could not locate '{section}' section for {fixed_source}")

    names: List[str] = []
    href_by_key: Dict[str, str] = {}
    for sib in section_head.find_next_siblings():
        if sib.name in {"h2", "h3"}:
            break
        if sib.name == "a":
            name = clean_text(sib.get_text(" ", strip=True))
            href = clean_text(sib.get("href", ""))
            if name and "SpellDisplay.aspx" in href:
                names.append(name)
                href_by_key[normalize_match_key(name)] = urllib.parse.urljoin("https://aonprd.com/", href)
            continue
        for link in sib.find_all("a"):
            name = clean_text(link.get_text(" ", strip=True))
            href = clean_text(link.get("href", ""))
            if not name or "SpellDisplay.aspx" not in href:
                continue
            names.append(name)
            href_by_key[normalize_match_key(name)] = urllib.parse.urljoin("https://aonprd.com/", href)
    if not names:
        raise RuntimeError(f"Found section '{section}' for {fixed_source}, but parsed 0 spell links")
    return names, href_by_key


def looks_like_spell_header(line: str) -> bool:
    if not line or len(line) > 140:
        return False
    if not re.search(r"[A-Za-z]", line):
        return False
    if line in {"Source", "School", "Level", "Casting", "Effect", "Description"}:
        return False
    return True


def parse_spell_entries_from_page(html: str) -> List[Dict[str, str]]:
    soup = BeautifulSoup(html, "html.parser")
    lines = [clean_text(x) for x in soup.get_text("\n").splitlines()]
    lines = [x for x in lines if x]

    starts: List[int] = []
    for i in range(len(lines) - 1):
        if looks_like_spell_header(lines[i]) and lines[i + 1] == "Source":
            starts.append(i)

    records: List[Dict[str, str]] = []
    for idx, start in enumerate(starts):
        end = starts[idx + 1] if idx + 1 < len(starts) else len(lines)
        block = lines[start:end]
        rec: Dict[str, str] = {"name": block[0]}

        j = 1
        while j < len(block):
            token = block[j]
            if token == "Source" and j + 1 < len(block):
                rec["source"] = block[j + 1]
                j += 2
                continue
            if token == "School":
                # Typical pattern:
                # School
                # evocation
                # ;
                # Level
                # bard 4, ...
                if j + 1 < len(block):
                    rec["school"] = block[j + 1]
                j += 2
                continue
            if token == "Level" and j + 1 < len(block):
                rec["level"] = block[j + 1]
                j += 2
                continue
            if token == "Casting Time" and j + 1 < len(block):
                rec["cast_time"] = block[j + 1]
                j += 2
                continue
            if token == "Components" and j + 1 < len(block):
                rec["components"] = block[j + 1]
                j += 2
                continue
            if token == "Range" and j + 1 < len(block):
                rec["range"] = block[j + 1]
                j += 2
                continue
            if token in {"Target", "Targets", "Area", "Effect"} and j + 1 < len(block):
                prev = rec.get("target", "")
                nxt = block[j + 1]
                rec["target"] = f"{prev}; {nxt}".strip("; ").strip()
                j += 2
                continue
            if token == "Duration" and j + 1 < len(block):
                rec["duration"] = block[j + 1]
                j += 2
                continue
            if token == "Saving Throw" and j + 1 < len(block):
                rec["save"] = block[j + 1].rstrip(";")
                j += 2
                continue
            if token == "Spell Resistance" and j + 1 < len(block):
                rec["spell_resistance"] = block[j + 1]
                j += 2
                continue
            if token == "Description":
                desc = clean_text(" ".join(block[j + 1 :]))
                rec["effect"] = desc
                break
            j += 1

        records.append(rec)
    return records


def fetch_spell_record_from_aon(spell_name: str, href_hint: Optional[str]) -> Dict[str, str]:
    candidates: List[str] = []
    if href_hint:
        candidates.append(href_hint)
    # Fallback direct URL by item name.
    candidates.append(
        "https://aonprd.com/SpellDisplay.aspx?ItemName="
        + urllib.parse.quote_plus(spell_name)
    )
    # Some "Mass" entries are grouped under base spell page.
    if ", " in spell_name:
        base = spell_name.split(", ", 1)[0]
        candidates.append(
            "https://aonprd.com/SpellDisplay.aspx?ItemName="
            + urllib.parse.quote_plus(base)
        )

    target_key = normalize_match_key(spell_name)
    seen = set()
    for url in candidates:
        if not url or url in seen:
            continue
        seen.add(url)
        try:
            html = request_text(url)
        except Exception:
            continue
        entries = parse_spell_entries_from_page(html)
        if not entries:
            continue
        for entry in entries:
            if normalize_match_key(entry.get("name", "")) == target_key:
                return entry
        if len(entries) == 1:
            return entries[0]
    raise RuntimeError(f"Unable to fetch AoN spell entry for: {spell_name}")


def raw_from_aon_entry(entry: Dict[str, str], source: str, canonical_name: str) -> Dict[str, str]:
    spell_type = spell_type_for_source(source)
    return {
        "name": canonical_name,
        "source_book": source.lower(),
        "学派": clean_text(entry.get("school", "")),
        "等级": clean_text(entry.get("level", "")),
        "施法时间": clean_text(entry.get("cast_time", "")),
        "成分": clean_text(entry.get("components", "")),
        "范围": clean_text(entry.get("range", "")),
        "目标": clean_text(entry.get("target", "")),
        "持续": clean_text(entry.get("duration", "")),
        "豁免": clean_text(entry.get("save", "")),
        "法术抗力": clean_text(entry.get("spell_resistance", "")),
        "效果": clean_text(entry.get("effect", "")),
        "法术类型": spell_type,
    }


def record_score(raw: Dict[str, str]) -> int:
    score = 0
    for key in ["学派", "等级", "施法时间", "成分", "范围", "目标", "持续", "豁免", "法术抗力", "效果"]:
        v = raw.get(key, "")
        if isinstance(v, str) and v.strip():
            score += 1
    score += len(raw.get("效果", "")) // 80
    return score


def load_raw_records(source: str) -> List[Dict[str, str]]:
    path = RESULT_DIR / source.lower() / f"spells-{source.lower()}.json"
    records = read_json(path)
    out: List[Dict[str, str]] = []
    for rec in records:
        row = dict(rec)
        row["source_book"] = source.lower()
        if "来源" in row:
            row.pop("来源", None)
        row.setdefault("法术类型", spell_type_for_source(source))
        out.append(row)
    return out


def reconcile_source(source: str) -> Dict:
    cfg = SOURCES[source]
    expected_names, href_map = parse_source_spell_catalog(cfg.fixed_source, cfg.section)
    expected_keys = [normalize_match_key(n) for n in expected_names]
    expected_key_to_name = {normalize_match_key(n): n for n in expected_names}

    raw_records = load_raw_records(source)

    # Keep best local record per normalized English key, only for spells expected by AoN.
    best: Dict[str, Dict[str, str]] = {}
    for rec in raw_records:
        key = spell_key_from_name(rec.get("name", ""), source)
        if not key or key not in expected_key_to_name:
            continue
        canonical_name = expected_key_to_name[key]
        rec["name"] = canonical_name
        rec["source_book"] = source.lower()
        rec["法术类型"] = spell_type_for_source(source)
        prev = best.get(key)
        if prev is None or record_score(rec) > record_score(prev):
            best[key] = rec

    missing: List[str] = []
    filled_from_aon: List[str] = []
    ordered_raw: List[Dict[str, str]] = []
    for key in expected_keys:
        rec = best.get(key)
        if rec is None:
            missing_name = expected_key_to_name[key]
            missing.append(missing_name)
            entry = fetch_spell_record_from_aon(missing_name, href_map.get(key))
            rec = raw_from_aon_entry(entry, source, missing_name)
            filled_from_aon.append(missing_name)
        ordered_raw.append(rec)

    # Rebuild model to keep field parsing consistent with current pipeline.
    ordered_model = [normalize_model(rec, source, i + 1) for i, rec in enumerate(ordered_raw)]
    for item in ordered_model:
        spell_type = spell_type_for_source(source)
        item["spell_type"] = spell_type
        item["type_label"] = "神话法术" if spell_type == "mythic" else "普通法术"
        item.setdefault("raw_fields", {})["法术类型"] = item["type_label"]

    out_dir = RESULT_DIR / source.lower()
    out_dir.mkdir(parents=True, exist_ok=True)
    write_json(out_dir / f"spells-{source.lower()}.json", ordered_raw)
    write_json(out_dir / f"spells-{source.lower()}-model.json", ordered_model)

    qa = {
        "source_book": source,
        "expected_spell_count": len(expected_names),
        "extracted_spell_count": len(ordered_model),
        "missing_before_backfill_count": len(missing),
        "missing_before_backfill": missing,
        "filled_from_aon_count": len(filled_from_aon),
        "filled_from_aon": filled_from_aon,
        "first_spell": ordered_model[0]["name"] if ordered_model else "",
        "last_spell": ordered_model[-1]["name"] if ordered_model else "",
    }
    write_json(out_dir / f"spells-{source.lower()}-qa.json", qa)

    return {
        "source": source,
        "aon_expected_count": len(expected_names),
        "local_final_count": len(ordered_model),
        "backfilled_count": len(filled_from_aon),
        "backfilled": filled_from_aon,
    }


def update_summary_files() -> None:
    # Update all-books count file from on-disk model files.
    counts_by_code: Dict[str, int] = {}
    for path in RESULT_DIR.glob("*/spells-*-model.json"):
        code = path.parent.name.upper()
        try:
            payload = read_json(path)
        except Exception:
            continue
        counts_by_code[code] = len(payload)

    counts_path = RESULT_DIR / "all-books-spell-counts.json"
    if counts_path.exists():
        counts_payload = read_json(counts_path)
        rows = counts_payload.get("rows", [])
        for row in rows:
            code = str(row.get("code", "")).upper()
            if code in counts_by_code:
                row["count"] = counts_by_code[code]
        counts_payload["total"] = sum(int(row.get("count", 0)) for row in rows)
        counts_payload["book_count"] = sum(1 for row in rows if int(row.get("count", 0)) > 0)
        write_json(counts_path, counts_payload)

    # Update aon_source_counts local_count for touched sources.
    aon_counts_path = ROOT / "data" / "aon_source_counts.json"
    if aon_counts_path.exists():
        payload = read_json(aon_counts_path)
        src = payload.get("sources", {})
        for code in SOURCES:
            if code in src:
                src[code]["local_count"] = counts_by_code.get(code, src[code].get("local_count", 0))
        payload["checked_at"] = str(date.today())
        write_json(aon_counts_path, payload)

    # Refresh selected source missing check for touched sources.
    mismatch_path = RESULT_DIR / "aon-selected-source-missing-check.json"
    if mismatch_path.exists():
        payload = read_json(mismatch_path)
        for code, cfg in SOURCES.items():
            expected_names, _ = parse_source_spell_catalog(cfg.fixed_source, cfg.section)
            expected_keys = [normalize_en_key(n) for n in expected_names]
            model_path = RESULT_DIR / code.lower() / f"spells-{code.lower()}-model.json"
            model = read_json(model_path)
            local_names = [extract_en_name(item.get("name", "")) for item in model]
            local_keys = [normalize_match_key(n) for n in local_names]
            exp_set = set(expected_keys)
            loc_set = set(local_keys)
            missing = [n for n in expected_names if normalize_match_key(n) not in loc_set]
            extra = [n for n in local_names if normalize_match_key(n) not in exp_set]
            payload[code] = {
                "fixed": cfg.fixed_source,
                "section": cfg.section,
                "url": source_display_url(cfg.fixed_source),
                "aon_expected": len(expected_names),
                "aon_count": len(expected_names),
                "local_count": len(local_names),
                "missing": missing,
                "extra": extra,
                "aon": expected_names,
                "local": local_names,
            }
        write_json(mismatch_path, payload)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--sources",
        default="AG,APG,MA,MC,UW,VC",
        help="Comma separated source codes to reconcile.",
    )
    args = parser.parse_args()
    selected = [x.strip().upper() for x in args.sources.split(",") if x.strip()]
    for code in selected:
        if code not in SOURCES:
            raise ValueError(f"unsupported source code: {code}")

    summary = []
    for code in selected:
        row = reconcile_source(code)
        summary.append(row)
        print(f"{code}: {row['local_final_count']}/{row['aon_expected_count']} backfilled={row['backfilled_count']}")
    write_json(RESULT_DIR / "fix-mismatched-books-report.json", {"summary": summary})
    update_summary_files()
    print("updated summary files")


if __name__ == "__main__":
    main()