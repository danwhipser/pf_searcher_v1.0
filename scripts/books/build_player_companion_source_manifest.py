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


ROOT = Path(__file__).resolve().parents[2]
TOC_PATH = ROOT / "result" / "toc.json"
OUT_JSON = ROOT / "result" / "player-companion-source-manifest.json"
OUT_MD = ROOT / "result" / "player-companion-source-manifest.md"


# Codes that are ambiguous in the CHM TOC or collide with an already loaded
# non-player-companion source. The key is (raw code, local file).
SOURCE_CODE_OVERRIDES = {
    ("MTT", "page_1169.html"): "MTT_2",  # Magic Tactics Toolbox
    ("DH", "page_1154.html"): "DH_1",  # Dragonslayer's Handbook
    ("DH", "page_1151.html"): "DH_2",  # Dungeoneer's Handbook
    ("P&P", "P&P-药剂与毒药.htm"): "PNP",  # Potions & Poisons
    ("POTR", "page_1010.html"): "POTR_1",  # People of the River
    ("POTR", "page_1171.html"): "POTR_2",  # Paths of the Righteous
    ("PA", "page_1173.html"): "PA_1",  # Psychic Anthology; PA is Planar Adventures
}


def walk(nodes, path=()):
    for node in nodes:
        next_path = path + (node.get("title", ""),)
        yield next_path, node
        yield from walk(node.get("children") or [], next_path)


def load_player_companion_children():
    data = json.loads(TOC_PATH.read_text(encoding="utf-8"))
    for path, node in walk(data):
        if len(path) >= 2 and path[-2:] == ("法术", "玩家伴侣"):
            return node.get("children") or []
    # The current toc has a stable layout; keep a fallback for robustness.
    return data[3]["children"][17]["children"]


def normalize_code(code: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "", code).upper()


def parse_title(title: str) -> tuple[str, str]:
    if "-" not in title:
        return normalize_code(title), title
    raw_code, name = title.split("-", 1)
    return raw_code.strip(), name.strip()


def existing_model_sources() -> set[str]:
    sources = set()
    for path in (ROOT / "result").glob("*/spells-*-model.json"):
        if path.parent.name.lower() == "index":
            continue
        sources.add(path.parent.name.upper())
    return sources


def build_manifest() -> dict:
    existing_sources = existing_model_sources()
    children = load_player_companion_children()
    entries = []

    for index, item in enumerate(children, start=1):
        title = item.get("title", "")
        local = item.get("local", "")
        if index == 1:
            entries.append(
                {
                    "order": index,
                    "source_code": "PCINDEX",
                    "display_code": "",
                    "raw_codes": [],
                    "title": title,
                    "local": local,
                    "output_dir": "",
                    "is_index_page": True,
                    "already_extracted": False,
                    "notes": "玩家伴侣总览/索引页，不作为单本法术来源解析。",
                }
            )
            continue

        raw_code, book_name = parse_title(title)
        raw_codes = [part.strip() for part in re.split(r"[/／]", raw_code) if part.strip()]
        if len(raw_codes) > 1:
            source_code = "/".join(normalize_code(part) for part in raw_codes)
            notes = "合并页面；解析时按 raw_codes 拆分到多个来源。"
        else:
            source_code = SOURCE_CODE_OVERRIDES.get(
                (raw_code.upper(), local),
                normalize_code(raw_code),
            )
            notes = ""

        already_extracted = False
        if "/" not in source_code:
            already_extracted = source_code in existing_sources
        else:
            already_extracted = all(normalize_code(part) in existing_sources for part in raw_codes)

        entries.append(
            {
                "order": index,
                "source_code": source_code,
                "display_code": raw_code,
                "raw_codes": raw_codes,
                "title": book_name,
                "toc_title": title,
                "local": local,
                "output_dir": "" if "/" in source_code else f"result/{source_code.lower()}",
                "is_index_page": False,
                "already_extracted": already_extracted,
                "notes": notes,
            }
        )

    singleton_codes = [item["source_code"] for item in entries if "/" not in item["source_code"]]
    duplicate_codes = {
        code: count
        for code, count in Counter(singleton_codes).items()
        if count > 1
    }
    duplicate_display_codes = {
        code: count
        for code, count in Counter(item["display_code"].upper() for item in entries if item["display_code"]).items()
        if count > 1
    }

    return {
        "source": "result/toc.json / 法术 / 玩家伴侣",
        "total_entries": len(entries),
        "book_entries": sum(1 for item in entries if not item["is_index_page"]),
        "duplicate_source_codes": duplicate_codes,
        "duplicate_display_codes": duplicate_display_codes,
        "code_overrides": [
            {"display_code": raw, "local": local, "source_code": code}
            for (raw, local), code in sorted(SOURCE_CODE_OVERRIDES.items())
        ],
        "entries": entries,
    }


def write_markdown(manifest: dict) -> None:
    lines = [
        "# Player Companion Source Manifest",
        "",
        f"- total entries: {manifest['total_entries']}",
        f"- book entries: {manifest['book_entries']}",
        f"- duplicate source codes: {manifest['duplicate_source_codes'] or '{}'}",
        "",
        "| # | source_code | display_code | title | local | notes |",
        "|---:|---|---|---|---|---|",
    ]
    for item in manifest["entries"]:
        lines.append(
            "| {order} | {source_code} | {display_code} | {title} | {local} | {notes} |".format(
                order=item["order"],
                source_code=item["source_code"],
                display_code=item["display_code"],
                title=item["title"].replace("|", "\\|"),
                local=item["local"],
                notes=item["notes"].replace("|", "\\|"),
            )
        )
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    manifest = build_manifest()
    OUT_JSON.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    write_markdown(manifest)
    print(f"wrote {OUT_JSON.relative_to(ROOT)}")
    print(f"wrote {OUT_MD.relative_to(ROOT)}")
    print(f"duplicate_source_codes={manifest['duplicate_source_codes']}")
    print(f"duplicate_display_codes={manifest['duplicate_display_codes']}")


if __name__ == "__main__":
    main()