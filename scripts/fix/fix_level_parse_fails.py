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
import time
import urllib.parse
import urllib.request
from typing import Dict, List, Tuple


ROOT = Path(__file__).resolve().parents[2]
RESULT = ROOT / "result"

KEY_LEVEL = "\u7b49\u7ea7"
KEY_EFFECT = "\u6548\u679c"

USER_AGENT = "Mozilla/5.0 (PF_RAG level-fix)"

# Chinese class -> AoN English class key
CN_TO_EN = {
    "\u70bc\u91d1\u672f\u58eb": "alchemist",
    "\u53cd\u5723\u9a91\u58eb": "antipaladin",
    "\u5965\u80fd\u5e08": "arcanist",
    "\u541f\u6e38\u8bd7\u4eba": "bard",
    "\u8840\u8109\u72c2\u6012\u8005": "bloodrager",
    "\u7267\u5e08": "cleric",
    "\u5fb7\u9c81\u4f0a": "druid",
    "\u730e\u4eba": "hunter",
    "\u5ba1\u5224\u8005": "inquisitor",
    "\u9b54\u6218\u58eb": "magus",
    "\u901a\u7075\u8005": "medium",
    "\u50ac\u7720\u5e08": "mesmerist",
    "\u79d8\u5b66\u58eb": "occultist",
    "\u5148\u77e5": "oracle",
    "\u5723\u6b66\u58eb": "paladin",
    "\u5f02\u80fd\u8005": "psychic",
    "\u6e38\u4fa0": "ranger",
    "\u8428\u6ee1": "shaman",
    "\u6b4c\u8005": "skald",
    "\u672f\u58eb": "sorcerer",
    "\u5524\u9b42\u5e08": "spiritualist",
    "\u53ec\u5524\u5e08": "summoner",
    "\u6218\u6597\u796d\u53f8": "warpriest",
    "\u5973\u5deb": "witch",
    "\u6cd5\u5e08": "wizard",
}


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def english_name(name: str) -> str:
    if not name:
        return ""
    m = re.search(r"[（(]([^（）()]*)[）)]", name)
    return m.group(1).strip() if m else ""


def normalize_digits(text: str) -> str:
    if not text:
        return ""
    return text.translate(str.maketrans("\uff10\uff11\uff12\uff13\uff14\uff15\uff16\uff17\uff18\uff19", "0123456789"))


def parse_level_pairs(level_text: str) -> Tuple[List[Dict], List[str]]:
    if not level_text:
        return [], []
    text = normalize_digits(level_text)
    text = text.replace("\n", "\uFF0C").replace(";", "\uFF0C").replace(",", "\uFF0C").replace("\u3001", "\uFF0C")
    parts = [p.strip() for p in text.split("\uFF0C") if p.strip()]
    entries: List[Dict] = []
    unparsed: List[str] = []
    for part in parts:
        matches = list(re.finditer(r"([A-Za-z\u4e00-\u9fff/（）()·\-\s]+?)\s*([0-9]{1,2})(?=(?:\s|$))", part))
        if not matches:
            unparsed.append(part)
            continue
        consumed = False
        for m in matches:
            cls = re.sub(r"\s+", " ", m.group(1)).strip()
            if not cls:
                continue
            consumed = True
            if cls.startswith("\u9886\u57df") or cls.endswith(("\u9886\u57df", "\u5b50\u57df")):
                continue
            entries.append({"class": cls, "level": int(m.group(2))})
        if not consumed:
            unparsed.append(part)
    return entries, unparsed


def strip_level_prose_tail(level_text: str) -> str:
    if not level_text:
        return ""
    text = normalize_digits(level_text).strip()
    markers = [
        "\u8be5\u6cd5\u672f",
        "\u8fd9\u4e2a\u6cd5\u672f",
        "\u672c\u6cd5\u672f",
        "\u6b64\u6cd5\u672f",
        "\u4f60",
        "\u5982\u679c",
        "\u53d7\u672f\u8005",
        "\u76ee\u6807",
        "\u751f\u7269",
        "\u5b83",
    ]
    cut = None
    for marker in markers:
        pos = text.find(" " + marker)
        if pos > 0:
            cut = pos if cut is None else min(cut, pos)
    return text[:cut].strip() if cut is not None else text


def pull_level_prefix_from_effect(level_text: str, effect: str) -> Tuple[str, str, bool]:
    """Recover cases where level numbers/class list leaked into effect head."""
    if not level_text or re.search(r"[0-9]", normalize_digits(level_text)):
        return level_text, effect, False
    if not effect:
        return level_text, effect, False

    text = normalize_digits(effect)
    m = re.match(r"^\s*([0-9]{1,2})\s*(?:[\uFF0C,]\s*)?", text)
    if not m:
        return level_text, effect, False

    head_num = m.group(1)
    remaining = text[m.end() :]

    # Keep consuming optional "class + level" chunks if they are packed in front.
    extra_parts: List[str] = []
    chunk = re.compile(r"^\s*([A-Za-z\u4e00-\u9fff/（）()·\-\s]+?)\s*([0-9]{1,2})\s*(?:[\uFF0C,]\s*)?")
    while True:
        m2 = chunk.match(remaining)
        if not m2:
            break
        cls = re.sub(r"\s+", " ", m2.group(1)).strip()
        if not cls or len(cls) > 40:
            break
        extra_parts.append(f"{cls} {m2.group(2)}")
        remaining = remaining[m2.end() :]

    new_level = f"{level_text.strip()} {head_num}"
    if extra_parts:
        new_level += "\uFF0C" + "\uFF0C".join(extra_parts)

    new_effect = remaining.lstrip(" \n\r\t\uFF0C,")
    return new_level, new_effect, True


