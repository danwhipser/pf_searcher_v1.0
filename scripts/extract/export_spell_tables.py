from __future__ import annotations

# Allow direct execution from nested scripts/ folders.
import sys
from pathlib import Path
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import json

from bs4 import BeautifulSoup  # type: ignore[import]


def extract_table_structure(effect_html: str) -> list[dict[str, list[list[str]]]]:
    soup = BeautifulSoup(effect_html, "html.parser")
    tables = []
    for table in soup.find_all("table"):
        rows = []
        for row in table.find_all("tr"):
            cells = []
            for cell in row.find_all(["th", "td"]):
                cells.append(cell.get_text(separator=" ", strip=True))
            if cells:
                rows.append(cells)
        if rows:
            tables.append({"rows": rows})
    return tables


def main() -> None:
    source = Path("result/spells-crb.json")
    output = Path("result/spells-crb-tables.json")
    if not source.is_file():
        raise SystemExit(f"{source} 不存在，请先生成 spells-crb.json。")
    spells = json.loads(source.read_text(encoding="utf-8"))
    table_entries = []
    for spell in spells:
        effect = spell.get("法术效果", "")
        tables = extract_table_structure(effect)
        if tables:
            table_entries.append(
                {
                    "name": spell.get("name", ""),
                    "来源": spell.get("来源", ""),
                    "tables": tables,
                }
            )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(table_entries, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"已提取 {len(table_entries)} 个含表格的法术，保存至 {output}")


if __name__ == "__main__":
    main()













