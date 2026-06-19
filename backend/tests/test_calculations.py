"""Tests that lock in the underwriting rules using the Alvarado reference deal.

These numbers were agreed with the business. If a change to calculations.py
breaks a test here, that is the test doing its job: either the change is wrong,
or the rule genuinely changed and the expected number must be updated
deliberately (with sign-off), not silently.

Run:  cd backend && pytest
"""

from datetime import date

import pytest

from app.models import (
    Extraction, LineItem, DealTerms, UsesOfFunds, DisbursementItem,
)
from app import calculations as calc
from app import memo as memo_service


# --- The Alvarado reference deal ------------------------------------------

ALVARADO_FACILITY_DUE = 2_703_754      # loan + interest, per the amort schedule
ALVARADO_LOAN = 2_499_000
ALVARADO_SALARY = 9_000_000            # guaranteed season salary


@pytest.fixture
def alvarado() -> Extraction:
    return Extraction(
        borrower_name="José Alvarado",
        salary=ALVARADO_SALARY,
        other_income=0,
        mortgage_payments=41_700,
        auto_payments=37_584,
        insurance=3_863,
        alimony=120_000,
        assets=[
            LineItem(label="Cash on Hand", amount=39_600),
            LineItem(label="Real Estate", amount=3_228_000),
            LineItem(label="Contract (Remaining)", amount=9_000_000),
        ],
        liabilities=[
            LineItem(label="Notes Payable: Contract Based", amount=5_454_402),
            LineItem(label="Notes Payable to: others", amount=303_095),
            LineItem(label="Mortgage Debt", amount=1_912_110),
        ],
        total_assets=12_267_600,
        facility_total_due=ALVARADO_FACILITY_DUE,
    )


# --- Balance sheet (PFS) ---------------------------------------------------

def test_total_liabilities_includes_facility_with_interest(alvarado):
    bs = calc.calc_balance_sheet(alvarado, ALVARADO_FACILITY_DUE)
    # 2,703,754 + 5,454,402 + 303,095 + 1,912,110
    assert bs["total_liab"] == 10_373_361


def test_net_worth_is_assets_minus_liabilities(alvarado):
    bs = calc.calc_balance_sheet(alvarado, ALVARADO_FACILITY_DUE)
    assert bs["assets_total"] == 12_267_600
    assert bs["net_worth"] == 1_894_239


def test_notes_payable_to_others_is_never_dropped(alvarado):
    bs = calc.calc_balance_sheet(alvarado, ALVARADO_FACILITY_DUE)
    labels = [l.label for l in bs["liab_items"]]
    assert "Notes Payable to: others" in labels


def test_alimony_is_not_a_pfs_liability():
    ed = Extraction(
        assets=[LineItem(label="Cash", amount=100_000)],
        total_assets=100_000,
        liabilities=[
            LineItem(label="Mortgage Debt", amount=50_000),
            LineItem(label="Alimony obligation", amount=84_000),  # misfiled
        ],
    )
    bs = calc.calc_balance_sheet(ed, 0)
    # alimony excluded -> only the mortgage counts
    assert bs["total_liab"] == 50_000


def test_auto_loan_balance_excluded_from_pfs():
    ed = Extraction(
        assets=[LineItem(label="Cash", amount=100_000)],
        total_assets=100_000,
        liabilities=[
            LineItem(label="Notes Payable to: others", amount=303_095),
            LineItem(label="Auto Loans", amount=60_000),  # already inside notes payable
        ],
    )
    bs = calc.calc_balance_sheet(ed, 0)
    assert bs["total_liab"] == 303_095


