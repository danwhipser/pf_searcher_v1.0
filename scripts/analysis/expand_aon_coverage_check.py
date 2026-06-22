#!/usr/bin/env python3
from __future__ import annotations

# Allow direct execution from nested scripts/ folders.
import sys
from pathlib import Path
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import argparse
import json
import re
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from bs4 import BeautifulSoup

from scripts.books.extract_missing_books import ROOT, RESULT_DIR, clean_text
from scripts.fix.fix_mismatched_books import extract_en_name, normalize_match_key


USER_AGENT = "Mozilla/5.0 (PF_RAG aon coverage checker)"
CACHE_PATH = ROOT / "data" / "aon_source_spell_cache.json"
REPORT_PATH = RESULT_DIR / "aon-expanded-source-check.json"
LOCAL_ONLY_PATH = ROOT / "data" / "local_chm_only_sources.json"

PRODUCT_LINES = ["RPG", "PlayerCompanion", "CampaignSetting", "Misc"]


@dataclass
class AonSource:
    product_line: str
    title: str
    fixed_source: str
    url: str


def request_text(url: str) -> str:
    err: Optional[Exception] = None
    for i in range(4):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=50) as resp:
                return resp.read().decode("utf-8", errors="replace")
        except Exception as exc:  # noqa: BLE001
            err = exc
            time.sleep(1.5 * (i + 1))
    raise RuntimeError(f"failed request: {url}") from err


def load_json(path: Path, default):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def parse_source_list(product_line: str) -> List[AonSource]:
    url = f"https://www.aonprd.com/Sources.aspx?ProductLine={product_line}"
    html = request_text(url)
    soup = BeautifulSoup(html, "html.parser")
    out: List[AonSource] = []
    seen = set()
    for link in soup.find_all("a"):
        href = link.get("href", "")
        if "SourceDisplay.aspx?FixedSource=" not in href:
            continue
        title = clean_text(link.get_text(" ", strip=True))
        full_url = urllib.parse.urljoin("https://www.aonprd.com/", href)
        parsed = urllib.parse.urlparse(full_url)
        params = urllib.parse.parse_qs(parsed.query)
        fixed = clean_text(params.get("FixedSource", [""])[0])
        if not fixed:
            continue
        full_url = f"https://www.aonprd.com/SourceDisplay.aspx?FixedSource={urllib.parse.quote_plus(fixed)}"
        key = fixed.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(
            AonSource(
                product_line=product_line,
                title=title,
                fixed_source=fixed,
                url=full_url,
            )
        )
    return out


def parse_source_spell_names(source_url: str) -> Tuple[List[str], List[str]]:
    html = request_text(source_url)
    soup = BeautifulSoup(html, "html.parser")

    def section_names(prefix: str) -> List[str]:
        head = None
        for h in soup.find_all(["h2", "h3"]):
            text = h.get_text(" ", strip=True)
            if text.startswith(prefix):
                head = h
                break
        if head is None:
            return []
        names: List[str] = []
        for sib in head.find_next_siblings():
            if sib.name in {"h2", "h3"}:
                break
            if sib.name == "a":
                n = clean_text(sib.get_text(" ", strip=True))
                if n:
                    names.append(n)
                continue
            for a in sib.find_all("a"):
                n = clean_text(a.get_text(" ", strip=True))
                if n:
                    names.append(n)
        return names

    return section_names("Spells ["), section_names("Mythic Spells [")


def local_source_rows() -> List[Dict]:
    payload = load_json(RESULT_DIR / "all-books-spell-counts.json", {})
    rows = payload.get("rows", [])
    return [r for r in rows if int(r.get("count", 0)) > 0]


def mapped_source_codes() -> set[str]:
    mapped = set(load_json(ROOT / "data" / "aon_source_counts.json", {}).get("sources", {}).keys())
    # Additional already-compared player companion sources.
    mapped.update(load_json(RESULT_DIR / "aon-player-companion-spell-check.json", {}).keys())
    return {m.upper() for m in mapped}


def local_spell_keys(source_code: str) -> List[str]:
    model_path = RESULT_DIR / source_code.lower() / f"spells-{source_code.lower()}-model.json"
    if not model_path.exists():
        return []
    data = load_json(model_path, [])
    keys: List[str] = []
    for row in data:
        name = extract_en_name(row.get("name", ""))
        key = normalize_match_key(name)
        if key:
            keys.append(key)
    return keys


