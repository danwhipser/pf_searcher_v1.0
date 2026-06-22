#!/usr/bin/env python3
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
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import unquote

import requests
from bs4 import BeautifulSoup


ROOT = Path(__file__).resolve().parents[2]
RESULT_DIR = ROOT / "result" / "feats"
TOC_PATH = ROOT / "result" / "toc.json"
VIEWER_PATH = ROOT / "result" / "Pathfinder-v2.14-SC-viewer-embedded.html"

AON_FEATS_URL = "https://www.aonprd.com/Feats.aspx"


def normalize_ws(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()


def normalize_key(text: str) -> str:
    text = (text or "").lower()
    text = text.replace("`", "'")
    text = text.replace("\u2019", "'").replace("\u2018", "'")
    return re.sub(r"[^a-z0-9]+", "", text)


def clean_en_name(text: str) -> str:
    text = normalize_ws(text).strip("*")
    text = text.replace("`", "'")
    text = text.replace("\u2019", "'").replace("\u2018", "'")
    text = re.sub(r"\s*\*\s*", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


EN_NAME_CORRECTIONS: dict[str, str] = {
    normalize_key("Attuned to the Wil"): "Attuned to the Wild",
    normalize_key("Believer's Hand"): "Believer's Hands",
    normalize_key("Believer`s Hand"): "Believer's Hands",
    normalize_key("Believer`s Boon"): "Believer's Boon",
    normalize_key("Greater Skald`s Vigor"): "Greater Skald's Vigor",
    normalize_key("Skald`s Vigor"): "Skald's Vigor",
    normalize_key("Weapon of Chosen"): "Weapon of the Chosen",
    normalize_key("Greater Weapon of Chosen"): "Greater Weapon of the Chosen",
    normalize_key("Improved Weapon of Chosen"): "Improved Weapon of the Chosen",
    normalize_key("Lay on the Land"): "Lay of the Land",
    normalize_key("Talent Magician"): "Talented Magician",
    normalize_key("Improve Lookout"): "Improved Lookout",
    normalize_key("Lighting Rager"): "Lightning Rager",
    normalize_key("hindrance dismissal"): "Hinderance Dismissal",
    normalize_key("weapen evoker mastery"): "Weapon Evoker Mastery",
    normalize_key("Durnken Brawler"): "Drunken Brawler",
    normalize_key("Spike Destroyer"): "Spiked Destroyer",
    normalize_key("Disciple of Sword"): "Disciple of the Sword",
    normalize_key("Oath of Unbound"): "Oath of the Unbound",
    normalize_key("Posioner`s Channel"): "Poisoner's Channel",
    normalize_key("Warining Shot"): "Warning Shot",
    normalize_key("Extra Arcane Poll"): "Extra Arcane Pool",
    normalize_key("Eye of Judgment"): "Eyes of Judgment",
    normalize_key("Hauted Gnome Shroud"): "Haunted Gnome Shroud",
    normalize_key("Instant Judgement"): "Instant Judgment",
    normalize_key("s Path"): "Exile's Path",
    normalize_key("s Blessing"): "Destroyer's Blessing",
    normalize_key("Mother"): "Mother's Gift",
    normalize_key("s Knack"): "Slayer's Knack",
    normalize_key("Trapper"): "Trapper's Setup",
}


def canonicalize_en_name(text: str) -> str:
    name = clean_en_name(text)
    if not name:
        return ""
    name = re.sub(r"\s*[\(\[]\s*$", "", name).strip()
    corrected = EN_NAME_CORRECTIONS.get(normalize_key(name))
    return corrected or name


def split_feat_name(name_raw: str) -> tuple[str, str]:
    """Return (name_en, name_cn). Either side may be empty."""
    text = normalize_ws(name_raw).strip("*")
    text = text.replace("\u2019", "'").replace("\u2018", "'")
    text = re.sub(r"\s*\*\s*", " ", text)

    if not text:
        return "", ""

    has_cn = bool(re.search(r"[\u4e00-\u9fff]", text))
    has_en = bool(re.search(r"[A-Za-z]", text))

    if has_en and not has_cn:
        return canonicalize_en_name(text), ""
    if has_cn and not has_en:
        return "", normalize_ws(text)

    m = re.search(r"([\u4e00-\u9fff][^()\uFF08\uFF09]*?)\s*[\(\uFF08]\s*([A-Za-z][^)\uFF09]+)\s*[\)\uFF09]", text)
    if m:
        return canonicalize_en_name(m.group(2)), normalize_ws(m.group(1))

    m = re.match(r"^([A-Za-z][A-Za-z0-9'`!/+&.,:\-\s()]+?)\s+([\u4e00-\u9fff].+)$", text)
    if m:
        return canonicalize_en_name(m.group(1)), normalize_ws(m.group(2))

    m = re.match(
        r"^([\u4e00-\u9fff][\u4e00-\u9fff0-9\s\-\(\)（）]*)\s+([A-Za-z][A-Za-z0-9'`!/+&.,:\-\s()]+)$",
        text,
    )
    if m:
        return canonicalize_en_name(m.group(2)), normalize_ws(m.group(1))

    en_parts = re.findall(r"[A-Za-z][A-Za-z0-9'`!/+&.,:\-\s()]{2,}", text)
    en_parts = [canonicalize_en_name(p) for p in en_parts if canonicalize_en_name(p)]
    name_en = max(en_parts, key=len) if en_parts else ""
    name_cn = normalize_ws(re.sub(r"[A-Za-z0-9'`!/+&.,:\-\s()]+", " ", text))
    return name_en, name_cn


RULE_MARKERS = ("先决条件", "前置条件", "专长效果", "效果", "收益", "Benefit")


def _has_rule_marker_nearby(lines: list[str], idx: int, window: int = 24) -> bool:
    end = min(len(lines), idx + 1 + window)
    for j in range(idx + 1, end):
        if any(m in lines[j] for m in RULE_MARKERS):
            return True
    return False


def _is_meta_line(text: str) -> bool:
    t = normalize_ws(text)
    if not t:
        return True
    if t.startswith(("http://", "https://")):
        return True
    if any(k in t for k in ("译者", "出自《", "出自《", "《", "》专长", "专长概述")) and len(t) > 18:
        return True
    if t in {"先决条件", "前置条件", "专长效果", "效果", "收益", "正常", "特殊"}:
        return True
    return False


def _is_candidate_name_line(text: str) -> bool:
    t = normalize_ws(text).strip("*†‡ ")
    if not t or len(t) > 80:
        return False
    if _is_meta_line(t):
        return False
    if re.fullmatch(r"[\d+\-.,;:()（）\[\]【】 ]+", t):
        return False
    if re.search(r"^[A-Za-z]+\s+[0-9]+$", t):
        return False
    return bool(re.search(r"[A-Za-z\u4e00-\u9fff]", t))


def _is_obvious_non_feat_en_name(name_en: str) -> bool:
    n = canonicalize_en_name(name_en or "")
    if not n:
        return False
    nk = normalize_key(n)
    if nk in {"leadershipscore", "leadershipmodifiers", "monstercohorts"}:
        return True
    if nk in {"ac", "dc", "hd", "cr", "bab", "cmd"}:
        return True
    if len(n) <= 2:
        return True
    if re.fullmatch(r"\d+[A-Za-z]?", n) or re.fullmatch(r"\d+[A-Za-z]?", nk):
        return True
    if re.fullmatch(r"\d+d\d+", n.lower()):
        return True
    return False


def extract_feats_from_plain_text(html: str, source_local: str, source_path: str) -> list["FeatRow"]:
    soup = BeautifulSoup(html, "html.parser")
    lines = [normalize_ws(x) for x in soup.get_text("\n").splitlines()]
    lines = [x for x in lines if x]

    rows: list[FeatRow] = []
    used_idx: set[int] = set()

    for i, line in enumerate(lines):
        if i in used_idx:
            continue
        if not _is_candidate_name_line(line):
            continue
        if not _has_rule_marker_nearby(lines, i):
            continue

        name_en = ""
        name_cn = ""
        name_raw = line

        # Pattern 1: "中文（English）" or "English 中文"
        en, cn = split_feat_name(line)
        if en or cn:
            name_en, name_cn = en, cn

        # Pattern 2: Chinese line + next English line
        if (not name_en) and re.search(r"[\u4e00-\u9fff]", line):
            if i + 1 < len(lines):
                nxt = lines[i + 1]
                if _is_candidate_name_line(nxt) and re.search(r"[A-Za-z]", nxt):
                    if _has_rule_marker_nearby(lines, i + 1):
                        en2, _ = split_feat_name(nxt)
                        if en2:
                            name_en = en2
                            name_cn = line
                            name_raw = f"{line} ({en2})"
                            used_idx.add(i + 1)

        # Pattern 3: English line + next Chinese line
        if (not name_cn) and re.search(r"[A-Za-z]", line):
            if i + 1 < len(lines):
                nxt = lines[i + 1]
                if _is_candidate_name_line(nxt) and re.search(r"[\u4e00-\u9fff]", nxt):
                    if _has_rule_marker_nearby(lines, i + 1):
                        _, cn2 = split_feat_name(nxt)
                        if cn2 or re.search(r"[\u4e00-\u9fff]", nxt):
                            name_cn = cn2 or nxt
                            name_en = name_en or canonicalize_en_name(line)
                            name_raw = f"{name_en} {name_cn}".strip()
                            used_idx.add(i + 1)

        # Final sanitation
        name_en = canonicalize_en_name(name_en)
        name_cn = normalize_ws(name_cn)
        if not name_en and not name_cn:
            continue
        # Plain-text fallback should keep feat titles with recoverable English names only.
        if not name_en:
            continue
        if _is_obvious_non_feat_en_name(name_en):
            continue
        # Filter obvious non-feat block headings.
        rough = name_en or name_cn
        if rough in {"专长", "专长列表", "专长名称"}:
            continue

        rows.append(
            FeatRow(
                name_raw=name_raw,
                name_en=name_en,
                name_cn=name_cn,
                source_local=source_local,
                source_path=source_path,
                table_index=-1,
                row_index=i,
            )
        )

    return rows


def extract_feats_for_page_1368(html: str, source_local: str, source_path: str) -> list["FeatRow"]:
    """Special parser for 内海种族（page_1368）where feat names are split across lines."""
    soup = BeautifulSoup(html, "html.parser")
    lines = [normalize_ws(x) for x in soup.get_text("\n").splitlines()]
    lines = [x for x in lines if x]

    rows: list[FeatRow] = []
    seen: set[str] = set()

    skip_tokens = ("出自", "先决条件", "前置条件", "专长效果", "效果", "收益", "译者", "PFS修订")

    i = 0
    while i < len(lines):
        line = lines[i]
        if any(tok in line for tok in skip_tokens):
            i += 1
            continue
        if len(line) > 80:
            i += 1
            continue
        if not re.search(r"[\u4e00-\u9fff]", line):
            i += 1
            continue

        candidate = line
        # join following lines when parentheses are split across lines
        if ("（" in candidate and "）" not in candidate) or ("(" in candidate and ")" not in candidate):
            j = i + 1
            while j < len(lines) and len(candidate) < 140:
                nxt = lines[j]
                if any(tok in nxt for tok in skip_tokens):
                    break
                candidate = f"{candidate} {nxt}"
                if ("）" in candidate) or (")" in candidate):
                    break
                j += 1
            i = j

        # chinese line + standalone english parenthetical next line
        if i + 1 < len(lines) and lines[i + 1].startswith("(") and len(lines[i + 1]) < 60:
            nxt = lines[i + 1]
            if not any(tok in nxt for tok in skip_tokens):
                candidate = f"{candidate} {nxt}"
                i += 1

        en, cn = split_feat_name(candidate)
        if not en and not cn:
            i += 1
            continue
        k = normalize_key(en or cn or candidate)
        if not k or k in seen:
            i += 1
            continue
        seen.add(k)
        rows.append(
            FeatRow(
                name_raw=candidate,
                name_en=en,
                name_cn=cn,
                source_local=source_local,
                source_path=source_path,
                table_index=-2,
                row_index=i,
            )
        )
        i += 1

    return rows


def extract_feats_for_page_527(html: str, source_local: str, source_path: str) -> list["FeatRow"]:
    """Special parser for 瓦瑞西亚，传说诞生之地（page_527） with split EN tokens."""
    soup = BeautifulSoup(html, "html.parser")
    lines = [normalize_ws(x) for x in soup.get_text("\n").splitlines()]
    lines = [x for x in lines if x]

    rows: list[FeatRow] = []
    seen: set[str] = set()

    for i, line in enumerate(lines):
        if "（" not in line and "(" not in line:
            continue
        if "先决条件" in line or "专长效果" in line:
            continue
        if "Varisia, Birthplace of Legends" in line:
            continue
        if len(line) > 60:
            continue

        candidate = line
        j = i + 1
        while j < len(lines) and len(candidate) < 140:
            if "）" in candidate or ")" in candidate:
                break
            nxt = lines[j]
            if any(x in nxt for x in ("先决条件", "专长效果", "效果", "正常情况", "特殊情况")):
                break
            candidate = f"{candidate} {nxt}"
            j += 1

        # need to look like a feat title (short CN prefix + optional EN in parens)
        if not re.search(r"[\u4e00-\u9fff]", candidate):
            continue
        if candidate.startswith(("你", "当你", "这是")):
            continue
        if not _has_rule_marker_nearby(lines, i):
            continue

        en, cn = split_feat_name(candidate)
        if not en and not cn:
            continue
        k = normalize_key(en or cn or candidate)
        if not k or k in seen:
            continue
        seen.add(k)
        rows.append(
            FeatRow(
                name_raw=candidate,
                name_en=en,
                name_cn=cn,
                source_local=source_local,
                source_path=source_path,
                table_index=-3,
                row_index=i,
            )
        )

    return rows


def extract_feats_for_page_623_624(html: str, source_local: str, source_path: str) -> list["FeatRow"]:
    """Special parser for Mythic Adventures feat pages (page_623/page_624)."""
    soup = BeautifulSoup(html, "html.parser")
    lines = [normalize_ws(x) for x in soup.get_text("\n").splitlines()]
    lines = [x for x in lines if x]

    pat_end = re.compile(r"\((Mythic|Metamagic)\)\s*$", re.I)
    pat_ascii = re.compile(r"^[A-Za-z][A-Za-z0-9'’+\-/,&.: ]{0,70}$")
    stop_tokens = {"CRB", "APG", "UM", "UC", "M", "Feat", "Feats", "Type", "Name"}

    def prev_ascii_tokens(idx: int, max_parts: int = 4) -> list[str]:
        parts: list[str] = []
        j = idx - 1
        while j >= 0 and len(parts) < max_parts:
            y = lines[j]
            if re.search(r"[\u4e00-\u9fff]", y):
                break
            if len(y) > 70 or (not re.search(r"[A-Za-z]", y)):
                break
            if y in stop_tokens:
                break
            if pat_ascii.match(y):
                parts.append(y)
                j -= 1
                continue
            break
        parts.reverse()
        return parts

    rows: list[FeatRow] = []
    seen: set[str] = set()

    for i, line in enumerate(lines):
        if not pat_end.search(line):
            continue

        name_en = ""
        m = re.match(r"^([A-Za-z][A-Za-z0-9'’+\-/,&.: ]+?)\s*\((Mythic|Metamagic)\)\s*$", line)
        if m:
            name_en = clean_en_name(m.group(1))
            # Handle split tokens like "Lucky" + "Surge (Mythic)".
            if len(name_en.split()) == 1:
                prev = prev_ascii_tokens(i, max_parts=2)
                if prev:
                    name_en = clean_en_name(" ".join(prev + [name_en]))
        else:
            prev = prev_ascii_tokens(i, max_parts=4)
            if prev:
                name_en = clean_en_name(" ".join(prev))

        if not name_en:
            continue

        k = normalize_key(name_en)
        if not k or k in seen:
            continue
        seen.add(k)
        rows.append(
            FeatRow(
                name_raw=f"{name_en} (Mythic)",
                name_en=name_en,
                name_cn="",
                source_local=source_local,
                source_path=source_path,
                table_index=-4,
                row_index=i,
            )
        )

    # Non-tagged feats present in MA compact table.
    all_text = "\n".join(lines)
    for name_en in ("Marked for Glory", "Mythic Companion"):
        if name_en not in all_text:
            continue
        k = normalize_key(name_en)
        if not k or k in seen:
            continue
        seen.add(k)
        rows.append(
            FeatRow(
                name_raw=name_en,
                name_en=name_en,
                name_cn="",
                source_local=source_local,
                source_path=source_path,
                table_index=-4,
                row_index=0,
            )
        )

    return rows


def _join_english_split(lines: list[str], idx: int) -> str:
    cur = lines[idx]
    nxt = lines[idx + 1] if idx + 1 < len(lines) else ""
    if not nxt:
        return cur
    if re.fullmatch(r"[()（）\[\]\s]*[A-Za-z][A-Za-z'’\-\s]{1,40}[()（）\[\]\s]*", nxt) or re.fullmatch(
        r"[()（）\[\]\s]*[a-z][A-Za-z'’\-\s]{1,40}[()（）\[\]\s]*", nxt
    ):
        return f"{cur} {nxt}".strip()
    return cur


def extract_feats_for_page_1089(html: str, source_local: str, source_path: str) -> list["FeatRow"]:
    """Armor Master's Handbook feat page with CN+EN titles."""
    soup = BeautifulSoup(html, "html.parser")
    lines = [normalize_ws(x) for x in soup.get_text("\n").splitlines()]
    lines = [x for x in lines if x]

    # Keep this strict to avoid body-text noise.
    feat_names = {
        "Armor Focus",
        "Improved Armor Focus",
        "Armor Material Expertise",
        "Armor Material Mastery",
        "Cushioning Armor",
        "Greater Ironclad Reactions",
        "Imposing Bearing",
        "Intense Blows",
        "Ironclad Reactions",
        "Knocking Blows",
        "Poised Bearing",
        "Secured Armor",
        "Sprightly Armor",
    }
    key_to_name = {normalize_key(n): n for n in feat_names}

    rows: list[FeatRow] = []
    seen: set[str] = set()
    for i in range(len(lines)):
        line = _join_english_split(lines, i)
        if re.search(r"[A-Za-z]$", line) and i + 1 < len(lines):
            m_next = re.match(r"^([a-z][A-Za-z'’\-]+)", lines[i + 1])
            if m_next:
                line = f"{line} {m_next.group(1)}"
        for m in re.finditer(r"[A-Z][A-Za-z'’\-\s]{2,45}", line):
            cand = clean_en_name(m.group(0))
            k = normalize_key(cand)
            if not k or k not in key_to_name:
                continue
            if k in seen:
                continue
            seen.add(k)
            rows.append(
                FeatRow(
                    name_raw=f"{key_to_name[k]} (Armor Mastery)",
                    name_en=key_to_name[k],
                    name_cn="",
                    source_local=source_local,
                    source_path=source_path,
                    table_index=-5,
                    row_index=i,
                )
            )
    return rows


def extract_feats_for_page_1184(html: str, source_local: str, source_path: str) -> list["FeatRow"]:
    """Armor Trick page: extract top-level feat names only."""
    soup = BeautifulSoup(html, "html.parser")
    lines = [normalize_ws(x) for x in soup.get_text("\n").splitlines()]
    lines = [x for x in lines if x]

    feat_names = {
        "Armor Trick",
        "Heavy Armor Tricks",
        "Light Armor Tricks",
        "Medium Armor Tricks",
    }
    key_to_name = {normalize_key(n): n for n in feat_names}

    rows: list[FeatRow] = []
    seen: set[str] = set()
    for i, line in enumerate(lines):
        for m in re.finditer(r"[A-Z][A-Za-z'’\-\s]{2,45}", line):
            cand = clean_en_name(m.group(0))
            k = normalize_key(cand)
            if not k or k not in key_to_name:
                continue
            if k in seen:
                continue
            seen.add(k)
            rows.append(
                FeatRow(
                    name_raw=f"{key_to_name[k]}",
                    name_en=key_to_name[k],
                    name_cn="",
                    source_local=source_local,
                    source_path=source_path,
                    table_index=-6,
                    row_index=i,
                )
            )
    return rows


def extract_feats_for_feat11(html: str, source_local: str, source_path: str) -> list["FeatRow"]:
    """Special parser for 专长11.htm where feat blocks are plain text."""
    soup = BeautifulSoup(html, "html.parser")
    lines = [normalize_ws(x) for x in soup.get_text("\n").splitlines()]
    lines = [x for x in lines if x]

    stop_tokens = (
        "出自",
        "出处",
        "先决条件",
        "前置条件",
        "专长效果",
        "效果",
        "特殊",
        "PFS",
        "pg.",
        "Ultimate Wilderness",
    )

    rows: list[FeatRow] = []
    seen: set[str] = set()
    bad_names = {"Ultimate", "Wilderness", "PFS", "Combat"}
    for i in range(len(lines)):
        line = _join_english_split(lines, i)
        if any(t in line for t in stop_tokens):
            continue
        # Prefer lines with explicit CN+EN feat pattern.
        if not re.search(r"[\u4e00-\u9fff]", line):
            continue
        en_parts = re.findall(r"[A-Z][A-Za-z'’\-\s]{2,50}", line)
        if not en_parts:
            continue
        cand = clean_en_name(max(en_parts, key=len))
        cand = cand.strip("()（）[]")
        if len(cand) < 4:
            continue
        if cand in bad_names:
            continue
        # Nearby should look like a feat block, not prose.
        if not _has_rule_marker_nearby(lines, i):
            continue
        k = normalize_key(cand)
        if not k or k in seen:
            continue
        seen.add(k)
        rows.append(
            FeatRow(
                name_raw=line,
                name_en=cand,
                name_cn="",
                source_local=source_local,
                source_path=source_path,
                table_index=-7,
                row_index=i,
            )
        )
    return rows


def extract_feats_for_page_1499(html: str, source_local: str, source_path: str) -> list["FeatRow"]:
    """Chronicles page: only keep explicit feat-like entries."""
    soup = BeautifulSoup(html, "html.parser")
    lines = [normalize_ws(x) for x in soup.get_text("\n").splitlines()]
    lines = [x for x in lines if x]

    allow = {
        "Concentrated Fire",
        "Coordinated Blast",
        "Death Roll",
        "Elemental Strike",
        "Eclipse Strike",
        "Kinslayer",
    }
    key_to_name = {normalize_key(n): n for n in allow}

    rows: list[FeatRow] = []
    seen: set[str] = set()
    for i, line in enumerate(lines):
        for m in re.finditer(r"[A-Z][A-Za-z'’\-\s]{2,45}", line):
            cand = clean_en_name(m.group(0))
            k = normalize_key(cand)
            if not k or k not in key_to_name:
                continue
            if k in seen:
                continue
            seen.add(k)
            rows.append(
                FeatRow(
                    name_raw=key_to_name[k],
                    name_en=key_to_name[k],
                    name_cn="",
                    source_local=source_local,
                    source_path=source_path,
                    table_index=-8,
                    row_index=i,
                )
            )
    return rows


PAGE_MANUAL_CN_TO_EN: dict[str, dict[str, str]] = {
    # 荒野英雄
    "page_694.html": {
        "大地魔法": "Earth Magic",
        "精类表演": "Fey Performance",
        "仇灾魔法": "Foebane Magic",
        "超自然追踪者": "Supernatural Tracker",
        "青翠法术": "Verdant Spell",
    },
    # 异能奥秘（当前页面对应 Occult Mysteries 相关专长）
    "page_744.html": {
        "算术占卜": "Arithmancy",
        "神圣几何学": "Sacred Geometry",
        "算术思维": "Calculating Mind",
        "痛苦仪典": "Agonizing Obedience",
    },
    # 内海种族
    "page_1368.html": {
        "上下合围": "Blades Above and Below",
        "密集阻击": "Barrage of Styles",
        "效死输忠": "Loyal to the Death",
        "纯种": "True Breed",
        "狠辣借机": "Ruthless Opportunist",
    },
    # 信仰与哲学
    "page_673.html": {
        "冥想大师": "Meditation Master",
        "战斗冥想": "Combat Meditation",
        "身体制御": "Body Control",
        "随风而变": "Bend with the Wind",
        "身体掌握": "Body Mastery",
        "冥想专注": "Meditative Concentration",
        "完美警觉": "Perfect Awareness",
        "完美集中": "Perfect Center",
        "固有时制御": "Slow Time",
        "自然之魂": "Nature Soul",
        "动物盟友": "Animal Ally",
        "密语解码": "Druidic Decoder",
        "动物之友": "Friend to Animals",
        "季节之眼": "Weather Eye",
        "万神祝福": "Pantheistic Blessing",
        "神力抵抗": "Divine Defiance",
        "无信防护": "Atheist Abjurations",
        "神力斥责者": "Divine Denouncer",
        "聚焦无信": "Focused Disbelief",
        "偶像破坏者": "Iconoclast",
        "怀疑之种": "Seeds of Doubt",
    },
}


def extract_feats_from_manual_map(
    html: str,
    source_local: str,
    source_path: str,
) -> list["FeatRow"]:
    local_key = source_local.lower()
    mapping = PAGE_MANUAL_CN_TO_EN.get(local_key)
    if not mapping:
        return []

    soup = BeautifulSoup(html, "html.parser")
    lines = [normalize_ws(x) for x in soup.get_text("\n").splitlines()]
    lines = [x for x in lines if x]

    rows: list[FeatRow] = []
    seen: set[str] = set()
    for idx, line in enumerate(lines):
        for cn_name, en_name in mapping.items():
            if cn_name not in line:
                continue
            # only treat as feat title when nearby has rules markers
            if not _has_rule_marker_nearby(lines, idx):
                continue
            key = normalize_key(en_name)
            if key in seen:
                continue
            seen.add(key)
            rows.append(
                FeatRow(
                    name_raw=f"{cn_name} ({en_name})",
                    name_en=en_name,
                    name_cn=cn_name,
                    source_local=source_local,
                    source_path=source_path,
                    table_index=-9,
                    row_index=idx,
                )
            )
    return rows


def load_toc(path: Path) -> list[dict[str, Any]]:
    return json.loads(path.read_text(encoding="utf-8"))


def find_top_level_node(nodes: list[dict[str, Any]], title: str) -> dict[str, Any]:
    for node in nodes:
        if normalize_ws(str(node.get("title", ""))) == title:
            return node
    raise ValueError(f"Top-level TOC node not found: {title}")


def collect_local_pages(node: dict[str, Any]) -> list[tuple[str, str]]:
    """Return list of (local_page, toc_path_text)."""
    out: list[tuple[str, str]] = []

    def walk(cur: dict[str, Any], path_parts: list[str]) -> None:
        title = normalize_ws(str(cur.get("title", "")))
        path_next = path_parts + ([title] if title else [])
        local = normalize_ws(str(cur.get("local", "")))
        if local:
            out.append((local, " / ".join(path_next)))
        for child in cur.get("children", []) or []:
            walk(child, path_next)

    walk(node, [])
    seen = set()
    unique: list[tuple[str, str]] = []
    for local, toc_path in out:
        key = local.lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append((local, toc_path))
    return unique


def load_embedded_pages(path: Path) -> dict[str, str]:
    text = path.read_text(encoding="utf-8", errors="ignore")
    m = re.search(
        r'<script id="pages-data" type="application/json">(.*?)</script>',
        text,
        re.S,
    )
    if not m:
        raise ValueError("pages-data JSON block not found in embedded viewer")
    raw = m.group(1).replace("<\\/", "</")
    return json.loads(raw)


def build_page_index(pages: dict[str, str]) -> dict[str, str]:
    index: dict[str, str] = {}
    for key in pages:
        index[unquote(key).lower()] = key
    return index


def find_name_col(header_cells: list[str]) -> int | None:
    for idx, text in enumerate(header_cells):
        if "专长名称" in text:
            return idx
    for idx, text in enumerate(header_cells):
        if normalize_ws(text).lower() == "name":
            return idx
    # ARG/部分书页会使用“种族专长/一般专长/团队专长”等表头而不是“专长名称”。
    for idx, text in enumerate(header_cells):
        t = normalize_ws(text)
        if "专长" in t and ("效果" not in t) and ("先决" not in t):
            return idx
    return None


def _find_col_by_keywords(header_cells: list[str], keywords: list[str]) -> int | None:
    lowered = [normalize_ws(x).lower() for x in header_cells]
    for i, t in enumerate(lowered):
        for kw in keywords:
            if kw in t:
                return i
    return None


def parse_table_matrix(table) -> list[list[str]]:
    """Expand rowspan/colspan into a rectangular-ish matrix of cell texts."""
    matrix: list[list[str]] = []
    pending: dict[int, tuple[str, int]] = {}

    for tr in table.find_all("tr"):
        row: list[str] = []
        col = 0

        def fill_pending_until(stop_col: int | None = None) -> None:
            nonlocal col
            while col in pending and (stop_col is None or col < stop_col):
                text, left = pending[col]
                row.append(text)
                if left <= 1:
                    pending.pop(col, None)
                else:
                    pending[col] = (text, left - 1)
                col += 1

        cells = tr.find_all(["th", "td"])
        for cell in cells:
            fill_pending_until()
            text = normalize_ws(cell.get_text(" ", strip=True))
            try:
                rowspan = int(str(cell.get("rowspan", "1")).strip() or "1")
            except Exception:
                rowspan = 1
            try:
                colspan = int(str(cell.get("colspan", "1")).strip() or "1")
            except Exception:
                colspan = 1

            for _ in range(max(1, colspan)):
                row.append(text)
                if rowspan > 1:
                    pending[col] = (text, rowspan - 1)
                col += 1

        fill_pending_until()
        matrix.append(row)

    return matrix


def _looks_like_monster_cohort_table(matrix: list[list[str]]) -> bool:
    if not matrix:
        return False
    # Leadership appendix table on CRB page_195; not a feat-name table.
    probe_rows = matrix[:4]
    probe_text = " ".join(" ".join(r) for r in probe_rows if r)
    if ("怪物部属" in probe_text) or ("Monster Cohorts" in probe_text):
        return True
    return False


@dataclass
class FeatRow:
    name_raw: str
    name_en: str
    name_cn: str
    source_local: str
    source_path: str
    table_index: int
    row_index: int
    prerequisites: str = ""
    benefit_summary: str = ""
    detail_text: str = ""


def extract_feats_from_page(html: str, source_local: str, source_path: str) -> list[FeatRow]:
    soup = BeautifulSoup(html, "html.parser")
    rows: list[FeatRow] = []

    # MA intro page is explanatory text only; skip to avoid noisy pseudo-feats.
    if source_local.lower() == "page_622.html":
        return []

    for table_index, table in enumerate(soup.find_all("table")):
        matrix = parse_table_matrix(table)
        if not matrix:
            continue
        if _looks_like_monster_cohort_table(matrix):
            continue

        header_row_idx: int | None = None
        name_col: int | None = None
        prereq_col: int | None = None
        benefit_col: int | None = None
        for i, cells in enumerate(matrix):
            if not cells:
                continue
            col = find_name_col(cells)
            if col is not None:
                header_row_idx = i
                name_col = col
                prereq_col = _find_col_by_keywords(
                    cells,
                    [
                        "先决条件",
                        "前置条件",
                        "需求",
                        "prerequisite",
                        "prereq",
                    ],
                )
                benefit_col = _find_col_by_keywords(
                    cells,
                    [
                        "专长效果",
                        "效果",
                        "收益",
                        "benefit",
                    ],
                )
                break

        if header_row_idx is None or name_col is None:
            continue

        for row_index, cells in enumerate(matrix[header_row_idx + 1 :], start=header_row_idx + 1):
            if not cells or name_col >= len(cells):
                continue

            raw_name = normalize_ws(cells[name_col]).strip("*†‡ ")
            if not raw_name:
                continue

            # Skip obvious non-name rows.
            if len(raw_name) > 90:
                continue
            if raw_name in {"专长名称", "Name"}:
                continue
            if raw_name.startswith("表：") or raw_name.startswith("注："):
                continue
            if raw_name in {"职业", "种族", "分类", "一般专长", "专长列表"}:
                continue
            has_en = bool(re.search(r"[A-Za-z]", raw_name))
            if (not has_en) and "，" in raw_name:
                continue
            if (not has_en) and re.search(r"\d+\s*级", raw_name):
                continue

            name_en, name_cn = split_feat_name(raw_name)
            prerequisites = ""
            benefit_summary = ""
            detail_text = ""

            if prereq_col is not None and prereq_col < len(cells):
                prerequisites = normalize_ws(cells[prereq_col])
            if benefit_col is not None and benefit_col < len(cells):
                benefit_summary = normalize_ws(cells[benefit_col])

            detail_parts: list[str] = []
            seen_parts: set[str] = set()
            for ci, cv in enumerate(cells):
                if ci == name_col:
                    continue
                t = normalize_ws(cv)
                if not t:
                    continue
                if t in {prerequisites, benefit_summary}:
                    # prerequisites/benefit are already stored separately.
                    continue
                if t in seen_parts:
                    continue
                seen_parts.add(t)
                detail_parts.append(t)
            if detail_parts:
                detail_text = " | ".join(detail_parts)
                # Guard: summary/list tables may leak neighboring feat cells into detail.
                # If the detail looks like a concatenated feat roster, keep it empty.
                if len(detail_text) > 120 and (
                    detail_text.count("*") >= 2
                    or len(re.findall(r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,3}\b", detail_text)) >= 6
                ):
                    detail_text = ""

            rows.append(
                FeatRow(
                    name_raw=raw_name,
                    name_en=name_en,
                    name_cn=name_cn,
                    source_local=source_local,
                    source_path=source_path,
                    table_index=table_index,
                    row_index=row_index,
                    prerequisites=prerequisites,
                    benefit_summary=benefit_summary,
                    detail_text=detail_text,
                )
            )

    # First, use manual page-specific mappings where available.
    manual_rows = extract_feats_from_manual_map(html, source_local, source_path)
    if manual_rows:
        return manual_rows

    # Special-case pages that need custom parsing.
    if source_local.lower() == "page_1368.html":
        special_rows = extract_feats_for_page_1368(html, source_local, source_path)
        if special_rows:
            return special_rows
    if source_local.lower() == "page_527.html":
        special_rows = extract_feats_for_page_527(html, source_local, source_path)
        if special_rows:
            return special_rows
    if source_local.lower() == "page_1089.html":
        special_rows = extract_feats_for_page_1089(html, source_local, source_path)
        if special_rows:
            return special_rows
    if source_local.lower() == "page_1184.html":
        special_rows = extract_feats_for_page_1184(html, source_local, source_path)
        if special_rows:
            return special_rows
    if source_local.lower() == "page_1499.html":
        special_rows = extract_feats_for_page_1499(html, source_local, source_path)
        if special_rows:
            return special_rows
    if source_local.endswith("11.htm") and ("专长" in source_local):
        special_rows = extract_feats_for_feat11(html, source_local, source_path)
        if special_rows:
            return special_rows
    if source_local.lower() in {"page_623.html", "page_624.html"}:
        special_rows = extract_feats_for_page_623_624(html, source_local, source_path)
        if special_rows:
            return special_rows

    # Some books are plain-text feat blocks instead of tables.
    # Use a fallback parser when table parsing yields little or no output.
    if len(rows) < 3:
        text_rows = extract_feats_from_plain_text(html, source_local, source_path)
        if text_rows:
            # merge by match key-ish tuple to avoid duplicates
            seen = {
                normalize_key(canonicalize_en_name(r.name_en) or (r.name_raw or "").strip()): True
                for r in rows
                if normalize_key(canonicalize_en_name(r.name_en) or (r.name_raw or "").strip())
            }
            for tr in text_rows:
                k = normalize_key(canonicalize_en_name(tr.name_en) or (tr.name_raw or "").strip())
                if not k or k in seen:
                    continue
                seen[k] = True
                rows.append(tr)

    return rows


def dedupe_feats(rows: list[FeatRow]) -> list[dict[str, Any]]:
    by_key: dict[str, dict[str, Any]] = {}
    for row in rows:
        base_en = canonicalize_en_name(row.name_en) if row.name_en else ""
        base = base_en or row.name_raw
        key = normalize_key(base)
        if not key:
            continue
        item = by_key.get(key)
        if item is None:
            item = {
                "match_key": key,
                "name_en": base_en,
                "name_cn": row.name_cn,
                "name_raw": row.name_raw,
                "prerequisites": row.prerequisites or "",
                "benefit_summary": row.benefit_summary or "",
                "detail_text": row.detail_text or "",
                "sources": [],
            }
            by_key[key] = item
        src = {
            "local": row.source_local,
            "toc_path": row.source_path,
            "table_index": row.table_index,
            "row_index": row.row_index,
        }
        if src not in item["sources"]:
            item["sources"].append(src)
        # Prefer keeping an English name when available.
        if not item["name_en"] and base_en:
            item["name_en"] = base_en
        if not item["name_cn"] and row.name_cn:
            item["name_cn"] = row.name_cn
        if not item.get("prerequisites") and row.prerequisites:
            item["prerequisites"] = row.prerequisites
        if not item.get("benefit_summary") and row.benefit_summary:
            item["benefit_summary"] = row.benefit_summary
        if not item.get("detail_text") and row.detail_text:
            item["detail_text"] = row.detail_text

    return sorted(by_key.values(), key=lambda x: (x.get("name_en") or x.get("name_raw") or ""))


def fetch_aon_feat_names(url: str) -> list[str]:
    resp = requests.get(url, timeout=60)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    names: list[str] = []
    for row in soup.select("table tr"):
        tds = row.find_all("td")
        if not tds:
            continue
        a = tds[0].find("a")
        if not a:
            continue
        name = clean_en_name(a.get_text(" ", strip=True))
        if name:
            names.append(name)
    # preserve order, unique
    seen = set()
    uniq: list[str] = []
    for n in names:
        k = normalize_key(n)
        if not k or k in seen:
            continue
        seen.add(k)
        uniq.append(n)
    return uniq


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def build_report(chm_feats: list[dict[str, Any]], aon_names: list[str], missing_pages: list[str]) -> dict[str, Any]:
    chm_keys = {f["match_key"] for f in chm_feats if f.get("match_key")}
    aon_items = [{"name_en": n, "match_key": normalize_key(n)} for n in aon_names]
    aon_keys = {x["match_key"] for x in aon_items if x["match_key"]}

    matched = sorted(chm_keys & aon_keys)
    chm_only = sorted(chm_keys - aon_keys)
    aon_only = sorted(aon_keys - chm_keys)

    by_key_chm = {f["match_key"]: f for f in chm_feats}
    by_key_aon = {normalize_key(n): n for n in aon_names}

    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "aon_url": AON_FEATS_URL,
        "counts": {
            "chm_unique_feats": len(chm_keys),
            "aon_unique_feats": len(aon_keys),
            "matched": len(matched),
            "chm_only": len(chm_only),
            "aon_only": len(aon_only),
            "coverage_vs_aon": (len(matched) / len(aon_keys)) if aon_keys else 0.0,
        },
        "missing_pages_in_embedded_viewer": missing_pages,
        "aon_only_missing_in_chm": [
            {"match_key": k, "name_en": by_key_aon.get(k, "")} for k in aon_only
        ],
        "chm_only_not_in_aon": [
            {
                "match_key": k,
                "name_en": by_key_chm.get(k, {}).get("name_en", ""),
                "name_cn": by_key_chm.get(k, {}).get("name_cn", ""),
                "name_raw": by_key_chm.get(k, {}).get("name_raw", ""),
            }
            for k in chm_only
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Extract all feats from CHM embedded viewer and verify against AoN English feats."
    )
    parser.add_argument("--toc", type=Path, default=TOC_PATH)
    parser.add_argument("--viewer", type=Path, default=VIEWER_PATH)
    parser.add_argument("--out-dir", type=Path, default=RESULT_DIR)
    parser.add_argument("--aon-url", default=AON_FEATS_URL)
    args = parser.parse_args()

    toc = load_toc(args.toc)
    feats_root = find_top_level_node(toc, "专长")
    toc_pages = collect_local_pages(feats_root)

    pages = load_embedded_pages(args.viewer)
    page_index = build_page_index(pages)

    rows: list[FeatRow] = []
    missing_pages: list[str] = []

    for local, toc_path in toc_pages:
        key = page_index.get(unquote(local).lower())
        if not key:
            missing_pages.append(local)
            continue
        rows.extend(extract_feats_from_page(pages[key], local, toc_path))

    chm_feats = dedupe_feats(rows)
    aon_names = fetch_aon_feat_names(args.aon_url)

    report = build_report(chm_feats, aon_names, missing_pages)

    out_dir = args.out_dir
    write_json(out_dir / "feats-chm-extracted.json", chm_feats)
    write_json(
        out_dir / "feats-aon-en.json",
        [{"name_en": n, "match_key": normalize_key(n)} for n in aon_names],
    )
    write_json(out_dir / "feat-coverage-report.json", report)

    print("Done.")
    print(f"CHM unique feats: {report['counts']['chm_unique_feats']}")
    print(f"AoN unique feats: {report['counts']['aon_unique_feats']}")
    print(f"Matched: {report['counts']['matched']}")
    print(f"AoN-only: {report['counts']['aon_only']}")
    print(f"CHM-only: {report['counts']['chm_only']}")
    print(f"Coverage vs AoN: {report['counts']['coverage_vs_aon']:.2%}")
    if missing_pages:
        print(f"Missing pages in embedded viewer: {len(missing_pages)}")


if __name__ == "__main__":
    main()