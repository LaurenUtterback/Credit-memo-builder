"""Pydantic models for the Closing Binder builder."""

from __future__ import annotations

from datetime import date
from typing import Optional

from pydantic import BaseModel, Field


class BinderDoc(BaseModel):
    """One executed document going into the binder. PDF only — executed
    closing documents come back from signing as PDFs (often scans)."""

    title: str = ""          # shown in the index and on the tab page;
                             # blank falls back to a cleaned-up filename
    filename: str = ""
    mime: str = "application/pdf"
    b64: str


class BinderInfo(BaseModel):
    """Deal-level fields shown on the binder's cover page."""

    borrower_name: str = ""
    team_name: str = ""
    loan_amount: Optional[float] = None
    loan_number: str = ""
    closing_date: Optional[date] = None


class BinderRequest(BaseModel):
    info: BinderInfo = Field(default_factory=BinderInfo)
    documents: list[BinderDoc] = Field(default_factory=list)
    # A numbered tab page before each document, like the tabs of a physical binder.
    tab_pages: bool = True
