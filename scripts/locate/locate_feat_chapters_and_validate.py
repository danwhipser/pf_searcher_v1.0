from __future__ import annotations

# Allow direct execution from nested scripts/ folders.
import sys
from pathlib import Path
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import argparse
import json
from typing import Any
from urllib.parse import unquote


ROOT = Path(__file__).resolve().parents[2]
TOC_PATH = ROOT / "result" / "toc.json"
BOOK_FEATS_PATH = ROOT / "result" / "feats" / "feat-book-feats.json"
OUT_PATH = ROOT / "result" / "feats" / "feat-chapter-locations-and-validation.json"

AGGREGATE_CHAPTERS = {
    "专长概述",
    "全专长列表",
    "超魔专长一览",
    "流派专长一览",
    "造物专长一览",
    "团队背叛专长",
    "皇庭英豪",
}


def normalize_ws(text: Any) -> str:
    return " ".join(str(text or "").split()).strip()


def normalize_local(local: str) -> str:
    return unquote(normalize_ws(local)).replace("\\", "/").lower()


def load_toc(path: Path) -> list[dict[str, Any]]:
    return json.loads(path.read_text(encoding="utf-8"))


def find_top_level_node(nodes: list[dict[str, Any]], title: str) -> dict[str, Any]:
    for node in nodes:
        if normalize_ws(node.get("title", "")) == title:
            return node
    raise ValueError(f"Top-level TOC node not found: {title}")


def collect_local_pages(node: dict[str, Any]) -> list[tuple[str, str]]:
    """
    Return unique list of (local, toc_path).
    """
    out: list[tuple[str, str]] = []

    def walk(cur: dict[str, Any], path_parts: list[str]) -> None:
        title = normalize_ws(cur.get("title", ""))
        next_path = path_parts + ([title] if title else [])
        local = normalize_ws(cur.get("local", ""))
        if local:
            out.append((local, " / ".join(next_path)))
        for child in cur.get("children", []) or []:
            walk(child, next_path)

    walk(node, [])

    seen: set[str] = set()
    uniq: list[tuple[str, str]] = []
    for local, path_text in out:
        key = normalize_local(local)
        if key in seen:
            continue
        seen.add(key)
        uniq.append((local, path_text))
    return uniq


def locate_chapters(toc: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, str]]:
    feats_root = find_top_level_node(toc, "专长")
    chapters = feats_root.get("children", []) or []

    result: list[dict[str, Any]] = []
    page_to_chapter: dict[str, str] = {}
    for node in chapters:
        chapter_title = normalize_ws(node.get("title", ""))
        chapter_local = normalize_ws(node.get("local", ""))
        chapter_type = "aggregate" if chapter_title in AGGREGATE_CHAPTERS else "book_chapter"
        pages = collect_local_pages(node)
        page_items = [{"local": local, "toc_path": toc_path} for local, toc_path in pages]
        for local, _ in pages:
            page_to_chapter[normalize_local(local)] = chapter_title
        result.append(
            {
                "chapter": chapter_title,
                "chapter_local": chapter_local,
                "chapter_type": chapter_type,
                "page_count": len(page_items),
                "pages": page_items,
            }
        )
    return result, page_to_chapter


def validate_feat_positions(
    book_feats: dict[str, list[dict[str, Any]]],
    chapter_manifest: list[dict[str, Any]],
) -> dict[str, Any]:
    chapter_by_name = {item["chapter"]: item for item in chapter_manifest}
    report_books: list[dict[str, Any]] = []

    for book_name, rows in book_feats.items():
        chapter = chapter_by_name.get(book_name)
        chapter_pages = {
            normalize_local(p["local"])
            for p in (chapter.get("pages", []) if chapter else [])
            if normalize_ws(p.get("local", ""))
        }

        outside_rows: list[dict[str, Any]] = []
        no_source_rows: list[dict[str, Any]] = []
        matched_rows = 0
        has_chapter = chapter is not None

        for row in rows:
            name = (
                normalize_ws(row.get("name_cn"))
                or normalize_ws(row.get("name_en"))
                or normalize_ws(row.get("name_raw"))
                or normalize_ws(row.get("match_key"))
            )
            source_pages = row.get("source_pages", []) or []
            if not source_pages:
                no_source_rows.append({"name": name})
                continue

            in_chapter = False
            page_refs: list[dict[str, str]] = []
            for sp in source_pages:
                local = normalize_ws(sp.get("local", ""))
                toc_path = normalize_ws(sp.get("toc_path", ""))
                key = normalize_local(local)
                page_refs.append({"local": local, "toc_path": toc_path})
                if key in chapter_pages:
                    in_chapter = True
            if in_chapter:
                matched_rows += 1
            else:
                outside_rows.append({"name": name, "source_pages": page_refs})

        total_rows = len(rows)
        report_books.append(
            {
                "book": book_name,
                "has_feat_chapter_in_toc": has_chapter,
                "chapter_local": chapter.get("chapter_local", "") if chapter else "",
                "chapter_page_count": len(chapter_pages),
                "total_feats": total_rows,
                "matched_in_chapter": matched_rows,
                "outside_chapter": len(outside_rows),
                "no_source_pages": len(no_source_rows),
                "outside_chapter_samples": outside_rows[:20],
                "no_source_samples": no_source_rows[:20],
            }
        )

    report_books.sort(key=lambda x: x["book"])
    return {"books": report_books}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Locate dedicated feat chapters in TOC and validate feat source positions."
    )
    parser.add_argument("--toc", type=Path, default=TOC_PATH)
    parser.add_argument("--book-feats", type=Path, default=BOOK_FEATS_PATH)
    parser.add_argument("--output", type=Path, default=OUT_PATH)
    args = parser.parse_args()

    toc = load_toc(args.toc)
    chapter_manifest, _ = locate_chapters(toc)
    book_feats = json.loads(args.book_feats.read_text(encoding="utf-8"))
    if not isinstance(book_feats, dict):
        raise ValueError("feat-book-feats.json top-level must be an object")

    validation = validate_feat_positions(book_feats, chapter_manifest)
    payload = {
        "source_toc": str(args.toc),
        "source_book_feats": str(args.book_feats),
        "chapters": chapter_manifest,
        "validation": validation,
    }
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    books = validation["books"]
    total_books = len(books)
    missing_chapter_books = sum(1 for b in books if not b["has_feat_chapter_in_toc"])
    outside_books = sum(1 for b in books if b["outside_chapter"] > 0)
    print(f"chapters: {len(chapter_manifest)}")
    print(f"books: {total_books}")
    print(f"books_missing_chapter: {missing_chapter_books}")
    print(f"books_with_outside_rows: {outside_books}")
    print(f"report: {args.output}")


if __name__ == "__main__":
    main()