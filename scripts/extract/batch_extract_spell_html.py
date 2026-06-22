#!/usr/bin/env python3
"""批量解析 spell 目录下所有以 Spell 开头的 HTML 文件。"""
from __future__ import annotations

# Allow direct execution from nested scripts/ folders.
import sys
from pathlib import Path
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import json
from typing import Iterable

from scripts.extract.extract_spells_html import extract_spells_from_html


def iter_spell_files(root: Path) -> Iterable[Path]:
    return sorted(root.glob("Spell *.html"))


def slugify(path: Path) -> str:
    # "Spell ACG" -> "spells-acg"
    tail = path.stem.replace("Spell", "", 1).strip()
    tail = tail.replace(" ", "-").lower()
    return f"spells-{tail}"


def main() -> None:
    spell_dir = Path("spell")
    out_dir = Path("result")
    out_dir.mkdir(parents=True, exist_ok=True)

    html_files = list(iter_spell_files(spell_dir))
    if not html_files:
        raise SystemExit("未在 spell 目录找到任何以 'Spell ' 开头的 HTML 文件。")

    summary = []
    for html in html_files:
        slug = slugify(html)
        book_code = slug.replace("spells-", "")
        book_dir = out_dir / book_code
        book_dir.mkdir(exist_ok=True)

        output = book_dir / f"{slug}.json"
        check_report = book_dir / f"{slug}-check.json"
        unparsed = book_dir / f"{slug}-unparsed.json"

        spells, issues = extract_spells_from_html(html)
        output.write_text(json.dumps(spells, ensure_ascii=False, indent=2), encoding="utf-8")

        report = {
            "file": html.name,
            "slug": slug,
            "spell_count": len(spells),
            "missing_effect": [s["name"] for s in spells if not s.get("法术效果")],
            "issues": issues,
        }
        check_report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        unparsed.write_text(json.dumps(issues, ensure_ascii=False, indent=2), encoding="utf-8")

        summary.append(report)
        print(f"[ok] {html.name} -> {output} ({len(spells)} 条)")

    summary_path = out_dir / "spells-summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n完成。共处理 {len(html_files)} 个文件。汇总：{summary_path}")


if __name__ == "__main__":
    main()







