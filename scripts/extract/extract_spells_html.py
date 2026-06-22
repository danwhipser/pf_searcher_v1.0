#!/usr/bin/env python3
"""
Pathfinder 法术提取核心逻辑 V11。
1. 记录所有未提取的段落到 issues 中。
2. 保持对标准规则书的稳健兼容。
"""
from __future__ import annotations

# Allow direct execution from nested scripts/ folders.
import sys
from pathlib import Path
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import io
import json
import re
from typing import Dict, List, Optional, Tuple

from bs4 import BeautifulSoup, Tag

if sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

LABEL_MAP = {
    "学派": "学派", "环位": "等级", "等级": "等级", "位阶": "等级",
    "施法时间": "施法时间", "施放时间": "施法时间", "成分": "成分",
    "距离": "范围", "射程": "范围", "范围": "范围", "区域": "范围",
    "目标": "目标", "效果": "效果", "持续时间": "持续", "持续": "持续",
    "豁免": "豁免", "法术抗力": "法术抗力", "抗力": "法术抗力",
    "描述": "效果",
}

def clean_text(text: str) -> str:
    if not text: return ""
    text = re.sub(r'[\s\xa0\u3000]+', ' ', text)
    return text.strip()

def is_spell_name_pattern(text: str, is_bold: bool, color: str) -> bool:
    if not text or len(text) < 2 or len(text) > 60: return False
    if text.startswith(("译者:", "来源:", "作者:")) or "http" in text: return False
    if any(text.startswith(kw) for kw in LABEL_MAP): return False
    if (":" in text or "：" in text) and len(text) > 30: return False

    has_en = re.search(r'[A-Za-z]{3,}', text)
    has_cn = re.search(r'[\u4e00-\u9fff]', text)
    
    if is_bold and "(" in text and ")" in text: return True
    if color in ['maroon', '#cc00cc', 'purple', 'navy', 'blue', '#0033cc'] and has_cn and has_en: return True
    if is_bold and has_cn and has_en: return True
    return False

def extract_spells_from_html(path: Path) -> Tuple[List[Dict], List[Dict]]:
    try:
        content = path.read_text(encoding="gb18030")
    except Exception:
        content = path.read_text(encoding="utf-8", errors="ignore")

    soup = BeautifulSoup(content, "html.parser")
    paragraphs = soup.find_all("p")

    spells = []
    issues = []
    current_spell: Optional[Dict] = None
    current_field: Optional[str] = None
    source = path.stem.replace("Spell ", "")
    pending_name: Optional[str] = None
    pending_buffer: List[str] = []

    for p in paragraphs:
        text = clean_text(p.get_text())
        if not text: continue

        is_bold = p.find(['b', 'strong']) is not None
        color = ""
        for tag in [p] + p.find_all(['span', 'font']):
            style = tag.get('style', '')
            c_match = re.search(r'color:\s*(#[0-9a-fA-F]+|[a-zA-Z]+)', style)
            if c_match: color = c_match.group(1).lower(); break
            if tag.get('color'): color = tag.get('color').lower(); break

        found_label = False
        for label, field in LABEL_MAP.items():
            if text.startswith(label):
                found_label = True
                if pending_name:
                    if current_spell: spells.append(current_spell)
                    current_spell = {"name": pending_name, "来源": source, "效果": ""}
                    pending_name = None
                    pending_buffer = []
                
                if current_spell:
                    val = re.sub(f"^{label}\s*[:：]?", "", text).strip()
                    if field == "效果":
                        current_spell["效果"] = (current_spell.get("效果", "") + "\n" + val).strip()
                    else:
                        current_spell[field] = val
                    current_field = field
                break
        
        if found_label: continue

        if is_spell_name_pattern(text, is_bold, color):
            if pending_name:
                if current_spell:
                    current_spell["效果"] += "\n" + pending_name
                    for b in pending_buffer: current_spell["效果"] += "\n" + b
                else:
                    issues.append({"type": "skipped_text", "text": pending_name})
                    for b in pending_buffer: issues.append({"type": "skipped_text", "text": b})
            pending_name = text
            pending_buffer = []
            current_field = None
            continue

        if pending_name:
            pending_buffer.append(text)
            if len(pending_buffer) > 3:
                if current_spell:
                    current_spell["效果"] += "\n" + pending_name
                    for b in pending_buffer: current_spell["效果"] += "\n" + b
                else:
                    issues.append({"type": "skipped_text", "text": pending_name})
                    for b in pending_buffer: issues.append({"type": "skipped_text", "text": b})
                pending_name = None
                pending_buffer = []
            continue

        if not current_spell:
            issues.append({"type": "unassigned_text", "text": text})
            continue

        if current_field and current_field != "效果" and len(text) < 100:
            current_spell[current_field] = (current_spell.get(current_field, "") + " " + text).strip()
        else:
            if text.strip("【】:： ") in ["法术效果", "描述", "效果"]:
                current_field = "效果"
            else:
                if current_spell["效果"]:
                    current_spell["效果"] += "\n" + text
                else:
                    current_spell["效果"] = text

    if pending_name:
        if current_spell:
            current_spell["效果"] += "\n" + pending_name
            for b in pending_buffer: current_spell["效果"] += "\n" + b
        else:
            issues.append({"type": "skipped_text", "text": pending_name})
            for b in pending_buffer: issues.append({"type": "skipped_text", "text": b})

    if current_spell: spells.append(current_spell)

    valid, final_issues = [], []
    for s in spells:
        if s.get("name") and (s.get("学派") or s.get("等级")):
            valid.append(s)
        else:
            final_issues.append({"type": "invalid_spell", "data": s})
    
    return valid, final_issues + issues