def build_cache(sources: List[AonSource], cache: Dict) -> Dict:
    out = dict(cache)
    total = len(sources)
    for idx, src in enumerate(sources, start=1):
        key = src.fixed_source.lower()
        if key in out:
            spell_count = int(out[key].get("spell_count", 0))
            mythic_count = int(out[key].get("mythic_count", 0))
            # Reuse only entries that already captured at least one spell/mythic spell.
            if spell_count + mythic_count > 0:
                print(f"cache-skip {idx}/{total}: {src.fixed_source} spells={spell_count} mythic={mythic_count}")
                continue
        try:
            spells, mythic = parse_source_spell_names(src.url)
            out[key] = {
                "product_line": src.product_line,
                "title": src.title,
                "fixed_source": src.fixed_source,
                "url": src.url,
                "spells": spells,
                "mythic_spells": mythic,
                "spell_count": len(spells),
                "mythic_count": len(mythic),
            }
            print(f"cached {idx}/{total}: {src.fixed_source} spells={len(spells)} mythic={len(mythic)}")
        except Exception as exc:  # noqa: BLE001
            out[key] = {
                "product_line": src.product_line,
                "title": src.title,
                "fixed_source": src.fixed_source,
                "url": src.url,
                "spells": [],
                "mythic_spells": [],
                "spell_count": 0,
                "mythic_count": 0,
                "error": str(exc),
            }
            print(f"cache-fail {idx}/{total}: {src.fixed_source} error={exc}")
        # Persist progress so interruption does not lose completed fetches.
        write_json(CACHE_PATH, out)
    return out


def score_match(local_keys: List[str], aon_keys: List[str]) -> Tuple[int, float, float]:
    if not local_keys or not aon_keys:
        return 0, 0.0, 0.0
    lset = set(local_keys)
    aset = set(aon_keys)
    inter = len(lset & aset)
    return inter, inter / max(1, len(lset)), inter / max(1, len(aset))


def best_match_for_source(local_keys: List[str], cache_values: List[Dict]) -> Optional[Dict]:
    best = None
    for item in cache_values:
        aon_names = item.get("spells", []) + item.get("mythic_spells", [])
        aon_keys = [normalize_match_key(n) for n in aon_names]
        inter, cover_local, cover_aon = score_match(local_keys, aon_keys)
        if inter == 0:
            continue
        score = (
            inter * 100000
            + int(cover_local * 10000)
            + int(cover_aon * 1000)
            - abs(len(local_keys) - len(aon_keys))
        )
        row = {
            "score": score,
            "intersection": inter,
            "cover_local": round(cover_local, 4),
            "cover_aon": round(cover_aon, 4),
            "aon_total": len(aon_keys),
            "aon": item,
            "aon_keys": aon_keys,
        }
        if best is None or row["score"] > best["score"]:
            best = row
    return best


def compare_sets(local_keys: List[str], aon_names: List[str]) -> Tuple[List[str], List[str]]:
    lset = set(local_keys)
    aon_pairs = [(n, normalize_match_key(n)) for n in aon_names]
    aset = {k for _, k in aon_pairs}
    missing = [name for name, key in aon_pairs if key not in lset]
    extra = [k for k in sorted(lset) if k not in aset]
    return missing, extra


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Compare local spell sources against AoN source spell lists.")
    p.add_argument(
        "--codes",
        default="",
        help="Comma-separated source codes to check only, e.g. SEPG,BOB",
    )
    p.add_argument(
        "--cache-only",
        action="store_true",
        help="Do not refetch AoN source pages; use existing data/aon_source_spell_cache.json only.",
    )
    return p.parse_args()


