#!/usr/bin/env python3
"""
OA 专用提取器 V11 - HTML 手术版。
1. 将 <BR> 替换为换行符，解决单段落粘连问题。
2. 深度识别嵌套样式中的法术名。
3. 严格的属性-正文状态机。
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
    "目标": "目标", "持续时间": "持续", "持续": "持续",
    "豁免": "豁免", "法术抗力": "法术抗力", "抗力": "法术抗力",
    "效果": "效果_属性", 
}

# 标签匹配：冒号可有可无（OA 中常见“学派 预言系”而非“学派: 预言系”）
LABEL_PATTERN = re.compile(r"^(?P<label>" + "|".join(LABEL_MAP.keys()) + r")\s*[:：]?")
# 名称模式：更宽松，兼容 Greater/Mass/Lesser 与中文逗号等变体
NAME_PATTERN = re.compile(
    r"^[A-Za-z0-9\u4e00-\u9fff\s\u00b7]{2,60}"
    r"(?:\s*[（(][^）)\n]{2,140}[）)])?$"
)


def _normalize_en_key(text: str) -> str:
    t = (text or "").lower()
    t = t.replace("，", ",").replace("（", "(").replace("）", ")")
    t = re.sub(r"[^a-z0-9]", "", t)
    return t


def _extract_en_from_name(name: str) -> str:
    m = re.search(r"[（(]([^）)]+)[）)]", name or "")
    if m:
        return m.group(1).strip()
    toks = re.findall(r"[A-Za-z0-9]+", name or "")
    return " ".join(toks).strip()


def _load_oa_index_targets(path: Path) -> Dict[str, str]:
    """
    Load OA spell names from index as fallback extraction targets.
    key: normalized english title; value: index raw name
    """
    repo_root = path.resolve().parents[1]
    index_path = repo_root / "result" / "index" / "spells-index.json"
    if not index_path.exists():
        return {}
    try:
        rows = json.loads(index_path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    targets: Dict[str, str] = {}
    for r in rows:
        if (r.get("source_book") or "").upper() != "OA":
            continue
        raw_name = (r.get("name") or "").strip()
        en = _extract_en_from_name(raw_name)
        k = _normalize_en_key(en)
        if k:
            targets[k] = raw_name
    return targets


def _load_oa_unmatched_targets(path: Path) -> Dict[str, str]:
    """
    Prefer unresolved OA items from located result as backfill targets.
    Falls back to all OA index targets when located file does not exist.
    """
    repo_root = path.resolve().parents[1]
    located_path = repo_root / "result" / "index" / "spells-index-located.json"
    if not located_path.exists():
        return _load_oa_index_targets(path)
    try:
        rows = json.loads(located_path.read_text(encoding="utf-8"))
    except Exception:
        return _load_oa_index_targets(path)

    targets: Dict[str, str] = {}
    for r in rows:
        if (r.get("source_book") or "").upper() != "OA":
            continue
        if r.get("status") != "unmatched":
            continue
        raw_name = (r.get("name") or "").strip()
        en = _extract_en_from_name(raw_name)
        k = _normalize_en_key(en)
        if k:
            targets[k] = raw_name
    return targets or _load_oa_index_targets(path)


def _split_name_and_inline_fields(line: str) -> Tuple[str, List[str]]:
    line = (line or "").strip()
    if not line:
        return "", []
    idxs = []
    for k in LABEL_MAP.keys():
        p = line.find(k)
        if p > 0:
            idxs.append(p)
    if not idxs:
        return line, []
    cut = min(idxs)
    if cut < 2:
        return line, []
    return line[:cut].strip(), [line[cut:].strip()]


def _merge_wrapped_title_infos(lines_with_info: List[Dict]) -> List[Dict]:
    """
    Merge wrapped title rows like:
      "心像门（Mindscape"
      "Door）"
    into one logical line to improve downstream name detection.
    """
    merged: List[Dict] = []
    i = 0
    while i < len(lines_with_info):
        cur = dict(lines_with_info[i])
        text = (cur.get("text") or "").strip()
        if (
            text
            and ("（" in text or "(" in text)
            and ("）" not in text and ")" not in text)
            and i + 1 < len(lines_with_info)
        ):
            nxt = dict(lines_with_info[i + 1])
            nxt_text = (nxt.get("text") or "").strip()
            if nxt_text and not any(nxt_text.startswith(k) for k in LABEL_MAP):
                cur["text"] = text + nxt_text
                cur["is_bold"] = bool(cur.get("is_bold")) or bool(nxt.get("is_bold"))
                i += 1
        merged.append(cur)
        i += 1
    return merged


def _parse_spell_block(lines: List[str]) -> Optional[Dict]:
    if not lines:
        return None
    name, inline = _split_name_and_inline_fields(lines[0])
    if not name:
        return None
    spell: Dict[str, str] = {"name": name, "来源": "OA", "效果": ""}
    in_desc = False
    payload = inline + lines[1:]
    for text in payload:
        text = text.strip()
        if not text:
            continue
        label_m = LABEL_PATTERN.match(text)
        if label_m and not in_desc:
            label = label_m.group("label")
            field = LABEL_MAP[label]
            val = text[label_m.end() :].strip().strip(":：")
            if field == "效果_属性":
                if len(val) > 25 or any(kw in val for kw in ["功能如同", "描述如下"]):
                    spell["效果"] = val
                    in_desc = True
                else:
                    spell["效果属性"] = val
            else:
                spell[field] = val
            if field == "法术抗力":
                in_desc = True
            continue

        if text.strip("【】:： ") in ["法术效果", "描述", "效果"]:
            in_desc = True
            continue
        if spell["效果"]:
            spell["效果"] += "\n" + text
        else:
            spell["效果"] = text
        if len(text) > 60:
            in_desc = True
    if spell.get("学派") or spell.get("等级"):
        return spell
    return None


def _extract_embedded_spells_from_effects(
    spells: List[Dict], targets: Dict[str, str], existed_en: set[str]
) -> List[Dict]:
    """
    Secondary pass:
    OA has merged entries where multiple spells are concatenated in one effect block.
    Recover embedded "name + header fields" chunks for unresolved targets.
    """
    target_keys = {k for k in targets.keys() if k and k not in existed_en}
    if not target_keys:
        return []

    def is_embedded_start(lines: List[str], i: int, keyset: set[str]) -> Optional[str]:
        t = (lines[i] or "").strip()
        if not t or any(t.startswith(k) for k in LABEL_MAP):
            return None
        lk = _normalize_en_key(t)
        if not lk:
            return None
        mk = None
        for k in sorted(keyset, key=len, reverse=True):
            if k in lk or (len(k) > 10 and k in lk):
                mk = k
                break
        if not mk:
            return None
        nxt = lines[i + 1 : i + 8]
        has_header = any(("学派" in n[:12]) or ("等级" in n[:12]) or ("施法时间" in n[:12]) for n in nxt)
        return mk if has_header else None

    extras: List[Dict] = []
    for base in spells:
        effect = (base.get("效果") or "").strip()
        if not effect:
            continue
        raw_lines = [x.strip() for x in effect.splitlines() if x.strip()]
        if len(raw_lines) < 4:
            continue

        # Merge wrapped title lines like "Object" + "Possession）".
        lines: List[str] = []
        i = 0
        while i < len(raw_lines):
            cur = raw_lines[i]
            if ("（" in cur or "(" in cur) and ("）" not in cur and ")" not in cur) and i + 1 < len(raw_lines):
                cur = cur + raw_lines[i + 1]
                i += 1
            lines.append(cur)
            i += 1

        starts: List[Tuple[int, str]] = []
        for idx in range(len(lines)):
            mk = is_embedded_start(lines, idx, target_keys)
            if mk:
                starts.append((idx, mk))
        if not starts:
            continue

        starts.sort(key=lambda x: x[0])
        for si, (start, mk) in enumerate(starts):
            end = starts[si + 1][0] if si + 1 < len(starts) else len(lines)
            block = lines[start:end]
            spell = _parse_spell_block(block)
            if not spell:
                continue
            spell["name"] = targets.get(mk, spell.get("name", ""))
            en_key = mk or _normalize_en_key(_extract_en_from_name(spell.get("name", "")))
            if not en_key or en_key in existed_en:
                continue
            extras.append(spell)
            existed_en.add(en_key)
            if mk in target_keys:
                target_keys.remove(mk)
        if not target_keys:
            break

    return extras


def _is_name_like_line(text: str) -> bool:
    if not text or len(text) < 3 or len(text) > 140:
        return False
    if text.endswith(("。", "；", "：", "”")):
        return False
    if text.startswith(("译者", "来源", "作者")) or "http" in text:
        return False
    if any(text.startswith(k) for k in LABEL_MAP):
        return False
    has_cn = re.search(r"[\u4e00-\u9fff]", text) is not None
    has_en = re.search(r"[A-Za-z]{3,}", text) is not None
    if not (has_cn and has_en):
        return False
    # Common OA suffixes that often appear in unmatched rows
    if any(k in text for k in ("Greater", "Mass", "Lesser", "Major")):
        return True
    return NAME_PATTERN.match(text) is not None

def extract_oa_spells_v12(path: Path) -> List[Dict]:
    # 1. 预处理：将 <BR> 转换为换行符，并读取内容
    content = path.read_text(encoding="gb18030", errors="ignore")
    content = re.sub(r'(?i)<br\s*/?>', '\n', content) # 强制换行
    
    soup = BeautifulSoup(content, "html.parser")
    
    # 2. 收集所有包含样式的文本块
    lines_with_info = []
    for p in soup.find_all("p"):
        raw_html = str(p)
        # 将段落按 \n 拆分
        for line in p.get_text().split('\n'):
            line = line.strip()
            if not line: continue
            
            info = {"text": line, "is_bold": False, "color": ""}
            # 强化判定：只要 line 出现在加粗标签内，或者颜色为 navy
            if f"<B>{line}" in raw_html or f"<strong>{line}" in raw_html or "COLOR: navy" in raw_html:
                info["is_bold"] = True 
            
            lines_with_info.append(info)

    # Normalize wrapped EN title lines before parsing passes.
    lines_with_info = _merge_wrapped_title_infos(lines_with_info)

    # 3. 状态机提取（主 pass）
    spells = []
    current_spell: Optional[Dict] = None
    in_desc = False

    for i, item in enumerate(lines_with_info):
        text = item["text"]
        
        # --- 识别名称 ---
        is_new = False
        if _is_name_like_line(text):
            # OA 核心特征：名称后面几行应出现“学派/等级/施法时间”标签之一
            search_range = lines_with_info[i + 1 : i + 8]
            has_header = any(
                (
                    "学派" in s["text"][:12]
                    or "等级" in s["text"][:12]
                    or "施法时间" in s["text"][:12]
                )
                for s in search_range
            )
            if has_header:
                is_new = True

        if is_new:
            if current_spell: spells.append(current_spell)
            # 提取完整的名称行（包含可能的尾随空格）
            current_spell = {"name": text, "来源": "OA", "效果": ""}
            in_desc = False
            continue

        if not current_spell: continue

        # --- 识别属性 ---
        label_m = LABEL_PATTERN.match(text)
        if label_m and not in_desc:
            label = label_m.group("label")
            field = LABEL_MAP[label]
            val = text[label_m.end():].strip().strip(":：")
            
            # 特殊处理：如果“效果”标签后的内容很长，或者包含描述性关键词，它实际上是描述
            if field == "效果_属性":
                if len(val) > 25 or any(kw in val for kw in ["功能如同", "描述如下"]):
                    current_spell["效果"] = val
                    in_desc = True
                else:
                    current_spell["效果属性"] = val
            else:
                current_spell[field] = val
            
            if field == "法术抗力":
                in_desc = True
            continue

        # --- 收集描述 ---
        # 排除掉一些标题
        if text.strip("【】:： ") in ["法术效果", "描述", "效果"]:
            in_desc = True
            continue
            
        if current_spell["效果"]:
            current_spell["效果"] += "\n" + text
        else:
            current_spell["效果"] = text
        
        # 一旦文字过长且不带标签，自动转为描述模式
        if len(text) > 60:
            in_desc = True

    if current_spell:
        spells.append(current_spell)
    spells = [s for s in spells if s.get("学派") or s.get("等级")]

    # 4. OA missing-target补抽（优先使用当前仍 unresolved 的 OA 目标）
    targets = _load_oa_unmatched_targets(path)
    if not targets:
        return spells

    existed_en = {
        _normalize_en_key(_extract_en_from_name(s.get("name", ""))) for s in spells
    }
    missing_keys = {k for k in targets.keys() if k and k not in existed_en}
    if not missing_keys:
        return spells

    lines = [x["text"] for x in lines_with_info]
    start_hits: List[Tuple[int, str]] = []
    used_idx = set()
    for i, text in enumerate(lines):
        if i in used_idx:
            continue
        if not text or any(text.startswith(k) for k in LABEL_MAP):
            continue
        line_key = _normalize_en_key(text)
        if not line_key:
            continue
        matched_key = None
        for k in sorted(missing_keys, key=len, reverse=True):
            if k in line_key or (len(k) > 10 and k in line_key):
                matched_key = k
                break
        if not matched_key:
            continue
        # Require nearby headers to avoid matching references in description.
        nxt = lines[i : i + 8]
        has_header = any(
            ("学派" in n[:12]) or ("等级" in n[:12]) or ("施法时间" in n[:12]) for n in nxt
        )
        if not has_header:
            continue
        start_hits.append((i, matched_key))
        used_idx.add(i)

    if start_hits:
        start_hits.sort(key=lambda x: x[0])
        parsed_extra: List[Dict] = []
        for idx, (start, mkey) in enumerate(start_hits):
            end = start_hits[idx + 1][0] if idx + 1 < len(start_hits) else len(lines)
            block = lines[start:end]
            spell = _parse_spell_block(block)
            if not spell:
                continue
            # Canonicalize name to index target so follow-up locator can align reliably.
            spell["name"] = targets.get(mkey, spell.get("name", ""))
            en_key = mkey or _normalize_en_key(_extract_en_from_name(spell.get("name", "")))
            if en_key in existed_en:
                continue
            parsed_extra.append(spell)
            existed_en.add(en_key)

        if parsed_extra:
            spells.extend(parsed_extra)

    # 5. Secondary split pass for merged OA entries inside effect text.
    second_pass = _extract_embedded_spells_from_effects(spells, targets, existed_en)
    if second_pass:
        spells.extend(second_pass)
    return spells

def main():
    input_path = Path("spell/Spell OA.html")
    out_dir = Path("result/oa")
    out_dir.mkdir(parents=True, exist_ok=True)
    spells = extract_oa_spells_v12(input_path)
    (out_dir / "spells-oa.json").write_text(json.dumps(spells, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"OA 解析完成：提取 {len(spells)} 条法术。")

if __name__ == "__main__":
    main()