def request_text(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=45) as resp:
        return resp.read().decode("utf-8", errors="replace")


def fetch_aon_level_map(spell_en_name: str) -> Dict[str, int]:
    url = "https://www.aonprd.com/SpellDisplay.aspx?ItemName=" + urllib.parse.quote_plus(spell_en_name)
    html = request_text(url)
    # Compact lines so "Level ...\nSchool ..." is easy to isolate.
    lines = [ln.strip() for ln in html.splitlines()]
    text = "\n".join(ln for ln in lines if ln)
    # Fallback to tag-stripped text if needed.
    m = re.search(r"Level\s+([^<\n\r]{1,400})", text)
    if not m:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "html.parser")
        plain = "\n".join(t.strip() for t in soup.get_text("\n").splitlines() if t.strip())
        m = re.search(r"Level\s+([^\n\r]{1,400})", plain)
    if not m:
        return {}

    level_line = m.group(1)
    out: Dict[str, int] = {}
    for part in [p.strip() for p in level_line.split(",") if p.strip()]:
        m2 = re.match(r"([A-Za-z/\-\s]+?)\s*([0-9]{1,2})$", part)
        if not m2:
            continue
        classes = [c.strip().lower() for c in m2.group(1).split("/") if c.strip()]
        lv = int(m2.group(2))
        for c in classes:
            out[c] = lv
    return out


def inject_level_from_aon(level_text: str, spell_en_name: str) -> str:
    if re.search(r"[0-9]", normalize_digits(level_text)):
        return level_text
    cls = level_text.strip()
    en_key = CN_TO_EN.get(cls)
    if not en_key:
        return level_text
    spell_candidates = [spell_en_name.strip()]
    # Fix local OCR spacing issues, e.g. "Wall ofEctoplasm" -> "Wall of Ectoplasm".
    spell_candidates.append(re.sub(r"([a-z])([A-Z])", r"\1 \2", spell_en_name.strip()))
    spell_candidates = [c for c in dict.fromkeys(spell_candidates) if c]
    level_map = {}
    for cand in spell_candidates:
        try:
            level_map = fetch_aon_level_map(cand)
        except Exception:
            level_map = {}
        if level_map:
            break
    if not level_map:
        return level_text
    lv = level_map.get(en_key)
    if lv is None:
        return level_text
    time.sleep(0.2)
    return f"{cls} {lv}"


def fix_book(book: str) -> Dict[str, int]:
    model_path = RESULT / book / f"spells-{book}-model.json"
    raw_path = RESULT / book / f"spells-{book}.json"
    qa_path = RESULT / book / f"spells-{book}-qa.json"

    model = load_json(model_path)
    raw = load_json(raw_path)
    qa = load_json(qa_path)

    fail_ids = set(qa.get("level_parse_fail_samples", []))
    fixed_rows = 0
    aon_backfilled = 0

    for idx, (m, r) in enumerate(zip(model, raw)):
        if m.get("spell_id") not in fail_ids:
            continue

        old_level = (m.get("level_raw") or "").strip()
        old_effect = (r.get(KEY_EFFECT) or "").strip()

        level = strip_level_prose_tail(old_level)
        effect = old_effect

        level, effect, moved = pull_level_prefix_from_effect(level, effect)
        if moved:
            fixed_rows += 1

        if not re.search(r"[0-9]", normalize_digits(level)):
            new_level = inject_level_from_aon(level, english_name(m.get("name", "")))
            if new_level != level:
                level = new_level
                aon_backfilled += 1
                fixed_rows += 1

        entries, unparsed = parse_level_pairs(level)

        # Update raw + model in-place.
        r[KEY_LEVEL] = level
        r[KEY_EFFECT] = effect

        m["level_raw"] = level
        m["level_by_class"] = entries
        m["level_unparsed"] = unparsed
        m["effect"] = effect
        if isinstance(m.get("raw_fields"), dict):
            m["raw_fields"][KEY_LEVEL] = level
            m["raw_fields"][KEY_EFFECT] = effect

        # Keep raw/model alignment explicit.
        raw[idx] = r
        model[idx] = m

    # Recalculate fail list for this book.
    new_fails = [
        row.get("spell_id")
        for row in model
        if (row.get("level_raw") or "").strip() and not row.get("level_by_class")
    ]
    qa["level_parse_fail_count"] = len(new_fails)
    qa["level_parse_fail_samples"] = new_fails[:20]

    write_json(raw_path, raw)
    write_json(model_path, model)
    write_json(qa_path, qa)

    return {
        "fixed_rows": fixed_rows,
        "aon_backfilled": aon_backfilled,
        "remaining_level_parse_fail": len(new_fails),
    }


def main() -> None:
    books = ["crb", "oa", "um", "acg"]
    summary = {}
    for book in books:
        summary[book] = fix_book(book)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()