def test_taxes_are_not_a_pfs_liability():
    # rule 11: the PFS may report an estimated tax figure, but it is never a
    # liability and never reduces Net Worth.
    ed = Extraction(
        assets=[LineItem(label="Cash", amount=100_000)],
        total_assets=100_000,
        liabilities=[
            LineItem(label="Mortgage Debt", amount=50_000),
            LineItem(label="Taxes (Est of 35% of Gtd NHL Contract Remaining)",
                     amount=13_825_000),
        ],
    )
    bs = calc.calc_balance_sheet(ed, 0)
    labels = [l.label for l in bs["liab_items"]]
    assert not any("Taxes" in lbl for lbl in labels)
    # taxes excluded -> only the mortgage counts
    assert bs["total_liab"] == 50_000
    assert bs["net_worth"] == 50_000


# --- Uses of Funds (disbursement waterfall) -------------------------------

def test_uses_of_funds_full_breakdown_from_documents():
    # The example disbursement table: every line provided is carried through,
    # and the two subtotals foot to the lines.
    uof = UsesOfFunds(
        gross_loan_amount=4_435_000,
        deductions=[
            DisbursementItem(label="Lender Origination Fee (Est)", amount=133_050),
            DisbursementItem(label="SS Underwriting Fee (Est)", amount=133_050),
            DisbursementItem(label="BMO Payments thru 6/15/27 (Est)", amount=3_709_235),
            DisbursementItem(label="Legal/Closing Costs (Est)", amount=16_165),
        ],
        additional_costs=[
            DisbursementItem(label="Death & Disgrace Insurance (Est)", amount=110_875),
            DisbursementItem(label="Interest Reserve (Est)", amount=332_625),
        ],
    )
    r = calc.calc_uses_of_funds(uof, loan=4_435_000, fee_pct=3)
    assert r["gross"] == 4_435_000
    assert [d["label"] for d in r["deductions"]] == [
        "Lender Origination Fee (Est)", "SS Underwriting Fee (Est)",
        "BMO Payments thru 6/15/27 (Est)", "Legal/Closing Costs (Est)",
    ]
    assert r["to_borrower"] == 443_500       # gross − Σ deductions
    assert [a["label"] for a in r["additional_costs"]] == [
        "Death & Disgrace Insurance (Est)", "Interest Reserve (Est)",
    ]
    assert r["net_to_borrower"] == 0         # to_borrower − Σ additional_costs


def test_uses_of_funds_subtotals_are_recomputed_not_copied():
    # Even if a single deduction changes, the subtotals follow the lines.
    uof = UsesOfFunds(
        gross_loan_amount=1_000_000,
        deductions=[DisbursementItem(label="Origination Fee", amount=20_000)],
        additional_costs=[DisbursementItem(label="Interest Reserve", amount=80_000)],
    )
    r = calc.calc_uses_of_funds(uof, loan=1_000_000, fee_pct=2)
    assert r["to_borrower"] == 980_000
    assert r["net_to_borrower"] == 900_000


def test_uses_of_funds_drops_zero_lines():
    uof = UsesOfFunds(
        gross_loan_amount=500_000,
        deductions=[
            DisbursementItem(label="Origination Fee", amount=10_000),
            DisbursementItem(label="Empty line", amount=0),
        ],
    )
    r = calc.calc_uses_of_funds(uof, loan=500_000, fee_pct=2)
    assert [d["label"] for d in r["deductions"]] == ["Origination Fee"]
    assert r["to_borrower"] == 490_000


def test_uses_of_funds_falls_back_to_deal_terms():
    # No disbursement breakdown in the documents -> gross loan less the
    # origination fee from the deal terms, so Section VI is never empty.
    r = calc.calc_uses_of_funds(None, loan=2_499_000, fee_pct=2)
    assert r["gross"] == 2_499_000
    assert len(r["deductions"]) == 1
    assert r["deductions"][0]["label"] == "Origination Fee (2%)"
    assert r["deductions"][0]["amount"] == 49_980
    assert r["to_borrower"] == 2_449_020
    assert r["additional_costs"] == []


