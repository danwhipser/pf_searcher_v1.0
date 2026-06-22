#!/usr/bin/env python3
"""
将 `result/spell-content.md` 中的法术段落提取为结构化 JSON。

这个脚本基于典型的法术排版（名字 + 学派/等级 + 施法时间/成分/等信息块 + 效果描述）。
它会从每个“施法时间”标签划分法术区块，提取常见字段，并以 `result/spells.json` 输出结果。
"""

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
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

START_PATTERN = re.compile(r"^\s*(?:施法时间|施放时间)\b")
LABEL_TO_FIELD = {
    "施法时间": "cast_time",
    "施放时间": "cast_time",
    "成分": "components",
    "射程": "range",
    "距离": "range",
    "范围": "area",
    "区域": "area",
    "目标": "target",
    "持续时间": "duration",
    "持续": "duration",
    "豁免": "save",
    "法术抗力": "spell_resistance",
    "抗力": "spell_resistance",
    "效果": "effect",
    "描述": "effect",
}
LABEL_PATTERN = re.compile(
    r"(?P<label>"
    + "|".join(sorted(LABEL_TO_FIELD.keys(), key=len, reverse=True))
    + r")\s*[:：]",
    re.MULTILINE,
)


def load_lines(path: Path) -> List[str]:
    return path.read_text(encoding="utf-8").splitlines()


def find_spell_ranges(lines: Sequence[str]) -> List[Tuple[int, int, int]]:
    indices = [idx for idx, line in enumerate(lines) if START_PATTERN.match(line)]
    ranges: List[Tuple[int, int, int]] = []
    for idx, start_idx in enumerate(indices):
        header_start = _find_header_start(lines, start_idx)
        next_start = indices[idx + 1] if idx + 1 < len(indices) else len(lines)
        ranges.append((header_start, start_idx, next_start))
    return ranges


def _find_header_start(lines: Sequence[str], time_idx: int) -> int:
    blank_streak = 0
    header_positions: List[int] = []
    for j in range(time_idx - 1, -1, -1):
        line = lines[j]
        if not line.strip():
            blank_streak += 1
            if blank_streak >= 2 and header_positions:
                break
            continue
        blank_streak = 0
        header_positions.append(j)
        if "等级" in line:
            break
    if header_positions:
        return min(header_positions)
    return time_idx


def _collect_header_prefix(lines: Sequence[str], header_start: int) -> Sequence[str]:
    prefix: List[str] = []
    idx = header_start - 1
    while idx >= 0 and len(prefix) < 2:
        line = lines[idx].strip()
        if not line:
            if prefix:
                break
            idx -= 1
            continue
        prefix.append(line)
        idx -= 1
        if prefix and not line.endswith("）") and "(" not in line:
            break
    return tuple(reversed(prefix))


def _parse_header(header_text: str) -> Dict[str, Optional[str]]:
    header_line = " ".join(header_text.split())
    if not header_line:
        return {}
    level_matches = list(re.finditer(r"等级\s*[:：]\s*([^等级]+)", header_line))
    name_value = header_line
    school = None
    level = None
    if level_matches:
        last = level_matches[-1]
        level = last.group(1).strip()
        name_value = header_line[: last.start()].strip()
        school_candidate = ""
        school_idx = name_value.rfind("学派")
        if school_idx != -1:
            name_value, school_candidate = (
                name_value[:school_idx].strip(),
                name_value[school_idx + len("学派") :].strip(),
            )
            school = school_candidate.strip("： ").strip()
        else:
            extra_idx = name_value.rfind("等级")
            if extra_idx != -1:
                school_candidate = name_value[extra_idx:]
                name_value = name_value[:extra_idx].strip()
                school = re.sub(r"等级\s*[:：]", "", school_candidate).strip()
    if not name_value:
        name_value = header_line
    cn, en = _split_names(name_value)
    return {
        "name": name_value,
        "name_cn": cn,
        "name_en": en,
        "school": school,
        "level": level,
    }


def _split_names(full_name: str) -> Tuple[str, Optional[str]]:
    if not full_name:
        return "", None
    m = re.match(r"(?P<cn>.+?)\s*[（(](?P<en>[^）)]+)[）)]\s*$", full_name)
    if m:
        return m.group("cn").strip(), m.group("en").strip()
    ascii_tail = re.search(r"([A-Za-z][A-Za-z0-9'’: &.\-]+)$", full_name)
    if ascii_tail:
        tail = ascii_tail.group(1).strip()
        if not re.search(r"[\u4e00-\u9fff]", tail):
            cn = full_name[: ascii_tail.start()].strip()
            if cn:
                return cn, tail
    return full_name, None


def _parse_body(body_text: str) -> Dict[str, str]:
    result: Dict[str, str] = {}
    matches = list(LABEL_PATTERN.finditer(body_text))
    for idx, match in enumerate(matches):
        field_key = LABEL_TO_FIELD[match.group("label")]
        start = match.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(body_text)
        value = body_text[start:end].strip()
        if not value:
            continue
        existing = result.get(field_key)
        result[field_key] = f"{existing}\n{value}".strip() if existing else value
    tail = ""
    if matches:
        tail = body_text[matches[-1].end() :].strip()
    else:
        tail = body_text.strip()
    if tail:
        if "effect" in result:
            result["effect"] = f"{result['effect']}\n{tail}".strip()
        else:
            result["effect"] = tail
    return result


def extract_spells(path: Path) -> List[Dict[str, Optional[str]]]:
    lines = load_lines(path)
    ranges = find_spell_ranges(lines)
    spells = []
    for start, time_idx, end in ranges:
        header_lines = lines[start:time_idx]
        prefix_lines = _collect_header_prefix(lines, start)
        if prefix_lines:
            header_lines = list(prefix_lines) + header_lines
        header_block = "\n".join(header_lines).strip()
        body_block = "\n".join(lines[time_idx:end]).strip()
        if not header_block or not body_block:
            continue
        spell = _parse_header(header_block)
        if not spell.get("name"):
            continue
        spell.update(_parse_body(body_block))
        spells.append(spell)
    return spells


def main() -> None:
    parser = argparse.ArgumentParser(
        description="从 spell-content 中抽取法术并输出 JSON。"
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=Path("result/spell-content.md"),
        help="输入的 Markdown 文件（默认 result/spell-content.md）。",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("result/spells.json"),
        help="输出的 JSON 文件（默认 result/spells.json）。",
    )
    args = parser.parse_args()
    if not args.input.is_file():
        raise SystemExit(f"{args.input} 不存在，先生成 spell-content 文件。")
    spells = extract_spells(args.input)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(spells, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"共提取 {len(spells)} 个法术，写入 {args.output}")


if __name__ == "__main__":
    main()
