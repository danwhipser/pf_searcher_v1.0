from __future__ import annotations

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from api.services.spell_sources import build_source_summaries, keyword_search_all_sources


router = APIRouter(prefix="/api", tags=["spells"])


@router.get("/spell-sources")
async def spell_sources():
    return JSONResponse({"sources": build_source_summaries()})


@router.get("/spells/keyword")
async def keyword_search(
    q: str = Query(..., min_length=1, description="keyword query"),
    limit: int = Query(500, ge=1, le=5000, description="max returned spells"),
):
    return JSONResponse(keyword_search_all_sources(q, limit))

