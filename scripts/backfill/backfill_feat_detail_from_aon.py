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
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[2]
IN_BOOK_FEATS = ROOT / "result" / "feats" / "feat-book-feats.json"
OUT_BOOK_FEATS = ROOT / "result" / "feats" / "feat-book-feats-with-aon-detail.json"
CACHE_PATH = ROOT / "result" / "feats" / "aon-feat-detail-cache.json"


def normalize_ws(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()


def parse_aon_feat_page(html: str, name_en: str) -> dict[str, str]:
    soup = BeautifulSoup(html, "html.parser")
    node = soup.find(id=re.compile(r"LabelName_\d+$"))
    if node is None:
        for td in soup.find_all("td"):
            t = normalize_ws(td.get_text(" ", strip=True))
            if "Prerequisites" in t and "Benefit" in t and len(t) > 50:
                node = td
                break
    if node is None:
        return {"detail_text": "", "prerequisites_en": "", "benefit_en": ""}

    text = normalize_ws(node.get_text(" ", strip=True))
    if not text:
        return {"detail_text": "", "prerequisites_en": "", "benefit_en": ""}

    # Trim leading "<name> Source ...".
    prefix = text
    pg_match = re.search(r"\bpg\.\s*\d+[A-Za-z]?\b", prefix, flags=re.IGNORECASE)
    if pg_match:
        prefix = normalize_ws(prefix[pg_match.end() :])
    else:
        source_pos = prefix.lower().find("source")
        if source_pos >= 0:
            prefix = normalize_ws(prefix[source_pos + len("source") :])

    prereq = ""
    benefit = ""
    detail = ""

    pre_idx = prefix.find("Prerequisites")
    ben_idx = prefix.find("Benefit")
    normal_idx = prefix.find("Normal")
    special_idx = prefix.find("Special")

    if pre_idx >= 0:
        detail = normalize_ws(prefix[:pre_idx])
        if ben_idx > pre_idx:
            prereq_seg = prefix[pre_idx:ben_idx]
            prereq = normalize_ws(re.sub(r"^Prerequisites\s*:\s*", "", prereq_seg, flags=re.IGNORECASE))
        else:
            prereq_seg = prefix[pre_idx:]
            prereq = normalize_ws(re.sub(r"^Prerequisites\s*:\s*", "", prereq_seg, flags=re.IGNORECASE))
    elif ben_idx >= 0:
        detail = normalize_ws(prefix[:ben_idx])
    else:
        detail = prefix

    if ben_idx >= 0:
        tail_cut = len(prefix)
        for idx in [normal_idx, special_idx]:
            if idx >= 0 and idx > ben_idx:
                tail_cut = min(tail_cut, idx)
        benefit_seg = prefix[ben_idx:tail_cut]
        benefit = normalize_ws(re.sub(r"^Benefit\s*:\s*", "", benefit_seg, flags=re.IGNORECASE))

    # Remove duplicate leading feat name if present.
    if detail.lower().startswith(name_en.lower()):
        detail = normalize_ws(detail[len(name_en) :])

    return {
        "detail_text": detail,
        "prerequisites_en": prereq,
        "benefit_en": benefit,
    }


def fetch_aon_feat_detail(session: requests.Session, name_en: str, timeout: int) -> dict[str, str]:
    url = f"https://aonprd.com/FeatDisplay.aspx?ItemName={quote(name_en)}"
    resp = session.get(url, timeout=timeout)
    if resp.status_code != 200:
        return {"detail_text": "", "prerequisites_en": "", "benefit_en": ""}
    return parse_aon_feat_page(resp.text, name_en)


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill missing feat detail_text from AoN feat pages.")
    parser.add_argument("--input", type=Path, default=IN_BOOK_FEATS)
    parser.add_argument("--output", type=Path, default=OUT_BOOK_FEATS)
    parser.add_argument("--cache", type=Path, default=CACHE_PATH)
    parser.add_argument("--max", type=int, default=0, help="Max feats to fetch from network (0 = all missing)")
    parser.add_argument("--timeout", type=int, default=20, help="HTTP timeout seconds")
    parser.add_argument("--sleep-ms", type=int, default=120, help="Delay between uncached requests")
    parser.add_argument(
        "--refetch-empty-cache",
        action="store_true",
        help="Refetch entries that exist in cache but have empty detail/prerequisite/benefit.",
    )
    parser.add_argument("--inplace", action="store_true")
    args = parser.parse_args()

    data: dict[str, list[dict[str, Any]]] = json.loads(args.input.read_text(encoding="utf-8"))
    cache: dict[str, dict[str, str]] = {}
    if args.cache.exists():
        try:
            cache = json.loads(args.cache.read_text(encoding="utf-8"))
        except Exception:
            cache = {}

    # Build unique feat targets by match_key.
    targets: dict[str, str] = {}
    for rows in data.values():
        for row in rows:
            key = normalize_ws(row.get("match_key", ""))
            if not key:
                continue
            name_en = normalize_ws(row.get("name_en", ""))
            detail_text = normalize_ws(row.get("detail_text", ""))
            if detail_text or not name_en:
                continue
            targets[key] = name_en

    keys = sorted(targets.keys())
    if args.max > 0:
        keys = keys[: args.max]

    session = requests.Session()
    fetched = 0
    cache_hits = 0
    filled = 0
    failed = 0

    for key in keys:
        name_en = targets[key]
        cache_key = key
        payload = cache.get(cache_key)
        cached_empty = False
        if isinstance(payload, dict):
            cached_empty = not any(
                [
                    normalize_ws(payload.get("detail_text", "")),
                    normalize_ws(payload.get("prerequisites_en", "")),
                    normalize_ws(payload.get("benefit_en", "")),
                ]
            )
        should_refetch = payload is None or (args.refetch_empty_cache and cached_empty)
        if should_refetch:
            try:
                payload = fetch_aon_feat_detail(session, name_en, args.timeout)
            except Exception:
                payload = {"detail_text": "", "prerequisites_en": "", "benefit_en": ""}
                failed += 1
            cache[cache_key] = payload
            fetched += 1
            if args.sleep_ms > 0:
                time.sleep(args.sleep_ms / 1000.0)
        else:
            cache_hits += 1

        detail_en = normalize_ws(payload.get("detail_text", ""))
        prereq_en = normalize_ws(payload.get("prerequisites_en", ""))
        benefit_en = normalize_ws(payload.get("benefit_en", ""))
        if not any([detail_en, prereq_en, benefit_en]):
            continue

        for rows in data.values():
            for row in rows:
                if row.get("match_key") != key:
                    continue
                changed = False
                if not normalize_ws(row.get("detail_text", "")) and detail_en:
                    row["detail_text"] = detail_en
                    changed = True
                if not normalize_ws(row.get("prerequisites", "")) and prereq_en:
                    row["prerequisites"] = prereq_en
                    changed = True
                if not normalize_ws(row.get("benefit_summary", "")) and benefit_en:
                    row["benefit_summary"] = benefit_en
                    changed = True
                if changed:
                    filled += 1

    args.cache.parent.mkdir(parents=True, exist_ok=True)
    args.cache.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")

    out_path = args.input if args.inplace else args.output
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    print("Done.")
    print(f"Input feats books: {len(data)}")
    print(f"Targets: {len(keys)}")
    print(f"Fetched: {fetched}")
    print(f"Cache hits: {cache_hits}")
    print(f"Filled rows: {filled}")
    print(f"Failed fetches: {failed}")
    print(f"Output: {out_path}")
    print(f"Cache: {args.cache}")


if __name__ == "__main__":
    main()