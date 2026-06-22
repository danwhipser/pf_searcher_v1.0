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
from collections import defaultdict
from difflib import SequenceMatcher
from typing import Dict, List, Tuple

REPO_ROOT = Path(__file__).resolve().parents[2]
RESULT_DIR = REPO_ROOT / "result"
INDEX_PATH = RESULT_DIR / "index" / "spells-index.json"
OA_MANUAL_ALIAS_PATH = RESULT_DIR / "index" / "oa-manual-alias-map.json"

# Only these extracted books are treated as source spell corpus.
# Index is metadata and should never be merged as real spell entries.
BOOK_CODES = [
    "acg",
    "apg",
    "arg",
    "crb",
    "oa",
    "uc",
    "ui",
    "um",
    "aarch",
    "cotr",
    "fob",
    "foc",
    "fop",
    "isg",
    "ism",
    "iswg",
    "mtt",
    "rtt",
    "tg",
    "ag",
    "mc",
    "ma",
    "vc",
    "ha",
    "uw",
    "pa",
    "botd",
]


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def normalize_cn(text: str) -> str:
    t = (text or "").strip().lower()
    t = t.replace("\uFF08", "(").replace("\uFF09", ")")
    t = re.sub(r"\s+", "", t)
    t = re.sub(r"[^\u4e00-\u9fffa-z0-9]", "", t)
    return t


def normalize_en(text: str) -> str:
    t = (text or "").strip().lower()
    # Normalize common delimiter/punctuation variants seen in OA dumps.
    t = t.replace("，", ",").replace("（", "(").replace("）", ")")
    t = t.replace("’", "'").replace("“", "\"").replace("”", "\"")
    t = t.replace("—", "-").replace("–", "-")
    t = re.sub(r"[^a-z0-9]", "", t)
    return t


def extract_english_fallback(name: str) -> str:
    """
    Extract an English-like phrase from noisy title text.
    This is useful for OA rows where delimiters are broken and we cannot
    rely on normal '(English Name)' structure.
    """
    if not name:
        return ""
    # Keep only latin words/numbers and join as a phrase.
    parts = re.findall(r"[A-Za-z0-9]+", name)
    if not parts:
        return ""
    return " ".join(parts).strip()


def split_cn_en(name: str) -> Tuple[str, str]:
    # Matches: Chinese(English) or Chinese (English)
    raw = (name or "").strip()
    m = re.match(r"^(.*?)\s*[\(\uFF08]\s*(.*?)\s*[\)\uFF09]\s*$", raw)
    if not m:
        # Fallback for malformed delimiters (frequent in OA source dumps).
        en = extract_english_fallback(raw)
        if not en:
            return raw, ""
        # Remove ASCII chunk from CN side to avoid key pollution.
        cn = re.sub(r"[A-Za-z0-9\s,\-'/\.]+", "", raw).strip()
        cn = cn or raw
        return cn, en
    left = m.group(1).strip()
    inside = m.group(2).strip()
    if not re.search(r"[A-Za-z]", inside) and re.search(r"[A-Za-z]", left):
        en = extract_english_fallback(left)
        cn = re.sub(r"[A-Za-z0-9\s,\-'/\.]+", "", left).strip()
        return cn or left, en
    return m.group(1).strip(), m.group(2).strip()


def similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def load_oa_manual_alias() -> Dict[str, str]:
    """
    Load OA manual alias map.
    key: normalized English title from index item
    val: normalized English title to target in extracted OA corpus
    """
    if not OA_MANUAL_ALIAS_PATH.exists():
        return {}
    try:
        payload = load_json(OA_MANUAL_ALIAS_PATH)
    except Exception:
        return {}
    aliases = payload.get("aliases", [])
    out: Dict[str, str] = {}
    for row in aliases:
        if (row.get("source_book") or "").upper() != "OA":
            continue
        src_en = normalize_en(row.get("index_en", ""))
        dst_en = normalize_en(row.get("target_en", ""))
        if src_en and dst_en:
            out[src_en] = dst_en
    return out


def oa_cn_fallback_match(cn_key: str, bucket: List[Dict]) -> List[Dict]:
    """
    OA has some rows where EN titles are missing in extracted names.
    Use a conservative CN fuzzy fallback only when best candidate is clear.
    """
    if not cn_key:
        return []
    scored = []
    for s in bucket:
        cand = s.get("cn_key", "")
        if not cand:
            continue
        sc = similarity(cn_key, cand)
        if sc <= 0:
            continue
        scored.append((sc, s))
    if not scored:
        return []
    scored.sort(key=lambda x: x[0], reverse=True)
    best_score, best_item = scored[0]
    second_score = scored[1][0] if len(scored) > 1 else 0.0
    # Strict gate: high similarity + visible margin to runner-up.
    if best_score >= 0.84 and (best_score - second_score) >= 0.02:
        return [best_item]
    return []


