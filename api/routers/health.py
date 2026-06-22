from __future__ import annotations

from fastapi import APIRouter

from api.dependencies import get_index_status, get_retriever
from api.models import HealthResponse


router = APIRouter(prefix="/api", tags=["health"])


@router.get("/health", response_model=HealthResponse)
async def health():
    try:
        get_retriever()
        spell_count, index_built_at = get_index_status()
        return HealthResponse(
            status="ok",
            version="1.0.0",
            spell_count=spell_count,
            index_built_at=index_built_at,
        )
    except Exception:
        return HealthResponse(
            status="error",
            version="1.0.0",
            spell_count=0,
            index_built_at=None,
        )

