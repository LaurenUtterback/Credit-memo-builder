"""Document extraction for the Deal Structuring tab.

The credit memo's extraction already captures the CERTAINTY side of a deal
(guaranteed salary, contract end, guarantee language). What structuring needs on
top of that is TIMING — the pay election, the season window, and every dated
lump payment — plus whatever the documents say about how the loan is expected to
be repaid.

Reuses the same subscription usage-token auth as extraction.py /
pa_extraction.py / loandocs_extraction.py via the shared ``_ask_claude`` helper.
"""

from __future__ import annotations

import re
from typing import Optional

from pydantic import BaseModel, field_validator

from .models import UploadedDoc
from .loandocs_extraction import _ask_claude


_NUMBER_RE = re.compile(r"-?\d+(?:\.\d+)?")


def _to_number(v):
    """"$1,650,000" / "15%" / "N/A" -> 1650000.0 / 15.0 / None.

    Claude is told to return plain numbers and mostly does, but it formats them
    often enough that a hard failure here would 500 the whole extraction.
    """
    if v is None or isinstance(v, (int, float)):
        return v
    m = _NUMBER_RE.search(str(v).replace(",", ""))
    return float(m.group()) if m else None


class _Tolerant(BaseModel):
    """Base model that survives what the extraction actually returns.

    The prompt instructs Claude to use null for anything the documents do not
    state — which includes the str and list fields. Declaring those as plain
    `str` / `list` means one unstated field raises ValidationError and the
    request 500s. This coerces null back to the field's own default, so a
    partially-stated document degrades to blanks instead of failing.
    """

    @field_validator("*", mode="before")
    @classmethod
    def _null_to_default(cls, v, info):
        if v is not None:
            return v
        field = cls.model_fields.get(info.field_name)
        if field is None:
            return v
        default = field.get_default(call_default_factory=True)
        # Optional fields keep their None; str/list fields get "" / [].
        return default if default is not None else v


class ExtractedBonus(_Tolerant):
    label: str = ""
    date: Optional[str] = None          # ISO yyyy-mm-dd
    amount: Optional[float] = None
    guaranteed: bool = True

    @field_validator("amount", mode="before")
    @classmethod
    def _clean_amount(cls, v):
        return _to_number(v)


class StructureExtraction(_Tolerant):
    """What Claude pulls from the deal documents for the structuring run."""

    # Who
    borrower_name: str = ""
    team: str = ""
    league: str = ""

    # Certainty
    salary: Optional[float] = None            # GUARANTEED season compensation
    other_income: Optional[float] = None
    other_debt_annual: Optional[float] = None
    contract_end: Optional[str] = None        # ISO
    salary_guaranteed: Optional[bool] = None
    salary_guarantee_note: str = ""

    # Timing — the fields the memo extraction does not carry
    pay_frequency: Optional[str] = None       # weekly | semimonthly | monthly
    season_start: Optional[str] = None        # ISO, the season's first pay date
    season_end: Optional[str] = None          # ISO, the season's last pay date
    pay_election_note: str = ""
    bonus_events: list[ExtractedBonus] = []

    # Terms as presented
    loan_amount: Optional[float] = None
    interest_rate: Optional[float] = None
    origination_fee_pct: Optional[float] = None
    funding_date: Optional[str] = None        # ISO
    maturity_date: Optional[str] = None       # ISO
    presented_type: str = ""                  # e.g. "Full balloon", "Interest only"

    # Exit
    expected_exit_date: Optional[str] = None  # ISO
    expected_exit_label: str = ""

    notes: str = ""

    @field_validator("salary", "other_income", "other_debt_annual", "loan_amount",
                     "interest_rate", "origination_fee_pct", mode="before")
    @classmethod
    def _clean_numbers(cls, v):
        return _to_number(v)

    @field_validator("pay_frequency", mode="before")
    @classmethod
    def _normalize_frequency(cls, v):
        """Map what Claude writes onto the three cadences the engine supports.

        LeagueCadence.pay_frequency is a Literal, so an unrecognised value would
        fail the /propose call later — much further from the cause. Anything we
        can't place returns None, which falls back to the league default.
        """
        if not v:
            return None
        key = re.sub(r"[\s_-]", "", str(v)).lower()
        if key in ("weekly", "everyweek", "pergame", "gamecheck", "gamechecks"):
            return "weekly"
        if key in ("semimonthly", "twicemonthly", "twiceamonth", "bimonthly",
                   "biweekly", "everytwoweeks", "fortnightly", "24payments"):
            # Biweekly (26/yr) is not exactly semi-monthly (24/yr), but it is far
            # closer than weekly or monthly and keeps the projection sane.
            return "semimonthly"
        if key in ("monthly", "permonth", "everymonth", "12payments"):
            return "monthly"
        return None

    @field_validator("salary_guaranteed", mode="before")
    @classmethod
    def _normalize_guaranteed(cls, v):
        if isinstance(v, str):
            key = v.strip().lower()
            if key in ("yes", "true", "y", "fully guaranteed", "guaranteed"):
                return True
            if key in ("no", "false", "n", "not guaranteed", "conditional", "partial"):
                return False
            return None
        return v