def build_spell_store() -> Dict[str, List[Dict]]:
    store: Dict[str, List[Dict]] = defaultdict(list)
    for code in BOOK_CODES:
        model_path = RESULT_DIR / code / f"spells-{code}-model.json"
        if not model_path.exists():
            continue
        spells = load_json(model_path)
        for spell in spells:
            name = spell.get("name", "")
            src = (spell.get("source_book") or code).upper()
            cn, en = split_cn_en(name)
            blob_parts = []
            for v in spell.values():
                if isinstance(v, str):
                    blob_parts.append(v)
            blob = "\n".join(blob_parts)
            store[src].append(
                {
                    "book_code": code,
                    "spell_id": spell.get("spell_id", ""),
                    "name": name,
                    "cn_key": normalize_cn(cn),
                    "en_key": normalize_en(en),
                    "blob_en_norm": normalize_en(blob),
                    "file": str(model_path.relative_to(REPO_ROOT)).replace("\\", "/"),
                }
            )
    return store


def locate_index_items(
    index_items: List[Dict], store: Dict[str, List[Dict]], oa_manual_alias: Dict[str, str]
) -> Tuple[List[Dict], Dict]:
    results: List[Dict] = []
    stats = {
        "total_index_items": len(index_items),
        "matched_unique": 0,
        "matched_ambiguous": 0,
        "unmatched": 0,
        "source_not_loaded": 0,
    }

    for idx, item in enumerate(index_items, start=1):
        index_name = item.get("name", "")
        source = (item.get("source_book") or "").upper().strip()
        cn, en = split_cn_en(index_name)
        cn_key = normalize_cn(cn)
        en_key = normalize_en(en)

        bucket = store.get(source, [])
        if not bucket:
            stats["source_not_loaded"] += 1
            results.append(
                {
                    "index_id": f"index-{idx:04d}",
                    "name": index_name,
                    "source_book": source,
                    "status": "source_not_loaded",
                    "matches": [],
                }
            )
            continue

        matches = []
        match_method = "exact"
        if en_key:
            matches = [s for s in bucket if s["en_key"] and s["en_key"] == en_key]
        if not matches and cn_key:
            matches = [s for s in bucket if s["cn_key"] and s["cn_key"] == cn_key]
        if not matches and source == "OA" and en_key:
            mapped_en = oa_manual_alias.get(en_key, "")
            if mapped_en:
                matches = [s for s in bucket if s["en_key"] and s["en_key"] == mapped_en]
                if matches:
                    match_method = "manual_alias"
        if not matches and source == "OA" and cn_key:
            matches = oa_cn_fallback_match(cn_key, bucket)
            if matches:
                match_method = "oa_cn_fallback"
        if not matches and en_key and len(en_key) >= 8:
            blob_hits = [s for s in bucket if en_key in s.get("blob_en_norm", "")]
            if len(blob_hits) == 1:
                matches = blob_hits
                match_method = "embedded_mention_unique"
            elif len(blob_hits) > 1:
                # Some parsers duplicate content within one spell record;
                # allow match when all hits point to the same spell_id.
                spell_ids = {x.get("spell_id", "") for x in blob_hits if x.get("spell_id", "")}
                if len(spell_ids) == 1:
                    sid = next(iter(spell_ids))
                    one = next((x for x in blob_hits if x.get("spell_id", "") == sid), None)
                    if one:
                        matches = [one]
                        match_method = "embedded_mention_spellid_unique"

        if not matches:
            stats["unmatched"] += 1
            results.append(
                {
                    "index_id": f"index-{idx:04d}",
                    "name": index_name,
                    "source_book": source,
                    "status": "unmatched",
                    "matches": [],
                }
            )
            continue

        status = "matched_unique" if len(matches) == 1 else "matched_ambiguous"
        stats[status] += 1
        results.append(
            {
                "index_id": f"index-{idx:04d}",
                "name": index_name,
                "source_book": source,
                "status": status,
                "match_method": match_method,
                "matches": [
                    {
                        "book_code": m["book_code"],
                        "spell_id": m["spell_id"],
                        "name": m["name"],
                        "file": m["file"],
                    }
                    for m in matches[:10]
                ],
            }
        )

    return results, stats


