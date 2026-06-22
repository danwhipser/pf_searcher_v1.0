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
from typing import Any

import requests

ROOT = Path(__file__).resolve().parents[2]
FRONTEND_PATH = ROOT / "result" / "feats" / "feats-frontend.json"
BOOK_FEATS_PATH = ROOT / "result" / "feats" / "feat-book-feats.json"
CACHE_PATH = ROOT / "result" / "feats" / "aon-feat-detail-cache.json"
AON_SOURCE_CACHE = ROOT / "result" / "feats" / "aon-source-feat-cache.json"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.backfill.backfill_feat_detail_from_aon import fetch_aon_feat_detail, normalize_ws
from scripts.extract.extract_feats_and_verify import normalize_key


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def has_payload(payload: dict[str, str] | None) -> bool:
    if not isinstance(payload, dict):
        return False
    return any(
        [
            normalize_ws(payload.get("detail_text", "")),
            normalize_ws(payload.get("prerequisites_en", "")),
            normalize_ws(payload.get("benefit_en", "")),
        ]
    )


def fetch_aon_feats_listing_map(session: requests.Session, timeout: int) -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    try:
        resp = session.get("https://aonprd.com/Feats.aspx", timeout=timeout)
        if resp.status_code != 200:
            return out
    except Exception:
        return out

    from bs4 import BeautifulSoup

    soup = BeautifulSoup(resp.text, "html.parser")
    for table in soup.find_all("table"):
        for tr in table.find_all("tr"):
            cells = tr.find_all(["td", "th"])
            if len(cells) < 3:
                continue
            name_text = normalize_ws(cells[0].get_text(" ", strip=True))
            prereq_text = normalize_ws(cells[1].get_text(" ", strip=True))
            desc_text = normalize_ws(cells[2].get_text(" ", strip=True))
            if not name_text or name_text.lower() in {"name", "feat", "feat name"}:
                continue
            name_text = re.sub(r"[\*\u2020\u2021]+$", "", name_text).strip()
            k = normalize_key(name_text)
            if not k:
                continue
            if k not in out:
                out[k] = {
                    "name_en": name_text,
                    "prerequisites_en": prereq_text,
                    "benefit_en": desc_text,
                    "detail_text": desc_text,
                }
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch AoN detail for frontend feats with missing detail_text.")
    parser.add_argument("--frontend", type=Path, default=FRONTEND_PATH)
    parser.add_argument("--book-feats", type=Path, default=BOOK_FEATS_PATH)
    parser.add_argument("--cache", type=Path, default=CACHE_PATH)
    parser.add_argument("--aon-source-cache", type=Path, default=AON_SOURCE_CACHE)
    parser.add_argument("--timeout", type=int, default=20)
    parser.add_argument("--sleep-ms", type=int, default=80)
    parser.add_argument("--max", type=int, default=0, help="max number of missing feats to fetch (0 = all)")
    args = parser.parse_args()

    frontend = load_json(args.frontend, {})
    book_feats = load_json(args.book_feats, {})
    cache: dict[str, dict[str, str]] = load_json(args.cache, {})
    aon_source_cache: dict[str, dict[str, Any]] = load_json(args.aon_source_cache, {})

    key_to_aon_name: dict[str, str] = {}
    for item in aon_source_cache.values():
        if not isinstance(item, dict):
            continue
        for name in item.get("names", []) or []:
            if not isinstance(name, str):
                continue
            k = normalize_key(name)
            if k and k not in key_to_aon_name:
                key_to_aon_name[k] = name
            # Also map stripped names like "Spell Bluff (UM)" -> "spellbluff".
            base = re.sub(r"\s*\([^)]*\)\s*$", "", name).strip()
            kb = normalize_key(base)
            if kb and kb not in key_to_aon_name:
                key_to_aon_name[kb] = name

    feats = frontend.get("feats", []) if isinstance(frontend, dict) else []
    missing = [f for f in feats if not normalize_ws(f.get("detail_text", ""))]
    if args.max > 0:
        missing = missing[: args.max]

    session = requests.Session()
    feats_listing_map: dict[str, dict[str, str]] | None = None
    fetched = 0
    cache_hits = 0
    filled_front = 0
    filled_book_rows = 0
    failures = 0
    still_missing_name = 0

    for feat in missing:
        key = normalize_ws(feat.get("match_key", ""))
        if not key:
            continue
        fetch_name = key_to_aon_name.get(key) or normalize_ws(feat.get("name_en", ""))
        if not fetch_name:
            still_missing_name += 1
            continue

        payload = cache.get(key)
        if not has_payload(payload):
            try:
                payload = fetch_aon_feat_detail(session, fetch_name, args.timeout)
            except Exception:
                payload = {"detail_text": "", "prerequisites_en": "", "benefit_en": ""}
                failures += 1
            if not has_payload(payload):
                if feats_listing_map is None:
                    feats_listing_map = fetch_aon_feats_listing_map(session, args.timeout)
                fallback = (feats_listing_map or {}).get(key)
                if isinstance(fallback, dict):
                    payload = {
                        "detail_text": normalize_ws(fallback.get("detail_text", "")),
                        "prerequisites_en": normalize_ws(fallback.get("prerequisites_en", "")),
                        "benefit_en": normalize_ws(fallback.get("benefit_en", "")),
                    }
            cache[key] = payload
            fetched += 1
            if args.sleep_ms > 0:
                time.sleep(args.sleep_ms / 1000.0)
        else:
            cache_hits += 1

        if not has_payload(payload):
            continue

        detail = normalize_ws(payload.get("detail_text", ""))
        prereq = normalize_ws(payload.get("prerequisites_en", ""))
        benefit = normalize_ws(payload.get("benefit_en", ""))

        changed_front = False
        if not normalize_ws(feat.get("detail_text", "")) and detail:
            feat["detail_text"] = detail
            changed_front = True
        if not normalize_ws(feat.get("prerequisites", "")) and prereq:
            feat["prerequisites"] = prereq
            changed_front = True
        if not normalize_ws(feat.get("benefit_summary", "")) and benefit:
            feat["benefit_summary"] = benefit
            changed_front = True
        if changed_front:
            filled_front += 1

        for rows in book_feats.values():
            for row in rows:
                if normalize_ws(row.get("match_key", "")) != key:
                    continue
                changed = False
                if not normalize_ws(row.get("detail_text", "")) and detail:
                    row["detail_text"] = detail
                    changed = True
                if not normalize_ws(row.get("prerequisites", "")) and prereq:
                    row["prerequisites"] = prereq
                    changed = True
                if not normalize_ws(row.get("benefit_summary", "")) and benefit:
                    row["benefit_summary"] = benefit
                    changed = True
                if changed:
                    filled_book_rows += 1

    args.cache.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
    args.frontend.write_text(json.dumps(frontend, ensure_ascii=False, indent=2), encoding="utf-8")
    args.book_feats.write_text(json.dumps(book_feats, ensure_ascii=False, indent=2), encoding="utf-8")

    remaining = sum(1 for f in feats if not normalize_ws(f.get("detail_text", "")))
    print("Done.")
    print(f"Missing feats processed: {len(missing)}")
    print(f"Fetched: {fetched}")
    print(f"Cache hits: {cache_hits}")
    print(f"Frontend filled: {filled_front}")
    print(f"Book rows filled: {filled_book_rows}")
    print(f"Fetch failures: {failures}")
    print(f"Missing English names: {still_missing_name}")
    print(f"Remaining frontend missing detail: {remaining}")


if __name__ == "__main__":
    main()