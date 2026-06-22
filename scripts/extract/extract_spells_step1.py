#!/usr/bin/env python3
"""Step 1: 分册抽取法术 + 统一数据模型 + 每册质量检测。"""
from __future__ import annotations

# Allow direct execution from nested scripts/ folders.
import sys
from pathlib import Path
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import json
import re
from typing import Dict, Iterable, List, Optional, Tuple

from bs4 import BeautifulSoup

from scripts.extract.extract_spells_html import extract_spells_from_html, is_spell_name_pattern
from scripts.extract.extract_spells_oa import extract_oa_spells_v12
from scripts.extract.extract_spells_apg import extract_apg_spells
from scripts.books import extract_special_books


BOOK_PATTERN = "Spell *.html"
REQUIRED_FIELDS = ("name", "source_book", "school", "level_raw", "effect")
MERGED_HINT_RE = re.compile(r"[（(][^）)\n]{1,80}[）)]\s*学派[:：]")
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


def iter_spell_files(root: Path) -> Iterable[Path]:
    return sorted(root.glob(BOOK_PATTERN))


def slugify(path: Path) -> str:
    # "Spell ACG" -> ("acg", "spells-acg")
    tail = path.stem.replace("Spell", "", 1).strip()
    code = tail.replace(" ", "-").lower()
    return code, f"spells-{code}"


def normalize_digit(text: str) -> str:
    zh_digits = str.maketrans("０１２３４５６７８９", "0123456789")
    return text.translate(zh_digits)


def parse_level_by_class(level_raw: str) -> Tuple[List[Dict[str, int | str]], List[str]]:
    items: List[Dict[str, int | str]] = []
    unparsed: List[str] = []
    if not level_raw:
        return items, unparsed
    text = normalize_digit(level_raw).replace("\n", " ")
    for part in re.split(r"[，,、；;]", text):
        token = part.strip()
        if not token:
            continue
        m = re.search(r"(.+?)\s+(\d+)\s*$", token)
        if m:
            cls = m.group(1).strip()
            lvl = int(m.group(2))
            items.append({"class": cls, "level": lvl})
        else:
            unparsed.append(token)
    return items, unparsed


def repair_level_from_effect(level_raw: str, effect: str) -> str:
    """
    Repair broken class-level lines where a trailing class level spills into the first
    line of effect, e.g.:
      等级: "通灵者 2，催眠师 3，秘学士 2，异能者"
      效果首行: "3，唤魂师 3"
    """
    if not level_raw:
        return level_raw

    tokens = [t.strip() for t in re.split(r"[，,、；;]", normalize_digit(level_raw).replace("\n", " ")) if t.strip()]
    if not tokens:
        return level_raw

    def has_level(token: str) -> bool:
        return bool(re.search(r"\d+\s*$", token))

    def is_missing_class_token(token: str) -> bool:
        t = token.strip()
        if not t:
            return False
        if has_level(t):
            return False
        if re.fullmatch(r"\d+", t):
            return False
        return bool(re.search(r"[A-Za-z\u4e00-\u9fff]", t))

    effect_first = ""
    for line in (effect or "").splitlines():
        s = line.strip()
        if s:
            effect_first = s
            break

    cont = re.match(r"^\s*(\d+)\s*[，,、]\s*(.+?)\s*$", effect_first)
    if cont:
        leading = cont.group(1)
        tail = cont.group(2)
        for i in range(len(tokens) - 1, -1, -1):
            if is_missing_class_token(tokens[i]):
                tokens[i] = f"{tokens[i]} {leading}"
                break
        if tail:
            tokens.extend([t.strip() for t in re.split(r"[，,、；;]", tail) if t.strip()])

    # Merge standalone numeric token into previous class token.
    merged: List[str] = []
    for token in tokens:
        only_num = re.fullmatch(r"\s*(\d+)\s*", token)
        if only_num and merged and is_missing_class_token(merged[-1]):
            merged[-1] = f"{merged[-1]} {only_num.group(1)}"
            continue
        merged.append(token)
    tokens = merged

    # Fill missing tail levels using nearest known neighbor.
    prev_level: str | None = None
    for i, token in enumerate(tokens):
        m = re.search(r"(\d+)\s*$", token)
        if m:
            prev_level = m.group(1)
            continue
        if prev_level and is_missing_class_token(token):
            tokens[i] = f"{token} {prev_level}"

    next_level: str | None = None
    for i in range(len(tokens) - 1, -1, -1):
        token = tokens[i]
        m = re.search(r"(\d+)\s*$", token)
        if m:
            next_level = m.group(1)
            continue
        if next_level and is_missing_class_token(token):
            tokens[i] = f"{token} {next_level}"

    return "，".join(tokens)


