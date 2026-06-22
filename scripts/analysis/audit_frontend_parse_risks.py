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
from collections import Counter
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
OUT_PATH = ROOT / "result" / "frontend-parse-risk-audit.json"

def U(s: str) -> str:
    return s.encode("ascii").decode("unicode_escape")

# Phrase-level starts only. Single nouns such as "spell" or "weapon" are valid PF titles.
SENTENCE_STARTS = tuple(U(s) for s in (
    r"\u5982\u679c",      # ??
    r"\u5f53",            # ?
    r"\u6bcf\u5f53",      # ??
    r"\u6bcf\u6b21",      # ??
    r"\u9664\u975e",      # ??
    r"\u90a3\u4e48",      # ??
    r"\u5e76\u4e14",      # ??
    r"\u4ee5\u53ca",      # ??
    r"\u8fd9\u4e2a\u6548\u679c",  # ????
    r"\u83b7\u5f97",      # ??
    r"\u4f7f\u7528",      # ??
    r"\u65e0\u6cd5",      # ??
    r"\u4e0d\u80fd",      # ??
    r"\u5fc5\u987b",      # ??
    r"\u76f4\u5230",      # ??
    r"\u4f60\u53ef\u4ee5", # ???
    r"\u4f60\u83b7\u5f97", # ???
    r"\u8be5\u80fd\u529b", # ???
    r"\u6b64\u80fd\u529b", # ???
))

SENTENCE_PUNCT = tuple(U(s) for s in (r"\uff0c", r"\u3002", r"\uff1b", r"\uff1a", r"\n"))
REFERENCE_EN = re.compile(
    r"^(?:DC|AC|CMD|CMB|HP|Blur|Fly|Scent|Sleep|Bane|Tongues|Still Spell|Silent Spell|Mounted Combat|"
    r"Power Attack|Dodge|Combat Reflexes|Weapon Focus|Improved Initiative|Toughness|Punching Dagger)$",
    re.I,
)
PAREN_NAME_RE = re.compile(r"^\s*(?P<cn>[^()??]+?)\s*[?(]\s*(?P<en>[^()??]+?)\s*[?)]")

SPELL_DATA_FILES = [
    "result/crb/spells-crb.json",
    "result/acg/spells-acg.json",
    "result/apg/spells-apg.json",
    "result/arg/spells-arg.json",
    "result/uc/spells-uc-model.json",
    "result/um/spells-um-model.json",
    "result/ui/spells-ui-model.json",
    "result/oa/spells-oa.json",
    "result/aarch/spells-aarch-model.json",
    "result/cotr/spells-cotr-model.json",
    "result/fob/spells-fob-model.json",
    "result/foc/spells-foc-model.json",
    "result/fop/spells-fop-model.json",
    "result/isg/spells-isg-model.json",
    "result/isi/spells-isi-model.json",
    "result/ism/spells-ism-model.json",
    "result/iswg/spells-iswg-model.json",
    "result/mtt/spells-mtt-model.json",
    "result/rtt/spells-rtt-model.json",
    "result/tg/spells-tg-model.json",
    "result/ag/spells-ag-model.json",
    "result/mc/spells-mc-model.json",
    "result/ma/spells-ma-model.json",
    "result/vc/spells-vc-model.json",
    "result/ha/spells-ha-model.json",
    "result/uw/spells-uw-model.json",
    "result/pa/spells-pa-model.json",
    "result/botd/spells-botd-model.json",
]


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def clean_text(value: Any) -> str:
    return str(value or "").strip()


def split_name(raw: str) -> tuple[str, str]:
    raw = clean_text(raw)
    m = PAREN_NAME_RE.match(raw)
    if m:
        return m.group("cn").strip(), m.group("en").strip()
    return raw, ""


def has_cjk(value: str) -> bool:
    return bool(re.search(r"[\u4e00-\u9fff]", value or ""))


