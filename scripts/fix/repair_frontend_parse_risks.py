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
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
REPORT = ROOT / "result" / "frontend-parse-risk-repair-report.json"
CLASSES = ROOT / "result" / "classes" / "classes-extracted.json"

SPELL_FILES = [
    "result/crb/spells-crb.json",
    "result/acg/spells-acg.json",
    "result/apg/spells-apg.json",
    "result/arg/spells-arg.json",
    "result/uc/spells-uc-model.json",
    "result/um/spells-um-model.json",
]


def U(s: str) -> str:
    return s.encode("ascii").decode("unicode_escape")

LP = U(r"\uff08")
RP = U(r"\uff09")

CN = {
    "effect": U(r"\u6548\u679c"),
    "spell_effect": U(r"\u6cd5\u672f\u6548\u679c"),
    "sr": U(r"\u6cd5\u672f\u6297\u529b"),
    "target": U(r"\u76ee\u6807"),
    "components": U(r"\u6210\u5206"),
    "range": U(r"\u8303\u56f4"),
    "duration": U(r"\u6301\u7eed"),
    "save": U(r"\u8c41\u514d"),
    "level": U(r"\u7b49\u7ea7"),
}

SPELL_FIELD_PAIRS = [
    ("level_raw", CN["level"], "level"),
    ("spell_resistance", CN["sr"], "spell_resistance"),
    ("target", CN["target"], "target"),
    ("components", CN["components"], "components"),
    ("range", CN["range"], "range"),
    ("duration", CN["duration"], "duration"),
    ("save", CN["save"], "save"),
]

