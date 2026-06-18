"""FastAPI application for the Credit Memo Builder.

Routes
------
GET  /api/health           - liveness check
POST /api/extract          - upload documents, get structured extraction back
POST /api/memo/html        - render memo as HTML
POST /api/memo/pdf         - render memo as PDF (download)
POST /api/memo/word        - render memo as Word .doc (download)

Interactive API docs are auto-generated at /docs (Swagger) and /redoc.

Run locally:  uvicorn app.main:app --reload --port 8000
"""

from __future__ import annotations

import os
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from dotenv import load_dotenv

from .models import Extraction, MemoRequest, UploadedDoc
from . import extraction as extraction_service
from . import memo as memo_service

# Load the project-root .env so the Claude usage token (CLAUDE_CODE_OAUTH_TOKEN)
# is available however the server is launched. This does NOT rely on uvicorn's
# --env-file flag, which silently does nothing unless python-dotenv is installed.
load_dotenv(os.path.join(os.path.dirname(__file__), "..", "..", ".env"))

app = FastAPI(
    title="Credit Memo Builder API",
    description="Backend for South River Capital's athlete-loan credit memos.",
    version="1.0.0",
)

# Allow the Vite dev server (and configured production origins) to call the API.
_origins = os.environ.get(
    "CORS_ORIGINS",
    "http://localhost:5173,http://127.0.0.1:5173",
).split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in _origins if o.strip()],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health() -> dict:
    return {
        "status": "ok",
        "extraction_configured": extraction_service.usage_token() is not None,
    }


class ExtractRequest(MemoRequest):  # reuse base for OpenAPI clarity
    pass


@app.post("/api/extract", response_model=Extraction)
def extract(docs: list[UploadedDoc]) -> Extraction:
    """Run document extraction. Documents are base64-encoded by the frontend."""
    if not docs:
        raise HTTPException(status_code=400, detail="No documents provided.")
    try:
        return extraction_service.extract_documents(docs)
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


def _filenames(req: MemoRequest) -> list[str]:
    # filenames are carried for the memo's document index; optional.
    return []


def _safe_name(name: str) -> str:
    """ASCII-safe borrower name for Content-Disposition (e.g. José -> Jose)."""
    import unicodedata
    ascii_name = (
        unicodedata.normalize("NFKD", name or "Borrower")
        .encode("ascii", "ignore")
        .decode("ascii")
    )
    ascii_name = "".join(c if c.isalnum() or c in "._-" else "_" for c in ascii_name)
    return ascii_name.strip("_") or "Borrower"


@app.post("/api/memo/html")
def memo_html(req: MemoRequest) -> Response:
    html = memo_service.render_html(req.terms, req.extraction, _filenames(req))
    return Response(content=html, media_type="text/html")


@app.post("/api/memo/pdf")
def memo_pdf(req: MemoRequest) -> Response:
    html = memo_service.render_html(req.terms, req.extraction, _filenames(req))
    try:
        pdf = memo_service.render_pdf(html)
    except RuntimeError as exc:
        raise HTTPException(status_code=501, detail=str(exc)) from exc
    name = _safe_name(req.terms.name)
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="Credit_Memorandum_{name}.pdf"'},
    )


@app.post("/api/memo/word")
def memo_word(req: MemoRequest) -> Response:
    html = memo_service.render_html(req.terms, req.extraction, _filenames(req))
    doc = memo_service.render_word(html)
    name = _safe_name(req.terms.name)
    return Response(
        content=doc,
        media_type="application/msword",
        headers={"Content-Disposition": f'attachment; filename="Credit_Memorandum_{name}.doc"'},
    )
