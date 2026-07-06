"""
Financial calculations for the credit memo.

This module is the authoritative home for every underwriting rule. The rules
were developed carefully and are locked in by tests/test_calculations.py using
the Alvarado reference deal. Do NOT change a rule here without updating the
tests to match the intended new behavior.

Underwriting rules encoded here
-------------------------------
1.  Taxes are ALWAYS 45% of gross income (salary + other income). Never pulled
    from documents.
2.  Ordinary living expenses are ALWAYS 10% of gross income.
3.  In the Guarantor Analysis cash flow, the Proposed Facility line is the loan
    PRINCIPAL only.
4.  On the Personal Financial Statement, the Proposed Facility is loan + interest
    (the full amount due). Interest comes from the amortization schedule, or
    falls back to a facility total stated in the documents.
5.  Net Worth = Total Assets - Total Liabilities. Always calculated, never copied
    from a stated figure in the documents.
6.  Total Liabilities includes the Proposed Facility (loan + interest).
7.  Alimony / child support is a cash-flow item ONLY (Available for Debt). It is
    never a PFS liability.
8.  Auto loan balances are never a separate PFS liability row (they live inside
    "Notes Payable to: others"). Monthly auto PAYMENTS still appear in the cash flow.
9.  Salary used everywhere is the GUARANTEED portion of compensation only:
    the guaranteed base salary PLUS any bonus that is guaranteed and paid
    every year of the contract (e.g. annual signing-bonus installments,
    guaranteed yearly roster bonuses). Non-guaranteed incentives, one-time
    bonuses, and endorsements stay excluded. When installments differ season
    to season, the amount used is the one SCHEDULED FOR THAT SPECIFIC
    current/upcoming season — never an average, never another season's.
    Reference example: $1,000,000 base + $9,000,000 guaranteed bonus
    scheduled for the season = $10,000,000 salary (never the base alone,
    never the $39,500,000 remaining contract value, and the bonus is not
    double-counted as other income). The total remaining contract value is
    captured separately (Extraction.contract_remaining) and shown in
    Section VII; it is display-only and feeds no calculation.
10. LTC (Loan-to-Contract) = loan amount / guaranteed earnings.
11. Taxes are NEVER a PFS liability. Even when the Personal Financial Statement
    reports an estimated tax figure (e.g. "Taxes (Est of 35% of ...)"), it is
    excluded from Total Liabilities and from Net Worth.
12. Section VI (Uses of Funds) reproduces EVERY disbursement line provided in the
    documents (fees, payoffs, closing costs, insurance, interest reserve, ...).
    The "To be disbursed to Borrower" and "Net to be Disbursed to Borrower"
    subtotals are always recomputed from the lines, never copied. When the
    documents carry no breakdown, it falls back to gross loan less the
    origination fee from the deal terms.
13. The loan term in months (Section II Action Request) prefers the term stated
    in the documents (a term sheet's "Term: N months"); it falls back to the
    funding-to-maturity span used for the amortization schedule.
14. The memo phrases the borrower as "a Professional <sport> player", so the
    sport value is normalized to drop a leading "professional" — the memo must
    never render "Professional Professional ...".
"""

from __future__ import annotations

import re
from datetime import date
from typing import Optional

from .models import Extraction, LineItem


# --- Row classifiers (mirror the JS regexes exactly) ----------------------

_FACILITY_RE = re.compile(r"proposed\s*facility", re.I)
_AUTO_RE_A = re.compile(r"\b(auto(mobile)?|vehicle|car)\b.*\b(loan|note|debt|financ)", re.I)
_AUTO_RE_B = re.compile(r"\b(auto|car)\s*loans?\b", re.I)
_ALIMONY_RE = re.compile(r"alimony|child\s*support", re.I)
_COMPUTED_RE_TAX = re.compile(r"\b(income\s*tax|federal.*tax|taxes)\b", re.I)
_COMPUTED_RE_LIVING = re.compile(r"ordinary\s*living|living\s*expense", re.I)