PROSE_MARKERS = tuple(U(s) for s in (
    r"\u5f53\u4f60",      # ??
    r"\u4f60",            # ?
    r"\u5982\u679c",      # ??
    r"\u82e5",            # ?
    r"\u5728",            # ?
    r"\u5982\u540c",      # ??
    r"\u9664\u4e86",      # ??
    r"\u8be5\u6cd5\u672f", # ???
    r"\u6b64\u6cd5\u672f", # ???
    r"\u672c\u6cd5\u672f", # ???
    r"\u8fd9\u4e2a\u6cd5\u672f", # ????
    r"\u6cd5\u672f",      # ??
    r"\u76f4\u81f3",      # ??
    r"\u76ee\u6807",      # ??
    r"\u53d7\u672f\u8005", # ???
    r"\u7684",            # ?
    r"\u83b7\u5f97",      # ??
    r"\u5931\u53bb",      # ??
    r"\u751f\u7269",      # ??
))
PUNCT = tuple(U(s) for s in (r"\u3002", r"\uff0c", r"\uff1b", r"\uff1a"))


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def dump(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def has_cjk(value: str) -> bool:
    return bool(re.search(r"[\u4e00-\u9fff]", value or ""))


def append_unique(old: str, extra: str) -> str:
    old = clean(old)
    extra = clean(extra)
    if not extra:
        return old
    if not old:
        return extra
    if extra in old:
        return old
    if old in extra and len(extra) > len(old):
        return extra
    return clean(old + " " + extra)


def split_sr(value: str) -> tuple[str, str]:
    value = clean(value)
    if not value:
        return "", ""
    if value.startswith(U(r"\u53ef\u4ee5")):
        return "", value
    sr_terms = U(r"\u4e0d\u53ef|\u53ef|\u5426|\u65e0|\u89c1\u4e0b\u6587|\u89c1\u540e\u6587")
    m = re.match(rf"^((?:{sr_terms})(?:\s*[?(][^?)]{{1,30}}[?)])?(?:\s*[,?/]\s*(?:{sr_terms})(?:\s*[?(][^?)]{{1,30}}[?)])?)*)\s+(.+)$", value)
    if m and len(m.group(2)) >= 12 and has_cjk(m.group(2)):
        return m.group(1).strip(), m.group(2).strip()
    fallback = split_at_prose(value)
    if fallback[1]:
        return fallback
    return value, ""


def split_components(value: str) -> tuple[str, str]:
    value = clean(value)
    if not value:
        return "", ""
    token_re = U(r"\u8bed\u8a00|\u59ff\u52bf|\u6750\u6599|\u6cd5\u5668|\u5668\u6750|\u795e\u672f\u7126\u70b9|\u60c5\u7eea|\u601d\u60f3|\u8a00\u8bed")
    m = re.match(rf"^((?:(?:{token_re})(?:\s*[?(][^?)]{{1,80}}[?)])?\s*[,?/?]?\s*)+)\s+(.+)$", value)
    if m and len(m.group(2)) >= 18 and has_cjk(m.group(2)):
        return m.group(1).strip(" ,??/"), m.group(2).strip()
    return split_at_prose(value)


def split_duration(value: str) -> tuple[str, str]:
    value = clean(value)
    if not value:
        return "", ""
    for token in (U(r"\u89c1\u4e0b\u6587"), U(r"\u89c1\u540e\u6587")):
        if value.startswith(token + " "):
            return token, value[len(token):].strip()
    if value.startswith(U(r"\u690d\u7269 ")) and U(r"\u4f60") in value:
        return U(r"\u89c1\u4e0b\u6587"), value
    m = re.match(r"^((?:\d+d?\d*(?:[+xX*]\d+)?|\d+|1)[^\s????]{0,34}(?:\s*[?(][^?)]{1,30}[?)])?(?:\s*?\s*[^\s????]{1,30}(?:\s*[?(][^?)]{1,30}[?)])?)?)\s+(.+)$", value)
    if m and len(m.group(2)) >= 18 and has_cjk(m.group(2)):
        return m.group(1).strip(), m.group(2).strip()
    return split_at_prose(value)


def split_at_prose(value: str) -> tuple[str, str]:
    value = clean(value)
    candidates = []
    for marker in PROSE_MARKERS:
        pos = value.find(" " + marker)
        if pos > 0:
            candidates.append(pos)
    # If the whole value is prose, keep no metadata prefix.
    if any(value.startswith(marker) for marker in PROSE_MARKERS) or (len(value) >= 28 and any(p in value for p in PUNCT) and not re.match(r"^[^????]{1,22}\s", value)):
        return "", value
    first_punct = min([pos for pos in (value.find(p) for p in PUNCT) if pos >= 0] or [9999])
    if len(value) >= 28 and first_punct <= 18:
        return "", value
    if candidates:
        pos = min(candidates)
        left, right = value[:pos].strip(), value[pos:].strip()
        if right and len(right) >= 18:
            return left, right
    return value, ""


def split_field(kind: str, value: str) -> tuple[str, str]:
    if kind == "spell_resistance":
        return split_sr(value)
    if kind == "components":
        return split_components(value)
    if kind == "duration":
        return split_duration(value)
    return split_at_prose(value)


def get_effect_key(spell: dict[str, Any]) -> str:
    if "effect" in spell:
        return "effect"
    if CN["effect"] in spell:
        return CN["effect"]
    if CN["spell_effect"] in spell:
        return CN["spell_effect"]
    return "effect"


def repair_spell_record(spell: dict[str, Any], file_rel: str, index: int) -> list[dict[str, Any]]:
    changes = []
    effect_key = get_effect_key(spell)
    raw = spell.get("raw_fields") if isinstance(spell.get("raw_fields"), dict) else None
    for en_key, cn_key, kind in SPELL_FIELD_PAIRS:
        keys = [k for k in (en_key, cn_key) if k in spell]
        if not keys and raw and cn_key in raw:
            keys = []
        for key in keys:
            old = clean(spell.get(key))
            new_value, remainder = split_field(kind, old)
            if remainder and new_value != old:
                spell[key] = new_value
                spell[effect_key] = append_unique(spell.get(effect_key, ""), remainder)
                changes.append({"file": file_rel, "index": index, "field": key, "old": old[:180], "new": new_value, "moved": remainder[:180]})
        if raw and cn_key in raw:
            old = clean(raw.get(cn_key))
            new_value, remainder = split_field(kind, old)
            if remainder and new_value != old:
                raw[cn_key] = new_value
                raw[CN["effect"]] = append_unique(raw.get(CN["effect"], ""), remainder)
                changes.append({"file": file_rel, "index": index, "field": f"raw_fields.{cn_key}", "old": old[:180], "new": new_value, "moved": remainder[:180]})
    return changes


def repair_spells() -> list[dict[str, Any]]:
    changes: list[dict[str, Any]] = []
    for rel in SPELL_FILES:
        path = ROOT / rel
        data = load(path)
        rows = data if isinstance(data, list) else data.get("spells", [])
        for idx, spell in enumerate(rows):
            if isinstance(spell, dict):
                changes.extend(repair_spell_record(spell, rel, idx))
        dump(path, data)
    return changes


def feature_name(feature: dict[str, Any]) -> str:
    return clean(feature.get("name_cn") or feature.get("name"))


def merge_feature_into_previous(items: list[dict[str, Any]], idx: int, note_name: bool = True) -> dict[str, Any]:
    bad = items[idx]
    target = items[idx - 1] if idx > 0 else None
    if target:
        prefix = feature_name(bad) if note_name else ""
        moved = append_unique(prefix, bad.get("text") or "")
        target["text"] = append_unique(target.get("text") or "", moved)
        if bad.get("replaces"):
            target["replaces"] = sorted(set((target.get("replaces") or []) + bad.get("replaces", [])))
    removed = items.pop(idx)
    return removed


def move_feature_to_description(archetype: dict[str, Any], items: list[dict[str, Any]], idx: int) -> dict[str, Any]:
    bad = items.pop(idx)
    text = append_unique(feature_name(bad), bad.get("text") or "")
    archetype["description"] = append_unique(archetype.get("description") or "", text)
    return bad


def set_feature_title(feature: dict[str, Any], cn: str, en: str, ability_type: str = "") -> None:
    feature["name_cn"] = cn
    feature["name_en"] = en
    if ability_type:
        feature["ability_type"] = ability_type
    if ability_type:
        feature["name"] = f"{cn}{LP}{en}, {ability_type}{RP}"
    else:
        feature["name"] = f"{cn}{LP}{en}{RP}"


def repair_class_data() -> list[dict[str, Any]]:
    data = load(CLASSES)
    changes: list[dict[str, Any]] = []
    class_title_fixes = {
        (U(r"\u62f3\u5e08"), "s Cunning"): (U(r"\u6b66\u5b66\u667a\u6167"), "Brawler's Cunning", "Ex"),
        (U(r"\u62f3\u5e08"), "s Flurry"): (U(r"\u8fde\u6253"), "Brawler's Flurry", "Ex"),
        (U(r"\u8c03\u67e5\u5458"), "Alchemy"): (U(r"\u70bc\u91d1\u672f"), "Alchemy", "Su"),
    }
    sentence_starts = tuple(U(s) for s in (r"\u90a3\u4e48", r"\u4f7f\u7528", r"\u4f60\u53ef\u4ee5", r"\u5e76\u4e14", r"\u5982\u679c"))
    for cls in data.get("classes", []):
        feats = cls.get("features") or []
        i = 0
        while i < len(feats):
            f = feats[i]
            name = feature_name(f)
            key = (cls.get("name_cn"), name)
            if key in class_title_fixes:
                cn, en, typ = class_title_fixes[key]
                old = dict(f)
                set_feature_title(f, cn, en, typ)
                changes.append({"scope": "class_feature_title", "class": cls.get("name_cn"), "old": old.get("name_cn") or old.get("name"), "new": f.get("name")})
            elif i > 0 and name.startswith(sentence_starts):
                removed = merge_feature_into_previous(feats, i)
                changes.append({"scope": "class_feature_merged", "class": cls.get("name_cn"), "removed": removed.get("name_cn") or removed.get("name"), "target": feats[i-1].get("name_cn") or feats[i-1].get("name")})
                continue
            i += 1
    # Choice systems are front-end visible via class_profile; remove obvious non-title options.
    for cls in data.get("classes", []):
        profile = cls.get("class_profile") or {}
        groups = ((profile.get("choice_systems") or {}).get("groups") or [])
        for group in groups:
            opts = group.get("options") or []
            kept = []
            for opt in opts:
                cn = clean(opt.get("name_cn") or opt.get("name"))
                en = clean(opt.get("name_en"))
                if not has_cjk(cn) or cn.startswith(sentence_starts):
                    changes.append({"scope": "choice_option_removed", "class": cls.get("name_cn"), "group": group.get("title"), "removed": cn, "name_en": en})
                    continue
                kept.append(opt)
            group["options"] = kept
            group["option_count"] = len(kept)
    # For archetypes, merge only sentence-like false titles and apostrophe-fragment titles.
    bad_noise = {"APG", "PFS", "PRC", "Pathfinder"}
    arch_title_fixes = {
        (U(r"\u6e38\u4fa0"), U(r"\u52ab\u63a0\u8005"), "s Bane"): (U(r"\u52ab\u63a0\u8005\u7684\u8bc5\u5492"), "Freebooter's Bane", "Ex"),
        (U(r"\u9a91\u5c06"), U(r"\u5361\u8482\u4e9a\u9a6c\u738b"), "favored terrain"): (U(r"\u504f\u597d\u5730\u5f62"), "favored terrain", ""),
        (U(r"\u8c03\u67e5\u5458"), U(r"\u72af\u7f6a\u9996\u8111"), "s Inspiration"): (U(r"\u72af\u7f6a\u9996\u8111\u7075\u611f"), "Mastermind's Inspiration", "Ex"),
        (U(r"\u8c03\u67e5\u5458"), U(r"\u540d\u4fa6\u63a2"), "s Luck"): (U(r"\u540d\u4fa6\u63a2\u4e4b\u5e78"), "Sleuth's Luck", "Ex"),
        (U(r"\u8c03\u67e5\u5458"), U(r"\u540d\u4fa6\u63a2"), "Make it Count"): (U(r"\u4e00\u51fb\u5373\u5012"), "Make it Count", "Ex"),
        (U(r"\u8c03\u67e5\u5458"), U(r"\u540d\u4fa6\u63a2"), "Run Like Hell"): (U(r"\u5feb\u8dd1\u554a"), "Run Like Hell", "Ex"),
    }
    force_merge_names = {"Impaling Critical", "Weapon Focus"}
    intro_noise = {"My Little Pony", "Avatar"}
    for arch in data.get("archetypes") or []:
        feats = arch.get("features") or []
        i = 0
        while i < len(feats):
            name = feature_name(feats[i])
            parent_name = (arch.get("parent_class") or {}).get("name_cn")
            arch_name = arch.get("name_cn") or arch.get("name_raw")
            title_key = (parent_name, arch_name, name)
            if title_key in arch_title_fixes:
                cn, en, typ = arch_title_fixes[title_key]
                old = feats[i].get("name_cn") or feats[i].get("name")
                set_feature_title(feats[i], cn, en, typ)
                changes.append({"scope": "archetype_feature_title", "class": parent_name, "archetype": arch_name, "old": old, "new": feats[i].get("name")})
                i += 1
                continue
            is_fragment = bool(re.match(r"^s\s+\w+", name))
            is_sentence = name.startswith(sentence_starts)
            is_noise = name in bad_noise
            if name in intro_noise or (i == 0 and is_sentence):
                removed = move_feature_to_description(arch, feats, i)
                changes.append({"scope": "archetype_feature_to_description", "class": parent_name, "archetype": arch_name, "removed": removed.get("name_cn") or removed.get("name")})
                continue
            if i > 0 and (is_fragment or is_sentence or is_noise or name in force_merge_names):
                removed = merge_feature_into_previous(feats, i)
                changes.append({"scope": "archetype_feature_merged", "class": parent_name, "archetype": arch_name, "removed": removed.get("name_cn") or removed.get("name"), "target": feats[i-1].get("name_cn") or feats[i-1].get("name")})
                continue
            i += 1
    dump(CLASSES, data)
    return changes


def main() -> None:
    spell_changes = repair_spells()
    class_changes = repair_class_data()
    report = {
        "spell_change_count": len(spell_changes),
        "class_change_count": len(class_changes),
        "spell_changes_sample": spell_changes[:300],
        "class_changes_sample": class_changes[:300],
    }
    dump(REPORT, report)
    print(json.dumps({"spell_changes": len(spell_changes), "class_changes": len(class_changes), "report": str(REPORT)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()