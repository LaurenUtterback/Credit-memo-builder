"""Claude-powered helpers for the Closing Binder.

Two jobs, both reusing the shared usage-token call from the Loan Documents
extractor:
- Step 1's deal-info reader: pull the four cover-page fields from whatever
  deal documents the user drops.
- Step 2's auto-sort: read the SIGNED closing package (and any insurance
  PDFs) and split it into the binder's standard sections with page ranges,
  ordered like SRC's executed example binder.
"""

from __future__ import annotations

import base64
import io
from typing import Optional

from pydantic import BaseModel, Field
from pypdf import PdfReader

from .models import UploadedDoc
from .loandocs_extraction import _ask_claude


class BinderInfoExtraction(BaseModel):
    """The binder cover fields Claude pulls from the uploaded documents."""

    borrower_name: Optional[str] = None
    loan_amount: Optional[float] = None
    loan_number: Optional[str] = None
    closing_date: Optional[str] = None   # ISO yyyy-mm-dd for the date picker
    notes: Optional[str] = None


PROMPT = """You are an analyst at South River Capital LLC assembling the CLOSING BINDER for a loan to a professional athlete — a single PDF that compiles the executed deal documents behind a cover page and table of contents. You have been given one or more deal documents: possibly a South River credit memorandum, the closing documents (promissory note, loan and security agreement, memo of settlement, ...), a term sheet, or executed/scanned versions of these.

Extract the binder's COVER PAGE fields. Return ONLY raw JSON, no markdown, no backticks:

{"borrower_name":null,"loan_amount":0,"loan_number":null,"closing_date":null,"notes":null}

Rules:
- Use null for anything not stated in the documents (0 for loan_amount). Do not invent values.
- borrower_name: the borrower/athlete the loan is made to.
- loan_amount: the loan / proposed facility principal as a plain number with no "$" or commas (e.g. 785000).
- loan_number: the loan or account number if shown, digits as a string; many documents omit it.
- closing_date: the loan's closing date, formatted "YYYY-MM-DD". Prefer an explicitly stated Closing Date (the closing documents' cover or dating clauses); the date the documents were executed or a credit memo's proposed funding date is acceptable — say which you used in notes. Null if no date is stated; never guess.
- notes: one or two short sentences flagging anything missing, ambiguous, or conflicting (e.g. "No loan number in the documents", "Closing date taken from the memo's proposed funding date"). Null if nothing notable."""


def extract_binder_info(docs: list[UploadedDoc]) -> BinderInfoExtraction:
    """Binder cover fields from the uploaded deal documents."""
    return BinderInfoExtraction(**_ask_claude(docs, PROMPT, max_tokens=600))


# --- Auto-sort the signed closing package into binder sections ---------------

class SortPart(BaseModel):
    """A page range within one of the uploaded files (both 1-indexed)."""

    file_index: int
    page_from: int
    page_to: int


class SortSection(BaseModel):
    """One proposed binder section: its TOC title and the page ranges
    (possibly from several files) that make it up."""

    title: str
    parts: list[SortPart]


class BinderSortResult(BaseModel):
    sections: list[SortSection] = Field(default_factory=list)
    notes: Optional[str] = None


# The binder's section order, from SRC's executed example: closing documents
# in signing order, anything unrecognized after them, insurance always last.
_CATEGORY_ORDER = [
    ("affidavit", "Business Entity Affidavit"),
    ("note", "Promissory Note"),
    ("repayment_schedule", "Repayment Schedule"),
    ("lsa", "Loan and Security Agreement"),
    ("guaranty", "Guaranty"),
    ("settlement", "Memo of Settlement"),
    ("ucc", "UCC"),
    ("direction_letter", "Direction Letter"),
]
_RANK = {cat: i for i, (cat, _) in enumerate(_CATEGORY_ORDER)}
# Unrecognized ("other") sections go after the closing documents, in page
# order; insurance is always the last tab, all files merged into one section.


SORT_PROMPT_HEADER = """You are an analyst at South River Capital LLC assembling the CLOSING BINDER for a loan to a professional athlete. You have been given the uploaded PDF file(s) listed below, in upload order. Together they contain the SIGNED/EXECUTED closing package and possibly separate insurance documents.

Files:
"""

