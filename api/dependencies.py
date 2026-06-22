from __future__ import annotations

from datetime import datetime
from pathlib import Path

from fastapi import HTTPException

from api.config import settings
from api.services.generator import LLMGenerator
from api.services.retriever import HybridRetriever


_retriever: HybridRetriever | None = None
_generator: LLMGenerator | None = None
_index_built_at: str | None = None
_spell_count: int = 0


def get_retriever() -> HybridRetriever:
    """Return the shared retriever, loading indexes on first use."""
    global _retriever, _index_built_at, _spell_count

    if _retriever is None:
        try:
            _retriever = HybridRetriever()
            _retriever._ensure_loaded()

            chroma_dir = settings.PROJECT_ROOT / settings.CHROMA_PERSIST_DIR
            if chroma_dir.exists():
                mtime = Path(chroma_dir).stat().st_mtime
                _index_built_at = datetime.fromtimestamp(mtime).isoformat()

            if _retriever._spells_dict:
                _spell_count = len(_retriever._spells_dict)
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail=f"Index load failed: {exc}. Please run python scripts/build_index.py first.",
            ) from exc

    return _retriever


def get_generator() -> LLMGenerator:
    """Return the shared LLM generator."""
    global _generator

    if _generator is None:
        _generator = LLMGenerator()
    return _generator


def get_index_status() -> tuple[int, str | None]:
    return _spell_count, _index_built_at

