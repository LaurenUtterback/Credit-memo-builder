"""FastAPI application for the Credit Memo Builder.

Routes
------
GET  /api/health           - liveness check
POST /api/extract          - upload documents, get structured extraction back
POST /api/memo/html        - render memo as HTML
POST /api/memo/pdf         - render memo as PDF (download)
POST /api/memo/word        - render memo as Word .doc (download)
POST /api/binder/pdf       - merge executed PDFs into an indexed closing binder

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
from .pa_models import PAExtraction, PARequest, PATerms, BreakdownResult
from . import pa_extraction as pa_extraction_service
from . import pa_agreement as pa_agreement_service
from . import pa_breakdown as pa_breakdown_service
from .loandocs_models import LoanDocsRequest, SettlementSheetResult
from . import loandocs as loandocs_service
from . import loandocs_sheet as loandocs_sheet_service
from .loandocs_extraction import TeamContractExtraction, MemoDealExtraction
from . import loandocs_extraction as loandocs_extraction_service
from .binder_models import BinderRequest
from . import binder as binder_service
from .binder_extraction import BinderInfoExtraction, BinderSortResult
from . import binder_extraction as binder_extraction_service

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
        "pa_templates": pa_agreement_service.templates_present(),
        "pa_pdf": pa_agreement_service.pdf_available(),
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


# --- Participation Agreement Builder ---------------------------------------

@app.post("/api/pa/extract", response_model=PAExtraction)
def pa_extract(docs: list[UploadedDoc]) -> PAExtraction:
    """Extract participation-deal fields from uploaded loan documents."""
    if not docs:
        raise HTTPException(status_code=400, detail="No documents provided.")
    try:
        return pa_extraction_service.extract_documents(docs)
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/api/pa/breakdown", response_model=BreakdownResult)
def pa_breakdown(docs: list[UploadedDoc]) -> BreakdownResult:
    """Parse a Participant Breakdown .xlsx into deal info + per-participant terms."""
    import base64

    xlsx = next(
        (d for d in docs
         if d.filename.lower().endswith((".xlsx", ".xlsm"))
         or "spreadsheet" in d.mime or "excel" in d.mime),
        docs[0] if docs else None,
    )
    if xlsx is None:
        raise HTTPException(status_code=400, detail="No spreadsheet provided.")
    try:
        data = base64.b64decode(xlsx.b64)
        return BreakdownResult(**pa_breakdown_service.parse_breakdown(data))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - surface parse failures cleanly
        raise HTTPException(status_code=422, detail=f"Could not read the breakdown: {exc}") from exc


def _pa_filename(terms: PATerms, ext: str) -> str:
    base = _safe_name(terms.borrower_name or "Participation")
    return f"Participation_Agreement_{base}.{ext}"


@app.post("/api/pa/docx")
def pa_docx(req: PARequest) -> Response:
    """Generate the filled Participation Agreement as a Word .docx."""
    data = pa_agreement_service.render_docx(req.terms, req.agreement_type)
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f'attachment; filename="{_pa_filename(req.terms, "docx")}"'},
    )


# --- Loan Documents Builder -------------------------------------------------

@app.get("/api/loandocs/defaults")
def loandocs_defaults() -> dict:
    """SRC bank/wire defaults for the Payment Direction Letter (from .env)."""
    return loandocs_service.bank_defaults()


@app.post("/api/loandocs/extract", response_model=TeamContractExtraction)
def loandocs_extract(docs: list[UploadedDoc]) -> TeamContractExtraction:
    """Extract Team & Contract fields (team name/address, league, contract
    title and date) from an uploaded player contract or other deal documents."""
    if not docs:
        raise HTTPException(status_code=400, detail="No documents provided.")
    try:
        return loandocs_extraction_service.extract_documents(docs)
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/api/loandocs/memo", response_model=MemoDealExtraction)
def loandocs_memo(docs: list[UploadedDoc]) -> MemoDealExtraction:
    """Read a previously generated credit memorandum (PDF is best) and return
    the deal-level fields for the closing documents."""
    if not docs:
        raise HTTPException(status_code=400, detail="No documents provided.")
    try:
        return loandocs_extraction_service.extract_memo(docs)
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/api/loandocs/settlement", response_model=SettlementSheetResult)
def loandocs_settlement(docs: list[UploadedDoc]) -> SettlementSheetResult:
    """Parse the deal's Balloon / Fully Amortized workbook into the Memo of
    Settlement fee lines and the Note's Exhibit A repayment schedule."""
    import base64

    xlsx = next(
        (d for d in docs
         if d.filename.lower().endswith((".xlsx", ".xlsm"))
         or "spreadsheet" in d.mime or "excel" in d.mime),
        docs[0] if docs else None,
    )
    if xlsx is None:
        raise HTTPException(status_code=400, detail="No spreadsheet provided.")
    try:
        data = base64.b64decode(xlsx.b64)
        return SettlementSheetResult(**loandocs_sheet_service.parse_settlement_workbook(data))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - surface parse failures cleanly
        raise HTTPException(status_code=422,
                            detail=f"Could not read the workbook: {exc}") from exc