def auto_align_unmatched(located: List[Dict], store: Dict[str, List[Dict]]) -> Tuple[List[Dict], Dict]:
    """
    Auto-align unmatched items with fuzzy matching.
    Scope:
      - only rows with status == unmatched
      - only when source_book exists in loaded store
    Confidence:
      - high: score >= 0.93
      - medium: score >= 0.86
      - low: score >= 0.78
      - unresolved: < 0.78
    """
    aligned: List[Dict] = []
    stats = {
        "unmatched_input": 0,
        "auto_aligned_high": 0,
        "auto_aligned_medium": 0,
        "auto_aligned_low": 0,
        "still_unresolved": 0,
    }

    for row in located:
        if row.get("status") != "unmatched":
            continue
        stats["unmatched_input"] += 1

        source = (row.get("source_book") or "").upper().strip()
        bucket = store.get(source, [])
        if not bucket:
            stats["still_unresolved"] += 1
            continue

        idx_name = row.get("name", "")
        idx_cn, idx_en = split_cn_en(idx_name)
        idx_cn_key = normalize_cn(idx_cn)
        idx_en_key = normalize_en(idx_en)

        scored = []
        for cand in bucket:
            en_score = similarity(idx_en_key, cand.get("en_key", ""))
            cn_score = similarity(idx_cn_key, cand.get("cn_key", ""))
            # Prefer English title score when available; use CN as backoff.
            if idx_en_key:
                total = max(en_score, cn_score * 0.85)
            else:
                total = cn_score
            if total <= 0:
                continue
            scored.append(
                {
                    "score": round(total, 4),
                    "en_score": round(en_score, 4),
                    "cn_score": round(cn_score, 4),
                    "book_code": cand["book_code"],
                    "spell_id": cand["spell_id"],
                    "name": cand["name"],
                    "file": cand["file"],
                }
            )

        if not scored:
            stats["still_unresolved"] += 1
            continue

        scored.sort(key=lambda x: x["score"], reverse=True)
        best = scored[0]
        second = scored[1] if len(scored) > 1 else None
        margin = round(best["score"] - (second["score"] if second else 0.0), 4)

        if best["score"] >= 0.93:
            confidence = "high"
            stats["auto_aligned_high"] += 1
        elif best["score"] >= 0.86:
            confidence = "medium"
            stats["auto_aligned_medium"] += 1
        elif best["score"] >= 0.78:
            confidence = "low"
            stats["auto_aligned_low"] += 1
        else:
            confidence = "unresolved"
            stats["still_unresolved"] += 1

        if confidence == "unresolved":
            continue

        aligned.append(
            {
                "index_id": row["index_id"],
                "name": row["name"],
                "source_book": source,
                "status": f"auto_aligned_{confidence}",
                "best_match": best,
                "score_margin_to_second": margin,
                "top_candidates": scored[:5],
            }
        )

    return aligned, stats


def main() -> None:
    index_items = load_json(INDEX_PATH)
    store = build_spell_store()
    oa_manual_alias = load_oa_manual_alias()
    located, stats = locate_index_items(index_items, store, oa_manual_alias)

    out_located = RESULT_DIR / "index" / "spells-index-located.json"
    out_report = RESULT_DIR / "index" / "spells-index-located-report.json"
    out_auto = RESULT_DIR / "index" / "spells-index-auto-aligned.json"
    out_auto_report = RESULT_DIR / "index" / "spells-index-auto-aligned-report.json"

    out_located.write_text(json.dumps(located, ensure_ascii=False, indent=2), encoding="utf-8")

    report = {
        "note": "index is only used for spell indexing; it is not merged into the full spell list.",
        "search_scope_books": BOOK_CODES,
        "oa_manual_alias_count": len(oa_manual_alias),
        "stats": stats,
        "samples": {
            "matched_unique": [x for x in located if x["status"] == "matched_unique"][:20],
            "matched_ambiguous": [x for x in located if x["status"] == "matched_ambiguous"][:20],
            "unmatched": [x for x in located if x["status"] == "unmatched"][:50],
            "source_not_loaded": [x for x in located if x["status"] == "source_not_loaded"][:50],
        },
    }
    out_report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    auto_aligned, auto_stats = auto_align_unmatched(located, store)
    out_auto.write_text(json.dumps(auto_aligned, ensure_ascii=False, indent=2), encoding="utf-8")
    auto_report = {
        "note": "Auto alignment suggestions for rows initially marked as unmatched.",
        "confidence_policy": {
            "high": "score >= 0.93",
            "medium": "score >= 0.86",
            "low": "score >= 0.78",
            "unresolved": "score < 0.78",
        },
        "stats": auto_stats,
        "samples": {
            "high": [x for x in auto_aligned if x["status"] == "auto_aligned_high"][:30],
            "medium": [x for x in auto_aligned if x["status"] == "auto_aligned_medium"][:30],
            "low": [x for x in auto_aligned if x["status"] == "auto_aligned_low"][:30],
        },
    }
    out_auto_report.write_text(json.dumps(auto_report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[ok] wrote: {out_located}")
    print(f"[ok] wrote: {out_report}")
    print(f"[ok] wrote: {out_auto}")
    print(f"[ok] wrote: {out_auto_report}")
    print(json.dumps(stats, ensure_ascii=False))
    print(json.dumps(auto_stats, ensure_ascii=False))


if __name__ == "__main__":
    main()