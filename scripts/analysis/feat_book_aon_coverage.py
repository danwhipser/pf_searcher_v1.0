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
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.extract.extract_feats_and_verify import normalize_key


FEAT_BOOK_FEATS_PATH = ROOT / "result" / "feats" / "feat-book-feats.json"
OUT_DIR = ROOT / "result" / "feats"
AON_SOURCE_URL = "https://www.aonprd.com/SourceDisplay.aspx?FixedSource={name}"


def canonical_feat_key(name: str) -> str:
    """Normalize feat name and strip trailing source tags like '(DTT)'."""
    text = (name or "").strip()
    text = " ".join(text.split())
    text = re.sub(r"\s*\(([A-Z]{2,6})\)\s*$", "", text)
    return normalize_key(text)


# CHM 专长节点书名 -> AoN SourceDisplay 固定来源名（可多本合并）
BOOK_TO_AON_SOURCES: dict[str, list[str]] = {
    "CRB 核心规则手册": ["PRPG Core Rulebook"],
    "APG 进阶玩家手册": ["Advanced Player's Guide"],
    "ARG 进阶种族手册": ["Advanced Race Guide"],
    "ACG 进阶职业手册": ["Advanced Class Guide"],
    "UM 极限魔法": ["Ultimate Magic"],
    "UC 极限战斗": ["Ultimate Combat"],
    "UCa 极限战役": ["Ultimate Campaign"],
    "UI 极限诡道": ["Ultimate Intrigue"],
    "OA异能冒险": ["Occult Adventures"],
    "MA 神话冒险": ["Mythic Adventures"],
    "B1 怪物图鉴": ["Pathfinder RPG Bestiary"],
    "内海种族": ["Inner Sea Races"],
    "内海诸神": ["Inner Sea Gods"],
    "内海魔法": ["Inner Sea Magic"],
    "内海战斗": ["Inner Sea Combat"],
    "内海世界指南": ["Inner Sea World Guide"],
    "纯善/平衡/堕落信念": ["Faiths of Purity", "Faiths of Balance", "Faiths of Corruption"],
    "近战战术工具箱": ["Melee Tactics Toolbox"],
    "远程战术工具箱": ["Ranged Tactics Toolbox"],
    "阴招战术工具箱": ["Dirty Tactics Toolbox"],
    "魔法战术工具箱": ["Magic Tactics Toolbox"],
    "DEP初探龙国": ["Dragon Empires Primer"],
    "进化职业起源（ACO）": ["Advanced Class Origins"],
    "冒险家手册": ["Dungeoneer's Handbook"],
    "亡灵杀手手册": ["Undead Slayer's Handbook"],
    "护甲大师": ["Armor Master's Handbook"],
    "格拉里昂的半身人": ["Halflings of Golarion"],
    "切里亚斯，魔鬼王朝": ["Cheliax, Empire of Devils"],
    "萨加瓦，失落的殖民地": ["Sargava, the Lost Colony"],
    "瓦瑞西亚，传说诞生之地": ["Varisia, Birthplace of Legends"],
    "信仰与哲学": ["Faiths and Philosophies"],
    "荒野英雄": ["Heroes of the Wild"],
    "异能奥秘": ["Occult Mysteries"],
}


AGGREGATE_BOOKS = {
    "专长概述",
    "全专长列表",
    "超魔专长一览",
    "流派专长一览",
    "造物专长一览",
    "团队背叛专长",
    "皇庭英豪",
}


def fetch_source_feats(session: requests.Session, source_name: str) -> tuple[list[str], str]:
    url = AON_SOURCE_URL.format(name=quote(source_name))
    last_err: Exception | None = None
    resp = None
    for _ in range(4):
        try:
            resp = session.get(url, timeout=60)
            resp.raise_for_status()
            last_err = None
            break
        except Exception as exc:
            last_err = exc
            resp = None
            continue
    if resp is None:
        assert last_err is not None
        raise last_err
    soup = BeautifulSoup(resp.text, "html.parser")
    links = soup.select('a[href*="FeatDisplay.aspx?ItemName="]')
    names: list[str] = []
    seen = set()
    for a in links:
        name = " ".join(a.get_text(" ", strip=True).split()).strip()
        key = normalize_key(name)
        if not key or key in seen:
            continue
        seen.add(key)
        names.append(name)
    return names, url


