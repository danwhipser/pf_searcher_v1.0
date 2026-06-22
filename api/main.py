"""FastAPI application assembly."""
from __future__ import annotations

from fastapi import FastAPI

from api.routers import health, rag, spells
from pf_rag.version import APP_VERSION


app = FastAPI(
    title="PF Spell RAG API",
    description="Pathfinder spell search and retrieval-augmented Q&A API.",
    version=APP_VERSION,
)

app.include_router(health.router)
app.include_router(spells.router)
app.include_router(rag.router)
