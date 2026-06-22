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
import urllib.parse
import urllib.request
from typing import Dict, List, Tuple

from bs4 import BeautifulSoup


ROOT = Path(__file__).resolve().parents[2]
OUT_PATH = ROOT / "data" / "player_companion_aon_supplements.json"

SPELLS: Dict[str, List[str]] = {
    "GHH": ["Resize Item"],
    "QC": ["Ceremony"],
    "BOTE": ["Enshroud Thoughts"],
    "MO": ["Elemental Bombardment", "Imbue with Flight", "Soulreaver", "Sustaining Legend"],
    "COL": ["Rival's Weald", "Song of Discord, Greater", "Uncanny Reminder"],
}


def clean(text: str) -> str:
    text = re.sub(r"\s+", " ", text or "")
    return text.strip()


def fetch_spell(name: str) -> str:
    query = urllib.parse.urlencode({"ItemName": name})
    url = f"https://aonprd.com/SpellDisplay.aspx?{query}"
    request = urllib.request.Request(url, headers={"User-Agent": "PF_RAG spell supplement fetcher"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read().decode("utf-8", errors="replace")


def text_lines(html: str) -> List[str]:
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text("\n")
    return [clean(line) for line in text.splitlines() if clean(line)]


def slice_spell_lines(lines: List[str], name: str) -> List[str]:
    indexes = [index for index, line in enumerate(lines) if line == name]
    if not indexes:
        return lines
    return lines[indexes[-1] :]


def find_value(lines: List[str], label: str) -> str:
    prefix = f"{label} "
    for index, line in enumerate(lines):
        if line.startswith(prefix):
            return line[len(prefix) :].strip()
        if line == label and index + 1 < len(lines):
            return lines[index + 1].strip()
    return ""


def parse_school_level(lines: List[str]) -> Tuple[str, str]:
    for index, line in enumerate(lines):
        if line.startswith("School ") and "; Level " in line:
            school, level = line.split("; Level ", 1)
            return school.removeprefix("School ").strip(), level.strip()
        if line == "School" and index + 4 < len(lines):
            school = lines[index + 1]
            level = ""
            for lookahead in range(index + 2, min(index + 16, len(lines))):
                if lines[lookahead] == "Level" and lookahead + 1 < len(lines):
                    level = lines[lookahead + 1]
                    break
            return school.strip(), level.strip()
    return "", ""


def parse_save_sr(lines: List[str]) -> Tuple[str, str]:
    value = find_value(lines, "Saving Throw")
    if "; Spell Resistance " in value:
        save, sr = value.split("; Spell Resistance ", 1)
        return save.strip(), sr.strip()
    sr = find_value(lines, "Spell Resistance")
    return value.rstrip(";").strip(), sr


def parse_description(lines: List[str]) -> str:
    try:
        start = lines.index("Description") + 1
    except ValueError:
        return ""
    tail = []
    for line in lines[start:]:
        if line.startswith("Site Owner:"):
            break
        if line in {"Image"}:
            continue
        tail.append(line)
    return "\n".join(tail).strip()


def parse_spell(source: str, name: str) -> Dict:
    html = fetch_spell(name)
    lines = slice_spell_lines(text_lines(html), name)
    school, level = parse_school_level(lines)
    save, sr = parse_save_sr(lines)
    target = (
        find_value(lines, "Target")
        or find_value(lines, "Targets")
        or find_value(lines, "Area")
        or find_value(lines, "Effect")
    )
    return {
        "name": name,
        "source_book": source.lower(),
        "学派": school,
        "等级": level,
        "施法时间": find_value(lines, "Casting Time"),
        "成分": find_value(lines, "Components"),
        "范围": find_value(lines, "Range"),
        "目标": target,
        "持续": find_value(lines, "Duration"),
        "豁免": save,
        "法术抗力": sr,
        "效果": parse_description(lines),
        "法术类型": "normal",
        "补充来源": f"AoN SpellDisplay.aspx?ItemName={urllib.parse.quote(name)}",
    }


def main() -> None:
    supplements: Dict[str, List[Dict]] = {}
    for source, names in SPELLS.items():
        supplements[source] = []
        for name in names:
            record = parse_spell(source, name)
            supplements[source].append(record)
            print(f"{source}: {name}")
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(supplements, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {OUT_PATH.relative_to(ROOT)}")


if __name__ == "__main__":
    main()