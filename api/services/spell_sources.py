from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from api.config import SOURCE_METADATA, settings


def count_spell_file(source_path: str) -> int | None:
    full_path = settings.PROJECT_ROOT / source_path
    try:
        data = json.loads(full_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return len(data) if isinstance(data, list) else None


def normalize_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip().lower()


def spell_matches_keyword(spell: dict[str, Any], normalized_query: str) -> bool:
    if not normalized_query:
        return True
    text = normalize_text(json.dumps(spell, ensure_ascii=False))
    return normalized_query in text


def build_source_summaries() -> list[dict[str, Any]]:
    sources: list[dict[str, Any]] = []
    for source_path in settings.SPELL_SOURCES:
        source_code = Path(source_path).parent.name.upper()
        metadata = SOURCE_METADATA.get(source_code, {})
        sources.append(
            {
                "source": source_code,
                "display_source": metadata.get("display_source", source_code),
                "title": metadata.get("title", ""),
                "aon_section": metadata.get("aon_section", ""),
                "aon_count": metadata.get("aon_count"),
                "indexed_count": count_spell_file(source_path),
                "path": "/" + source_path.replace("\\", "/"),
            }
        )
    return sources


def keyword_search_all_sources(query: str, limit: int) -> dict[str, Any]:
    normalized_query = normalize_text(query)
    matches: list[dict[str, Any]] = []
    total = 0

    for source_path in settings.SPELL_SOURCES:
        full_path = settings.PROJECT_ROOT / source_path
        source_code = Path(source_path).parent.name.upper()
        try:
            data = json.loads(full_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(data, list):
            continue

        for spell in data:
            if not isinstance(spell, dict):
                continue
            if not spell_matches_keyword(spell, normalized_query):
                continue
            total += 1
            if len(matches) < limit:
                enriched = dict(spell)
                if not enriched.get("鏉ユ簮"):
                    enriched["鏉ユ簮"] = source_code
                if not enriched.get("source_book"):
                    enriched["source_book"] = source_code
                matches.append(enriched)

    return {
        "query": query,
        "total": total,
        "returned": len(matches),
        "limit": limit,
        "hits": matches,
    }