def risk_name(name_cn: str, name_en: str, detail: str, *, require_detail: bool = True) -> list[str]:
    name_cn = clean_text(name_cn)
    name_en = clean_text(name_en)
    detail = clean_text(detail)
    reasons: list[str] = []
    if name_cn.startswith(SENTENCE_STARTS):
        reasons.append("sentence_start_name")
    # A real title can be long; treat it as suspicious only if it looks like prose.
    if len(name_cn) > 44 and any(mark in name_cn for mark in SENTENCE_PUNCT):
        reasons.append("long_sentence_name")
    if name_en in {"Ex", "Su", "Sp", "EX", "SU", "SP"}:
        reasons.append("english_is_ability_type")
    if REFERENCE_EN.search(name_en):
        reasons.append("english_reference_token")
    if not has_cjk(name_cn):
        reasons.append("english_or_empty_cn_name")
    if require_detail:
        if not detail:
            reasons.append("empty_detail")
        elif len(detail) < 8:
            reasons.append("very_short_detail")
    return reasons


def strong(reasons: list[str]) -> list[str]:
    # Ability types (Ex/Su/Sp), short text, and reference English are clues, not standalone proof.
    weak = {"very_short_detail", "english_reference_token", "english_is_ability_type"}
    return [r for r in reasons if r not in weak]


def scan_feats() -> list[dict[str, Any]]:
    path = ROOT / "result" / "feats" / "feats-frontend.json"
    data = load_json(path)
    feats = data if isinstance(data, list) else data.get("feats", [])
    risks = []
    for idx, feat in enumerate(feats):
        name_cn = clean_text(feat.get("name_cn") or feat.get("name") or feat.get("cn_name"))
        name_en = clean_text(feat.get("name_en") or feat.get("en_name"))
        detail = clean_text(feat.get("detail_text") or feat.get("detail") or feat.get("benefit") or feat.get("effect"))
        reasons = risk_name(name_cn, name_en, detail)
        if strong(reasons):
            risks.append({
                "index": idx,
                "feat_id": feat.get("feat_id"),
                "match_key": feat.get("match_key"),
                "name_cn": name_cn,
                "name_en": name_en,
                "books": feat.get("books") or feat.get("book") or feat.get("source"),
                "reasons": reasons,
                "detail": detail[:220],
            })
    return risks


def pick(obj: dict[str, Any], keys: list[str]) -> Any:
    for key in keys:
        if key in obj and obj.get(key) not in (None, ""):
            return obj.get(key)
    return ""


def scan_spells() -> list[dict[str, Any]]:
    risks = []
    for rel in SPELL_DATA_FILES:
        path = ROOT / rel
        if not path.exists():
            risks.append({"file": rel, "error": "missing_file"})
            continue
        try:
            data = load_json(path)
        except Exception as exc:
            risks.append({"file": rel, "error": str(exc)})
            continue
        spells = data if isinstance(data, list) else data.get("spells", [])
        for idx, spell in enumerate(spells):
            raw_name = clean_text(spell.get("name"))
            split_cn, split_en = split_name(raw_name)
            name_cn = clean_text(spell.get("name_zh") or pick(spell, [U(r"\u540d\u79f0"), U(r"\u4e2d\u6587\u540d")]) or split_cn)
            name_en = clean_text(spell.get("name_en") or pick(spell, [U(r"\u82f1\u6587"), "english"]) or split_en)
            detail = clean_text(pick(spell, [U(r"\u6cd5\u672f\u6548\u679c"), U(r"\u6548\u679c"), "effect", U(r"\u63cf\u8ff0"), "description"]))
            polluted_fields = []
            if not detail:
                for field in [
                    U(r"\u6cd5\u672f\u6297\u529b"), "spell_resistance",
                    U(r"\u76ee\u6807"), "target",
                    U(r"\u6210\u5206"), "components",
                    U(r"\u8303\u56f4"), "range",
                    U(r"\u6301\u7eed"), "duration",
                    U(r"\u8c41\u514d"), "save",
                ]:
                    value = clean_text(spell.get(field))
                    if len(value) >= 28 and has_cjk(value) and any(mark in value for mark in SENTENCE_PUNCT):
                        polluted_fields.append({"field": field, "value": value[:220]})
                if polluted_fields:
                    detail = polluted_fields[0]["value"]
            reasons = risk_name(name_cn, name_en, detail)
            if polluted_fields:
                reasons.append("detail_in_metadata_field")
            if strong(reasons) or polluted_fields:
                risks.append({
                    "file": rel,
                    "index": idx,
                    "spell_id": spell.get("spell_id"),
                    "name": raw_name,
                    "name_cn": name_cn,
                    "name_en": name_en,
                    "reasons": reasons,
                    "polluted_fields": polluted_fields,
                    "detail": detail[:220],
                })
    return risks


