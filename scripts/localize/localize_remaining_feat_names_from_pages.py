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
FEAT_DIR = ROOT / "result" / "feats"
EMBEDDED = ROOT / "result" / "Pathfinder-v2.14-SC-viewer-embedded.html"

HAN_RE = re.compile(r"[\u4e00-\u9fff]")
ASCII_RE = re.compile(r"[A-Za-z]")


def has_cn(value: Any) -> bool:
    return bool(HAN_RE.search(str(value or "")))


def normalize_key(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = text.replace("’", "'").replace("`", "'").replace("，", ",")
    return re.sub(r"[^a-z0-9]+", "", text)


def normalize_ws(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def clean_cn(value: str) -> str:
    text = normalize_ws(value)
    text = re.sub(r"^[：:、，,。\s]+", "", text)
    text = re.sub(r"[：:、，,。\s]+$", "", text)
    text = re.sub(r"\s*[（(]\s*(?:战斗|团队|格斗|流派|风格|表现|故事|造物|超魔|神话)?专?长?\s*[）)]\s*$", "", text)
    text = re.sub(r"\s+", "", text)
    return text


def formatted(cn: str, en: str) -> str:
    return f"{clean_cn(cn)}（{normalize_ws(en)}）"


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def remaining_frontend_feats() -> list[dict[str, Any]]:
    data = read_json(FEAT_DIR / "feats-frontend.json")
    return [
        row
        for row in data.get("feats", [])
        if row.get("name_en") and not has_cn(row.get("name_cn", ""))
    ]


def load_embedded_pages() -> dict[str, str]:
    text = EMBEDDED.read_text(encoding="utf-8", errors="ignore")
    match = re.search(r'<script id="pages-data" type="application/json">(.*?)</script>', text, re.S)
    if not match:
        raise RuntimeError("pages-data script not found")
    return json.loads(match.group(1))


def plausible_cn_title(text: str) -> bool:
    value = clean_cn(text)
    if not value or not has_cn(value):
        return False
    if len(value) > 40:
        return False
    blocked = {"战斗专长", "先决条件", "专长效果", "类型", "专长", "译者", "目录"}
    return value not in blocked


def collect_table_pairs(soup: BeautifulSoup) -> dict[str, set[str]]:
    found: dict[str, set[str]] = defaultdict(set)
    for table in soup.find_all("table"):
        rows = []
        for tr in table.find_all("tr"):
            cells = [normalize_ws(c.get_text(" ", strip=True)) for c in tr.find_all(["td", "th"])]
            cells = [c for c in cells if c]
            if cells:
                rows.append(cells)
        for idx, cells in enumerate(rows):
            first = cells[0]
            if not ASCII_RE.search(first):
                continue
            cn = ""
            if idx + 1 < len(rows) and len(rows[idx + 1]) == 1 and plausible_cn_title(rows[idx + 1][0]):
                cn = rows[idx + 1][0]
            if not cn:
                cn = cn_before_en(first)
            if cn:
                found[normalize_key(first)].add(clean_cn(cn))
    return found


def cn_before_en(text: str) -> str:
    value = normalize_ws(text)
    # Common title forms: 中文 English, 中文（English）, 中文 English（战斗）
    m = re.match(r"^(?P<cn>.*?[\u4e00-\u9fff][\u4e00-\u9fff·・、\s-]{0,40})\s*[（(]?\s*(?P<en>[A-Za-z][A-Za-z0-9'’ ,/-]{2,})", value)
    if m:
        return clean_cn(m.group("cn"))
    return ""


def collect_prose_pairs(soup: BeautifulSoup) -> dict[str, set[str]]:
    found: dict[str, set[str]] = defaultdict(set)
    text = soup.get_text("\n")
    lines = [normalize_ws(x) for x in text.splitlines()]
    lines = [x for x in lines if x]
    for line in lines:
        cn = cn_before_en(line)
        if cn and plausible_cn_title(cn):
            # Keep all English tail fragments in the line; the target matcher
            # will fuzzy-match within the same source book.
            tail = line[line.find(cn) + len(cn) :]
            for m in re.finditer(r"[A-Za-z][A-Za-z0-9'’ ,/-]{2,80}", tail):
                found[normalize_key(m.group(0))].add(clean_cn(cn))
    return found


def merge_maps(dst: dict[str, set[str]], src: dict[str, set[str]]) -> None:
    for key, values in src.items():
        for value in values:
            if value:
                dst[key].add(value)


def best_match_name(target: dict[str, Any], candidates: dict[str, set[str]]) -> tuple[str, str, float]:
    keys = [normalize_key(target.get("name_en")), normalize_key(target.get("name_raw")), normalize_key(target.get("match_key"))]
    keys = [k for k in keys if k]
    for key in keys:
        values = candidates.get(key)
        if values:
            return sorted(values, key=len)[0], key, 1.0
    best_key = ""
    best_score = 0.0
    for candidate_key in candidates:
        for key in keys:
            score = difflib.SequenceMatcher(None, key, candidate_key).ratio()
            if key in candidate_key or candidate_key in key:
                score += 0.08
            if score > best_score:
                best_score = score
                best_key = candidate_key
    if best_key and best_score >= 0.86:
        return sorted(candidates[best_key], key=len)[0], best_key, best_score
    return "", "", 0.0


def source_pages_for_targets(targets: list[dict[str, Any]]) -> dict[str, set[str]]:
    raw = read_json(FEAT_DIR / "feat-book-feats.json")
    target_keys = {row.get("match_key") for row in targets}
    pages_by_book: dict[str, set[str]] = defaultdict(set)
    for book, rows in raw.items():
        if not isinstance(rows, list):
            continue
        for row in rows:
            if row.get("match_key") not in target_keys:
                continue
            for page in row.get("source_pages", []) or []:
                local = page.get("local") if isinstance(page, dict) else ""
                if local:
                    pages_by_book[book].add(local)
    return pages_by_book


def build_book_candidate_maps(
    pages: dict[str, str],
    targets_by_book: dict[str, list[dict[str, Any]]],
    pages_by_book: dict[str, set[str]],
) -> dict[str, dict[str, set[str]]]:
    maps: dict[str, dict[str, set[str]]] = {}
    for book, targets in targets_by_book.items():
        book_map: dict[str, set[str]] = defaultdict(set)
        for local in sorted(pages_by_book.get(book, [])):
            html_text = pages.get(local)
            if not html_text:
                continue
            soup = BeautifulSoup(html_text, "html.parser")
            merge_maps(book_map, collect_table_pairs(soup))
            merge_maps(book_map, collect_prose_pairs(soup))
        maps[book] = book_map
    return maps


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


def apply_updates(updates: dict[str, str]) -> list[dict[str, Any]]:
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
        stats = Counter()
        for row in iter_feat_rows(payload):
            key = row.get("match_key")
            if key not in updates:
                continue
            cn = updates[key]
            en = row.get("name_en", "")
            if cn and en:
                if not has_cn(row.get("name_cn", "")):
                    row["name_cn"] = cn
                    stats["filled_name_cn"] += 1
                row["name_raw"] = formatted(cn, en)
                stats["normalized_name_raw"] += 1
        if stats:
            write_json(path, payload)
        reports.append({"path": str(path.relative_to(ROOT)), "stats": dict(stats)})
    return reports


def main() -> None:
    targets = remaining_frontend_feats()
    by_book: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in targets:
        book = (row.get("books") or [""])[0]
        by_book[book].append(row)

    pages = load_embedded_pages()
    pages_by_book = source_pages_for_targets(targets)
    candidate_maps = build_book_candidate_maps(pages, by_book, pages_by_book)
    updates: dict[str, str] = {}
    match_report = []
    unresolved = []

    for book, rows in sorted(by_book.items()):
        candidates = candidate_maps.get(book, {})
        for row in rows:
            cn, matched_key, score = best_match_name(row, candidates)
            if cn:
                updates[row["match_key"]] = cn
                match_report.append(
                    {
                        "book": book,
                        "match_key": row.get("match_key"),
                        "name_en": row.get("name_en"),
                        "name_cn": cn,
                        "matched_candidate_key": matched_key,
                        "score": round(score, 4),
                    }
                )
            else:
                unresolved.append(
                    {
                        "book": book,
                        "match_key": row.get("match_key"),
                        "name_en": row.get("name_en"),
                        "candidate_count": len(candidates),
                        "candidate_sample": [
                            {"key": k, "cn": sorted(v)}
                            for k, v in list(candidates.items())[:20]
                        ],
                    }
                )

    file_reports = apply_updates(updates)
    out = FEAT_DIR / "remaining-feat-name-localization-report.json"
    write_json(
        out,
        {
            "targets": len(targets),
            "matched": len(match_report),
            "unresolved": len(unresolved),
            "matches": match_report,
            "unresolved_items": unresolved,
            "file_reports": file_reports,
        },
    )
    print(f"wrote {out.relative_to(ROOT)}")
    print(f"targets={len(targets)} matched={len(match_report)} unresolved={len(unresolved)}")
    for row in unresolved[:50]:
        print("UNRESOLVED", row["book"], row["name_en"], "candidates", row["candidate_count"])


if __name__ == "__main__":
    main()