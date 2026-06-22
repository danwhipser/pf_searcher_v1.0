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
from typing import Any

from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[2]

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.extract.extract_feats_and_verify import load_embedded_pages, normalize_key, normalize_ws

IN_BOOK_FEATS = ROOT / "result" / "feats" / "feat-book-feats.json"
IN_VIEWER = ROOT / "result" / "Pathfinder-v2.14-SC-viewer-embedded.html"
OUT_BOOK_FEATS = ROOT / "result" / "feats" / "feat-book-feats-book-specific.json"


def _lines_from_html(html: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    return [normalize_ws(x) for x in soup.get_text("\n").splitlines() if normalize_ws(x)]


def _parse_ma_page_623(lines: list[str]) -> dict[str, dict[str, str]]:
    """Parse MA summary table rows where title is 'EN (Mythic/Metamagic)' on a standalone line."""
    out: dict[str, dict[str, str]] = {}

    en_title_idxs: list[int] = []
    skip_tokens = {
        "CRB",
        "APG",
        "UM",
        "UC",
        "ACG",
        "Feat",
        "Feats",
        "Type",
        "Name",
    }

    for i, line in enumerate(lines):
        if not re.search(r"[A-Za-z]", line):
            continue
        if len(line) > 80:
            continue
        # Skip URLs/header noise.
        if line.startswith("http"):
            continue
        if line in skip_tokens:
            continue
        has_paren_title = "(" in line and ")" in line and " " in line
        is_plain_en_title = bool(re.fullmatch(r"[A-Za-z][A-Za-z0-9'`\- ]{4,}", line)) and (" " in line)
        if not has_paren_title and not is_plain_en_title:
            continue
        en_title_idxs.append(i)

    def _tail_kind(text: str) -> str:
        t = normalize_ws(text)
        if not t:
            return "empty"
        if t in {"专长", "神话专长", "神话增强专长"}:
            return "section_header"
        if "（神话）" in t or "(Mythic)" in t:
            return "mythic_title"
        # Short pure-CN chunks without punctuation are often next-entry titles.
        if re.fullmatch(r"[\u4e00-\u9fff]+", t) and len(t) <= 6:
            return "short_cn"
        return "other"

    for pos, idx in enumerate(en_title_idxs):
        line = lines[idx]
        en_name = normalize_ws(line.split("(", 1)[0])
        k = normalize_key(en_name)
        if not k:
            continue

        next_idx = en_title_idxs[pos + 1] if pos + 1 < len(en_title_idxs) else len(lines)
        block = [x for x in lines[idx + 1 : next_idx] if x]
        if not block:
            continue

        # Trim a trailing next-entry CN title or section header leaked by line flattening.
        popped_short_cn = False
        popped_any_tail = False
        while block:
            tail = block[-1]
            kind = _tail_kind(tail)
            if kind in {"empty", "section_header", "mythic_title"}:
                block.pop()
                popped_any_tail = True
                continue
            if kind == "short_cn":
                # Pop at most one trailing short-CN token; the previous one can be real benefit text.
                if popped_any_tail:
                    break
                if popped_short_cn:
                    break
                block.pop()
                popped_short_cn = True
                popped_any_tail = True
                continue
            break
        if not block:
            continue

        cn_name = lines[idx - 1] if idx - 1 >= 0 else ""
        prereq = normalize_ws(" ".join(block[:-1])) if len(block) >= 2 else ""
        benefit = block[-1]

        out[k] = {
            "name_en": en_name,
            "name_cn": cn_name,
            "prereq": prereq,
            "benefit": benefit,
            "detail": benefit,
        }

    return out


def _parse_hotw_page_694(lines: list[str]) -> list[dict[str, str]]:
    """Parse Heroes of the Wild compact feat detail layout (CN title + detail + 前置条件 + 效果)."""
    blocks: list[dict[str, str]] = []
    i = 0
    n = len(lines)

    while i < n:
        line = lines[i]
        if line.startswith("前置条件") or line.startswith("效果"):
            i += 1
            continue

        # Need at least three following lines to form one block.
        if i + 3 >= n:
            break
        detail = lines[i + 1]
        pre_line = lines[i + 2]
        ben_line = lines[i + 3]

        if (not pre_line.startswith("前置条件")) or (not ben_line.startswith("效果")):
            i += 1
            continue

        prereq = normalize_ws(re.sub(r"^前置条件\s*[：:]\s*", "", pre_line))
        benefit = normalize_ws(re.sub(r"^效果\s*[：:]\s*", "", ben_line))

        # Optional trailing “正常：...” line belongs to current block detail.
        extra = ""
        if i + 4 < n and lines[i + 4].startswith("正常"):
            extra = lines[i + 4]

        full_detail = detail if not extra else f"{detail} {extra}"
        blocks.append(
            {
                "name_cn": line,
                "detail": normalize_ws(full_detail),
                "prereq": prereq,
                "benefit": benefit,
            }
        )

        i = i + 5 if extra else i + 4

    return blocks


def _parse_page_624_split_en_blocks(lines: list[str]) -> dict[str, dict[str, str]]:
    """
    Parse page_624 cases where EN title is split across two lines:
    e.g. 'Valiant' + 'Vault' + '(Mythic)'.
    """
    out: dict[str, dict[str, str]] = {}
    n = len(lines)

    def _is_en_token(s: str) -> bool:
        return bool(re.fullmatch(r"[A-Za-z][A-Za-z'`-]{1,24}", s))

    for i in range(0, n - 6):
        a = lines[i]
        b = lines[i + 1]
        c = lines[i + 2]
        if not (_is_en_token(a) and _is_en_token(b)):
            continue
        if not (c.startswith("(") and ")" in c):
            continue

        en_name = f"{a} {b}"
        key = normalize_key(en_name)
        if not key:
            continue

        detail_start = i + 3
        pr_idx = -1
        for j in range(detail_start, min(n, detail_start + 20)):
            if lines[j] in {"先决条件", "前置条件"}:
                pr_idx = j
                break
        if pr_idx < 0:
            continue

        be_idx = -1
        for j in range(pr_idx + 1, min(n, pr_idx + 24)):
            if lines[j] in {"好处", "专长效果", "效果"}:
                be_idx = j
                break
        if be_idx < 0:
            continue

        detail = normalize_ws(" ".join(lines[detail_start:pr_idx]))
        prereq = normalize_ws(" ".join(lines[pr_idx + 1 : be_idx]))
        benefit = normalize_ws(" ".join(lines[be_idx + 1 : min(n, be_idx + 8)]))
        # Trim leaked next entry title from benefit tail.
        benefit = re.sub(r"\s+[\u4e00-\u9fff]{2,10}（神话）\s*$", "", benefit)

        out[key] = {
            "name_en": en_name,
            "name_cn": lines[i - 1] if i - 1 >= 0 else "",
            "detail": detail,
            "prereq": prereq,
            "benefit": benefit,
        }

    return out


def _match_label(line: str, labels: list[str]) -> tuple[bool, str]:
    s = normalize_ws(line)
    if not s:
        return False, ""
    sl = s.lower()
    for lb in labels:
        ll = lb.lower()
        if sl == ll:
            return True, ""
        if sl.startswith(ll):
            rest = normalize_ws(s[len(lb) :])
            rest = re.sub(r"^[：:\s]+", "", rest)
            return True, rest
    return False, ""


def _parse_uc_page_651(lines: list[str]) -> dict[str, dict[str, str]]:
    """
    Parse UC long-form feat blocks from page_651 (Combat Stamina appendix style).
    """
    prereq_labels = ["先决条件", "前置条件", "鍏堝喅鏉′欢", "鍓嶇疆鏉′欢"]
    benefit_labels = ["专长效果", "效果", "好处", "涓撻暱鏁堟灉", "鏁堟灉"]
    stop_labels = {"通常情况", "通常状况", "战策", "特殊说明", "特别说明", "先决条件", "前置条件"}

    def _is_cn_short_title(s: str) -> bool:
        t = normalize_ws(s)
        if not t or len(t) > 28:
            return False
        if not re.fullmatch(r"[\u4e00-\u9fff·]+", t):
            return False
        return True

    def _parse_title(i: int) -> tuple[int, str, str, str]:
        # Return (end_idx, key, en_name, cn_name)
        if i >= len(lines):
            return -1, "", "", ""
        cur = normalize_ws(lines[i])
        if not cur:
            return -1, "", "", ""

        # Case A: CN(EN) possibly split across up to 3 lines.
        if re.search(r"[\u4e00-\u9fff]", cur) and ("（" in cur or "(" in cur):
            combo = " ".join(normalize_ws(lines[j]) for j in range(i, min(i + 4, len(lines))))
            m = re.search(
                r"([\u4e00-\u9fff·]{2,28})\s*[（(]\s*([A-Za-z][A-Za-z0-9'`/&,+\-\s]{2,100}?)\s*[）)]",
                combo,
            )
            if m:
                cn = normalize_ws(m.group(1))
                en = normalize_ws(m.group(2))
                k = normalize_key(en)
                if k:
                    end_idx = i
                    for j in range(i, min(i + 4, len(lines))):
                        if "）" in lines[j] or ")" in lines[j]:
                            end_idx = j
                            break
                    return end_idx, k, en, cn

        # Case B: CN title line + EN line.
        if _is_cn_short_title(cur) and i + 1 < len(lines):
            nxt = normalize_ws(lines[i + 1])
            m = re.match(r"([A-Za-z][A-Za-z0-9'`/&,+\-\s]{2,100})", nxt)
            if m:
                en = normalize_ws(m.group(1))
                k = normalize_key(en)
                if k:
                    return i + 1, k, en, cur

        return -1, "", "", ""

    starts: list[tuple[int, int, str, str, str]] = []
    i = 0
    while i < len(lines):
        end_i, key, en, cn = _parse_title(i)
        if key:
            starts.append((i, end_i, key, en, cn))
            i = end_i + 1
            continue
        i += 1

    blocks: dict[str, dict[str, str]] = {}
    for idx, (st, ed, key, en, cn) in enumerate(starts):
        nxt = starts[idx + 1][0] if idx + 1 < len(starts) else len(lines)
        body = lines[ed + 1 : nxt]
        if not body:
            continue

        first_label = -1
        pr_idx = -1
        be_idx = -1
        pr_inline = ""
        be_inline = ""
        for j, line in enumerate(body):
            is_pr, rest_pr = _match_label(line, prereq_labels)
            is_be, rest_be = _match_label(line, benefit_labels)
            if is_pr and pr_idx < 0:
                pr_idx = j
                pr_inline = rest_pr
                if first_label < 0:
                    first_label = j
            if is_be and be_idx < 0:
                be_idx = j
                be_inline = rest_be
                if first_label < 0:
                    first_label = j
            if pr_idx >= 0 and be_idx >= 0:
                break
        if be_idx < 0:
            continue

        flavor = normalize_ws(" ".join(body[: first_label if first_label >= 0 else be_idx]))

        prereq = ""
        if pr_idx >= 0 and pr_idx < be_idx:
            parts = []
            if pr_inline:
                parts.append(pr_inline)
            parts.extend(body[pr_idx + 1 : be_idx])
            prereq = normalize_ws(" ".join(parts))

        ben_parts = []
        if be_inline:
            ben_parts.append(be_inline)
        for line in body[be_idx + 1 :]:
            t = normalize_ws(line)
            if not t:
                continue
            if t in stop_labels:
                break
            is_pr2, _ = _match_label(t, prereq_labels)
            is_be2, _ = _match_label(t, benefit_labels)
            if is_pr2 or is_be2:
                break
            ben_parts.append(t)
        benefit = normalize_ws(" ".join(ben_parts))
        if len(benefit) < 24:
            continue

        old = blocks.get(key)
        cand = {"name_en": en, "name_cn": cn, "flavor": flavor, "prereq": prereq, "benefit": benefit}
        if old is None:
            blocks[key] = cand
        else:
            old_score = len(old.get("benefit", "")) + len(old.get("prereq", ""))
            new_score = len(cand.get("benefit", "")) + len(cand.get("prereq", ""))
            if new_score > old_score:
                blocks[key] = cand
    return blocks


def _has_local(row: dict[str, Any], local: str) -> bool:
    for sp in row.get("source_pages") or []:
        if sp.get("local") == local:
            return True
    return False


def main() -> None:
    parser = argparse.ArgumentParser(description="Book-specific CHM feat detail backfill.")
    parser.add_argument("--book-feats", type=Path, default=IN_BOOK_FEATS)
    parser.add_argument("--viewer", type=Path, default=IN_VIEWER)
    parser.add_argument("--output", type=Path, default=OUT_BOOK_FEATS)
    parser.add_argument("--inplace", action="store_true")
    parser.add_argument(
        "--force-page623",
        action="store_true",
        help="Force overwrite prereq/benefit/detail for rows sourced from page_623.html.",
    )
    parser.add_argument(
        "--force-page694",
        action="store_true",
        help="Force overwrite prereq/benefit/detail for rows sourced from page_694.html.",
    )
    args = parser.parse_args()

    data: dict[str, list[dict[str, Any]]] = json.loads(args.book_feats.read_text(encoding="utf-8"))
    pages = load_embedded_pages(args.viewer)

    # MA page_623 mapping by English key.
    ma_map: dict[str, dict[str, str]] = {}
    if "page_623.html" in pages:
        ma_map = _parse_ma_page_623(_lines_from_html(pages["page_623.html"]))

    # Heroes of the Wild page_694 blocks by row order.
    hotw_blocks: list[dict[str, str]] = []
    if "page_694.html" in pages:
        hotw_blocks = _parse_hotw_page_694(_lines_from_html(pages["page_694.html"]))
    split624_map: dict[str, dict[str, str]] = {}
    if "page_624.html" in pages:
        split624_map = _parse_page_624_split_en_blocks(_lines_from_html(pages["page_624.html"]))
    uc651_map: dict[str, dict[str, str]] = {}
    if "page_651.html" in pages:
        uc651_map = _parse_uc_page_651(_lines_from_html(pages["page_651.html"]))

    updated_rows = 0
    by_book: dict[str, int] = {}

    # Pass 1: MA by key match.
    for book, rows in data.items():
        for row in rows:
            if not _has_local(row, "page_623.html"):
                continue
            k = normalize_ws(row.get("match_key", ""))
            info = ma_map.get(k)
            if not k or not info:
                continue

            changed = False
            if not normalize_ws(row.get("name_cn", "")) and normalize_ws(info.get("name_cn", "")):
                row["name_cn"] = info["name_cn"]
                changed = True
            if (args.force_page623 or (not normalize_ws(row.get("prerequisites", "")))) and normalize_ws(info.get("prereq", "")):
                row["prerequisites"] = info["prereq"]
                changed = True
            if (args.force_page623 or (not normalize_ws(row.get("benefit_summary", "")))) and normalize_ws(info.get("benefit", "")):
                row["benefit_summary"] = info["benefit"]
                changed = True
            if (args.force_page623 or (not normalize_ws(row.get("detail_text", "")))) and normalize_ws(info.get("detail", "")):
                row["detail_text"] = info["detail"]
                changed = True

            if changed:
                sp = row.get("source_pages") or []
                sp.append(
                    {
                        "local": "page_623.html",
                        "toc_path": "book_specific_page623_summary",
                        "table_index": -96,
                        "row_index": -1,
                    }
                )
                row["source_pages"] = sp
                updated_rows += 1
                by_book[book] = by_book.get(book, 0) + 1

    # Pass 2: Heroes of the Wild by page row order.
    if hotw_blocks:
        for book, rows in data.items():
            target_rows = [r for r in rows if _has_local(r, "page_694.html")]
            if not target_rows:
                continue

            def _row_idx(rr: dict[str, Any]) -> int:
                idxs = [sp.get("row_index", 10**9) for sp in (rr.get("source_pages") or []) if sp.get("local") == "page_694.html"]
                return min(idxs) if idxs else 10**9

            target_rows.sort(key=_row_idx)
            for i, row in enumerate(target_rows):
                if i >= len(hotw_blocks):
                    break
                blk = hotw_blocks[i]

                changed = False
                if not normalize_ws(row.get("name_cn", "")) and normalize_ws(blk.get("name_cn", "")):
                    row["name_cn"] = blk["name_cn"]
                    changed = True
                if (args.force_page694 or (not normalize_ws(row.get("prerequisites", "")))) and normalize_ws(blk.get("prereq", "")):
                    row["prerequisites"] = blk["prereq"]
                    changed = True
                if (args.force_page694 or (not normalize_ws(row.get("benefit_summary", "")))) and normalize_ws(blk.get("benefit", "")):
                    row["benefit_summary"] = blk["benefit"]
                    changed = True
                if (args.force_page694 or (not normalize_ws(row.get("detail_text", "")))) and normalize_ws(blk.get("detail", "")):
                    row["detail_text"] = blk["detail"]
                    changed = True

                if changed:
                    sp = row.get("source_pages") or []
                    sp.append(
                        {
                            "local": "page_694.html",
                            "toc_path": "book_specific_page694_compact_blocks",
                            "table_index": -97,
                            "row_index": -1,
                        }
                    )
                    row["source_pages"] = sp
                    updated_rows += 1
                    by_book[book] = by_book.get(book, 0) + 1

    # Pass 3: page_624 split EN title blocks.
    for book, rows in data.items():
        for row in rows:
            if not _has_local(row, "page_624.html"):
                continue
            k = normalize_ws(row.get("match_key", ""))
            info = split624_map.get(k)
            if not k or not info:
                continue

            changed = False
            if not normalize_ws(row.get("name_cn", "")) and normalize_ws(info.get("name_cn", "")):
                row["name_cn"] = info["name_cn"]
                changed = True
            if not normalize_ws(row.get("prerequisites", "")) and normalize_ws(info.get("prereq", "")):
                row["prerequisites"] = info["prereq"]
                changed = True
            if not normalize_ws(row.get("benefit_summary", "")) and normalize_ws(info.get("benefit", "")):
                row["benefit_summary"] = info["benefit"]
                changed = True
            if not normalize_ws(row.get("detail_text", "")) and normalize_ws(info.get("detail", "")):
                row["detail_text"] = info["detail"]
                changed = True

            if changed:
                sp = row.get("source_pages") or []
                sp.append(
                    {
                        "local": "page_624.html",
                        "toc_path": "book_specific_page624_split_en_title",
                        "table_index": -93,
                        "row_index": -1,
                    }
                )
                row["source_pages"] = sp
                updated_rows += 1
                by_book[book] = by_book.get(book, 0) + 1

    # Pass 4: UC long-form from page_651.
    for book, rows in data.items():
        if not book.startswith("UC "):
            continue
        for row in rows:
            key = normalize_ws(row.get("match_key", ""))
            if not key:
                continue
            old_detail = normalize_ws(row.get("detail_text", ""))
            if old_detail and len(old_detail) > 20:
                continue
            blk = uc651_map.get(key)
            if not blk:
                continue

            changed = False
            new_pr = normalize_ws(blk.get("prereq", ""))
            new_ben = normalize_ws(blk.get("benefit", ""))
            if new_pr and len(new_pr) > len(normalize_ws(row.get("prerequisites", ""))):
                row["prerequisites"] = new_pr
                changed = True
            if new_ben and len(new_ben) > len(old_detail):
                row["detail_text"] = new_ben
                changed = True
            if (not normalize_ws(row.get("flavor_text", ""))) and normalize_ws(blk.get("flavor", "")):
                row["flavor_text"] = normalize_ws(blk.get("flavor", ""))
                changed = True
            if changed:
                sp = row.get("source_pages") or []
                sp.append(
                    {
                        "local": "page_651.html",
                        "toc_path": "book_specific_uc_page651_longform",
                        "table_index": -98,
                        "row_index": -1,
                    }
                )
                row["source_pages"] = sp
                updated_rows += 1
                by_book[book] = by_book.get(book, 0) + 1

    out_path = args.book_feats if args.inplace else args.output
    out_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    print("Done.")
    print(f"MA page_623 parsed keys: {len(ma_map)}")
    print(f"Heroes page_694 parsed blocks: {len(hotw_blocks)}")
    print(f"Split EN page_624 parsed keys: {len(split624_map)}")
    print(f"UC page_651 parsed keys: {len(uc651_map)}")
    print(f"Updated rows: {updated_rows}")
    for b, c in sorted(by_book.items(), key=lambda x: (-x[1], x[0])):
        print(f"{b}: +{c}")
    print(f"Output: {out_path}")


if __name__ == "__main__":
    main()