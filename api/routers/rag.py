from __future__ import annotations

import time

from fastapi import APIRouter, HTTPException

from api.dependencies import get_generator, get_retriever
from api.models import RagAskRequest, RagAskResponse, RagSearchRequest, RagSearchResponse


router = APIRouter(prefix="/api/rag", tags=["rag"])


@router.post("/search", response_model=RagSearchResponse)
async def rag_search(request: RagSearchRequest):
    start_time = time.time()

    try:
        retriever = get_retriever()
        results = retriever.search(
            question=request.question,
            top_k=request.top_k,
            filters=request.filters,
        )

        hits = []
        for result in results:
            spell = result["spell_record"]
            context_text = result["context_text"]
            hits.append(
                {
                    "spell_id": spell.spell_id,
                    "name": spell.name,
                    "source": spell.source,
                    "spell_type": spell.spell_type,
                    "school": spell.school,
                    "level_raw": spell.level_raw,
                    "area": spell.area,
                    "score": result["score"],
                    "snippet": context_text[:200] + "..." if len(context_text) > 200 else context_text,
                }
            )

        latency_ms = int((time.time() - start_time) * 1000)

        return RagSearchResponse(
            hits=hits,
            total=len(hits),
            latency_ms=latency_ms,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/ask", response_model=RagAskResponse)
async def rag_ask(request: RagAskRequest):
    start_time = time.time()
    request_api_key = (request.api_key or "").strip()
    if not request_api_key:
        raise HTTPException(status_code=400, detail="API key is required for smart search")

    try:
        retriever = get_retriever()
        generator = get_generator()

        results = retriever.search(
            question=request.question,
            top_k=request.top_k,
            filters=request.filters,
        )

        answer, citations, degraded, llm_error = await generator.generate_answer(
            question=request.question,
            context_chunks=results,
            api_key=request_api_key,
        )

        latency_ms = int((time.time() - start_time) * 1000)

        return RagAskResponse(
            answer=answer,
            citations=[citation.dict() for citation in citations],
            retrieved_count=len(results),
            latency_ms=latency_ms,
            degraded=degraded,
            llm_error=llm_error,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
