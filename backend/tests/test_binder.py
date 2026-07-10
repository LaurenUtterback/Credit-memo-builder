"""Closing Binder assembly tests.

The binder renders its cover/TOC/title pages through the real Playwright
pipeline and merges the uploads with pypdf, so most of these run the pipeline
end-to-end with tiny generated PDFs and verify the page math via the outline
bookmarks and the TOC's clickable link annotations.

Binder layout (modelled on an executed example binder): page 1 cover, page 2+
Table of Contents (every row a link), then per document an optional title
page followed by the document itself, untouched.
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


def _toc_links(reader: PdfReader, page_index: int = 1) -> list[int]:
    """Target page (0-indexed) of every /Link GoTo annotation on a page."""
    annots = reader.pages[page_index].get("/Annots")
    if annots is None:
        return []
    targets = []
    for a in annots.get_object():
        o = a.get_object()
        if o.get("/Subtype") != "/Link":
            continue
        action = o.get("/A")
        dest = action.get_object().get("/D") if action else o.get("/Dest")
        first = dest.get_object()[0].get_object()
        # the destination must REFERENCE the page object — a bare page number
        # is only valid for remote go-to and Adobe Reader won't follow it
        assert not isinstance(first, (int, float)), "TOC link uses a bare page number"
        targets.append(reader.get_page_number(first))
    return targets


def test_toc_entries_page_math():
    counts = [("A", 3), ("B", 1)]
    # cover + TOC = 2 lead pages; A = title 3 + doc 4-6, B = title 7 + doc 8
    with_tabs = binder._toc_entries(2, counts, tab_pages=True)
    assert [e["range_label"] for e in with_tabs] == ["3-6", "7-8"]
    assert [e["target"] for e in with_tabs] == [2, 6]
    # no title pages: A = 3-5, B = 6
    without = binder._toc_entries(2, counts, tab_pages=False)
    assert [e["range_label"] for e in without] == ["3-5", "6"]
    assert [e["target"] for e in without] == [2, 5]


def test_binder_with_title_pages_orders_pages_and_bookmarks():
    pdf = binder.build_binder(BinderInfo(borrower_name="Test Borrower"), _docs())
    r = PdfReader(io.BytesIO(pdf))
    # cover + 1 TOC page + (title + 2) + (title + 3)
    assert len(r.pages) == 2 + (1 + 2) + (1 + 3)
    marks = _outline_pages(r)
    assert marks["Cover"] == 0
    assert marks["Table of Contents"] == 1
    assert marks["Promissory Note"] == 2                    # its title page
    assert marks["Loan and Security Agreement"] == 2 + 1 + 2
    # the TOC lists both titles (the blank one from its filename) with ranges
    toc_text = r.pages[1].extract_text().lower()
    assert "table of contents" in toc_text
    assert "promissory note" in toc_text
    assert "loan and security agreement" in toc_text
    assert "3-5" in toc_text and "6-9" in toc_text


def test_binder_toc_rows_are_clickable_links():
    pdf = binder.build_binder(BinderInfo(borrower_name="Test Borrower"), _docs())
    r = PdfReader(io.BytesIO(pdf))
    # one link per document, jumping to that section's title page
    assert _toc_links(r) == [2, 5]
    # no links anywhere else in the front matter
    assert _toc_links(r, page_index=0) == []


def test_binder_without_title_pages():
    pdf = binder.build_binder(BinderInfo(), _docs(), tab_pages=False)
    r = PdfReader(io.BytesIO(pdf))
    assert len(r.pages) == 2 + 2 + 3
    marks = _outline_pages(r)
    assert marks["Promissory Note"] == 2
    assert marks["Loan and Security Agreement"] == 4
    assert _toc_links(r) == [2, 4]


def test_binder_rejects_non_pdf_with_filename():
    bad = BinderDoc(title="Contract", filename="contract.docx",
                    b64=base64.b64encode(b"this is not a pdf").decode())
    with pytest.raises(ValueError) as exc:
        binder.build_binder(BinderInfo(), [bad])
    assert "contract.docx" in str(exc.value)


def test_binder_requires_documents():
    with pytest.raises(ValueError):
        binder.build_binder(BinderInfo(), [])