def _label(item) -> str:
    if item is None:
        return ""
    if isinstance(item, dict):
        return item.get("label") or ""
    return getattr(item, "label", "") or ""


def _amount(item) -> float:
    if item is None:
        return 0.0
    if isinstance(item, dict):
        return item.get("amount") or 0.0
    return getattr(item, "amount", 0.0) or 0.0


def is_facility_row(item) -> bool:
    return bool(_FACILITY_RE.search(_label(item)))


def is_auto_loan_row(item) -> bool:
    lbl = _label(item)
    return bool(_AUTO_RE_A.search(lbl) or _AUTO_RE_B.search(lbl))


def is_alimony_row(item) -> bool:
    return bool(_ALIMONY_RE.search(_label(item)))


def is_computed_row(item) -> bool:
    lbl = _label(item)
    return bool(_COMPUTED_RE_TAX.search(lbl) or _COMPUTED_RE_LIVING.search(lbl))


def is_tax_row(item) -> bool:
    return bool(_COMPUTED_RE_TAX.search(_label(item)))


def _sum(items) -> float:
    return sum(_amount(i) for i in (items or []))


# --- Amortization ----------------------------------------------------------

def calc_amort(principal: float, rate: float, fund_date: date, mat_date: date) -> dict:
    """Single-payment balloon facility. Interest accrues actual/365.

    Returns interest, balloon (principal + interest), month count, and a row
    schedule for display.
    """
    months = (mat_date.year - fund_date.year) * 12 + (mat_date.month - fund_date.month)
    days = (mat_date - fund_date).days
    interest = round(principal * (rate / 100 / 365) * days)
    balloon = principal + interest

    rows = [{
        "num": "",
        "date": f"Funding — {fund_date.strftime('%b %Y')}",
        "principal": None, "interest": None, "payment": None,
        "balance": principal, "is_fund": True,
    }]
    for i in range(1, months + 1):
        # advance i months from funding
        y = fund_date.year + (fund_date.month - 1 + i) // 12
        m = (fund_date.month - 1 + i) % 12 + 1
        d = date(y, m, min(fund_date.day, 28))
        is_balloon = i == months
        rows.append({
            "num": i,
            # integer fields, not the glibc-only "%-m/%-d" codes (ValueError on Windows)
            "date": f"{d.month}/{d.day}/{d.year}",
            "principal": principal if is_balloon else 0,
            "interest": interest if is_balloon else 0,
            "payment": balloon if is_balloon else 0,
            "balance": 0 if is_balloon else principal,
            "is_balloon": is_balloon,
        })
    return {"rows": rows, "interest": interest, "balloon": balloon, "months": months}


# --- Repayment schedule (display) -----------------------------------------

def calc_repayment_schedule(principal: float, rate: float,
                            fund_date: date, mat_date: date) -> dict:
    """Fallback repayment schedule for Section X when the documents don't carry one.

    Mirrors how South River's facilities actually repay: interest is paid every
    month (equal installments of principal * rate / 12) and the principal is
    repaid as a single balloon on the final payment. This is presentation only —
    it does NOT change ``calc_amort`` (the actual/365 interest used for the
    facility total on the PFS).

    Returns one row per monthly payment ({num, date, interest, principal, total,
    is_balloon}) plus column totals. Totals are the sum of the displayed rows so
    the table always foots.
    """
    months = (mat_date.year - fund_date.year) * 12 + (mat_date.month - fund_date.month)
    months = max(months, 1)
    monthly_interest = round((principal or 0) * (rate / 100) / 12)

    rows = []
    for i in range(1, months + 1):
        y = fund_date.year + (fund_date.month - 1 + i) // 12
        m = (fund_date.month - 1 + i) % 12 + 1
        d = date(y, m, min(fund_date.day, 28))
        is_balloon = i == months
        prin = principal if is_balloon else 0
        rows.append({
            "num": i,
            # integer day + %b/%y, never the glibc-only "%-d" (ValueError on Windows)
            "date": f"{d.day}-{d.strftime('%b')}-{d.strftime('%y')}",
            "interest": monthly_interest,
            "principal": prin,
            "total": monthly_interest + prin,
            "is_balloon": is_balloon,
        })

    total_interest = sum(r["interest"] for r in rows)
    total_principal = sum(r["principal"] for r in rows)
    return {
        "rows": rows,
        "total_interest": total_interest,
        "total_principal": total_principal,
        "total_payment": total_interest + total_principal,
        "months": months,
    }