def main() -> None:
    parser = argparse.ArgumentParser(description="Per-book feat coverage against AoN source pages.")
    parser.add_argument("--input", type=Path, default=FEAT_BOOK_FEATS_PATH)
    parser.add_argument("--out-dir", type=Path, default=OUT_DIR)
    args = parser.parse_args()

    book_feats: dict[str, list[dict[str, Any]]] = json.loads(args.input.read_text(encoding="utf-8"))
    session = requests.Session()
    session.headers.update({"User-Agent": "PF-RAG-Feat-Coverage/1.0"})

    source_cache: dict[str, dict[str, Any]] = {}
    existing_cache_path = args.out_dir / "aon-source-feat-cache.json"
    if existing_cache_path.exists():
        try:
            source_cache = json.loads(existing_cache_path.read_text(encoding="utf-8"))
        except Exception:
            source_cache = {}
    book_reports: list[dict[str, Any]] = []

    for book, feats in book_feats.items():
        chm_names = [x.get("name_en") or x.get("name_raw") or "" for x in feats]
        chm_keys = {canonical_feat_key(x) for x in chm_names if canonical_feat_key(x)}

        if book in AGGREGATE_BOOKS:
            book_reports.append(
                {
                    "book": book,
                    "status": "aggregate_skip",
                    "chm_unique_feats": len(chm_keys),
                    "aon_expected_feats": None,
                    "matched_feats": None,
                    "coverage": None,
                    "sources": [],
                    "missing_in_chm": [],
                    "chm_only_not_in_aon_source": [],
                }
            )
            continue

        source_names = BOOK_TO_AON_SOURCES.get(book, [])
        if not source_names:
            book_reports.append(
                {
                    "book": book,
                    "status": "unmapped",
                    "chm_unique_feats": len(chm_keys),
                    "aon_expected_feats": None,
                    "matched_feats": None,
                    "coverage": None,
                    "sources": [],
                    "missing_in_chm": [],
                    "chm_only_not_in_aon_source": [],
                }
            )
            continue

        aon_names_all: list[str] = []
        fetch_errors: list[str] = []
        source_entries: list[dict[str, Any]] = []
        for src in source_names:
            if src in source_cache:
                src_item = source_cache[src]
                source_entries.append(
                    {
                        "source_name": src,
                        "url": src_item["url"],
                        "feat_count": len(src_item["names"]),
                        "from_cache": True,
                    }
                )
                aon_names_all.extend(src_item["names"])
                continue
            try:
                names, url = fetch_source_feats(session, src)
                source_cache[src] = {"names": names, "url": url}
                source_entries.append(
                    {
                        "source_name": src,
                        "url": url,
                        "feat_count": len(names),
                        "from_cache": False,
                    }
                )
                aon_names_all.extend(names)
            except Exception as exc:
                # fallback to previously cached source when available
                cached = source_cache.get(src)
                if cached and isinstance(cached.get("names"), list):
                    names = cached["names"]
                    url = cached.get("url", AON_SOURCE_URL.format(name=quote(src)))
                    source_entries.append(
                        {
                            "source_name": src,
                            "url": url,
                            "feat_count": len(names),
                            "from_cache": True,
                            "cache_fallback": True,
                        }
                    )
                    aon_names_all.extend(names)
                else:
                    fetch_errors.append(f"{src}: {exc}")

        if fetch_errors and not aon_names_all:
            book_reports.append(
                {
                    "book": book,
                    "status": "fetch_error",
                    "errors": fetch_errors,
                    "chm_unique_feats": len(chm_keys),
                    "aon_expected_feats": None,
                    "matched_feats": None,
                    "coverage": None,
                    "sources": source_entries,
                    "missing_in_chm": [],
                    "chm_only_not_in_aon_source": [],
                }
            )
            continue

        # union dedupe while preserving order
        seen = set()
        aon_names_unique: list[str] = []
        for n in aon_names_all:
            k = canonical_feat_key(n)
            if not k or k in seen:
                continue
            seen.add(k)
            aon_names_unique.append(n)

        aon_key_to_name = {}
        for n in aon_names_unique:
            k = canonical_feat_key(n)
            if not k:
                continue
            # keep first display name for stable output
            aon_key_to_name.setdefault(k, n)
        aon_keys = set(aon_key_to_name.keys())

        matched = sorted(chm_keys & aon_keys)
        missing = sorted(aon_keys - chm_keys)
        chm_only = sorted(chm_keys - aon_keys)

        book_reports.append(
            {
                "book": book,
                "status": "ok" if not fetch_errors else "partial_fetch_error",
                "errors": fetch_errors,
                "chm_unique_feats": len(chm_keys),
                "aon_expected_feats": len(aon_keys),
                "matched_feats": len(matched),
                "coverage": (len(matched) / len(aon_keys)) if aon_keys else 0.0,
                "sources": source_entries,
                "missing_in_chm": [{"match_key": k, "name_en": aon_key_to_name[k]} for k in missing],
                "chm_only_not_in_aon_source": [{"match_key": k} for k in chm_only],
            }
        )

    # sort for readability: mapped/ok first by coverage asc (worst first)
    def sort_key(x: dict[str, Any]) -> tuple[int, float]:
        st = x.get("status")
        priority = 0 if st in {"ok", "partial_fetch_error"} else 1
        cov = x.get("coverage")
        cov_val = cov if isinstance(cov, (int, float)) else 1.0
        return (priority, cov_val)

    book_reports.sort(key=sort_key)

    summary = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "books_total": len(book_reports),
        "books_ok": sum(1 for x in book_reports if x.get("status") == "ok"),
        "books_partial_fetch_error": sum(1 for x in book_reports if x.get("status") == "partial_fetch_error"),
        "books_fetch_error": sum(1 for x in book_reports if x.get("status") == "fetch_error"),
        "books_unmapped": sum(1 for x in book_reports if x.get("status") == "unmapped"),
        "books_aggregate_skip": sum(1 for x in book_reports if x.get("status") == "aggregate_skip"),
    }

    args.out_dir.mkdir(parents=True, exist_ok=True)
    out_report = args.out_dir / "feat-book-aon-coverage.json"
    out_sources = args.out_dir / "aon-source-feat-cache.json"
    out_report.write_text(
        json.dumps({"summary": summary, "books": book_reports}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    out_sources.write_text(json.dumps(source_cache, ensure_ascii=False, indent=2), encoding="utf-8")

    print("Done.")
    print(summary)
    for b in book_reports:
        if b["status"] in {"ok", "partial_fetch_error"}:
            print(
                f"{b['book']}: {b['matched_feats']}/{b['aon_expected_feats']} "
                f"({(b['coverage'] or 0):.2%}) status={b['status']}"
            )
        else:
            print(f"{b['book']}: status={b['status']}")


if __name__ == "__main__":
    main()