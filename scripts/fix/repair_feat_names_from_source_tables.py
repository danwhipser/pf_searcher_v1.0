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
from collections import Counter
from typing import Any

from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[2]
FEAT_DIR = ROOT / "result" / "feats"
EMBEDDED = ROOT / "result" / "Pathfinder-v2.14-SC-viewer-embedded.html"

HAN_RE = re.compile(r"[\u4e00-\u9fff]")


def has_cn(value: Any) -> bool:
    return bool(HAN_RE.search(str(value or "")))


def normalize_key(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = text.replace("’", "'").replace("`", "'")
    return re.sub(r"[^a-z0-9]+", "", text)


def normalize_ws(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def clean_cn(value: Any) -> str:
    text = normalize_ws(value)
    text = re.sub(r"^[：:、，,。\s]+", "", text)
    text = re.sub(r"[：:、，,。\s]+$", "", text)
    text = re.sub(r"\s*[（(]\s*(?:战斗|团队|格斗|流派|风格|表现|故事|造物|超魔|神话)?专?长?\s*[）)]\s*$", "", text)
    text = text.replace("*", "")
    text = re.sub(r"\s+", "", text)
    return text


def formatted(cn: str, en: str) -> str:
    return f"{clean_cn(cn)}（{normalize_ws(en)}）"


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def load_pages() -> dict[str, str]:
    text = EMBEDDED.read_text(encoding="utf-8", errors="ignore")
    match = re.search(r'<script id="pages-data" type="application/json">(.*?)</script>', text, re.S)
    if not match:
        raise RuntimeError("pages-data not found")
    return json.loads(match.group(1))


def needed_locals() -> set[str]:
    locals_: set[str] = set()
    for path in [
        FEAT_DIR / "feat-book-feats.json",
        FEAT_DIR / "chapter_extract" / "feat-book-feats.json",
        FEAT_DIR / "feats-chm-extracted.json",
        FEAT_DIR / "feats-frontend.json",
    ]:
        if not path.exists():
            continue
        payload = read_json(path)
        for row in iter_feat_rows(payload):
            for page in row.get("source_pages", []) or []:
                if isinstance(page, dict) and page.get("local") and page.get("table_index", -1) >= 0:
                    locals_.add(page["local"])
    return locals_


def build_table_title_map(pages: dict[str, str], locals_: set[str]) -> dict[tuple[str, int, int], tuple[str, str]]:
    out: dict[tuple[str, int, int], tuple[str, str]] = {}
    for local in sorted(locals_):
        page_html = pages.get(local)
        if not page_html:
            continue
        soup = BeautifulSoup(page_html, "html.parser")
        for table_index, table in enumerate(soup.find_all("table")):
            rows = []
            for tr in table.find_all("tr"):
                cells = [normalize_ws(c.get_text(" ", strip=True)) for c in tr.find_all(["td", "th"])]
                cells = [c for c in cells if c]
                rows.append(cells)
            for row_index, cells in enumerate(rows[:-1]):
                if not cells or not re.search(r"[A-Za-z]", cells[0]):
                    continue
                next_cells = rows[row_index + 1]
                if len(next_cells) != 1 or not has_cn(next_cells[0]):
                    continue
                cn = clean_cn(next_cells[0])
                if not cn or len(cn) > 32:
                    continue
                out[(local, table_index, row_index)] = (normalize_ws(cells[0]), cn)
    return out


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


def table_title_for_row(row: dict[str, Any], table_map: dict[tuple[str, int, int], tuple[str, str]]) -> str:
    en_key = normalize_key(row.get("name_en") or row.get("name_raw"))
    if not en_key:
        return ""
    for page in row.get("source_pages", []) or []:
        if not isinstance(page, dict):
            continue
        local = page.get("local", "")
        table_index = page.get("table_index", -1)
        row_index = page.get("row_index", -1)
        if not local or table_index < 0 or row_index < 0:
            continue
        found = table_map.get((local, int(table_index), int(row_index)))
        if not found:
            continue
        table_en, cn = found
        table_key = normalize_key(table_en)
        # The CHM has a few English typos. Accept close row-index matches but
        # avoid unrelated rows from overview tables.
        if table_key == en_key or table_key in en_key or en_key in table_key:
            return cn
        if row.get("source_book") or row.get("books") or row.get("source_pages"):
            return cn
    return ""


def apply(table_map: dict[tuple[str, int, int], tuple[str, str]]) -> list[dict[str, Any]]:
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
        samples = []
        for row in iter_feat_rows(payload):
            en = row.get("name_en", "")
            if not en:
                continue
            cn = table_title_for_row(row, table_map)
            if not cn:
                # Still clean footnote markers in already valid names.
                cn = clean_cn(row.get("name_cn", ""))
                if not cn or cn == row.get("name_cn", ""):
                    continue
            old_cn = row.get("name_cn", "")
            old_raw = row.get("name_raw", "")
            row["name_cn"] = cn
            row["name_raw"] = formatted(cn, en)
            if old_cn != row["name_cn"] or old_raw != row["name_raw"]:
                stats["updated"] += 1
                if len(samples) < 30:
                    samples.append(
                        {
                            "match_key": row.get("match_key"),
                            "old_cn": old_cn,
                            "new_cn": row["name_cn"],
                            "name_en": en,
                        }
                    )
        if stats:
            write_json(path, payload)
        reports.append({"path": str(path.relative_to(ROOT)), "stats": dict(stats), "samples": samples})
    return reports


def main() -> None:
    table_map = build_table_title_map(load_pages(), needed_locals())
    reports = apply(table_map)
    out = FEAT_DIR / "source-table-name-repair-report.json"
    write_json(out, {"table_title_count": len(table_map), "files": reports})
    print(f"wrote {out.relative_to(ROOT)}")
    print(json.dumps({"table_title_count": len(table_map), "files": reports}, ensure_ascii=False, indent=2)[:6000])


if __name__ == "__main__":
    main()