# --- Facility total --------------------------------------------------------

def facility_total(ed: Optional[Extraction], amort: Optional[dict], loan: float) -> float:
    """Facility amount due = loan + interest.

    Prefer interest computed from the form's rate/dates. If those aren't set,
    fall back to a facility total stated in the uploaded documents.
    """
    if amort and amort.get("interest", 0) > 0:
        return (loan or 0) + amort["interest"]
    if ed and (ed.facility_total_due or 0) > 0:
        return ed.facility_total_due
    return loan or 0


def loan_term_months(ed: Optional[Extraction], amort: Optional[dict]) -> int:
    """Number of months the lender provides the loan, for the Section II
    Action Request.

    Prefers the term stated in the deal documents (a term sheet's
    "Term: N months"); falls back to the funding-to-maturity span computed for
    the amortization schedule. Returns 0 when neither is available.
    """
    if ed and (ed.loan_term_months or 0) > 0:
        return int(ed.loan_term_months)
    if amort and (amort.get("months") or 0) > 0:
        return int(amort["months"])
    return 0


# --- Balance sheet (PFS) ---------------------------------------------------

def calc_balance_sheet(ed: Optional[Extraction], facility_due: float) -> dict:
    """Net Worth = Total Assets - Total Liabilities, where liabilities include
    the proposed facility at loan + interest.

    Excludes from liabilities: the facility itself (added once below), auto-loan
    rows (folded into Notes Payable to: others), alimony/child support
    (a cash-flow item only), and tax rows (never a PFS liability, even when the
    Personal Financial Statement reports an estimated tax figure).
    """
    assets_total = _sum(ed.assets if ed else None) or (ed.total_assets if ed else 0) or 0

    liab_items = [
        l for l in (ed.liabilities if ed else [])
        if not is_facility_row(l) and not is_auto_loan_row(l)
        and not is_alimony_row(l) and not is_tax_row(l)
    ]
    stated_liab = _sum(liab_items) or (ed.total_liabilities if ed else 0) or 0
    total_liab = stated_liab + (facility_due or 0)
    return {
        "assets_total": assets_total,
        "stated_liab": stated_liab,
        "total_liab": total_liab,
        "net_worth": assets_total - total_liab,
        "liab_items": liab_items,
    }


# --- Uses of Funds (disbursement waterfall) -------------------------------

def calc_uses_of_funds(uof, loan: float, fee_pct: float) -> dict:
    """Build the Section VI disbursement waterfall.

    Prefers the disbursement breakdown captured from the uploaded documents so
    EVERY line provided (origination/underwriting fees, payoffs, closing costs,
    insurance, interest reserve, ...) appears on the memo. The two subtotals are
    ALWAYS recomputed from the line items, never copied from the documents
    (consistent with rule 5 — totals are calculated):

        to_borrower     = gross loan − Σ deductions
        net_to_borrower = to_borrower − Σ additional_costs

    Falls back to a gross-loan / origination-fee table built from the deal terms
    when the documents carry no breakdown, so Section VI is never empty.

    ``uof`` is a UsesOfFunds (or None). All input amounts are positive
    magnitudes; zero-amount lines are dropped.
    """
    if uof and (uof.gross_loan_amount or uof.deductions or uof.additional_costs):
        gross = uof.gross_loan_amount or (loan or 0)
        deductions = [{"label": _label(d), "amount": _amount(d)}
                      for d in uof.deductions if _amount(d)]
        additional = [{"label": _label(a), "amount": _amount(a)}
                      for a in uof.additional_costs if _amount(a)]
    else:
        gross = loan or 0
        fee_amt = round(gross * (fee_pct or 0) / 100)
        deductions = [{"label": f"Origination Fee ({fee_pct:g}%)", "amount": fee_amt}] if fee_amt else []
        additional = []

    to_borrower = gross - sum(d["amount"] for d in deductions)
    net_to_borrower = to_borrower - sum(a["amount"] for a in additional)
    return {
        "gross": gross,
        "deductions": deductions,
        "to_borrower": to_borrower,
        "additional_costs": additional,
        "net_to_borrower": net_to_borrower,
    }