SORT_PROMPT_BODY = """
Identify every distinct document and exactly which pages it spans. Return ONLY raw JSON, no markdown, no backticks:

{"documents":[{"file_index":1,"first_page":1,"last_page":2,"category":"package_cover","title":null}],"notes":null}

Rules:
- file_index is 1-based in the upload order listed above. first_page/last_page are 1-based page numbers WITHIN that file, inclusive.
- Account for EVERY page of every file exactly once — no gaps, no overlaps. Attach a blank or unidentifiable page to the document it most likely belongs with and mention it in notes.
- category must be one of:
  "package_cover" — the closing package's own overall cover/summary page and its document-index pages, AND every standalone title/cover sheet: a page that is mostly blank and shows only a document's name (possibly with the borrower's name, a logo, or a kicker line) announcing the document that follows. A title sheet is ALWAYS its own "package_cover" entry — NEVER included in the following document's span, even though it belongs to that document — because the binder adds its own title pages and keeping them would print two covers per section.
  "affidavit" — Business Entity Affidavit (sworn statement).
  "note" — Promissory Note.
  "repayment_schedule" — the Note's repayment/payment schedule (Exhibit A, "Loan Repayments by Month"). Treat it as its OWN document even though it is the Note's exhibit — the binder format separates them.
  "lsa" — Loan and Security Agreement, INCLUDING its Exhibit A definitions.
  "guaranty" — Guaranty.
  "settlement" — Memo of Settlement.
  "ucc" — UCC Financing Statement, including its Exhibit A.
  "direction_letter" — Payment Direction Letter to the team.
  "insurance" — insurance paperwork: quotes, policies, binders, death/disability/disgrace coverage.
  "other" — anything that is none of the above.
- title: null for the known categories; for "other" give a short title suitable for a table of contents row.
- notes: one or two short sentences about anything ambiguous or unidentifiable. Null if nothing notable."""


def _page_counts(docs: list[UploadedDoc]) -> list[int]:
    counts = []
    for d in docs:
        try:
            reader = PdfReader(io.BytesIO(base64.b64decode(d.b64)))
            if reader.is_encrypted:
                reader.decrypt("")
            counts.append(len(reader.pages))
        except Exception as exc:  # noqa: BLE001 - surface parse failures cleanly
            raise ValueError(
                f"'{d.filename or 'upload'}' could not be read as a PDF ({exc}). "
                "The binder takes PDF files only."
            ) from exc
    return counts


def _organize(entries: list[dict], page_counts: list[int]) -> tuple[list[SortSection], list[str]]:
    """Claude's raw page-range list -> ordered binder sections + extra notes.

    Deterministic on purpose: canonical section order, all insurance ranges
    merged into one final section, package cover sheets dropped, and any
    pages Claude failed to assign reported so nothing goes missing silently.
    """
    notes: list[str] = []
    covered = [set() for _ in page_counts]
    by_category: dict[str, list[SortPart]] = {}  # known categories merge into
    # ONE section each (an LSA reported as body + exhibit stays one section)
    others: list[tuple] = []                     # ((file, first), section)
    insurance_parts: list[SortPart] = []

    for e in entries:
        try:
            fi = int(e.get("file_index"))
            first = int(e.get("first_page"))
            last = int(e.get("last_page"))
        except (TypeError, ValueError):
            notes.append(f"Skipped an unreadable range entry: {e!r}.")
            continue
        cat = str(e.get("category") or "other")
        if not 1 <= fi <= len(page_counts):
            notes.append(f"Skipped a range for a file that wasn't uploaded (file {fi}).")
            continue
        n = page_counts[fi - 1]
        first, last = max(1, first), min(n, last)
        if first > last:
            notes.append(f"Skipped an empty page range in file {fi}.")
            continue
        covered[fi - 1].update(range(first, last + 1))
        if cat == "package_cover":
            continue  # the binder adds its own cover and title pages
        part = SortPart(file_index=fi, page_from=first, page_to=last)
        if cat == "insurance":
            insurance_parts.append(part)
        elif cat in _RANK:
            by_category.setdefault(cat, []).append(part)
        else:
            title = str(e.get("title") or "").strip() or "Document"
            others.append(((fi, first), SortSection(title=title, parts=[part])))

    sections = []
    for cat, title in _CATEGORY_ORDER:
        parts = by_category.get(cat)
        if parts:
            parts.sort(key=lambda p: (p.file_index, p.page_from))
            sections.append(SortSection(title=title, parts=parts))
    others.sort(key=lambda kv: kv[0])
    sections.extend(s for _, s in others)
    if insurance_parts:
        insurance_parts.sort(key=lambda p: (p.file_index, p.page_from))
        sections.append(SortSection(title="Insurance Documents", parts=insurance_parts))

    for i, (count, got) in enumerate(zip(page_counts, covered)):
        missing = sorted(set(range(1, count + 1)) - got)
        if missing:
            runs, start = [], missing[0]
            for a, b in zip(missing, missing[1:] + [None]):
                if b != a + 1:
                    runs.append(str(start) if start == a else f"{start}-{a}")
                    start = b
            notes.append(f"Pages {', '.join(runs)} of file {i + 1} were not assigned "
                         "to any section — add them manually if they belong in the binder.")
    return sections, notes


def sort_documents(docs: list[UploadedDoc]) -> BinderSortResult:
    """Split the uploaded signed package (+ insurance PDFs) into ordered
    binder sections with page ranges."""
    page_counts = _page_counts(docs)
    listing = "".join(
        f"Document {i + 1}: {d.filename or f'upload {i + 1}'} ({n} pages)\n"
        for i, (d, n) in enumerate(zip(docs, page_counts)))
    data = _ask_claude(docs, SORT_PROMPT_HEADER + listing + SORT_PROMPT_BODY,
                       max_tokens=2000)
    sections, extra = _organize(data.get("documents") or [], page_counts)
    notes = " ".join(x for x in [str(data.get("notes") or "").strip() or None, *extra] if x)
    return BinderSortResult(sections=sections, notes=notes or None)