PROMPT = """You are reading loan documents for a professional athlete lender.
Your job is to pull the facts needed to decide the loan's REPAYMENT STRUCTURE:
when the borrower actually receives cash, and how certain that cash is.

Return RAW JSON only — no prose, no markdown fence. Use this exact shape:

{
  "borrower_name": "", "team": "", "league": "",
  "salary": null, "other_income": null, "other_debt_annual": null,
  "contract_end": null, "salary_guaranteed": null, "salary_guarantee_note": "",
  "pay_frequency": null, "season_start": null, "season_end": null,
  "pay_election_note": "",
  "bonus_events": [{"label": "", "date": null, "amount": null, "guaranteed": true}],
  "loan_amount": null, "interest_rate": null, "origination_fee_pct": null,
  "funding_date": null, "maturity_date": null, "presented_type": "",
  "expected_exit_date": null, "expected_exit_label": "",
  "notes": ""
}

RULES — these are load-bearing:

1. SALARY is the GUARANTEED portion of the season's compensation ONLY: the
   guaranteed base salary PLUS any bonus that is guaranteed and paid every year
   of the contract (annual signing-bonus installments, guaranteed yearly roster
   bonuses). Exclude non-guaranteed incentives, one-time bonuses and
   endorsements. When installments differ season to season, use the one
   SCHEDULED FOR THE SEASON BEING UNDERWRITTEN — never an average, never
   another season's. If the contract states a Paragraph 5 salary but an
   addendum guarantees only part of it, salary is the GUARANTEED part.

2. salary_guaranteed is TRUE only for a clean, unconditional guarantee. If the
   guarantee is conditional, partial, voidable (failure to report, refusal to
   play, non-football injury, retirement, breach), or a one-way/limited SPC,
   set it FALSE and quote the condition in salary_guarantee_note. This flips
   which structures make sense, so do not round it up to true.

3. BONUS_EVENTS are dated lump payments that fall OUTSIDE the regular salary
   cadence: signing-bonus installments, roster bonuses, workout bonuses,
   endorsement milestones, deferred payments. Give each its real payment date
   from the schedule. NEVER include an amount here that you already counted in
   salary — if a guaranteed annual installment is inside salary, leave it out
   of bonus_events. Mark guaranteed=false for anything contingent.

4. TIMING: pay_frequency is "weekly", "semimonthly" or "monthly" — only if the
   documents actually state how the player is paid. season_start / season_end
   are the first and last SCHEDULED PAY DATES of the season, in ISO form, again
   only if stated. If the documents are silent, return null and say so in
   pay_election_note — a league default will be applied instead. Do NOT invent
   a cadence. If the player has ELECTED a pay schedule (e.g. an NBA player
   electing payment over 12 months instead of the season), record that election
   in pay_election_note; it matters more than the league default.

5. other_debt_annual is the borrower's total ANNUAL non-facility debt service
   (mortgage, autos, alimony/child support, student loans, other notes, HOA)
   from a personal financial statement or annual expenditures schedule. Sum
   them. Do not include taxes or ordinary living expenses — those are computed.

6. EXIT: fill expected_exit_date / expected_exit_label ONLY when the documents
   indicate repayment depends on a specific EVENT rather than on income — a
   contract extension or new contract being signed, an endorsement closing, a
   property sale, a refinance. expected_exit_label is a SHORT NOUN PHRASE
   naming the event, at most 8 words ("contract extension signing", "sale of
   the Miami property") — never a sentence and never your reasoning; that
   belongs in notes. Set the label even if no date is stated. If repayment
   simply comes from salary, leave both null.

7. presented_type is how the loan is structured AS PRESENTED, in the documents'
   own words ("Full balloon", "Interest only with balloon", "Fully amortized").

8. Dates are ISO yyyy-mm-dd. Amounts are plain numbers, no symbols or commas.
   Percentages are numbers (15 for 15%). Use null for anything the documents do
   not state — NEVER guess, and never carry a figure over from a different
   season or a different loan.

9. NOTES is read by a person in a single glance. Give at most 5 short items,
   semicolon-separated, covering only what would change the structuring
   decision or what you were unsure about — a conflict between documents, a
   figure you had to interpret, a repayment source that is not the salary.
   Do not restate figures that are already in the fields above, and do not
   summarize the deal.
"""


def extract_documents(docs: list[UploadedDoc]) -> StructureExtraction:
    """Pull the structuring fields from uploaded deal documents."""
    return StructureExtraction(**_ask_claude(docs, PROMPT, max_tokens=2000))
