"""Closing Binder assembly.

Merges the deal's executed documents (uploaded as PDFs) into one indexed
binder: an SRC-styled cover page with an Index of Documents, an optional
numbered tab page in front of each document (like the tabs of a physical
binder), PDF outline bookmarks for every tab, and the documents themselves,
byte-for-byte untouched.

The cover and tab pages are rendered from templates/closing_binder.html.j2
through the same Playwright/Chromium pipeline as the memo (memo.render_pdf);
pypdf stitches that front matter together with the uploaded PDFs. The front
matter is rendered WITHOUT Chromium's "Page X of N" footer — N would count
only the front matter, not the merged documents.
"""

from __future__ import annotations

import base64
import io
import re
from datetime import date
from pathlib import Path

from jinja2 import Environment, FileSystemLoader
from pypdf import PdfReader, PdfWriter

from . import memo
from .binder_models import BinderDoc, BinderInfo

_TEMPLATE_DIR = Path(__file__).parent / "templates"
_LOGO_PATH = Path(__file__).parent / "logo.txt"

# Autoescape is ON here (unlike the loandocs template): every context value is
# a plain string — document titles come straight from user input / filenames.
_env = Environment(loader=FileSystemLoader(str(_TEMPLATE_DIR)), autoescape=True)

FOOTER_TEXT = "South River Capital — Closing Binder"


def _money(n) -> str:
    if n is None:
        return "—"
    try:
        return f"${round(float(n)):,}"
    except (TypeError, ValueError):
        return "—"


def _fmt_long(d: date | None) -> str:
    return f"{d.strftime('%B')} {d.day}, {d.year}" if d else "________________"


def _title_from_filename(name: str) -> str:
    """'Loan_and_Security_Agreement (executed).pdf' -> a readable title."""
    stem = re.sub(r"\.[^.]+$", "", name or "")
    return re.sub(r"\s+", " ", stem.replace("_", " ")).strip()


def _read_documents(docs: list[BinderDoc]) -> list[tuple[str, PdfReader, int]]:
    """Decode and parse every upload, failing with the offending filename."""
    items: list[tuple[str, PdfReader, int]] = []
    for i, d in enumerate(docs):
        title = d.title.strip() or _title_from_filename(d.filename) or f"Document {i + 1}"
        label = d.filename or title
        try:
            reader = PdfReader(io.BytesIO(base64.b64decode(d.b64)))
            if reader.is_encrypted:
                reader.decrypt("")
            count = len(reader.pages)
        except Exception as exc:  # noqa: BLE001 - surface parse failures cleanly
            raise ValueError(
                f"'{label}' could not be read as a PDF ({exc}). "
                "The binder takes PDF files only — export or scan the document to PDF first."
            ) from exc
        if count == 0:
            raise ValueError(f"'{label}' has no pages.")
        items.append((title, reader, count))
    return items


def _index_entries(cover_pages: int, counts: list[tuple[str, int]],
                   tab_pages: bool) -> list[dict]:
    """Tab number, title and 1-indexed first page of each document."""
    entries = []
    page = cover_pages
    for i, (title, count) in enumerate(counts):
        if tab_pages:
            page += 1
        entries.append({"tab": i + 1, "title": title, "start": page + 1, "pages": count})
        page += count
    return entries


def _front_html(info: BinderInfo, entries: list[dict], tab_pages: bool) -> str:
    context = {
        "logo": _LOGO_PATH.read_text().strip(),
        "borrower_name": info.borrower_name or "____________________",
        "team_name": info.team_name or "—",
        "loan_amount": _money(info.loan_amount),
        "loan_number": info.loan_number,
        "closing_date": _fmt_long(info.closing_date),
        "doc_count": len(entries),
        "entries": entries,
        "tab_pages": tab_pages,
    }
    return _env.get_template("closing_binder.html.j2").render(**context)


def build_binder(info: BinderInfo, docs: list[BinderDoc], tab_pages: bool = True) -> bytes:
    if not docs:
        raise ValueError("No documents provided.")
    items = _read_documents(docs)
    counts = [(title, count) for title, _, count in items]

    # The index's "Begins on Page" numbers depend on how many pages the
    # cover/index itself takes, which isn't known until it's rendered — so
    # render, measure, and re-render until the count is stable (in practice
    # one extra pass at most: page-number digits never add a cover page).
    cover_pages = 1
    front = None
    for _ in range(3):
        html = _front_html(info, _index_entries(cover_pages, counts, tab_pages), tab_pages)
        front = PdfReader(io.BytesIO(
            memo.render_pdf(html, footer_text=FOOTER_TEXT, page_numbers=False)))
        actual = len(front.pages) - (len(items) if tab_pages else 0)
        if actual < 1:
            raise RuntimeError("Binder cover rendered fewer pages than expected.")
        if actual == cover_pages:
            break
        cover_pages = actual
    entries = _index_entries(cover_pages, counts, tab_pages)

    writer = PdfWriter()
    for p in front.pages[:cover_pages]:
        writer.add_page(p)
    writer.add_outline_item("Cover & Index", 0)
    page = cover_pages
    for i, ((title, reader, count), entry) in enumerate(zip(items, entries)):
        start = page
        if tab_pages:
            writer.add_page(front.pages[cover_pages + i])
            page += 1
        for p in reader.pages:
            writer.add_page(p)
        page += count
        # after the pages exist in the writer — a bookmark added before its
        # target page resolves to a dead destination
        writer.add_outline_item(f"Tab {entry['tab']} — {title}", start)

    writer.add_metadata({
        "/Title": f"Closing Binder — {info.borrower_name or 'Borrower'}",
        "/Author": "South River Capital, LLC",
    })
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()