def test_render_html_contains_uses_of_funds_lines():
    ed = Extraction(
        uses_of_funds=UsesOfFunds(
            gross_loan_amount=4_435_000,
            deductions=[
                DisbursementItem(label="Lender Origination Fee (Est)", amount=133_050),
                DisbursementItem(label="BMO Payments thru 6/15/27 (Est)", amount=3_709_235),
            ],
            additional_costs=[
                DisbursementItem(label="Interest Reserve (Est)", amount=332_625),
            ],
        ),
    )
    terms = DealTerms(name="Test Borrower", loan=4_435_000, salary=9_000_000)
    html = memo_service.render_html(terms, ed, [])
    assert "Gross Loan Amount" in html
    assert "Lender Origination Fee (Est)" in html
    assert "BMO Payments thru 6/15/27 (Est)" in html
    assert "($3,709,235)" in html                       # deduction shown in parens
    assert "To be disbursed to Borrower (Est)" in html
    assert "Interest Reserve (Est)" in html
    assert "Net to be Disbursed to Borrower (Est)" in html


# --- Cash flow (Guarantor Analysis) ---------------------------------------

def test_taxes_are_45_percent_of_gross(alvarado):
    cf = calc.build_cash_flow(alvarado, None, ALVARADO_LOAN, ALVARADO_SALARY)
    assert cf["income"] == 9_000_000
    assert cf["taxes"] == 4_050_000          # 45%
    assert cf["living"] == 900_000           # 10%


def test_other_income_is_added_to_gross():
    ed = Extraction(salary=9_000_000, other_income=52_553)
    cf = calc.build_cash_flow(ed, None, ALVARADO_LOAN, 0)
    assert cf["income"] == 9_052_553
    assert cf["taxes"] == round(9_052_553 * 0.45)


def test_facility_in_cash_flow_is_principal_only(alvarado):
    cf = calc.build_cash_flow(alvarado, None, ALVARADO_LOAN, ALVARADO_SALARY)
    assert cf["proposed_ds"] == ALVARADO_LOAN  # NOT the interest-included figure


def test_alimony_always_appears_in_cash_flow(alvarado):
    cf = calc.build_cash_flow(alvarado, None, ALVARADO_LOAN, ALVARADO_SALARY)
    labels = [d["label"] for d in cf["debt_items"]]
    assert "Alimony / child support" in labels


def test_alimony_from_other_expenses_row():
    ed = Extraction(
        salary=1_000_000,
        other_expenses=[LineItem(label="Child Support", amount=96_000)],
    )
    cf = calc.build_cash_flow(ed, None, 0, 0)
    alimony = [d for d in cf["debt_items"] if d["label"] == "Alimony / child support"]
    assert alimony and alimony[0]["amt"] == 96_000


def test_computed_rows_not_double_counted():
    ed = Extraction(
        salary=1_000_000,
        other_expenses=[
            LineItem(label="Income Taxes", amount=450_000),       # skipped (computed)
            LineItem(label="Ordinary Living Expenses", amount=100_000),  # skipped
            LineItem(label="Private school tuition", amount=60_000),     # kept
        ],
    )
    cf = calc.build_cash_flow(ed, None, 0, 0)
    labels = [d["label"] for d in cf["debt_items"]]
    assert "Private school tuition" in labels
    assert "Income Taxes" not in labels
    assert "Ordinary Living Expenses" not in labels


# --- Facility total --------------------------------------------------------

def test_facility_total_prefers_computed_interest():
    amort = {"interest": 203_754, "balloon": 0, "rows": [], "months": 12}
    assert calc.facility_total(None, amort, ALVARADO_LOAN) == ALVARADO_LOAN + 203_754


def test_facility_total_falls_back_to_documents(alvarado):
    # No amort computed -> use the documents' stated facility total
    assert calc.facility_total(alvarado, None, ALVARADO_LOAN) == ALVARADO_FACILITY_DUE


# --- Amortization ----------------------------------------------------------

