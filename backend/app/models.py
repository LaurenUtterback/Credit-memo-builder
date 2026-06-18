"""Pydantic models — the typed contract between the Vue frontend, the FastAPI
backend, and the Anthropic extraction step.

FastAPI uses these to validate requests and to auto-generate the OpenAPI schema
at /docs, which is the single source of truth your frontend can read.
"""

from __future__ import annotations

from typing import Optional
from datetime import date

from pydantic import BaseModel, Field


class LineItem(BaseModel):
    label: str
    amount: float = 0.0


class Extraction(BaseModel):
    """Structured data pulled from uploaded documents by Claude.

    Field names match the JSON the extraction prompt asks the model to return,
    so the prompt and this model must stay in sync (see extraction.py).
    """
    borrower_name: Optional[str] = None
    dob: Optional[str] = None
    address: Optional[str] = None
    phone: Optional[str] = None
    team: Optional[str] = None
    league: Optional[str] = None
    sport: Optional[str] = None
    ssn_masked: Optional[str] = None
    drivers_license: Optional[str] = None
    agent: Optional[str] = None

    salary: float = 0.0               # GUARANTEED season compensation only
    other_income: float = 0.0
    total_income: float = 0.0
    federal_taxes: float = 0.0        # captured but NOT used (taxes computed at 45%)
    mortgage_payments: float = 0.0
    hoa_payments: float = 0.0
    student_loans: float = 0.0
    interest_principal_loans: float = 0.0
    insurance: float = 0.0
    alimony: float = 0.0
    auto_payments: float = 0.0
    living_expenses: float = 0.0      # captured but NOT used (living computed at 10%)
    other_expenses: list[LineItem] = Field(default_factory=list)
    total_expenditures: float = 0.0

    assets: list[LineItem] = Field(default_factory=list)
    total_assets: float = 0.0
    liabilities: list[LineItem] = Field(default_factory=list)
    total_liabilities: float = 0.0
    net_worth: float = 0.0            # captured but NOT used (recomputed)
    facility_total_due: float = 0.0

    credit_notes: Optional[str] = None
    contract_notes: Optional[str] = None
    sponsorship_narrative: Optional[str] = None


class DealTerms(BaseModel):
    """The deal terms a user confirms before generating a memo."""
    name: str = ""
    dob: str = ""
    addr: str = ""
    phone: str = ""
    team: str = ""
    league: str = ""
    sport: str = ""
    ssn: str = ""
    dl: str = ""
    agent: str = ""

    loan: float = 0.0
    rate: float = 0.0                 # annual % (e.g. 12 for 12%)
    fee: float = 0.0
    salary: float = 0.0               # guaranteed season salary
    fund: Optional[date] = None       # funding date
    mat: Optional[date] = None        # maturity date
    loan_type: str = "Single-Pay Balloon"


class MemoRequest(BaseModel):
    terms: DealTerms
    extraction: Optional[Extraction] = None


class UploadedDoc(BaseModel):
    filename: str
    mime: str
    b64: str                          # base64-encoded file contents
