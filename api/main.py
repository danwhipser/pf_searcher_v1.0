"""FastAPI application assembly."""
from __future__ import annotations

from fastapi import FastAPI

from api.routers import health, rag, spells


app = FastAPI(
    title="PF Spell RAG API",
    description="Pathfinder spell search and retrieval-augmented Q&A API.",
    version="1.0.0",
)

app.include_router(health.router)
app.include_router(spells.router)
app.include_router(rag.router)