def normalize_en_key(name: str) -> str:
    if not name:
        return ""
    m = re.search(r"[（(]\s*([A-Za-z][^）)]{1,120})\s*[）)]", name)
    en = m.group(1) if m else name
    en = en.lower().replace("’", "'")
    en = re.sub(r"[^a-z0-9]+", " ", en)
    return en.strip()


def should_continue_non_effect_field(field: Optional[str], text: str) -> bool:
    if not field or field == "效果":
        return False
    # Keep strict for noisy HTML rows: some unlabeled body text follows immediately.
    if field in {"法术抗力", "豁免"}:
        return False
    if field == "等级":
        if len(text) > 60:
            return False
        if re.search(r"[（(][A-Za-z]{3,}", text):
            return False
        return bool(re.search(r"\d", text))
    return len(text) < 100


def _backfill_crb_spell_resistance(path: Path, spells: List[Dict]) -> None:
    """Backfill CRB Spell Resistance when a title+field block was skipped."""
    existing_idx: Optional[int] = None
    for i, s in enumerate(spells):
        if normalize_en_key(s.get("name", "")) == "spell resistance":
            existing_idx = i
            break
    try:
        content = path.read_text(encoding="gb18030")
    except Exception:
        content = path.read_text(encoding="utf-8", errors="ignore")
    soup = BeautifulSoup(content, "html.parser")
    lines = [re.sub(r"[\s\xa0\u3000]+", " ", p.get_text()).strip() for p in soup.find_all("p")]
    for i, text in enumerate(lines):
        if "Spell Resistance" not in text:
            continue
        rec: Dict[str, str] = {"name": text.strip(), "来源": "CRB", "效果": ""}
        current_field: Optional[str] = None
        seen_field = False
        for tail in lines[i + 1 : i + 45]:
            t = tail.strip()
            if not t:
                continue
            if seen_field and re.search(r"[（(][A-Za-z][^）)]{1,120}[）)]", t) and not any(
                t.startswith(lb) for lb in LABEL_MAP
            ):
                break
            matched = False
            for label, field in LABEL_MAP.items():
                if t.startswith(label):
                    matched = True
                    seen_field = True
                    val = re.sub(rf"^{re.escape(label)}\s*[:：]?\s*", "", t).strip()
                    if field == "效果":
                        rec["效果"] = (rec.get("效果", "") + "\n" + val).strip()
                    else:
                        rec[field] = val
                    current_field = field
                    break
            if matched:
                continue
            if seen_field:
                if should_continue_non_effect_field(current_field, t):
                    rec[current_field] = (rec.get(current_field, "") + " " + t).strip()
                else:
                    current_field = "效果"
                    rec["效果"] = (rec.get("效果", "") + "\n" + t).strip()
        if rec.get("学派") or rec.get("等级"):
            if existing_idx is None:
                spells.append(rec)
            else:
                existing = spells[existing_idx]
                # Merge parsed fields into existing record and repair empty effect.
                for k, v in rec.items():
                    if k == "name":
                        continue
                    if v and (not existing.get(k)):
                        existing[k] = v
                if not existing.get("效果"):
                    existing["效果"] = rec.get("效果", "")
            return


def _recover_effect_from_title_window(path: Path, en_title: str) -> str:
    """Pick the longest body-like line between title and next English title."""
    try:
        content = path.read_text(encoding="gb18030")
    except Exception:
        content = path.read_text(encoding="utf-8", errors="ignore")
    soup = BeautifulSoup(content, "html.parser")
    lines = [re.sub(r"[\s\xa0\u3000]+", " ", p.get_text()).strip() for p in soup.find_all("p")]
    for i, text in enumerate(lines):
        if en_title not in text:
            continue
        j = i + 1
        while j < len(lines) and j <= i + 70:
            t = lines[j].strip()
            if re.search(r"[（(][A-Za-z][^）)]{1,120}[）)]", t):
                break
            j += 1
        block = [x for x in lines[i + 1 : j] if x and en_title not in x]
        if not block:
            return ""
        block.sort(key=len, reverse=True)
        return block[0]
    return ""


def _repair_crb_spell_resistance_effect(path: Path, spells: List[Dict]) -> None:
    for rec in spells:
        if normalize_en_key(rec.get("name", "")) != "spell resistance":
            continue
        if (rec.get("效果") or "").strip():
            return
        recovered = _recover_effect_from_title_window(path, "Spell Resistance")
        if recovered:
            rec["效果"] = recovered
        return


