#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
四本专用解析器 v1.0 —— Spell UC / Spell UM / Spell UI / Spell index

【解析策略】
  UC/UM  : 增强型段落扫描。法术名识别增加"无加粗/无颜色 + 中文(English)"模式；
            跳过纯英文分节行；字段标签与值同一 <P> 内一并提取。
  UI     : BR 切行模式。将所有 <BR> 替换成换行符，按行扫描；
            法术名 = <STRONG> 包裹 navy 色 <SPAN>；章节标题 = navy <SPAN> 包裹 <STRONG>。
  index  : TABLE 行提取。两列：来源缩写 + 法术名，仅提取索引条目，无详情字段。

【QA 质量评估 — 6 步骤】
  步骤1  : 从源 HTML 独立估算"期望法术数"（每本用专用逻辑）。
  步骤2  : 计算覆盖率 = 提取数 / 期望数；识别疑似漏提取条目。
  步骤3  : 校验必填字段（name / source_book / school / level_raw / effect）完整性。
  步骤4  : 检查等级字段能否结构化解析为 {class, level} 对。
  步骤5  : 检测疑似"串条"（效果字段内出现下一条法术头部格式）。
  步骤6  : 汇总解析问题，计算综合质量分（0~100）。
"""

from __future__ import annotations

# Allow direct execution from nested scripts/ folders.
import sys
from pathlib import Path
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import json
import re
from typing import Dict, List, Optional, Tuple

from bs4 import BeautifulSoup

# ─────────────────────── 目录 ────────────────────────────────────────────────
_HERE = Path(__file__).resolve().parent
SPELL_DIR  = _HERE.parents[1] / "spell"
RESULT_DIR = _HERE.parents[1] / "result"

BOOKS = [
    ("uc",    "Spell UC.html"),
    ("um",    "Spell UM.html"),
    ("ui",    "Spell UI.html"),
    ("index", "Spell index.html"),
]

# ─────────────────────── 公共工具 ─────────────────────────────────────────────
LABEL_MAP: Dict[str, str] = {
    "学派": "学派", "环位": "等级", "等级": "等级", "位阶": "等级",
    "施法时间": "施法时间", "施放时间": "施法时间", "成分": "成分",
    "距离": "范围", "射程": "范围", "范围": "范围", "区域": "区域",
    "目标": "目标", "效果": "效果", "描述": "效果",
    "持续时间": "持续", "持续": "持续",
    "豁免": "豁免", "法术抗力": "法术抗力", "抗力": "法术抗力",
}
REQUIRED_FIELDS = ("name", "source_book", "school", "level_raw", "effect")
MERGED_HINT_RE  = re.compile(r"[（(][^）)\n]{1,80}[）)]\s*学派[:：]")


def clean_text(s: str) -> str:
    if not s:
        return ""
    return re.sub(r"[\s\xa0\u3000\ufeff]+", " ", s).strip()


def normalize_digit(s: str) -> str:
    return s.translate(str.maketrans("０１２３４５６７８９", "0123456789"))


def parse_level_by_class(level_raw: str) -> Tuple[List[Dict], List[str]]:
    items, unparsed = [], []
    if not level_raw:
        return items, unparsed
    text = normalize_digit(level_raw).replace("\n", " ")
    for part in re.split(r"[，,；;]", text):
        tok = part.strip()
        if not tok:
            continue
        m = re.search(r"(.+?)\s+(\d+)\s*$", tok)
        if m:
            items.append({"class": m.group(1).strip(), "level": int(m.group(2))})
        else:
            unparsed.append(tok)
    return items, unparsed


def normalize_spell(raw: Dict, book_code: str, idx: int) -> Dict:
    """将专用解析器的原始字段转换为与 step1 相同的标准模型。"""
    level_raw  = (raw.get("等级") or raw.get("level_raw") or "").strip()
    level_by_class, level_unparsed = parse_level_by_class(level_raw)
    return {
        "spell_id":         f"{book_code}-{idx:04d}",
        "name":             (raw.get("name") or "").strip(),
        "source_book":      (raw.get("source_book") or book_code).strip(),
        "school":           (raw.get("学派") or "").strip(),
        "level_raw":        level_raw,
        "level_by_class":   level_by_class,
        "cast_time":        (raw.get("施法时间") or "").strip(),
        "components":       (raw.get("成分") or "").strip(),
        "range":            (raw.get("范围") or "").strip(),
        "area":             (raw.get("区域") or "").strip(),
        "target":           (raw.get("目标") or "").strip(),
        "duration":         (raw.get("持续") or "").strip(),
        "save":             (raw.get("豁免") or "").strip(),
        "spell_resistance": (raw.get("法术抗力") or "").strip(),
        "effect":           (raw.get("效果") or "").strip(),
        "level_unparsed":   level_unparsed,
        "raw_fields":       raw,
    }


# ═══════════════════════════════════════════════════════════════════════════════
#  UC / UM 专用解析器
#  关键改动：is_ucum_spell_name() 增加"无样式中英混合"模式识别
# ═══════════════════════════════════════════════════════════════════════════════

_UCUM_CN_EN_PARENS = re.compile(
    r"[\u4e00-\u9fff].{0,30}"          # 以中文开头，中间可有少量字符
    r"[（(][A-Za-z][A-Za-z\s\-',/0-9]{2,}[）)]"  # 接 (English Name)
)


def _get_color(p) -> str:
    """从 <p> 及其内部 span/font 中提取首个颜色值。"""
    for tag in [p] + p.find_all(["span", "font"]):
        style = tag.get("style", "")
        m = re.search(r"color:\s*(#[0-9a-fA-F]+|[a-zA-Z]+)", style, re.I)
        if m:
            return m.group(1).lower()
        c = tag.get("color", "")
        if c:
            return c.lower()
    return ""


def _is_ucum_spell_name(text: str, is_bold: bool, color: str) -> bool:
    """
    UC/UM 法术名识别（在原有规则基础上新增纯文本中英混合模式）：
      规则 A（原有）：加粗 + 括号           → 标准规则书通用
      规则 B（原有）：特定颜色 + 中英混合   → UM 首批紫色法术名
      规则 C（新增）：有中文 + (English) 括号，长度 ≤50  → UC/UM 无样式法术名
    """
    if not text or len(text) < 2 or len(text) > 60:
        return False
    if text.startswith(("译者:", "来源:", "作者:")) or "http" in text:
        return False
    if any(text.startswith(kw) for kw in LABEL_MAP):
        return False
    if (":" in text or "：" in text) and len(text) > 30:
        return False

    has_cn = bool(re.search(r"[\u4e00-\u9fff]", text))
    has_en = bool(re.search(r"[A-Za-z]{3,}", text))

    # 规则 A
    if is_bold and "(" in text and ")" in text:
        return True
    # 规则 B
    _COLOR_SET = {"maroon", "#cc00cc", "purple", "navy", "blue", "#0033cc"}
    if color in _COLOR_SET and has_cn and has_en:
        return True
    if is_bold and has_cn and has_en:
        return True
    # 规则 C — UC/UM 无样式法术名（中文名 + (English) 括号）
    if has_cn and len(text) <= 50 and _UCUM_CN_EN_PARENS.search(text):
        return True

    return False


def extract_ucum_spells(path: Path, book_code: str) -> Tuple[List[Dict], List[Dict]]:
    """
    UC/UM 专用提取器。

    UC 格式特征：
      - 法术名：平文 <P>，中文名 + (English Name)，无加粗，无颜色
      - 字段标签：<B><SPAN COLOR="#0033cc">学派</SPAN></B>，标签与值同一 <P>
      - 分节行：单字母 "A/B/C…" 或纯英文行（如 "Absorb Toxicity"）→ 跳过

    UM 格式特征：
      - 第一批法术名：紫色加粗（#cc00cc），已被原规则 A/B 识别
      - 后续法术名：与 UC 相同（平文中英混合）
      - 存在纯英文"索引行"→ 同样跳过
    """
    try:
        content = path.read_text(encoding="gb18030")
    except Exception:
        content = path.read_text(encoding="utf-8", errors="ignore")

    soup = BeautifulSoup(content, "html.parser")
    paragraphs = soup.find_all("p")

    spells:  List[Dict] = []
    issues:  List[Dict] = []
    current_spell: Optional[Dict] = None
    current_field: Optional[str]  = None

    for p in paragraphs:
        raw_text = p.get_text()
        text = clean_text(raw_text)
        if not text:
            continue

        # ① 跳过分节字母行（单个大写字母，如 "A"）
        if re.fullmatch(r"[A-Z]", text):
            continue

        # ② 跳过纯英文行（UC/UM 中字母索引/法术英文标题行）
        if not re.search(r"[\u4e00-\u9fff]", text) and re.match(r"^[A-Za-z0-9\s\-'',./()]+$", text):
            continue

        is_bold = p.find(["b", "strong"]) is not None
        color   = _get_color(p)

        # ③ 先检查字段标签（优先于法术名，防止含"括号"的字段值被误识别）
        found_label = False
        for label, mapped_field in LABEL_MAP.items():
            if text.startswith(label):
                found_label = True
                if current_spell:
                    val = re.sub(rf"^{re.escape(label)}\s*[:：]?\s*", "", text).strip()
                    if mapped_field == "效果":
                        current_spell["效果"] = (
                            current_spell.get("效果", "") + "\n" + val
                        ).strip()
                    else:
                        current_spell[mapped_field] = val
                    current_field = mapped_field
                break
        if found_label:
            continue

        # ④ 识别法术名
        if _is_ucum_spell_name(text, is_bold, color):
            # 保存上一条法术
            if current_spell:
                if current_spell.get("学派") or current_spell.get("等级"):
                    spells.append(current_spell)
                else:
                    issues.append({"type": "invalid_spell", "data": current_spell})
            current_spell = {"name": text, "source_book": book_code, "效果": ""}
            current_field = None
            continue

        # ⑤ 正文/描述
        if current_spell:
            if current_field and current_field != "效果" and len(text) < 100:
                current_spell[current_field] = (
                    current_spell.get(current_field, "") + " " + text
                ).strip()
            else:
                current_field = "效果"
                eff = current_spell.get("效果", "")
                current_spell["效果"] = (eff + "\n" + text).strip() if eff else text
        else:
            issues.append({"type": "unassigned_text", "text": text})

    # 收尾
    if current_spell:
        if current_spell.get("学派") or current_spell.get("等级"):
            spells.append(current_spell)
        else:
            issues.append({"type": "invalid_spell", "data": current_spell})

    return spells, issues


# ═══════════════════════════════════════════════════════════════════════════════
#  UI 专用解析器
#  格式：全文在少数 <P> 中，用 <BR> 分行；法术名 = STRONG 包裹 navy-SPAN
# ═══════════════════════════════════════════════════════════════════════════════

_BR_RE = re.compile(r"<[Bb][Rr][^>]*/?>\s*", re.DOTALL)


def _chunk_is_spell_name(chunk_soup) -> Optional[str]:
    """
    判断一个 BR 分割块是否是 UI 格式的法术名。

    法术名特征：
      <STRONG> 是外层，内含 navy 色 <SPAN/FONT>，文本包含中文 + （EN）
    章节标题特征：
      navy/purple/maroon 色 <SPAN> 是外层，<STRONG> 是内层 → 排除

    返回法术名字符串；非法术名返回 None。
    """
    for strong in chunk_soup.find_all("strong"):
        # 检查 STRONG 的直系祖先是否含有颜色（= 章节标题，跳过）
        parent = strong.parent
        is_header = False
        while parent and getattr(parent, "name", None) in ("span", "font", "p", "div"):
            style = parent.get("style", "") or ""
            cm = re.search(r"color:\s*(#[0-9a-fA-F]+|[a-zA-Z]+)", style, re.I)
            if cm and cm.group(1).lower() in ("navy", "purple", "maroon"):
                is_header = True
                break
            c = parent.get("color", "") or ""
            if c.lower() in ("navy", "purple", "maroon"):
                is_header = True
                break
            parent = getattr(parent, "parent", None)
        if is_header:
            continue

        # 检查 STRONG 内部是否有 navy 子元素
        navy_child = strong.find(
            lambda t: getattr(t, "name", None) in ("span", "font") and (
                "navy" in (t.get("style", "") + (t.get("color") or "")).lower()
            )
        )
        if navy_child is None:
            continue

        # 验证内容：含中文 + (English) 括号格式
        txt = clean_text(strong.get_text())
        if re.search(r"[\u4e00-\u9fff]", txt) and re.search(r"[（(][A-Za-z]", txt):
            return txt

    return None


def extract_ui_spells(path: Path) -> Tuple[List[Dict], List[Dict]]:
    """
    UI 专用提取器。

    格式特征：
      - 文档由若干 <P> 构成，段落内容以 <BR> 分行
      - 法术名：<STRONG><FONT><FONT><SPAN color="navy">中文（English）</SPAN>...</STRONG>
      - 字段标签：<STRONG>学派</STRONG>  （无颜色，紧跟值 SPAN）
      - 章节标题：<SPAN color="navy/purple"><STRONG>标题</STRONG></SPAN>（与法术名嵌套方向相反）
      - 章节与法术间以 <HR> 分隔，法术间以空 <BR> 分隔
    """
    try:
        content = path.read_text(encoding="gb18030")
    except Exception:
        content = path.read_text(encoding="utf-8", errors="ignore")

    soup = BeautifulSoup(content, "html.parser")

    spells:  List[Dict] = []
    issues:  List[Dict] = []
    current_spell: Optional[Dict] = None
    current_field: Optional[str]  = None

    for p in soup.find_all("p"):
        p_html = str(p)
        # 按 <BR> 切割，每块为一个"虚拟行"
        chunks = _BR_RE.split(p_html)

        for chunk in chunks:
            chunk_soup = BeautifulSoup(chunk, "html.parser")
            text = clean_text(chunk_soup.get_text())
            if not text:
                continue

            # ① 检测法术名（STRONG 包裹 navy SPAN）
            spell_name = _chunk_is_spell_name(chunk_soup)
            if spell_name:
                if current_spell:
                    if current_spell.get("学派") or current_spell.get("等级"):
                        spells.append(current_spell)
                    else:
                        issues.append({"type": "invalid_spell", "data": current_spell})
                current_spell = {"name": spell_name, "source_book": "UI", "效果": ""}
                current_field = None
                continue

            # ② 检测字段标签（chunk 中有不带颜色的 STRONG，文本匹配 LABEL_MAP）
            found_label = False
            for strong in chunk_soup.find_all("strong"):
                label_text = clean_text(strong.get_text())
                for label, mapped_field in LABEL_MAP.items():
                    if label_text.startswith(label):
                        found_label = True
                        if current_spell:
                            # 值 = 整行文本去掉标签前缀
                            val = re.sub(
                                rf"^{re.escape(label_text)}\s*[:：]?\s*", "", text
                            ).strip()
                            if not val:
                                val = re.sub(
                                    rf"^{re.escape(label)}\s*[:：]?\s*", "", text
                                ).strip()
                            if mapped_field == "效果":
                                current_spell["效果"] = (
                                    current_spell.get("效果", "") + "\n" + val
                                ).strip()
                            else:
                                current_spell[mapped_field] = val
                            current_field = mapped_field
                        break
                if found_label:
                    break
            if found_label:
                continue

            # ③ 跳过页首的链接/作者行
            if text.startswith(("http", "译者:", "作者:", "来源:")):
                continue

            # ④ 正文（描述）
            if current_spell:
                # 跳过过短的分隔行
                if len(text) < 3:
                    continue
                current_field = "效果"
                eff = current_spell.get("效果", "")
                current_spell["效果"] = (eff + "\n" + text).strip() if eff else text

    # 收尾
    if current_spell:
        if current_spell.get("学派") or current_spell.get("等级"):
            spells.append(current_spell)
        else:
            issues.append({"type": "invalid_spell", "data": current_spell})

    return spells, issues


# ═══════════════════════════════════════════════════════════════════════════════
#  index 专用解析器
#  格式：两列 TABLE，列1 = 来源缩写，列2 = 中文名(英文名)
# ═══════════════════════════════════════════════════════════════════════════════

def extract_index_spells(path: Path) -> Tuple[List[Dict], List[Dict]]:
    """
    index 专用提取器。

    本文件是「法术总索引」，包含所有法术的来源缩写和中英文名，
    不含学派/等级等详情字段。

    提取内容：
      - source_book  : 来源缩写（如 ACG、APG、CRB、UC…）
      - name         : 中文法术名（含英文名）
    """
    try:
        content = path.read_text(encoding="gb18030")
    except Exception:
        content = path.read_text(encoding="utf-8", errors="ignore")

    soup = BeautifulSoup(content, "html.parser")

    spells:  List[Dict] = []
    issues:  List[Dict] = []
    rows = soup.find_all("tr")

    for i, row in enumerate(rows):
        tds = row.find_all("td")
        if len(tds) < 2:
            continue
        source = clean_text(tds[0].get_text())
        name   = clean_text(tds[1].get_text())
        if not source or not name:
            continue
        # 跳过表头行（表头文字不像来源缩写）
        if "来源" in source or "书籍" in source or source in ("来源", "书名", "出处"):
            continue
        # 来源缩写通常是 2–10 个字符的字母数字串
        if re.fullmatch(r"[A-Za-z0-9\-]{1,12}", source):
            spells.append({"name": name, "source_book": source})
        else:
            issues.append({"type": "skipped_row", "source": source, "name": name})

    return spells, issues


# ═══════════════════════════════════════════════════════════════════════════════
#  每本书的"期望法术数"估算器（QA 步骤1 核心）
# ═══════════════════════════════════════════════════════════════════════════════

def _estimate_expected_ucum(html_path: Path) -> List[str]:
    """
    UC/UM 期望法术名估算：
      主规则：识别"中文 (English)"模式（含已有颜色/加粗的法术名）
      回退：找 CN+(EN) 行，且后几行出现"学派"和"等级"字段
    """
    try:
        content = html_path.read_text(encoding="gb18030")
    except Exception:
        content = html_path.read_text(encoding="utf-8", errors="ignore")

    # 替换 BR 为换行，便于行扫描
    text_with_newlines = re.sub(r"<[Bb][Rr][^>]*/?>\s*", "\n", content)
    soup = BeautifulSoup(text_with_newlines, "html.parser")

    line_pool: List[str] = []
    for p in soup.find_all("p"):
        for seg in re.split(r"[\r\n]+", p.get_text()):
            t = clean_text(seg)
            if t:
                line_pool.append(t)

    # 法术名模式：中文开头，含 (English Name) 括号，长度 ≤ 50
    name_re = re.compile(
        r"^[\u4e00-\u9fff].{0,30}[（(][A-Za-z][A-Za-z\s\-',/0-9]{1,}[）)]$"
    )
    candidates: List[str] = []
    for i, line in enumerate(line_pool):
        if not name_re.match(line):
            continue
        # 不是字段标签
        if any(line.startswith(kw) for kw in LABEL_MAP):
            continue
        # 后几行必须有学派或等级（验证是真正的法术名，排除章节标题）
        window = " ".join(line_pool[i + 1: i + 6])
        if "学派" in window or "等级" in window or "环位" in window:
            candidates.append(line)

    # 去重
    seen = set()
    result = []
    for n in candidates:
        if n not in seen:
            seen.add(n)
            result.append(n)
    return result


def _estimate_expected_ui(html_path: Path) -> List[str]:
    """
    UI 期望法术名估算：
      在 BR 切行后，找含中文 + （English）且后续有学派/等级字段的行。
    """
    try:
        content = html_path.read_text(encoding="gb18030")
    except Exception:
        content = html_path.read_text(encoding="utf-8", errors="ignore")

    # 替换 BR 为换行
    content2 = re.sub(r"<[Bb][Rr][^>]*/?>\s*", "\n", content)
    soup = BeautifulSoup(content2, "html.parser")

    line_pool: List[str] = []
    for p in soup.find_all("p"):
        for seg in re.split(r"[\r\n]+", p.get_text()):
            t = clean_text(seg)
            if t:
                line_pool.append(t)

    name_re = re.compile(
        r"^[\u4e00-\u9fff].{0,40}[（(][A-Za-z][A-Za-z\s\-',/0-9]{1,}[）)]$"
    )
    candidates: List[str] = []
    seen: set = set()
    for i, line in enumerate(line_pool):
        if not name_re.match(line):
            continue
        if any(line.startswith(kw) for kw in LABEL_MAP):
            continue
        window = " ".join(line_pool[i + 1: i + 8])
        if ("学派" in window and "等级" in window) or ("学派" in window and "施法时间" in window):
            if line not in seen:
                seen.add(line)
                candidates.append(line)
    return candidates


def _estimate_expected_index(html_path: Path) -> int:
    """index 期望数 = 表格数据行数（减去表头）。"""
    try:
        content = html_path.read_text(encoding="gb18030")
    except Exception:
        content = html_path.read_text(encoding="utf-8", errors="ignore")
    soup = BeautifulSoup(content, "html.parser")
    rows = soup.find_all("tr")
    count = 0
    for row in rows:
        tds = row.find_all("td")
        if len(tds) < 2:
            continue
        source = clean_text(tds[0].get_text())
        if re.fullmatch(r"[A-Za-z0-9\-]{1,12}", source):
            count += 1
    return count


# ═══════════════════════════════════════════════════════════════════════════════
#  QA 质量评估
# ═══════════════════════════════════════════════════════════════════════════════

def _bilingual_rate(spells: List[Dict]) -> float:
    """名称同时含中英文的比例。"""
    if not spells:
        return 0.0
    count = sum(
        1 for s in spells
        if re.search(r"[\u4e00-\u9fff]", s.get("name", ""))
        and re.search(r"[A-Za-z]{2,}", s.get("name", ""))
    )
    return round(count / len(spells), 4)


def _field_completeness(spells: List[Dict], fields: Tuple[str, ...]) -> Dict[str, float]:
    """每个字段的填充率。"""
    if not spells:
        return {f: 0.0 for f in fields}
    return {
        f: round(sum(1 for s in spells if s.get(f)) / len(spells), 4)
        for f in fields
    }


def quality_report_ucum(
    book_code: str,
    html_path: Path,
    spells: List[Dict],
    raw_spells: List[Dict],
    issues: List[Dict],
) -> Dict:
    """UC/UM 质量报告（6步 QA）。"""
    expected_names = _estimate_expected_ucum(html_path)
    expected_count = len(expected_names)
    extracted_count = len(spells)

    extracted_name_set = {s.get("name", "").strip() for s in spells}
    missing_candidates = [n for n in expected_names if n not in extracted_name_set]

    # 重复名
    name_counts: Dict[str, int] = {}
    for s in spells:
        n = s.get("name", "")
        name_counts[n] = name_counts.get(n, 0) + 1
    duplicates = [{"name": k, "count": v} for k, v in name_counts.items() if v > 1]

    # 覆盖率
    coverage = (extracted_count / expected_count) if expected_count else (1.0 if extracted_count == 0 else 0.0)

    # 必填字段
    missing_required: Dict = {}
    for f in REQUIRED_FIELDS:
        missing = [s["spell_id"] for s in spells if not s.get(f)]
        missing_required[f] = {"count": len(missing), "samples": missing[:20]}

    # 等级可解析
    level_fail  = [s["spell_id"] for s in spells if s.get("level_raw") and not s.get("level_by_class")]
    # 疑似串条
    merged_susp = [s["spell_id"] for s in spells if s.get("effect") and MERGED_HINT_RE.search(s["effect"])]
    # 短效果
    short_eff   = [s["spell_id"] for s in spells if 0 < len(s.get("effect", "")) < 15]
    # 双语名称率
    bilingual   = _bilingual_rate(spells)

    # 质量分（0~100）
    score = 100.0
    score -= min(40.0, max(0.0, (1.0 - min(coverage, 1.0)) * 50.0))
    score -= min(20.0, len(missing_candidates) * 0.3)
    score -= min(15.0, len(level_fail) * 0.4)
    score -= min(10.0, len(merged_susp) * 0.5)
    score -= min(5.0,  len(short_eff) * 0.1)
    score -= min(10.0, max(0.0, (0.9 - bilingual) * 30.0))
    score = max(0.0, round(score, 2))

    return {
        "book_code":    book_code,
        "source_html":  html_path.name,
        "parser_type":  "ucum_enhanced",
        "quality_check_steps": [
            "步骤1：用专用期望计数器（中英括号模式 + 学派/等级后置验证）识别期望法术数。",
            "步骤2：计算覆盖率=提取数/期望数；列出疑似漏提取条目。",
            "步骤3：校验 name/source_book/school/level_raw/effect 必填字段完整性。",
            "步骤4：检测等级字段（level_by_class）结构化解析失败条目。",
            "步骤5：检测效果字段内含下一条法术头部的疑似串条。",
            "步骤6：统计双语名称率和解析问题，计算综合质量分（0~100）。",
        ],
        "expected_spell_count":       expected_count,
        "extracted_spell_count":      extracted_count,
        "coverage_ratio":             round(coverage, 4),
        "bilingual_name_rate":        bilingual,
        "missing_candidates_count":   len(missing_candidates),
        "missing_candidates_samples": missing_candidates[:50],
        "duplicate_name_count":       len(duplicates),
        "duplicate_name_samples":     duplicates[:20],
        "missing_required_fields":    missing_required,
        "level_parse_fail_count":     len(level_fail),
        "level_parse_fail_samples":   level_fail[:50],
        "merged_entry_suspect_count": len(merged_susp),
        "merged_entry_suspect_samples": merged_susp[:30],
        "short_effect_count":         len(short_eff),
        "short_effect_samples":       short_eff[:30],
        "parser_issue_count":         len(issues),
        "parser_issue_samples":       issues[:30],
        "quality_score":              score,
    }


def quality_report_ui(
    book_code: str,
    html_path: Path,
    spells: List[Dict],
    raw_spells: List[Dict],
    issues: List[Dict],
) -> Dict:
    """UI 质量报告（6步 QA）。"""
    expected_names  = _estimate_expected_ui(html_path)
    expected_count  = len(expected_names)
    extracted_count = len(spells)

    extracted_name_set = {s.get("name", "").strip() for s in spells}
    missing_candidates = [n for n in expected_names if n not in extracted_name_set]

    name_counts: Dict[str, int] = {}
    for s in spells:
        n = s.get("name", "")
        name_counts[n] = name_counts.get(n, 0) + 1
    duplicates = [{"name": k, "count": v} for k, v in name_counts.items() if v > 1]

    coverage = (extracted_count / expected_count) if expected_count else (1.0 if extracted_count == 0 else 0.0)

    missing_required: Dict = {}
    for f in REQUIRED_FIELDS:
        missing = [s["spell_id"] for s in spells if not s.get(f)]
        missing_required[f] = {"count": len(missing), "samples": missing[:20]}

    level_fail  = [s["spell_id"] for s in spells if s.get("level_raw") and not s.get("level_by_class")]
    merged_susp = [s["spell_id"] for s in spells if s.get("effect") and MERGED_HINT_RE.search(s["effect"])]
    short_eff   = [s["spell_id"] for s in spells if 0 < len(s.get("effect", "")) < 15]
    bilingual   = _bilingual_rate(spells)

    score = 100.0
    score -= min(40.0, max(0.0, (1.0 - min(coverage, 1.0)) * 50.0))
    score -= min(20.0, len(missing_candidates) * 0.5)
    score -= min(15.0, len(level_fail) * 0.4)
    score -= min(10.0, len(merged_susp) * 0.5)
    score -= min(5.0,  len(short_eff) * 0.1)
    score -= min(10.0, max(0.0, (0.9 - bilingual) * 30.0))
    score = max(0.0, round(score, 2))

    return {
        "book_code":    book_code,
        "source_html":  html_path.name,
        "parser_type":  "ui_br_split",
        "quality_check_steps": [
            "步骤1：BR切行后用中英括号模式 + 学派/等级后置验证估算期望法术数。",
            "步骤2：计算覆盖率；列出疑似漏提取条目。",
            "步骤3：校验 name/source_book/school/level_raw/effect 必填字段完整性。",
            "步骤4：检测等级字段结构化解析失败。",
            "步骤5：检测疑似串条（效果字段内含法术头部模式）。",
            "步骤6：统计双语名称率和解析问题，计算综合质量分。",
        ],
        "expected_spell_count":         expected_count,
        "extracted_spell_count":        extracted_count,
        "coverage_ratio":               round(coverage, 4),
        "bilingual_name_rate":          bilingual,
        "missing_candidates_count":     len(missing_candidates),
        "missing_candidates_samples":   missing_candidates[:50],
        "duplicate_name_count":         len(duplicates),
        "duplicate_name_samples":       duplicates[:20],
        "missing_required_fields":      missing_required,
        "level_parse_fail_count":       len(level_fail),
        "level_parse_fail_samples":     level_fail[:50],
        "merged_entry_suspect_count":   len(merged_susp),
        "merged_entry_suspect_samples": merged_susp[:30],
        "short_effect_count":           len(short_eff),
        "short_effect_samples":         short_eff[:30],
        "parser_issue_count":           len(issues),
        "parser_issue_samples":         issues[:30],
        "quality_score":                score,
    }


def quality_report_index(
    book_code: str,
    html_path: Path,
    spells: List[Dict],
    issues: List[Dict],
) -> Dict:
    """
    index 质量报告（QA）。

    index 是纯索引文件，无学派/等级等详情字段，因此：
      - 只考核 name + source_book 填充率
      - 覆盖率 = 提取行数 / 表格总行数
    """
    expected_count  = _estimate_expected_index(html_path)
    extracted_count = len(spells)
    coverage = (extracted_count / expected_count) if expected_count else 0.0

    bilingual = _bilingual_rate(spells)

    # 来源缩写多样性
    source_set = {s.get("source_book", "") for s in spells}

    # 名称格式问题（只有中文、只有英文，或太短）
    name_issues = [
        s["spell_id"] for s in spells
        if not re.search(r"[\u4e00-\u9fff]", s.get("name", ""))
        or len(s.get("name", "")) < 2
    ]

    score = 100.0
    score -= min(40.0, max(0.0, (1.0 - min(coverage, 1.0)) * 50.0))
    score -= min(10.0, max(0.0, (0.9 - bilingual) * 20.0))
    score -= min(10.0, len(name_issues) * 0.1)
    score = max(0.0, round(score, 2))

    return {
        "book_code":      book_code,
        "source_html":    html_path.name,
        "parser_type":    "index_table",
        "quality_check_steps": [
            "步骤1：统计 TABLE 数据行数作为期望提取数（排除表头）。",
            "步骤2：计算覆盖率 = 提取行数 / 期望行数。",
            "步骤3：验证 name + source_book 字段填充率（index 无详情字段）。",
            "步骤4：统计双语名称率（中英文同时存在）。",
            "步骤5：检测来源缩写多样性，验证有效来源种类数。",
            "步骤6：汇总计算综合质量分（index 满分侧重覆盖率+名称质量）。",
        ],
        "expected_spell_count":   expected_count,
        "extracted_spell_count":  extracted_count,
        "coverage_ratio":         round(coverage, 4),
        "bilingual_name_rate":    bilingual,
        "distinct_sources_count": len(source_set),
        "distinct_sources":       sorted(source_set)[:50],
        "name_format_issues":     len(name_issues),
        "name_format_issue_samples": name_issues[:30],
        "parser_issue_count":     len(issues),
        "parser_issue_samples":   issues[:30],
        "quality_score":          score,
    }


# ═══════════════════════════════════════════════════════════════════════════════
#  主入口
# ═══════════════════════════════════════════════════════════════════════════════

def main() -> None:
    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    summary = []

    for book_code, filename in BOOKS:
        html_path = SPELL_DIR / filename
        if not html_path.exists():
            print(f"[skip] {filename} 不存在: {html_path}")
            continue

        book_dir = RESULT_DIR / book_code
        book_dir.mkdir(parents=True, exist_ok=True)

        # ── 提取原始数据 ───────────────────────────────────────────────────────
        print(f"\n正在处理 {filename} ...")
        if book_code in ("uc", "um"):
            raw_spells, issues = extract_ucum_spells(html_path, book_code)
        elif book_code == "ui":
            raw_spells, issues = extract_ui_spells(html_path)
        else:  # index
            raw_spells, issues = extract_index_spells(html_path)

        # ── 标准化数据模型 ──────────────────────────────────────────────────────
        model_spells = [normalize_spell(s, book_code, i + 1) for i, s in enumerate(raw_spells)]

        # ── QA 评估 ─────────────────────────────────────────────────────────────
        if book_code in ("uc", "um"):
            qa = quality_report_ucum(book_code, html_path, model_spells, raw_spells, issues)
        elif book_code == "ui":
            qa = quality_report_ui(book_code, html_path, model_spells, raw_spells, issues)
        else:
            qa = quality_report_index(book_code, html_path, model_spells, issues)

        # ── 保存结果 ─────────────────────────────────────────────────────────────
        slug       = f"spells-{book_code}"
        raw_out    = book_dir / f"{slug}.json"
        model_out  = book_dir / f"{slug}-model.json"
        qa_out     = book_dir / f"{slug}-qa.json"

        raw_out.write_text(
            json.dumps(raw_spells, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        model_out.write_text(
            json.dumps(model_spells, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        qa_out.write_text(
            json.dumps(qa, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        print(
            f"  [ok] 提取: {qa['extracted_spell_count']} 条  "
            f"期望: {qa['expected_spell_count']}  "
            f"覆盖率: {qa['coverage_ratio']:.4f}  "
            f"质量分: {qa['quality_score']}"
        )
        print(f"  → {model_out}  |  QA: {qa_out}")

        summary.append({
            "book_code":             book_code,
            "parser_type":           qa["parser_type"],
            "raw_json":              str(raw_out.relative_to(RESULT_DIR.parent)).replace("\\", "/"),
            "model_json":            str(model_out.relative_to(RESULT_DIR.parent)).replace("\\", "/"),
            "qa_json":               str(qa_out.relative_to(RESULT_DIR.parent)).replace("\\", "/"),
            "extracted_spell_count": qa["extracted_spell_count"],
            "expected_spell_count":  qa["expected_spell_count"],
            "coverage_ratio":        qa["coverage_ratio"],
            "quality_score":         qa["quality_score"],
        })

    # 写入汇总
    summary_out = RESULT_DIR / "spells-special-books-summary.json"
    summary_out.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\n全部完成。汇总文件：{summary_out}")


if __name__ == "__main__":
    main()
