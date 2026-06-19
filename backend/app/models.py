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


class DisbursementItem(BaseModel):
    """One line in the Section VI disbursement / Uses-of-Funds waterfall."""
    label: str
    amount: float = 0.0


class UsesOfFunds(BaseModel):
    """The disbursement waterfall as the deal documents present it (Section VI).

    ``gross_loan_amount`` is the full proposed facility. ``deductions`` are the
    fees and payoffs taken out of the gross loan to reach the amount "to be
    disbursed to Borrower" (origination/underwriting fees, payoffs of existing
    loans, legal/closing costs, etc.). ``additional_costs`` are amounts funded
    from the loan and carved out of the to-Borrower figure to reach the NET
    disbursed (e.g. Death & Disgrace insurance premium, Interest Reserve).

    All amounts are positive dollar magnitudes; the subtotals (to-Borrower, net)
    are always recomputed from these lines, never copied from the documents.
    """
    gross_loan_amount: float = 0.0
    deductions: list[DisbursementItem] = Field(default_factory=list)
    additional_costs: list[DisbursementItem] = Field(default_factory=list)


class RepaymentRow(BaseModel):
    """One scheduled payment in the loan's repayment/amortization schedule.

    Captured from the uploaded documents when present (see extraction.py); the
    memo's Section X reproduces these rows verbatim. ``total`` is that payment's
    interest + principal and may be left 0 to be computed at render time.
    """
    date: str = ""                    # payment date as shown, e.g. "15-Jul-26"
    interest: float = 0.0
    principal: float = 0.0
    total: float = 0.0


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

    # The loan's repayment schedule as it appears in the uploaded documents.
    # Empty when the documents contain no schedule (Section X then computes one).
    repayment_schedule: list[RepaymentRow] = Field(default_factory=list)

    # The disbursement / Uses-of-Funds breakdown from the documents (Section VI).
    # None when the documents carry no breakdown (Section VI then falls back to a
    # gross-loan/origination-fee table built from the deal terms).
    uses_of_funds: Optional[UsesOfFunds] = None

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
    loan_type: str = "New Loan"


class MemoRequest(BaseModel):
    terms: DealTerms
    extraction: Optional[Extraction] = None


class UploadedDoc(BaseModel):
    filename: str
    mime: str
    b64: str                          # base64-encoded file contents
