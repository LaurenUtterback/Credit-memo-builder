"""Deal-info extraction for the Closing Binder's Step 1.

Reads whatever deal documents the user drops (a South River credit
memorandum, the closing/loan documents package, a term sheet, or the executed
set) and pulls the four cover-page fields the binder needs. Reuses the shared
Claude usage-token call from the Loan Documents extractor.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel

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