def main() -> None:
    args = parse_args()
    code_filter = {x.strip().upper() for x in args.codes.split(",") if x.strip()}

    rows = local_source_rows()
    mapped_codes = mapped_source_codes()
    remaining = [r for r in rows if str(r.get("code", "")).upper() not in mapped_codes]
    if code_filter:
        remaining = [r for r in remaining if str(r.get("code", "")).upper() in code_filter]

    cache = load_json(CACHE_PATH, {})
    if not args.cache_only:
        all_sources: List[AonSource] = []
        for pl in PRODUCT_LINES:
            all_sources.extend(parse_source_list(pl))
        uniq: Dict[str, AonSource] = {}
        for src in all_sources:
            uniq[src.fixed_source.lower()] = src
        all_sources = sorted(uniq.values(), key=lambda x: (x.product_line, x.fixed_source.lower()))
        cache = build_cache(all_sources, cache)
        write_json(CACHE_PATH, cache)

    cache_values = list(cache.values())
    local_only_sources = load_json(LOCAL_ONLY_PATH, {})
    matched = []
    unmatched = []
    needs_fix = []

    for row in remaining:
        code = str(row.get("code", "")).upper()
        local_keys = local_spell_keys(code)
        if not local_keys:
            unmatched.append(
                {
                    "code": code,
                    "reason": "local_model_missing_or_empty",
                    "local_count": int(row.get("count", 0)),
                }
            )
            continue
        if code in local_only_sources:
            cfg = local_only_sources.get(code) or {}
            matched.append(
                {
                    "code": code,
                    "display": row.get("display", ""),
                    "title": row.get("title", ""),
                    "local_count": len(local_keys),
                    "best_fixed_source": "",
                    "best_product_line": "",
                    "best_url": "",
                    "aon_total_spells_plus_mythic": 0,
                    "intersection": 0,
                    "cover_local": 1.0,
                    "cover_aon": 0.0,
                    "accepted_mapping": True,
                    "match_mode": "local_chm_only",
                    "local_only_reason": cfg.get("reason", "aon_source_not_available"),
                    "local_only_note": cfg.get("note", ""),
                    "missing_spells_from_local": [],
                    "missing_count": 0,
                    "extra_local_keys_not_in_aon": [],
                    "extra_count": 0,
                }
            )
            continue
        best = best_match_for_source(local_keys, cache_values)
        if best is None:
            unmatched.append(
                {
                    "code": code,
                    "reason": "no_overlap_with_aon_sources",
                    "local_count": len(local_keys),
                }
            )
            continue

        aon_item = best["aon"]
        aon_names = aon_item.get("spells", []) + aon_item.get("mythic_spells", [])
        missing, extra = compare_sets(local_keys, aon_names)
        accepted = (
            best["cover_local"] >= 0.8 and best["cover_aon"] >= 0.8
        ) or (
            best["intersection"] == len(set(local_keys)) == len(set([normalize_match_key(n) for n in aon_names]))
        )
        result = {
            "code": code,
            "display": row.get("display", ""),
            "title": row.get("title", ""),
            "local_count": len(local_keys),
            "best_fixed_source": aon_item.get("fixed_source", ""),
            "best_product_line": aon_item.get("product_line", ""),
            "best_url": aon_item.get("url", ""),
            "aon_total_spells_plus_mythic": len(aon_names),
            "intersection": best["intersection"],
            "cover_local": best["cover_local"],
            "cover_aon": best["cover_aon"],
            "accepted_mapping": accepted,
            "missing_spells_from_local": missing[:200],
            "missing_count": len(missing),
            "extra_local_keys_not_in_aon": extra[:200],
            "extra_count": len(extra),
        }
        matched.append(result)
        if accepted and (missing or extra):
            needs_fix.append(result)
        if not accepted:
            unmatched.append(
                {
                    "code": code,
                    "reason": "low_confidence_mapping",
                    "candidate": result,
                }
            )

    report = {
        "checked_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "product_lines": PRODUCT_LINES,
        "known_local_sources_with_spells": len(rows),
        "already_mapped_sources": len(mapped_codes),
        "remaining_sources_checked": len(remaining),
        "aon_source_cache_count": len(cache_values),
        "matched": matched,
        "needs_fix": needs_fix,
        "unmatched": unmatched,
        "summary": {
            "checked_codes_filter": sorted(code_filter) if code_filter else [],
            "matched_count": len(matched),
            "accepted_mapping_count": sum(1 for x in matched if x["accepted_mapping"]),
            "local_chm_only_count": sum(1 for x in matched if x.get("match_mode") == "local_chm_only"),
            "needs_fix_count": len(needs_fix),
            "unmatched_count": len(unmatched),
        },
    }
    write_json(REPORT_PATH, report)

    print(f"remaining checked: {len(remaining)}")
    print(f"matched: {len(matched)}")
    print(f"needs_fix: {len(needs_fix)}")
    print(f"unmatched: {len(unmatched)}")
    print(f"wrote: {REPORT_PATH.relative_to(ROOT)}")


if __name__ == "__main__":
    main()