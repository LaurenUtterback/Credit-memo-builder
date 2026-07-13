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
from app import binder_extraction
from app.binder_models import BinderDoc, BinderInfo, BinderPart


def _pdf_b64(pages: int, width: float = 612) -> str:
    """A blank PDF; a custom width marks its pages so slicing is checkable."""
    w = PdfWriter()
    for _ in range(pages):
        w.add_blank_page(width=width, height=792)
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


def test_binder_sections_from_page_ranges_and_merged_parts():
    # one "signed package" split into two sections by page range, plus two
    # insurance files merged into a single section — widths mark the sources
    package = _pdf_b64(5, width=100)
    ins1, ins2 = _pdf_b64(1, width=200), _pdf_b64(2, width=300)
    docs = [
        BinderDoc(title="Promissory Note",
                  parts=[BinderPart(filename="pkg.pdf", b64=package, page_from=1, page_to=2)]),
        BinderDoc(title="Loan and Security Agreement",
                  parts=[BinderPart(filename="pkg.pdf", b64=package, page_from=3, page_to=5)]),
        BinderDoc(title="Insurance Documents",
                  parts=[BinderPart(filename="quote.pdf", b64=ins1),
                         BinderPart(filename="policy.pdf", b64=ins2)]),
    ]
    pdf = binder.build_binder(BinderInfo(borrower_name="Test Borrower"), docs, tab_pages=False)
    r = PdfReader(io.BytesIO(pdf))
    assert len(r.pages) == 2 + 2 + 3 + 3
    widths = [round(float(p.mediabox.width)) for p in r.pages[2:]]
    assert widths == [100, 100, 100, 100, 100, 200, 300, 300]
    marks = _outline_pages(r)
    assert marks["Loan and Security Agreement"] == 4
    assert marks["Insurance Documents"] == 7


def test_binder_rejects_bad_page_range():
    doc = BinderDoc(title="Note", parts=[
        BinderPart(filename="pkg.pdf", b64=_pdf_b64(3), page_from=2, page_to=9)])
    with pytest.raises(ValueError) as exc:
        binder.build_binder(BinderInfo(), [doc])
    assert "pkg.pdf" in str(exc.value) and "2-9" in str(exc.value)


def test_organize_orders_sections_and_merges_categories():
    entries = [
        {"file_index": 1, "first_page": 1, "last_page": 1, "category": "package_cover"},
        {"file_index": 1, "first_page": 10, "last_page": 12, "category": "ucc"},
        {"file_index": 1, "first_page": 2, "last_page": 3, "category": "note"},
        {"file_index": 1, "first_page": 4, "last_page": 7, "category": "lsa"},
        # the LSA's Exhibit A reported separately -> must merge into one section
        {"file_index": 1, "first_page": 8, "last_page": 9, "category": "lsa"},
        {"file_index": 3, "first_page": 1, "last_page": 2, "category": "insurance"},
        {"file_index": 1, "first_page": 13, "last_page": 13, "category": "other",
         "title": "Wire Confirmation"},
        {"file_index": 2, "first_page": 1, "last_page": 4, "category": "insurance"},
    ]
    sections, notes = binder_extraction._organize(entries, [13, 4, 2])
    assert [s.title for s in sections] == [
        "Promissory Note", "Loan and Security Agreement", "UCC",
        "Wire Confirmation", "Insurance Documents"]
    lsa = sections[1].parts
    assert [(p.page_from, p.page_to) for p in lsa] == [(4, 7), (8, 9)]
    ins = sections[-1].parts
    assert [(p.file_index, p.page_from, p.page_to) for p in ins] == [(2, 1, 4), (3, 1, 2)]
    assert notes == []  # every page accounted for (cover dropped but counted)


def test_organize_reports_unassigned_pages_and_bad_entries():
    entries = [
        {"file_index": 1, "first_page": 1, "last_page": 2, "category": "note"},
        {"file_index": 9, "first_page": 1, "last_page": 1, "category": "ucc"},
    ]
    sections, notes = binder_extraction._organize(entries, [5])
    assert [s.title for s in sections] == ["Promissory Note"]
    assert any("3-5" in n and "file 1" in n for n in notes)
    assert any("file 9" in n for n in notes)


def test_binder_rejects_non_pdf_with_filename():
    bad = BinderDoc(title="Contract", filename="contract.docx",
                    b64=base64.b64encode(b"this is not a pdf").decode())
    with pytest.raises(ValueError) as exc:
        binder.build_binder(BinderInfo(), [bad])
    assert "contract.docx" in str(exc.value)


def test_binder_requires_documents():
    with pytest.raises(ValueError):
        binder.build_binder(BinderInfo(), [])
