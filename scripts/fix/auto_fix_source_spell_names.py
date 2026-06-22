#!/usr/bin/env python3
from __future__ import annotations

# Allow direct execution from nested scripts/ folders.
import sys
from pathlib import Path
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import difflib
import json
import re
import urllib.parse
import urllib.request
from typing import Dict, List, Tuple

from bs4 import BeautifulSoup

from scripts.books.extract_missing_books import ROOT, RESULT_DIR, clean_text, normalize_model
from scripts.fix.fix_mismatched_books import extract_en_name, normalize_match_key


USER_AGENT = "Mozilla/5.0 (PF_RAG auto fixer)"

# Extra sources from unmatched list that should be force-mapped.
FORCE_FIXED_SOURCE = {
    "BOTM": "Blood of the Moon",
    "POTN": "People of the North",
    "SEPG": "Second Darkness Player's Guide",
    "THH": "The Harrow Handbook",
    "HOG": "Humans of Golarion",
}


def request_text(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=45) as resp:
        return resp.read().decode("utf-8", errors="replace")


def aon_spell_names(fixed_source: str) -> List[str]:
    url = "https://www.aonprd.com/SourceDisplay.aspx?FixedSource=" + urllib.parse.quote_plus(fixed_source)
    html = request_text(url)
    soup = BeautifulSoup(html, "html.parser")
    head = None
    for h in soup.find_all(["h2", "h3"]):
        if h.get_text(" ", strip=True).startswith("Spells ["):
            head = h
            break
    if head is None:
        return []
    out: List[str] = []
    for sib in head.find_next_siblings():
        if sib.name in {"h2", "h3"}:
            break
        if sib.name == "a":
            t = clean_text(sib.get_text(" ", strip=True))
            if t:
                out.append(t)
            continue
        for a in sib.find_all("a"):
            t = clean_text(a.get_text(" ", strip=True))
            if t:
                out.append(t)
    return out


def load_model(source_code: str) -> List[Dict]:
    path = RESULT_DIR / source_code.lower() / f"spells-{source_code.lower()}-model.json"
    return json.loads(path.read_text(encoding="utf-8"))


def load_raw(source_code: str) -> List[Dict]:
    path = RESULT_DIR / source_code.lower() / f"spells-{source_code.lower()}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def save_source(source_code: str, raw_rows: List[Dict], model_rows: List[Dict]) -> None:
    out_dir = RESULT_DIR / source_code.lower()
    out_dir.mkdir(parents=True, exist_ok=True)
    raw_path = out_dir / f"spells-{source_code.lower()}.json"
    model_path = out_dir / f"spells-{source_code.lower()}-model.json"
    qa_path = out_dir / f"spells-{source_code.lower()}-qa.json"
    raw_path.write_text(json.dumps(raw_rows, ensure_ascii=False, indent=2), encoding="utf-8")
    model_path.write_text(json.dumps(model_rows, ensure_ascii=False, indent=2), encoding="utf-8")
    qa = {
        "source_book": source_code,
        "extracted_spell_count": len(model_rows),
        "note": "auto_fix_source_spell_names",
    }
    qa_path.write_text(json.dumps(qa, ensure_ascii=False, indent=2), encoding="utf-8")


def similarity(a: str, b: str) -> float:
    a_norm = re.sub(r"[^a-z0-9]+", "", a.lower())
    b_norm = re.sub(r"[^a-z0-9]+", "", b.lower())
    return difflib.SequenceMatcher(None, a_norm, b_norm).ratio()


def replace_english_part(name: str, old_en: str, new_en: str) -> str:
    n = name
    if not n:
        return n
    # Prefer replacing explicit old English token.
    pattern = re.compile(re.escape(old_en), re.I)
    if pattern.search(n):
        return pattern.sub(new_en, n, count=1)
    # Replace first english phrase in parentheses.
    n2, count = re.subn(r"[（(]\s*[A-Za-z][^）)]{0,200}\s*[）)]", f"({new_en})", n, count=1)
    if count:
        return n2
    # Fallback: append canonical name.
    return f"{n} ({new_en})"