def normalize_spell(raw: Dict, book_code: str, index: int) -> Dict:
    level_raw = raw.get("等级", "").strip()
    effect = (raw.get("法术效果") or raw.get("效果") or "").strip()
    repaired_level = repair_level_from_effect(level_raw, effect)
    if repaired_level and repaired_level != level_raw:
        raw["等级"] = repaired_level
        level_raw = repaired_level
    level_by_class, level_unparsed = parse_level_by_class(level_raw)
    source = (raw.get("来源") or book_code).strip()
    name = (raw.get("name") or "").strip()
    model = {
        "spell_id": f"{book_code}-{index:04d}",
        "name": name,
        "source_book": source,
        "school": (raw.get("学派") or "").strip(),
        "level_raw": level_raw,
        "level_by_class": level_by_class,
        "cast_time": (raw.get("施法时间") or "").strip(),
        "components": (raw.get("成分") or "").strip(),
        "range": (raw.get("范围") or "").strip(),
        "target": (raw.get("目标") or "").strip(),
        "duration": (raw.get("持续") or "").strip(),
        "save": (raw.get("豁免") or "").strip(),
        "spell_resistance": (raw.get("法术抗力") or "").strip(),
        "effect": effect,
        "level_unparsed": level_unparsed,
        "raw_fields": raw,
    }
    return model


def collect_expected_spell_candidates(html_path: Path) -> List[str]:
    try:
        content = html_path.read_text(encoding="gb18030")
    except Exception:
        content = html_path.read_text(encoding="utf-8", errors="ignore")
    soup = BeautifulSoup(content, "html.parser")
    names: List[str] = []
    for p in soup.find_all("p"):
        text = re.sub(r"[\s\xa0\u3000]+", " ", p.get_text()).strip()
        if not text:
            continue
        is_bold = p.find(["b", "strong"]) is not None
        color = ""
        for tag in [p] + p.find_all(["span", "font"]):
            style = tag.get("style", "")
            c_match = re.search(r"color:\s*(#[0-9a-fA-F]+|[a-zA-Z]+)", style)
            if c_match:
                color = c_match.group(1).lower()
                break
            if tag.get("color"):
                color = tag.get("color").lower()
                break
        if is_spell_name_pattern(text, is_bold, color):
            names.append(text)
    # 回退策略：某些分册没有加粗/颜色标题，用“名称 + 邻近学派/等级标签”推断
    if not names:
        line_pool: List[str] = []
        content_for_lines = content.replace("<BR>", "\n").replace("<br>", "\n").replace("<br/>", "\n")
        soup_lines = BeautifulSoup(content_for_lines, "html.parser")
        for p in soup_lines.find_all("p"):
            for seg in re.split(r"[\r\n]+", p.get_text()):
                t = re.sub(r"[\s\xa0\u3000]+", " ", seg).strip()
                if t:
                    line_pool.append(t)
        name_line_re = re.compile(r"^[^:：]{2,80}[（(][^）)]{2,80}[）)]$")
        for i, line in enumerate(line_pool):
            if not name_line_re.match(line):
                continue
            window = " ".join(line_pool[i + 1 : i + 6])
            if ("学派" in window and "等级" in window) or ("学派" in window and "施法时间" in window):
                names.append(line)

    # 去重并保留顺序
    seen = set()
    ordered = []
    for n in names:
        if n in seen:
            continue
        seen.add(n)
        ordered.append(n)
    return ordered


def quality_report(
    book_code: str,
    source_html: Path,
    spells: List[Dict],
    issues: List[Dict],
) -> Dict:
    expected = collect_expected_spell_candidates(source_html)
    extracted_names = [s.get("name", "").strip() for s in spells if s.get("name")]
    extracted_set = set(extracted_names)
    missing_candidates = [n for n in expected if n not in extracted_set]

    duplicate_counts: Dict[str, int] = {}
    for n in extracted_names:
        duplicate_counts[n] = duplicate_counts.get(n, 0) + 1
    duplicates = [{"name": k, "count": v} for k, v in duplicate_counts.items() if v > 1]

    missing_required = {}
    for f in REQUIRED_FIELDS:
        missing = [s["spell_id"] for s in spells if not s.get(f)]
        missing_required[f] = {"count": len(missing), "samples": missing[:20]}

    level_parse_fail = [s["spell_id"] for s in spells if s.get("level_raw") and not s.get("level_by_class")]
    merged_suspects = [
        s["spell_id"] for s in spells if s.get("effect") and MERGED_HINT_RE.search(s["effect"])
    ]
    short_effect = [s["spell_id"] for s in spells if 0 < len(s.get("effect", "")) < 15]

    expected_count = len(expected)
    extracted_count = len(spells)
    if expected_count == 0:
        coverage = 1.0 if extracted_count == 0 else 0.0
    else:
        coverage = extracted_count / expected_count

    score = 100.0
    score -= min(30.0, max(0.0, (1.0 - min(coverage, 1.0)) * 40.0))
    score -= min(20.0, len(missing_candidates) * 0.3)
    score -= min(20.0, len(level_parse_fail) * 0.4)
    score -= min(20.0, len(merged_suspects) * 0.5)
    score -= min(10.0, len(short_effect) * 0.2)
    score = max(0.0, round(score, 2))

    return {
        "book_code": book_code,
        "source_html": source_html.name,
        "quality_check_steps": [
            "步骤1：从源 HTML 识别期望法术标题候选（主规则+回退规则）。",
            "步骤2：统计提取条目与候选覆盖率，识别疑似漏提取。",
            "步骤3：校验必填字段（name/source_book/school/level_raw/effect）完整性。",
            "步骤4：检查等级字段结构化解析失败条目（level_by_class 为空）。",
            "步骤5：检测疑似串条（effect 内出现下一条法术头部模式）。",
            "步骤6：汇总 parser issues 与样本，计算质量分数。",
        ],
        "expected_spell_candidates_count": expected_count,
        "extracted_spell_count": extracted_count,
        "coverage_ratio": round(coverage, 4),
        "missing_candidates_count": len(missing_candidates),
        "missing_candidates_samples": missing_candidates[:50],
        "duplicate_name_count": len(duplicates),
        "duplicate_name_samples": duplicates[:50],
        "missing_required_fields": missing_required,
        "level_parse_fail_count": len(level_parse_fail),
        "level_parse_fail_samples": level_parse_fail[:50],
        "merged_entry_suspect_count": len(merged_suspects),
        "merged_entry_suspect_samples": merged_suspects[:50],
        "short_effect_count": len(short_effect),
        "short_effect_samples": short_effect[:50],
        "parser_issue_count": len(issues),
        "parser_issue_samples": issues[:50],
        "quality_score": score,
    }