# --- Cash flow (Guarantor Analysis) ---------------------------------------

def build_cash_flow(ed: Optional[Extraction], amort: Optional[dict],
                    loan: float, form_salary: float) -> dict:
    salary_income = (ed.salary if ed and ed.salary else 0) or (form_salary or 0)
    other_income = (ed.other_income if ed else 0) or 0
    income = salary_income + other_income

    taxes = round(income * 0.45)         # rule 1
    living = round(income * 0.10)        # rule 2
    avail = income - taxes - living

    proposed_ds = loan or 0              # rule 3: principal only in cash flow

    debt_items: list[dict] = []
    if ed:
        if ed.mortgage_payments:
            debt_items.append({"label": "Mortgage payments (incl. taxes & ins.)", "amt": ed.mortgage_payments})
        if ed.auto_payments:
            debt_items.append({"label": "Automobile payments", "amt": ed.auto_payments})
        if ed.insurance:
            debt_items.append({"label": "Insurance (home, health, vehicles)", "amt": ed.insurance})

        # rule 7 sourcing: alimony from the dedicated field, an other-expenses
        # row, or even a misfiled liabilities row — always surfaced here.
        alimony_amt = (
            ed.alimony
            or _sum([x for x in (ed.other_expenses or []) if is_alimony_row(x)])
            or _sum([x for x in (ed.liabilities or []) if is_alimony_row(x)])
        )
        if alimony_amt:
            debt_items.append({"label": "Alimony / child support", "amt": alimony_amt})

        if ed.student_loans:
            debt_items.append({"label": "Student loans", "amt": ed.student_loans})
        if ed.interest_principal_loans:
            debt_items.append({"label": "Interest & principal on loans", "amt": ed.interest_principal_loans})
        if ed.hoa_payments:
            debt_items.append({"label": "HOA payments", "amt": ed.hoa_payments})

        # Every remaining annual-expenditure item flows in, except the rows we
        # compute ourselves (taxes/living) and alimony (already added once).
        for x in (ed.other_expenses or []):
            if _amount(x) and not is_alimony_row(x) and not is_computed_row(x):
                debt_items.append({"label": _label(x), "amt": _amount(x)})

    other_debt = sum(d["amt"] for d in debt_items)
    total_ds = proposed_ds + other_debt

    return {
        "income": income,
        "salary_income": salary_income,
        "other_income": other_income,
        "taxes": taxes,
        "living": living,
        "avail": avail,
        "proposed_ds": proposed_ds,
        "debt_items": debt_items,
        "total_ds": total_ds,
        "net_cf": avail - total_ds,
    }


def calc_ltc(loan: float, guaranteed_salary: float) -> float:
    """Loan-to-Contract = loan / guaranteed earnings, as a percentage."""
    return (loan / guaranteed_salary * 100) if guaranteed_salary else 0.0


# --- SSN masking -----------------------------------------------------------

def mask_ssn(value) -> str:
    digits = re.sub(r"\D", "", str(value or ""))[-4:]
    return f"XXX-XX-{digits}" if digits else ""


# --- Sport label -----------------------------------------------------------

_PRO_PREFIX_RE = re.compile(r"^\s*professional\s+", re.I)


def normalize_sport(sport) -> str:
    """Strip a leading 'Professional' from the sport name.

    The memo phrases this as "a Professional <sport> player", so a sport value
    of "Professional Ice Hockey" would render the word twice. Dropping the
    prefix here guarantees the memo never says "Professional Professional ...".
    """
    return _PRO_PREFIX_RE.sub("", str(sport or "")).strip()