def guess_mapping(local_en: List[str], aon_names: List[str]) -> Dict[str, str]:
    local_set = {x for x in local_en if x}
    aon_set = set(aon_names)
    local_keys = {normalize_match_key(x): x for x in local_set}
    aon_keys = {normalize_match_key(x): x for x in aon_set}
    missing = [v for k, v in aon_keys.items() if k not in local_keys]
    extra = [v for k, v in local_keys.items() if k not in aon_keys]

    mapping: Dict[str, str] = {}
    # Greedy best-match by string similarity.
    used_missing = set()
    for ex in extra:
        best = None
        best_score = 0.0
        for ms in missing:
            if ms in used_missing:
                continue
            score = similarity(ex, ms)
            # Bonus when one side is prefix with source annotation.
            ex_base = re.sub(r"\s*[（(].*?[）)]\s*$", "", ex).strip()
            ms_base = re.sub(r"\s*[（(].*?[）)]\s*$", "", ms).strip()
            if ex_base.lower() == ms_base.lower():
                score += 0.25
            if score > best_score:
                best_score = score
                best = ms
        if best and best_score >= 0.62:
            mapping[ex] = best
            used_missing.add(best)
    return mapping


def fixed_source_for_code(code: str, report: Dict) -> str:
    if code in FORCE_FIXED_SOURCE:
        return FORCE_FIXED_SOURCE[code]
    for row in report.get("matched", []):
        if row.get("code") == code:
            return row.get("best_fixed_source", "")
    return ""


def regenerate_model_rows(raw_rows: List[Dict], source_code: str) -> List[Dict]:
    model_rows = [normalize_model(row, source_code, i + 1) for i, row in enumerate(raw_rows)]
    for item in model_rows:
        item["spell_type"] = "normal"
        item["type_label"] = "普通法术"
        item.setdefault("raw_fields", {})["法术类型"] = "普通法术"
    return model_rows


def main() -> None:
    report_path = RESULT_DIR / "aon-expanded-source-check.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    targets = {x["code"] for x in report.get("needs_fix", [])}
    targets.update({"BOTM", "POTN", "SEPG", "THH", "HOG"})
    targets = sorted(targets)

    summary = []
    for code in targets:
        fixed = fixed_source_for_code(code, report)
        if not fixed:
            summary.append({"code": code, "status": "skip_no_fixed_source"})
            continue
        raw_rows = load_raw(code)
        model_rows = load_model(code)
        local_en = [extract_en_name(r.get("name", "")) for r in model_rows]
        aon_names = aon_spell_names(fixed)
        mapping = guess_mapping(local_en, aon_names)
        if not mapping:
            summary.append({"code": code, "fixed_source": fixed, "status": "no_mapping_generated"})
            continue

        changed = 0
        for row in raw_rows:
            old_name = row.get("name", "")
            old_en = extract_en_name(old_name)
            new_en = mapping.get(old_en)
            if not new_en:
                continue
            row["name"] = replace_english_part(old_name, old_en, new_en)
            changed += 1

        if changed:
            new_model = regenerate_model_rows(raw_rows, code)
            save_source(code, raw_rows, new_model)
            summary.append(
                {
                    "code": code,
                    "fixed_source": fixed,
                    "status": "updated",
                    "changed_rows": changed,
                    "mapping": mapping,
                }
            )
        else:
            summary.append({"code": code, "fixed_source": fixed, "status": "no_changes"})

    out = RESULT_DIR / "auto-fix-source-spell-names-report.json"
    out.write_text(json.dumps({"summary": summary}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {out}")
    for row in summary:
        print(row["code"], row["status"], row.get("changed_rows", 0))


if __name__ == "__main__":
    main()