def extract_for_book(path: Path, book_code: str) -> Tuple[List[Dict], List[Dict]]:
    if book_code == "oa":
        # OA 单独使用专用提取器，issues 由 QA 规则补足。
        spells = extract_oa_spells_v12(path)
        return spells, []
    if book_code == "uc":
        return extract_special_books.extract_ucum_spells(path, "uc")
    if book_code == "um":
        return extract_special_books.extract_ucum_spells(path, "um")
    if book_code == "ui":
        return extract_special_books.extract_ui_spells(path)
    if book_code == "index":
        return extract_special_books.extract_index_spells(path)
    if book_code == "apg":
        # APG 后半段名称经常不带加粗/颜色，使用 APG 专用解析器。
        return extract_apg_spells(path)
    spells, issues = extract_spells_from_html(path)
    if book_code == "crb":
        _backfill_crb_spell_resistance(path, spells)
        _repair_crb_spell_resistance_effect(path, spells)
    return spells, issues


def main() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    spell_dir = repo_root / "spell"
    result_dir = repo_root / "result"
    result_dir.mkdir(parents=True, exist_ok=True)

    html_files = list(iter_spell_files(spell_dir))
    if not html_files:
        raise SystemExit(f"未找到 {BOOK_PATTERN}: {spell_dir}")

    summary = []
    for html_path in html_files:
        book_code, slug = slugify(html_path)
        book_dir = result_dir / book_code
        book_dir.mkdir(parents=True, exist_ok=True)

        raw_spells, issues = extract_for_book(html_path, book_code)
        model_spells = [normalize_spell(s, book_code, idx + 1) for idx, s in enumerate(raw_spells)]
        qa = quality_report(book_code, html_path, model_spells, issues)

        raw_out = book_dir / f"{slug}.json"
        model_out = book_dir / f"{slug}-model.json"
        qa_out = book_dir / f"{slug}-qa.json"

        raw_out.write_text(json.dumps(raw_spells, ensure_ascii=False, indent=2), encoding="utf-8")
        model_out.write_text(json.dumps(model_spells, ensure_ascii=False, indent=2), encoding="utf-8")
        qa_out.write_text(json.dumps(qa, ensure_ascii=False, indent=2), encoding="utf-8")

        summary.append(
            {
                "book_code": book_code,
                "raw_json": str(raw_out.relative_to(repo_root)).replace("\\", "/"),
                "model_json": str(model_out.relative_to(repo_root)).replace("\\", "/"),
                "qa_json": str(qa_out.relative_to(repo_root)).replace("\\", "/"),
                "extracted_spell_count": qa["extracted_spell_count"],
                "coverage_ratio": qa["coverage_ratio"],
                "quality_score": qa["quality_score"],
            }
        )
        print(
            f"[ok] {html_path.name} -> {model_out.name} | "
            f"count={qa['extracted_spell_count']} coverage={qa['coverage_ratio']:.4f} score={qa['quality_score']}"
        )

    summary_out = result_dir / "spells-step1-summary.json"
    summary_out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n完成。汇总文件：{summary_out}")


if __name__ == "__main__":
    main()