def _loandocs_filename(req: LoanDocsRequest, ext: str) -> str:
    return f"Loan_Documents_{_safe_name(req.terms.borrower_name or 'Borrower')}.{ext}"


@app.post("/api/loandocs/html")
def loandocs_html(req: LoanDocsRequest) -> Response:
    html = loandocs_service.render_html(req.terms, req.include)
    return Response(content=html, media_type="text/html")


@app.post("/api/loandocs/pdf")
def loandocs_pdf(req: LoanDocsRequest) -> Response:
    html = loandocs_service.render_html(req.terms, req.include)
    try:
        pdf = memo_service.render_pdf(html, footer_text=loandocs_service.FOOTER_TEXT)
    except RuntimeError as exc:
        raise HTTPException(status_code=501, detail=str(exc)) from exc
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition":
                 f'attachment; filename="{_loandocs_filename(req, "pdf")}"'},
    )


@app.post("/api/loandocs/word")
def loandocs_word(req: LoanDocsRequest) -> Response:
    html = loandocs_service.render_html(req.terms, req.include)
    doc = memo_service.render_word(html, footer_text=loandocs_service.FOOTER_TEXT)
    return Response(
        content=doc,
        media_type="application/msword",
        headers={"Content-Disposition":
                 f'attachment; filename="{_loandocs_filename(req, "doc")}"'},
    )


# --- Closing Binder -----------------------------------------------------------

@app.post("/api/binder/extract", response_model=BinderInfoExtraction)
def binder_extract(docs: list[UploadedDoc]) -> BinderInfoExtraction:
    """Read uploaded deal documents (credit memo, loan documents, term sheet)
    and return the binder's cover-page fields."""
    if not docs:
        raise HTTPException(status_code=400, detail="No documents provided.")
    try:
        return binder_extraction_service.extract_binder_info(docs)
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/api/binder/sort", response_model=BinderSortResult)
def binder_sort(docs: list[UploadedDoc]) -> BinderSortResult:
    """Read the uploaded signed closing package (and insurance PDFs) and
    split it into the binder's standard sections with page ranges."""
    if not docs:
        raise HTTPException(status_code=400, detail="No documents provided.")
    try:
        return binder_extraction_service.sort_documents(docs)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/api/binder/pdf")
def binder_pdf(req: BinderRequest) -> Response:
    """Merge the uploaded executed documents into one indexed closing binder."""
    if not req.documents:
        raise HTTPException(status_code=400, detail="No documents provided.")
    try:
        pdf = binder_service.build_binder(req.info, req.documents, req.tab_pages)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=501, detail=str(exc)) from exc
    name = _safe_name(req.info.borrower_name)
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="Closing_Binder_{name}.pdf"'},
    )


@app.post("/api/pa/pdf")
def pa_pdf(req: PARequest) -> Response:
    """Generate the filled Participation Agreement as a PDF (via LibreOffice)."""
    try:
        data = pa_agreement_service.render_pdf(req.terms, req.agreement_type)
    except RuntimeError as exc:
        raise HTTPException(status_code=501, detail=str(exc)) from exc
    return Response(
        content=data,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{_pa_filename(req.terms, "pdf")}"'},
    )
