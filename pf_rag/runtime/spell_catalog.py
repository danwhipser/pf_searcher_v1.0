from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pf_rag.runtime.paths import RuntimePaths


def normalize_query_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip().lower()


class SpellCatalog:
    """Filesystem-backed spell catalog used by the lightweight runtime."""

    def __init__(self, paths: RuntimePaths):
        self.paths = paths

    def discover_sources(self) -> list[str]:
        if not self.paths.result_dir.exists():
            return []

        sources: list[str] = []
        for source_dir in sorted(path for path in self.paths.result_dir.iterdir() if path.is_dir()):
            code = source_dir.name
            if code.lower() == "index":
                continue
            raw_path = source_dir / f"spells-{code}.json"
            model_path = source_dir / f"spells-{code}-model.json"
            chosen = raw_path if raw_path.exists() else model_path
            if chosen.exists():
                sources.append(chosen.relative_to(self.paths.base_dir).as_posix())
        return sources

    def load_source_metadata(self) -> dict[str, dict[str, Any]]:
        path = self.paths.data_dir / "aon_source_counts.json"
        if not path.exists():
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        sources = payload.get("sources", {})
        if not isinstance(sources, dict):
            return {}
        return {str(k).upper(): v for k, v in sources.items() if isinstance(v, dict)}

    def count_source(self, source_path: str) -> int | None:
        data = self.load_source_records(source_path)
        return len(data) if data is not None else None

    def load_source_records(self, source_path: str) -> list[Any] | None:
        try:
            data = json.loads((self.paths.base_dir / source_path).read_text(encoding="utf-8"))
        except Exception:
            return None
        return data if isinstance(data, list) else None

    def source_summaries(self) -> list[dict[str, Any]]:
        metadata_by_source = self.load_source_metadata()
        summaries: list[dict[str, Any]] = []
        for source_path in self.discover_sources():
            source_code = Path(source_path).parent.name.upper()
            metadata = metadata_by_source.get(source_code, {})
            summaries.append(
                {
                    "source": source_code,
                    "display_source": metadata.get("display_source", source_code),
                    "title": metadata.get("title", ""),
                    "aon_section": metadata.get("aon_section", ""),
                    "aon_count": metadata.get("aon_count"),
                    "indexed_count": self.count_source(source_path),
                    "path": "/" + source_path.replace("\\", "/"),
                }
            )
        return summaries

    def spell_count(self) -> int:
        return sum(self.count_source(path) or 0 for path in self.discover_sources())

    def keyword_search(self, query: str, limit: int = 500) -> dict[str, Any]:
        normalized_query = normalize_query_text(query)
        matches: list[dict[str, Any]] = []
        total = 0

        for source_path in self.discover_sources():
            source_code = Path(source_path).parent.name.upper()
            records = self.load_source_records(source_path)
            if not records:
                continue

            for spell in records:
                if not isinstance(spell, dict):
                    continue
                if not self._matches_keyword(spell, normalized_query):
                    continue
                total += 1
                if len(matches) < limit:
                    enriched = dict(spell)
                    enriched.setdefault("source_book", source_code)
                    matches.append(enriched)

        return {
            "query": query,
            "total": total,
            "returned": len(matches),
            "limit": limit,
            "hits": matches,
        }

    @staticmethod
    def _matches_keyword(spell: dict[str, Any], normalized_query: str) -> bool:
        if not normalized_query:
            return True
        text = normalize_query_text(json.dumps(spell, ensure_ascii=False))
        return normalized_query in text