def scan_classes() -> dict[str, list[dict[str, Any]]]:
    path = ROOT / "result" / "classes" / "classes-extracted.json"
    data = load_json(path)
    class_choice_risks = []
    class_core_risks = []
    archetype_risks = []
    for cls in data.get("classes", []):
        profile = cls.get("class_profile") or {}
        for feature in ((profile.get("core_features") or {}).get("items") or []):
            name_cn = feature.get("name_cn") or feature.get("name") or ""
            reasons = risk_name(name_cn, feature.get("name_en") or "", feature.get("text") or "")
            if strong(reasons):
                class_core_risks.append({
                    "class": cls.get("name_cn"),
                    "name_cn": name_cn,
                    "name_en": feature.get("name_en"),
                    "reasons": reasons,
                    "detail": (feature.get("text") or "")[:220],
                })
        for group in ((profile.get("choice_systems") or {}).get("groups") or []):
            for option in group.get("options") or []:
                reasons = risk_name(option.get("name_cn") or "", option.get("name_en") or "", option.get("detail_text") or "")
                if strong(reasons):
                    class_choice_risks.append({
                        "class": cls.get("name_cn"),
                        "group": group.get("title"),
                        "name_cn": option.get("name_cn"),
                        "name_en": option.get("name_en"),
                        "reasons": reasons,
                        "detail": (option.get("detail_text") or "")[:220],
                    })
    for archetype in data.get("archetypes") or []:
        parent = archetype.get("parent_class") or {}
        for feature in archetype.get("features") or []:
            name_cn = feature.get("name_cn") or feature.get("name") or ""
            reasons = risk_name(name_cn, feature.get("name_en") or "", feature.get("text") or "", require_detail=False)
            if strong(reasons):
                archetype_risks.append({
                    "class": parent.get("name_cn") or parent.get("name"),
                    "archetype": archetype.get("name_cn") or archetype.get("name_raw"),
                    "name_cn": name_cn,
                    "name_en": feature.get("name_en"),
                    "reasons": reasons,
                    "detail": (feature.get("text") or "")[:220],
                })
    return {
        "class_core_risks": class_core_risks,
        "class_choice_risks": class_choice_risks,
        "archetype_risks": archetype_risks,
    }



def summarize_risks(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_reason: Counter[str] = Counter()
    by_file: Counter[str] = Counter()
    polluted_by_field: Counter[str] = Counter()
    for row in rows:
        for reason in row.get("reasons") or []:
            by_reason[reason] += 1
        if row.get("file"):
            by_file[row["file"]] += 1
        for field in row.get("polluted_fields") or []:
            polluted_by_field[f"{row.get('file')}::{field.get('field')}"] += 1
    return {
        "by_reason": dict(by_reason.most_common()),
        "by_file": dict(by_file.most_common()),
        "polluted_by_field": dict(polluted_by_field.most_common()),
    }

def main() -> None:
    feats = scan_feats()
    spells = scan_spells()
    classes = scan_classes()
    report = {
        "scope": {
            "note": "Audits front-end visible feats/classes and the spell files used by the web spell browser.",
            "spell_files": SPELL_DATA_FILES,
        },
        "feats": {"risk_count": len(feats), **summarize_risks(feats), "samples": feats[:300]},
        "spells": {"risk_count": len(spells), **summarize_risks(spells), "samples": spells[:300]},
        "classes": {
            "class_core_risk_count": len(classes["class_core_risks"]),
            "class_choice_risk_count": len(classes["class_choice_risks"]),
            "archetype_risk_count": len(classes["archetype_risks"]),
            "class_core_samples": classes["class_core_risks"][:160],
            "class_choice_samples": classes["class_choice_risks"][:160],
            "archetype_samples": classes["archetype_risks"][:160],
        },
    }
    OUT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"written {OUT_PATH}")
    print(json.dumps({
        "feats": len(feats),
        "spells": len(spells),
        "class_core": len(classes["class_core_risks"]),
        "class_choice": len(classes["class_choice_risks"]),
        "archetypes": len(classes["archetype_risks"]),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()