def test_amort_interest_actual_365():
    amort = calc.calc_amort(2_499_762, 12.0, date(2026, 1, 1), date(2027, 1, 1))
    # 2,499,762 * 12% * 365/365 = 299,971 (rounded)
    assert amort["interest"] == round(2_499_762 * 0.12)
    assert amort["balloon"] == 2_499_762 + amort["interest"]
    assert amort["months"] == 12


# --- Repayment schedule (Section X display) --------------------------------

def test_repayment_schedule_interest_monthly_principal_balloon():
    # Interest paid every month; principal repaid as a single balloon at the end.
    sched = calc.calc_repayment_schedule(4_435_000, 15.0, date(2026, 6, 15), date(2026, 12, 15))
    monthly = round(4_435_000 * 0.15 / 12)   # 55,438

    assert sched["months"] == 6
    assert len(sched["rows"]) == 6
    assert monthly == 55_438
    # interest is paid in every period
    assert all(r["interest"] == monthly for r in sched["rows"])
    # principal appears only on the final (balloon) payment
    assert [r["principal"] for r in sched["rows"]] == [0, 0, 0, 0, 0, 4_435_000]
    assert sched["rows"][-1]["is_balloon"] is True
    # totals foot to the displayed rows
    assert sched["total_interest"] == monthly * 6
    assert sched["total_principal"] == 4_435_000
    assert sched["total_payment"] == monthly * 6 + 4_435_000
    # payments run on the 15th, July through December 2026
    assert sched["rows"][0]["date"] == "15-Jul-26"
    assert sched["rows"][-1]["date"] == "15-Dec-26"


def test_repayment_schedule_uses_documents_when_present(alvarado):
    # When the extraction carries a schedule, the memo reproduces THOSE rows
    # verbatim rather than computing its own.
    from app.models import RepaymentRow
    alvarado.repayment_schedule = [
        RepaymentRow(date="15-Jul-26", interest=55_438, principal=0, total=55_438),
        RepaymentRow(date="15-Aug-26", interest=55_438, principal=4_435_000, total=4_490_438),
    ]
    terms = DealTerms(
        name="José Alvarado", loan=ALVARADO_LOAN, rate=15, fee=2, salary=ALVARADO_SALARY,
        fund=date(2026, 6, 15), mat=date(2026, 8, 15),
    )
    html = memo_service.render_html(terms, alvarado, [])
    assert "15-Jul-26" in html and "15-Aug-26" in html
    assert "$4,490,438" in html      # a document row's total, copied through
    assert "$4,545,876" in html      # totals row = sum of the document's rows
    # the computed fallback (monthly interest on the 2,499,000 loan) must NOT run
    computed_monthly = round(ALVARADO_LOAN * 0.15 / 12)  # 31,238
    assert f"${computed_monthly:,}" not in html


# --- LTC -------------------------------------------------------------------

def test_ltc_is_loan_over_guaranteed_earnings():
    assert calc.calc_ltc(2_499_000, 9_000_000) == pytest.approx(27.77, abs=0.01)


def test_ltc_zero_salary_safe():
    assert calc.calc_ltc(2_499_000, 0) == 0.0


# --- SSN masking -----------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("123-45-6789", "XXX-XX-6789"),
    ("123456789", "XXX-XX-6789"),
    ("6789", "XXX-XX-6789"),
    ("", ""),
    (None, ""),
])
def test_ssn_masking(raw, expected):
    assert calc.mask_ssn(raw) == expected


# --- Memo rendering smoke test --------------------------------------------

def test_render_html_contains_key_figures(alvarado):
    terms = DealTerms(
        name="José Alvarado", team="Pelicans", league="NBA", sport="basketball",
        loan=ALVARADO_LOAN, rate=12, fee=2, salary=ALVARADO_SALARY,
        fund=date(2026, 1, 1), mat=date(2027, 1, 1),
    )
    html = memo_service.render_html(terms, alvarado, ["PFS.pdf"])
    assert "José Alvarado" in html
    assert "Net Worth" in html
    assert "Proposed Facility" in html
    # the general-business-purposes verbiage must stay removed
    assert "general business purposes" not in html
