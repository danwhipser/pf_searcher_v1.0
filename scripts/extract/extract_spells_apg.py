#!/usr/bin/env python3
"""
APG dedicated extractor.

Why dedicated:
- In Spell APG.html, many spell name rows are plain text "中文 (English)"
  without bold or highlight color.
- Generic extractor relies on bold/color name hints, so APG later sections
  are missed.
"""
from __future__ import annotations

# Allow direct execution from nested scripts/ folders.
import sys
from pathlib import Path
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import re
from typing import Dict, List, Optional, Tuple

from bs4 import BeautifulSoup

LABEL_MAP = {
    "学派": "学派",
    "环位": "等级",
    "等级": "等级",
    "位阶": "等级",
    "施法时间": "施法时间",
    "施放时间": "施法时间",
    "成分": "成分",
    "距离": "范围",
    "射程": "范围",
    "范围": "范围",
    "区域": "范围",
    "目标": "目标",
    "效果": "效果",
    "描述": "效果",
    "持续时间": "持续",
    "持续": "持续",
    "豁免": "豁免",
    "法术抗力": "法术抗力",
    "抗力": "法术抗力",
}

NAME_RE = re.compile(r"^[^\n:：]{2,80}[（(][A-Za-z][^）)]{1,120}[）)]$")
EN_ONLY_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z'’\-]*(?:\s*[,:]\s*[A-Za-z'’\-]+)*(?:\s+[A-Za-z'’\-]+){0,6}$")


def clean_text(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r"[\s\xa0\u3000]+", " ", text)
    return text.strip()


def is_name_candidate(text: str) -> bool:
    if not text:
        return False
    if text.startswith(("译者:", "来源:", "作者:")) or "http" in text:
        return False
    if any(text.startswith(k) for k in LABEL_MAP):
        return False
    if re.fullmatch(r"[A-Z]", text):
        return False
    if text.endswith(("。", "；", "：")):
        return False
    if not NAME_RE.match(text):
        return False
    # Must contain Chinese and English.
    if not re.search(r"[\u4e00-\u9fff]", text):
        return False
    if not re.search(r"[A-Za-z]{3,}", text):
        return False
    return True


def is_en_only_name_candidate(text: str) -> bool:
    if not text:
        return False
    if any(ch in text for ch in "。；："):
        return False
    if any(text.startswith(k) for k in LABEL_MAP):
        return False
    t = text.strip()
    if len(t) < 4 or len(t) > 60:
        return False
    if not EN_ONLY_NAME_RE.match(t):
        return False
    # Keep it strict to avoid grabbing sentence fragments.
    words = re.findall(r"[A-Za-z]+", t)
    if len(words) < 2:
        return False
    return True


def has_nearby_field_markers(lines: List[str], idx: int, window: int = 8) -> bool:
    chunk = " ".join(lines[idx + 1 : idx + 1 + window])
    return (
        ("学派" in chunk and "等级" in chunk)
        or ("学派" in chunk and "施法时间" in chunk)
        or ("学派" in chunk and "成分" in chunk)
    )


def _normalize_en_title(name: str) -> str:
    if not name:
        return ""
    m = re.search(r"[（(]\s*([A-Za-z][^）)]{1,120})\s*[）)]", name)
    en = m.group(1) if m else name
    en = en.lower()
    en = en.replace("’", "'")
    en = re.sub(r"[^a-z0-9]+", " ", en)
    return en.strip()


def _parse_spell_tail_from_lines(lines: List[str], start_idx: int, max_window: int = 36) -> Dict[str, str]:
    parsed: Dict[str, str] = {"效果": ""}
    current_field: Optional[str] = None
    seen_field = False
    end = min(len(lines), start_idx + 1 + max_window)
    for j in range(start_idx + 1, end):
        text = lines[j]
        if not text:
            continue

        # Stop once a plausible next title appears after we've entered fields.
        if seen_field and (is_name_candidate(text) or is_en_only_name_candidate(text)):
            break

        found_label = False
        for label, field in LABEL_MAP.items():
            if text.startswith(label):
                found_label = True
                seen_field = True
                val = re.sub(rf"^{re.escape(label)}\s*[:：]?\s*", "", text).strip()
                if field == "效果":
                    parsed["效果"] = (parsed.get("效果", "") + "\n" + val).strip()
                else:
                    parsed[field] = val
                current_field = field
                break
        if found_label:
            continue
            

        if seen_field:
            if _should_continue_non_effect_field(current_field, text):
                parsed[current_field] = (parsed.get(current_field, "") + " " + text).strip()
            else:
                current_field = "效果"
                parsed["效果"] = (parsed.get("效果", "") + "\n" + text).strip()

    return parsed


def _should_continue_non_effect_field(field: Optional[str], text: str) -> bool:
    if not field or field == "效果":
        return False
    # Level lines are compact class-level pairs; long free text usually means
    # description body and should go to effect.
    if field == "等级":
        if len(text) > 60:
            return False
        if re.search(r"[（(][A-Za-z]{3,}", text):
            return False
        return bool(re.search(r"\d", text))
    return len(text) < 100


def _targeted_backfill_apg_variants(lines: List[str], source: str, spells: List[Dict]) -> None:
    targets = ("Evolution Surge", "Evolution Surge, Greater")
    existing = {_normalize_en_title(s.get("name", "")) for s in spells}
    for target in targets:
        target_norm = _normalize_en_title(target)
        if target_norm in existing:
            continue
        for i, text in enumerate(lines):
            if text.strip().lower() != target.lower():
                continue
            if not has_nearby_field_markers(lines, i, window=18):
                continue
            fields = _parse_spell_tail_from_lines(lines, i)
            if not (fields.get("学派") or fields.get("等级")):
                continue
            rec: Dict[str, str] = {"name": target, "来源": source, "效果": fields.get("效果", "")}
            for k, v in fields.items():
                if k != "效果":
                    rec[k] = v
            spells.append(rec)
            existing.add(target_norm)
            break

    # APG index contains base "Evolution Surge". If source only has
    # "Evolution Surge, Lesser", clone it as a conservative fallback.
    base_norm = _normalize_en_title("Evolution Surge")
    if base_norm not in existing:
        lesser = None
        for s in spells:
            if _normalize_en_title(s.get("name", "")) == _normalize_en_title("Evolution Surge, Lesser"):
                lesser = s
                break
        if lesser:
            clone = dict(lesser)
            clone["name"] = "Evolution Surge"
            spells.append(clone)


def extract_apg_spells(path: Path) -> Tuple[List[Dict], List[Dict]]:
    try:
        content = path.read_text(encoding="gb18030")
    except Exception:
        content = path.read_text(encoding="utf-8", errors="ignore")

    soup = BeautifulSoup(content, "html.parser")
    paragraphs = soup.find_all("p")
    lines = [clean_text(p.get_text()) for p in paragraphs]

    spells: List[Dict] = []
    issues: List[Dict] = []
    current_spell: Optional[Dict] = None
    current_field: Optional[str] = None
    source = path.stem.replace("Spell ", "")

    for i, text in enumerate(lines):
        if not text:
            continue

        # Field lines are parsed first.
        found_label = False
        for label, field in LABEL_MAP.items():
            if text.startswith(label):
                found_label = True
                if current_spell:
                    val = re.sub(rf"^{re.escape(label)}\s*[:：]?\s*", "", text).strip()
                    if field == "效果":
                        current_spell["效果"] = (
                            current_spell.get("效果", "") + "\n" + val
                        ).strip()
                    else:
                        current_spell[field] = val
                    current_field = field
                break
        if found_label:
            continue

        # APG name detection: plain "中文(English)" + nearby field labels.
        if is_name_candidate(text) and has_nearby_field_markers(lines, i):
            if current_spell:
                if current_spell.get("学派") or current_spell.get("等级"):
                    spells.append(current_spell)
                else:
                    issues.append({"type": "invalid_spell", "data": current_spell})
            current_spell = {"name": text, "来源": source, "效果": ""}
            current_field = None
            continue
        # APG fallback: some entries are english-only heading lines
        # (e.g. "Evolution Surge, Greater") followed by field labels.
        if is_en_only_name_candidate(text) and has_nearby_field_markers(lines, i, window=14):
            if current_spell:
                if current_spell.get("学派") or current_spell.get("等级"):
                    spells.append(current_spell)
                else:
                    issues.append({"type": "invalid_spell", "data": current_spell})
            current_spell = {"name": text, "来源": source, "效果": ""}
            current_field = None
            continue

        if current_spell:
            # Multi-line field continuation.
            if _should_continue_non_effect_field(current_field, text):
                current_spell[current_field] = (
                    current_spell.get(current_field, "") + " " + text
                ).strip()
            else:
                current_field = "效果"
                if current_spell.get("效果"):
                    current_spell["效果"] += "\n" + text
                else:
                    current_spell["效果"] = text
        else:
            # Keep a small set of diagnostics.
            if len(issues) < 200:
                issues.append({"type": "unassigned_text", "text": text})

    if current_spell:
        if current_spell.get("学派") or current_spell.get("等级"):
            spells.append(current_spell)
        else:
            issues.append({"type": "invalid_spell", "data": current_spell})

    # APG targeted rescue: these two are common "english-only heading" misses.
    _targeted_backfill_apg_variants(lines, source, spells)

    return spells, issues
