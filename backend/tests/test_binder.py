"""Closing Binder assembly tests.

The binder renders its cover/index through the real Playwright pipeline and
merges the uploads with pypdf, so most of these run the pipeline end-to-end
with tiny generated PDFs and verify the page math via the outline bookmarks.
"""

import base64
import io

import pytest
from pypdf import PdfReader, PdfWriter

from app import binder
from app.binder_models import BinderDoc, BinderInfo


def _pdf_b64(pages: int) -> str:
    w = PdfWriter()
    for _ in range(pages):
        w.add_blank_page(width=612, height=792)
    buf = io.BytesIO()
    w.write(buf)
    return base64.b64encode(buf.getvalue()).decode()


def _docs():
    return [
        BinderDoc(title="Promissory Note", filename="note.pdf", b64=_pdf_b64(2)),
        # blank title -> must fall back to a cleaned-up filename
        BinderDoc(title="", filename="Loan_and_Security_Agreement.pdf", b64=_pdf_b64(3)),
    ]


def _outline_pages(reader: PdfReader) -> dict[str, int]:
    return {item.title: reader.get_destination_page_number(item)
            for item in reader.outline}


def test_index_entries_page_math():
    counts = [("A", 3), ("B", 1)]
    with_tabs = binder._index_entries(2, counts, tab_pages=True)
    assert [e["start"] for e in with_tabs] == [4, 8]   # cover 1-2, tab 3, A 4-6, tab 7, B 8
    without = binder._index_entries(2, counts, tab_pages=False)
    assert [e["start"] for e in without] == [3, 6]


def test_binder_with_tab_pages_orders_pages_and_bookmarks():
    pdf = binder.build_binder(BinderInfo(borrower_name="Test Borrower"), _docs())
    r = PdfReader(io.BytesIO(pdf))
    marks = _outline_pages(r)
    assert marks["Cover & Index"] == 0
    cover_pages = marks["Tab 1 — Promissory Note"]  # first tab page follows the cover
    assert cover_pages >= 1
    # tab 2 sits after tab 1's page + the note's 2 pages
    assert marks["Tab 2 — Loan and Security Agreement"] == cover_pages + 1 + 2
    assert len(r.pages) == cover_pages + (1 + 2) + (1 + 3)
    # the cover's index lists both titles (the blank one from its filename)
    cover_text = r.pages[0].extract_text().lower()
    assert "promissory note" in cover_text
    assert "loan and security agreement" in cover_text


def test_binder_without_tab_pages():
    pdf = binder.build_binder(BinderInfo(), _docs(), tab_pages=False)
    r = PdfReader(io.BytesIO(pdf))
    marks = _outline_pages(r)
    cover_pages = marks["Tab 1 — Promissory Note"]
    assert marks["Tab 2 — Loan and Security Agreement"] == cover_pages + 2
    assert len(r.pages) == cover_pages + 2 + 3


def test_binder_rejects_non_pdf_with_filename():
    bad = BinderDoc(title="Contract", filename="contract.docx",
                    b64=base64.b64encode(b"this is not a pdf").decode())
    with pytest.raises(ValueError) as exc:
        binder.build_binder(BinderInfo(), [bad])
    assert "contract.docx" in str(exc.value)


def test_binder_requires_documents():
    with pytest.raises(ValueError):
        binder.build_binder(BinderInfo(), [])
