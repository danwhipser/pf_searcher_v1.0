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
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.extract.extract_feats_and_verify import (
    parse_table_matrix,
    extract_feats_from_page,
    find_top_level_node,
    load_embedded_pages,
    load_toc,
    normalize_key,
    normalize_ws,
    canonicalize_en_name,
)
from bs4 import BeautifulSoup


TOC_PATH = ROOT / "result" / "toc.json"
VIEWER_PATH = ROOT / "result" / "Pathfinder-v2.14-SC-viewer-embedded.html"
OUT_DIR = ROOT / "result" / "feats"


def _make_table_source(local: str, toc_path: str, table_index: int) -> dict[str, Any]:
    return {
        "local": local,
        "toc_path": toc_path,
        "table_index": table_index,
    }


def _contains_any(text: str, needles: list[str]) -> bool:
    lower = normalize_ws(text).lower()
    return any(x.lower() in lower for x in needles)


def _dedup_attached_tables(tables: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen_signatures: set[str] = set()
    for item in tables:
        signature_payload = {
            "table_type": item.get("table_type"),
            "columns": item.get("columns"),
            "rows": item.get("rows"),
            "sections": item.get("sections"),
        }
        signature = json.dumps(signature_payload, ensure_ascii=False, sort_keys=True)
        if signature in seen_signatures:
            continue
        seen_signatures.add(signature)
        out.append(item)
    return out


def extract_leadership_score_table(html: str, local: str, toc_path: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    out: list[dict[str, Any]] = []

    for ti, table in enumerate(soup.find_all("table")):
        matrix = parse_table_matrix(table)
        if not matrix:
            continue

        probe_text = " ".join(" ".join(r) for r in matrix[:5] if r)
        if not (
            _contains_any(probe_text, ["领导力值", "Leadership Score"])
            and _contains_any(probe_text, ["部属等级", "Cohort Level"])
        ):
            continue

        header_idx = None
        for i, row in enumerate(matrix[:8]):
            cells = [normalize_ws(x) for x in row]
            if len(cells) < 8:
                continue
            if _contains_any(cells[0], ["领导力值", "Leadership Score"]) and _contains_any(
                cells[1], ["部属等级", "Cohort Level"]
            ):
                header_idx = i
                break
        if header_idx is None:
            continue

        level_headers: list[str] = []
        level_header_idx = header_idx
        for i in range(header_idx, min(header_idx + 3, len(matrix))):
            cells = [normalize_ws(x) for x in matrix[i]]
            if len(cells) < 8:
                continue
            candidate = cells[2:8]
            if sum(1 for x in candidate if re.search(r"\d", x)) >= 3:
                level_headers = candidate
                level_header_idx = i
                break
        if not level_headers:
            level_headers = [f"{i}级" for i in range(1, 7)]

        level_keys: list[str] = []
        for i, label in enumerate(level_headers, start=1):
            m = re.search(r"(\d+)", label)
            if m:
                level_keys.append(f"followers_level_{m.group(1)}")
            else:
                level_keys.append(f"followers_col_{i}")

        rows: list[dict[str, str]] = []
        for row in matrix[level_header_idx + 1 :]:
            cells = [normalize_ws(x) for x in row]
            if not any(cells):
                continue
            if _contains_any(cells[0], ["表：领导力", "Table: Leadership", "领导力值", "Leadership Score"]):
                continue
            if len(cells) < 2:
                continue
            item = {
                "leadership_score": cells[0],
                "cohort_level": cells[1],
            }
            for idx, key in enumerate(level_keys, start=2):
                item[key] = cells[idx] if idx < len(cells) else ""
            if item["leadership_score"] and item["cohort_level"]:
                rows.append(item)

        if rows:
            out.append(
                {
                    "table_type": "leadership_score",
                    "table_title": "领导力值（Leadership Score）",
                    "source_page": _make_table_source(local, toc_path, ti),
                    "columns": ["leadership_score", "cohort_level", *level_keys],
                    "level_headers": level_headers,
                    "rows": rows,
                }
            )

    return out


def _classify_leadership_modifier_section(probe_text: str) -> tuple[str, str]:
    if _contains_any(probe_text, ["领导者声誉", "leader reputation", "reputation"]):
        return "leader_reputation", "领导者声誉（Leader Reputation）"
    if _contains_any(probe_text, ["追随者", "follower"]):
        return "follower_modifiers", "追随者修正（Follower Modifiers）"
    if _contains_any(probe_text, ["部属", "cohort"]):
        return "cohort_modifiers", "部属修正（Cohort Modifiers）"
    return "", ""


def extract_leadership_modifiers_table(html: str, local: str, toc_path: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    sections: list[dict[str, Any]] = []
    source_pages: list[dict[str, Any]] = []
    all_notes: list[str] = []

    for ti, table in enumerate(soup.find_all("table")):
        matrix = parse_table_matrix(table)
        if not matrix:
            continue
        if len(matrix[0]) < 3 or len(matrix[0]) > 5:
            continue

        probe_text = " ".join(" ".join(r) for r in matrix[:6] if r)
        if not _contains_any(probe_text, ["修正值", "modifier"]):
            continue
        if not _contains_any(probe_text, ["领导者", "leader"]):
            continue

        section_key, section_title = _classify_leadership_modifier_section(probe_text)
        if not section_key:
            continue

        rows: list[dict[str, str]] = []
        notes: list[str] = []
        for row in matrix[1:]:
            cells = [normalize_ws(x) for x in row]
            if len(cells) < 3:
                continue
            factor = cells[0]
            modifier = cells[2]
            if not factor:
                continue
            if factor.startswith("*"):
                notes.append(factor)
                continue
            if _contains_any(factor, ["领导者声誉", "领导者", "leader"]) and _contains_any(modifier, ["修正值", "modifier"]):
                continue
            if not modifier:
                continue
            rows.append({"factor": factor, "modifier": modifier})

        if not rows and not notes:
            continue

        sections.append(
            {
                "section_key": section_key,
                "section_title": section_title,
                "rows": rows,
                "notes": notes,
            }
        )
        source_pages.append(_make_table_source(local, toc_path, ti))
        all_notes.extend(notes)

    if not sections:
        return []

    flat_rows: list[dict[str, str]] = []
    for section in sections:
        for row in section["rows"]:
            flat_rows.append(
                {
                    "category": section["section_key"],
                    "factor": row["factor"],
                    "modifier": row["modifier"],
                }
            )

    return [
        {
            "table_type": "leadership_modifiers",
            "table_title": "领导力修正（Leadership Modifiers）",
            "source_pages": source_pages,
            "columns": ["category", "factor", "modifier"],
            "rows": flat_rows,
            "sections": sections,
            "notes": all_notes,
        }
    ]


def extract_leadership_monster_cohorts_table(html: str, local: str, toc_path: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    out: list[dict[str, Any]] = []

    for ti, table in enumerate(soup.find_all("table")):
        matrix = parse_table_matrix(table)
        if not matrix:
            continue

        probe_text = " ".join(" ".join(r) for r in matrix[:4] if r)
        if ("怪物部属" not in probe_text) and ("Monster Cohorts" not in probe_text):
            continue

        header_idx = None
        for i, row in enumerate(matrix[:8]):
            cells = [normalize_ws(x) for x in row]
            if len(cells) < 4:
                continue
            if cells[0] == "怪物部属" and cells[1] == "阵营" and cells[2] == "等级" and cells[3] == "出处":
                header_idx = i
                break
        if header_idx is None:
            continue

        entries: list[dict[str, str]] = []
        note_lines: list[str] = []
        for row in matrix[header_idx + 1 :]:
            cells = [normalize_ws(x) for x in row if normalize_ws(x)]
            if not cells:
                continue
            if cells[0].startswith("*"):
                note_lines.append(cells[0])
                continue
            if len(cells) < 4:
                continue

            blocks = []
            if len(cells) >= 4:
                blocks.append(cells[:4])
            if len(cells) >= 8:
                blocks.append(cells[4:8])
            for b in blocks:
                if len(b) < 4:
                    continue
                name, alignment, level, source = b[0], b[1], b[2], b[3]
                if not name or name == "怪物部属":
                    continue
                entries.append(
                    {
                        "name": name,
                        "alignment": alignment,
                        "level": level,
                        "source": source,
                    }
                )

        if entries:
            out.append(
                {
                    "table_type": "leadership_monster_cohorts",
                    "table_title": "怪物部属（Monster Cohorts）",
                    "source_page": _make_table_source(local, toc_path, ti),
                    "columns": ["name", "alignment", "level", "source"],
                    "rows": entries,
                    "notes": note_lines,
                }
            )

    return out


def collect_pages_under_node(node: dict[str, Any]) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []

    def walk(cur: dict[str, Any], path_parts: list[str]) -> None:
        title = normalize_ws(str(cur.get("title", "")))
        path_next = path_parts + ([title] if title else [])
        local = normalize_ws(str(cur.get("local", "")))
        if local:
            out.append((local, " / ".join(path_next)))
        for child in cur.get("children", []) or []:
            walk(child, path_next)

    walk(node, [])
    seen = set()
    uniq: list[tuple[str, str]] = []
    for local, path_text in out:
        k = local.lower()
        if k in seen:
            continue
        seen.add(k)
        uniq.append((local, path_text))
    return uniq


def main() -> None:
    parser = argparse.ArgumentParser(description="Build per-book feat extraction report.")
    parser.add_argument("--toc", type=Path, default=TOC_PATH)
    parser.add_argument("--viewer", type=Path, default=VIEWER_PATH)
    parser.add_argument("--out-dir", type=Path, default=OUT_DIR)
    args = parser.parse_args()

    toc = load_toc(args.toc)
    feats_root = find_top_level_node(toc, "专长")
    pages = load_embedded_pages(args.viewer)
    page_idx = {unquote(k).lower(): k for k in pages.keys()}

    books = feats_root.get("children", []) or []
    report_books: list[dict[str, Any]] = []
    all_book_feats: dict[str, list[dict[str, Any]]] = {}

    for book_node in books:
        book_name = normalize_ws(str(book_node.get("title", "")))
        page_pairs = collect_pages_under_node(book_node)

        rows = []
        attached_tables: list[dict[str, Any]] = []
        missing_pages: list[str] = []
        found_pages: list[str] = []

        for local, toc_path in page_pairs:
            key = page_idx.get(unquote(local).lower())
            if not key:
                missing_pages.append(local)
                continue
            found_pages.append(local)
            page_html = pages[key]
            rows.extend(extract_feats_from_page(page_html, local, toc_path))
            attached_tables.extend(extract_leadership_score_table(page_html, local, toc_path))
            attached_tables.extend(extract_leadership_modifiers_table(page_html, local, toc_path))
            attached_tables.extend(extract_leadership_monster_cohorts_table(page_html, local, toc_path))

        attached_tables = _dedup_attached_tables(attached_tables)

        dedup = {}
        for row in rows:
            name_en = canonicalize_en_name(row.name_en) if row.name_en else ""
            k = normalize_key(name_en or row.name_raw)
            if not k:
                continue
            if k not in dedup:
                dedup[k] = {
                    "match_key": k,
                    "name_en": name_en,
                    "name_cn": row.name_cn,
                    "name_raw": row.name_raw,
                    "prerequisites": row.prerequisites or "",
                    "benefit_summary": row.benefit_summary or "",
                    "detail_text": row.detail_text or "",
                    "source_pages": [],
                }
            if not dedup[k].get("prerequisites") and row.prerequisites:
                dedup[k]["prerequisites"] = row.prerequisites
            if not dedup[k].get("benefit_summary") and row.benefit_summary:
                dedup[k]["benefit_summary"] = row.benefit_summary
            if not dedup[k].get("detail_text") and row.detail_text:
                dedup[k]["detail_text"] = row.detail_text
            dedup[k]["source_pages"].append(
                {
                    "local": row.source_local,
                    "toc_path": row.source_path,
                    "table_index": row.table_index,
                    "row_index": row.row_index,
                }
            )

        if attached_tables:
            leadership_key = normalize_key("Leadership")
            if leadership_key in dedup:
                dedup[leadership_key]["attached_tables"] = attached_tables

        feat_list = sorted(dedup.values(), key=lambda x: (x.get("name_en") or x.get("name_raw") or ""))
        all_book_feats[book_name] = feat_list

        status = "ok" if len(feat_list) > 0 else "needs_review"
        report_books.append(
            {
                "book": book_name,
                "status": status,
                "page_total": len(page_pairs),
                "page_found": len(found_pages),
                "page_missing": len(missing_pages),
                "feat_unique": len(feat_list),
                "feat_rows": len(rows),
                "missing_pages": missing_pages,
                "sample_feats": [x.get("name_en") or x.get("name_raw") for x in feat_list[:12]],
            }
        )

    report_books.sort(key=lambda x: (x["feat_unique"], x["book"]), reverse=True)

    summary = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "book_count": len(report_books),
        "books_with_feats": sum(1 for x in report_books if x["feat_unique"] > 0),
        "books_zero_feats": sum(1 for x in report_books if x["feat_unique"] == 0),
    }

    args.out_dir.mkdir(parents=True, exist_ok=True)
    (args.out_dir / "feat-book-extraction-report.json").write_text(
        json.dumps({"summary": summary, "books": report_books}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (args.out_dir / "feat-book-feats.json").write_text(
        json.dumps(all_book_feats, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print("Done.")
    print(summary)
    for item in report_books:
        print(
            f"{item['book']}: feats={item['feat_unique']} pages={item['page_found']}/{item['page_total']} status={item['status']}"
        )


if __name__ == "__main__":
    main()