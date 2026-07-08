"""Pydantic models for the Loan Documents builder.

LoanDocTerms carries the confirmed values injected into
templates/loan_documents.html.j2 — one field per template placeholder (or the
raw value it is formatted from). Everything is optional so a partially filled
form still renders (blank placeholders come out as underscored blanks or empty
strings, same as the source template's unexecuted copies).
"""

from __future__ import annotations

from datetime import date
from typing import Optional

from pydantic import BaseModel


class SettlementLine(BaseModel):
    """One deduction row on the Memo of Settlement (entered positive)."""

    label: str = ""
    amount: Optional[float] = None


class ScheduleRow(BaseModel):
    """One repayment row for the Note's Exhibit A (overrides the computed
    schedule when supplied, e.g. pulled from the credit memo extraction)."""

    date: str = ""
    interest: float = 0
    principal: float = 0
    total: Optional[float] = None


class LoanDocsInclude(BaseModel):
    affidavit: bool = True
    note: bool = True
    lsa: bool = True
    guaranty: bool = True
    settlement: bool = True
    ucc: bool = True
    letter: bool = True


class LoanDocTerms(BaseModel):
    # Borrower
    borrower_name: str = ""
    borrower_street: str = ""
    borrower_city: str = ""
    borrower_state_abbr: str = ""   # e.g. "FL" (UCC form cells)
    borrower_zip: str = ""
    borrower_state: str = ""        # spelled out, e.g. "Florida" (LSA text)
    occupation: str = ""            # e.g. "Professional Baseball Player"
    use_of_proceeds: str = "Business related expenses"

    # Deal
    loan_amount: Optional[float] = None
    interest_rate: Optional[float] = None       # % p.a.
    # Repayment structure: drives the Note's Payment clause wording and the
    # computed Exhibit A schedule (a workbook/extraction schedule still wins).
    amortization_type: str = "balloon"          # balloon | interest_only | fully_amortized
    # Whether the loan carries a death & disgrace Insurance Policy. Swaps the
    # LSA Exhibit A "Insurance Policy" definition between the policy wording
    # (True) and the sports template's waived wording (False, the default).
    has_insurance_policy: bool = False
    origination_fee_pct: Optional[float] = None  # % of loan (commitment fee)
    origination_fee_amount: Optional[float] = None  # overrides pct calc
    closing_date: Optional[date] = None
    maturity_date: Optional[date] = None
    loan_number: str = ""

    # Note terms
    prepay_min_months: str = "two"
    late_charge_pct: Optional[float] = 10
    exit_fee_pct: Optional[float] = 10
    default_rate_points: Optional[float] = 5

    # Team / contract
    team_name: str = ""
    team_street: str = ""
    team_city_state_zip: str = ""
    league: str = ""
    contract_title: str = ""        # defaults to "<league> Professional Contract"
    contract_date: Optional[date] = None

    # Lender signatory (name is fixed: James Plack)
    lender_signatory_title: str = "CEO"

    # Payment direction letter — SRC's receiving account. Empty fields fall
    # back to the SRC_BANK_* values in .env so account numbers never live in
    # this (public) repo.
    account_name: str = ""
    bank_name: str = ""
    bank_account_no: str = ""
    bank_aba: str = ""
    bank_address_1: str = ""
    bank_address_2: str = ""
    bank_contact: str = ""
    bank_phone: str = ""

    # Memo of Settlement deductions (rendered between the Gross Loan Amount
    # row and the computed "To be disbursed to Borrower (Est)" row)
    settlement_lines: list[SettlementLine] = []

    # Optional Exhibit A schedule override (else computed from the deal terms)
    repayment_schedule: Optional[list[ScheduleRow]] = None


class LoanDocsRequest(BaseModel):
    terms: LoanDocTerms
    include: LoanDocsInclude = LoanDocsInclude()


class SettlementSheetResult(BaseModel):
    """What /api/loandocs/settlement pulls out of the amortization workbook."""

    settlement_sheet: str = ""              # tab the fee block came from
    gross_loan_amount: Optional[float] = None
    lines: list[SettlementLine] = []
    disbursed_check: Optional[float] = None  # sheet's own figure, for comparison
    schedule_sheet: str = ""                # tab the Exhibit A rows came from
    schedule: list[ScheduleRow] = []
