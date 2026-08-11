"""FastAPI application entrypoint.

Warms the local embedding model at startup (so no request pays the load cost)
and mounts the notebooks + sources routers. Chat / gateway routers are added
in later phases.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import embeddings
from app.config import get_settings
from app.routers import auth, chat, notebooks, search, sources, stats

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("app")

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Load the sentence-transformers model once, before serving traffic.
    logger.info("Loading embedding model %s ...", settings.embedding_model)
    embeddings.get_model()
    logger.info("Embedding model ready.")
    yield


app = FastAPI(title=settings.app_name, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(notebooks.router)
app.include_router(sources.router)
app.include_router(chat.router)
app.include_router(search.router)
app.include_router(stats.router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
