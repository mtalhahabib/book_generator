"""FastAPI application entry point."""

import logging
import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

# ── Logging ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
)
logger = logging.getLogger(__name__)

# ── Config validation ─────────────────────────────────────────────────────────

try:
    from app.config import settings
except Exception as exc:
    logger.error("Failed to load configuration: %s", exc)
    logger.error("Make sure .env file exists — copy .env.example to .env")
    sys.exit(1)

# ── App ───────────────────────────────────────────────────────────────────────

from app.routes import books, outlines, chapters, compilation

app = FastAPI(
    title="Book Generator API",
    description=(
        "Automated book generation system with LLM-powered outline and "
        "chapter writing, editor review workflow, and multi-format export."
    ),
    version="1.0.0",
)

# CORS — allow the static dashboard and external clients
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Routes ────────────────────────────────────────────────────────────────────

app.include_router(books.router)
app.include_router(outlines.router)
app.include_router(chapters.router)
app.include_router(compilation.router)

# ── Static Files (Editor Dashboard) ──────────────────────────────────────────

_static_dir = Path(__file__).resolve().parent / "app" / "static"
if _static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(_static_dir), html=True), name="static")
else:
    logger.warning("Static directory not found at %s — dashboard won't be served", _static_dir)

# ── Health ────────────────────────────────────────────────────────────────────


@app.get("/health", tags=["System"])
def health_check():
    """Basic health check."""
    return {"status": "ok", "service": "book-generator"}


@app.get("/api/quota-stats", tags=["System"])
def quota_stats():
    """Live Gemini quota diagnostics.

    Returns cache hit rate and per-model rate-limiter state.
    Useful for monitoring free-tier consumption without opening Google Cloud console.
    """
    from app.services.llm_service import get_quota_stats
    return get_quota_stats()


@app.get("/", tags=["System"])
def root():
    """Redirect to dashboard."""
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/static/index.html")

