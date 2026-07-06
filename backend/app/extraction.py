"""Document extraction via the Anthropic API.

This is the security-critical reason the project moved to a backend: the
credential lives here in an environment variable and never reaches the browser.

Extraction authenticates STRICTLY with a Claude subscription usage token
(an OAuth token), never a pay-per-token API key. See `extract_documents`.

The extraction PROMPT is the second authoritative artifact in this project
(alongside calculations.py). Its rules about guaranteed-compensation-only
salary (guaranteed base PLUS guaranteed annual bonuses, nothing non-guaranteed),
capturing every liability and expenditure line, SSN redaction, and auto-loan
folding are load-bearing — keep them in sync with the rules documented in
calculations.py.

After extraction, a SECOND Claude call composes the Section V (Project
Sponsorship) narrative from public research on the athlete (Wikipedia +
Spotrac, gathered by research.py) so the section describes the athlete, not
just the facility. See `_compose_sponsorship`.
"""

from __future__ import annotations

import json
import logging
import os

from .models import Extraction, UploadedDoc
from .calculations import mask_ssn
from .research import gather_athlete_research

EXTRACTION_MODEL = os.environ.get("EXTRACTION_MODEL", "claude-sonnet-4-6")

# OAuth ("usage") authentication, not an API key. A Claude subscription token
# authenticates via `Authorization: Bearer` plus this beta header, and the
# request must identify as Claude Code (the system prompt below) to be accepted.
_OAUTH_BETA_HEADER = "oauth-2025-04-20"
_CLAUDE_CODE_SYSTEM = "You are Claude Code, Anthropic's official CLI for Claude."
_TOKEN_PLACEHOLDER = "paste-your-token-here"


def usage_token() -> str | None:
    """Return the Claude subscription usage (OAuth) token, or None if unset.

    Treats the .env placeholder as unset so a real token is always required.
    """
    token = (
        os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")
        or os.environ.get("ANTHROPIC_AUTH_TOKEN")
    )
    if not token or token == _TOKEN_PLACEHOLDER:
        return None
    return token

PROMPT = """You are a financial analyst at South River Capital preparing a credit memorandum for a professional athlete loan. You have been given one or more documents which may include any combination of: Personal Financial Statement (PFS), player contract, credit report, pay stubs, or other deal documents.

Read ALL documents together and extract every piece of information needed for the credit memo. Use the most authoritative source for each field.

Return ONLY raw JSON, no markdown, no backticks:

{"borrower_name":null,"dob":null,"address":null,"phone":null,"team":null,"league":null,"sport":null,"ssn_masked":null,"drivers_license":null,"agent":null,"salary":0,"other_income":0,"total_income":0,"federal_taxes":0,"mortgage_payments":0,"hoa_payments":0,"student_loans":0,"interest_principal_loans":0,"insurance":0,"alimony":0,"auto_payments":0,"living_expenses":0,"other_expenses":[],"total_expenditures":0,"assets":[],"total_assets":0,"liabilities":[],"total_liabilities":0,"net_worth":0,"facility_total_due":0,"loan_amount":0,"interest_rate_pct":0,"origination_fee_pct":0,"loan_term_months":0,"repayment_schedule":[],"uses_of_funds":null,"credit_notes":null,"contract_notes":null,"sponsorship_narrative":null}

Rules: assets and liabilities are arrays of {label, amount}. other_expenses is array of {label, amount}. Use 0 for missing numbers, null for missing strings. Include ALL non-zero line items. sport is the sport name ONLY (e.g. "Ice Hockey", "Basketball", "Football"); never prefix it with the word "professional" — the memo already phrases it as "a professional <sport> player". CRITICAL — the liabilities array must include EVERY liability line item EXACTLY as the uploaded Personal Financial Statement lists them, with their exact labels and amounts, omitting NOTHING: all Notes Payable categories (e.g., "Notes Payable: Contract Based", "Notes Payable to: others"), mortgage debt, credit card debt, taxes payable, and every other liability shown. Never merge, rename, or drop a liability row (the ONLY exception: auto loan balances fold into Notes Payable to: others as instructed below). CRITICAL — capture the PROPOSED facility's deal terms exactly as the documents state them (a term sheet, approval, or commitment line such as "Loan Amount: $4,435,000", "Origination: 3%", "Rate: 13.5%", "Term: 6 months"): loan_amount is the loan / proposed facility PRINCIPAL as a numeric dollar amount (e.g. "$4.435M" -> 4435000, "$4,435,000" -> 4435000); interest_rate_pct is the facility's annual interest rate as a percent NUMBER (e.g. "13.5%" -> 13.5); origination_fee_pct is the origination/upfront/lender fee as a percent NUMBER (e.g. "3%" or "Origination: 3%" -> 3). Use 0 for any of these not stated in the documents. loan_term_months is the term/duration of the PROPOSED facility expressed in WHOLE months, as stated in the deal documents (e.g. a term sheet line such as "Term: 6 months", a stated loan period, or a "6 mo." note). If the term is stated in years, convert to months (1 year = 12 months). If the documents give only funding and maturity dates with no explicitly stated term, return 0 (the memo computes the span from the dates). Use 0 if no term is stated. facility_total_due is the proposed facility's TOTAL amount due (principal + interest) if any amortization schedule, payoff figure, or proposed-facility liability appears in the documents; otherwise 0. repayment_schedule is the proposed facility's repayment/amortization schedule WHEN the documents contain one (e.g., a payment/amortization table for the new loan): return an array with one object per scheduled payment, IN ORDER, each {"date","interest","principal","total"} where date is the payment date copied as shown (a short string such as "15-Jul-26"), interest and principal are that payment's dollar amounts (use 0 where a column is blank or zero — typically principal is 0 until the final balloon payment), and total is that payment's interest + principal. Copy the figures EXACTLY as the schedule lists them; do not recompute, round, or reorder. Capture ONLY the proposed facility's own schedule (not schedules for the borrower's other/existing debts). If the documents contain no repayment schedule for the proposed facility, return an empty array []. CRITICAL — uses_of_funds is the proposed facility's disbursement / sources-and-uses / settlement breakdown WHEN the documents contain one (e.g. a "Disbursement", "Uses of Funds", or settlement-statement table showing how the gross loan is applied): capture EVERY line provided, omitting NOTHING, as an object {"gross_loan_amount":0,"deductions":[{"label","amount"}],"additional_costs":[{"label","amount"}]}. gross_loan_amount is the full proposed facility (gross) amount. deductions are the fees and payoffs subtracted from the gross loan to reach the amount "to be disbursed to Borrower" — e.g. origination fee, underwriting fee, payoffs of existing loans (such as a "Bank Payments thru <date>" payoff line), and legal/closing costs. additional_costs are amounts funded from the loan and carved out of the to-Borrower figure to reach the NET disbursed — e.g. Death & Disgrace (DDD) insurance premium and Interest Reserve. Copy each label as shown and give every amount as a POSITIVE dollar magnitude (do not use negatives or parentheses). Do NOT include any subtotal/total lines themselves (e.g. "To be disbursed to Borrower", "Net to be Disbursed to Borrower") — those are recomputed. If the documents contain no disbursement breakdown, return null. Auto/vehicle loan balances must NOT appear as a separate row in the liabilities array — they are already included in "Notes Payable to: others"; if a document lists an auto loan balance not already within notes payable, fold it into the "Notes Payable to: others" amount instead of listing it separately. (Monthly auto payments still go in auto_payments for the cash flow.) CRITICAL — salary is the GUARANTEED compensation for the current/upcoming season: the guaranteed base salary PLUS every bonus that is guaranteed and paid every year of the contract (e.g. an annual signing-bonus installment, or a guaranteed yearly roster/reporting bonus). When the contract pays the athlete a guaranteed bonus each year on top of the base salary, ADD that bonus into salary — never report the base salary alone, and never ALSO count that bonus in other_income (no double counting). WORKED EXAMPLE: the documents show a remaining contract value of $39,500,000, an annual professional-contract base salary of $1,000,000, and guaranteed annual Bonus & Commission Income of $9,000,000 -> salary = 10000000 ($1,000,000 base + $9,000,000 guaranteed annual bonus). NOT 1000000 (the base alone) and NOT 39500000 (the remaining/total contract value is never the salary). Still EXCLUDE everything that is not guaranteed or not paid annually: one-time bonuses, performance/incentive bonuses, non-guaranteed years, options, and endorsement income. If the contract distinguishes total compensation from guaranteed compensation, always use the guaranteed figure. In contract_notes, summarize the contract structure (total value, term, guaranteed amount, current season base salary and any guaranteed annual bonus), state explicitly which portion is guaranteed, and show how the salary figure was composed (e.g. base + annual bonus). sponsorship_narrative is a brief factual narrative about the ATHLETE themselves (background, career path, current team and role) if the documents support one; null otherwise. CRITICAL — capture EVERY line item from the Annual Expenditures section of the PFS, omitting NOTHING: map mortgage payments to mortgage_payments, automobile payments to auto_payments, insurance premiums to insurance, alimony/child support to alimony, student loans to student_loans, interest & principal on loans to interest_principal_loans, HOA dues to hoa_payments, and EVERY other expenditure line item (with its exact label and amount) into the other_expenses array. Also capture insurance premiums and alimony/child support whenever they appear in ANY other document. ssn_masked must NEVER contain a full Social Security or Tax ID number — return ONLY the last 4 digits formatted exactly as "XXX-XX-1234". If a full SSN appears in any document, redact it to that format. Do not echo a full SSN anywhere in your output."""


SPONSORSHIP_PROMPT = """You are a credit analyst at South River Capital writing Section V — "Project Sponsorship" — of a credit memorandum for a proposed loan to a professional athlete.

Borrower: {who}

Write the Project Sponsorship narrative about the ATHLETE using the source material below. Cover, as available: who the athlete is and their current team and role; their path (college, draft position or signing, prior teams); notable achievements or distinctions; and their contract history and career earnings, including the current contract's total value, guaranteed compensation, and any guaranteed annual bonuses (Spotrac is the authority for contract figures).

Rules: write 2 short paragraphs of plain prose — no markdown, bullets, headings, or citations. Professional credit-memo tone. State only facts present in the sources; never invent or estimate a figure. If a source appears to describe a DIFFERENT person than the borrower (wrong sport, team, or league), ignore that source entirely. Never use the phrase "general business purposes". Mention the proposed facility in at most one closing sentence, if at all. Return ONLY the narrative text.

SOURCES:

{sources}"""


def _compose_sponsorship(client, data: dict) -> str | None:
    """Second Claude call: write Section V from public research + doc notes.

    The Project Sponsorship section is athlete-centric — researched from
    Wikipedia and Spotrac (see research.py) rather than taken from the deal
    documents. Returns None when there is nothing to write from (no borrower
    name, or neither public source could be fetched), so the caller keeps the
    document-derived narrative instead.
    """
    name = (data.get("borrower_name") or "").strip()
    if not name:
        return None

    research = gather_athlete_research(name, data.get("sport"), data.get("league"))
    if not (research["wiki_text"] or research["spotrac_text"]):
        return None

    sources = []
    if research["wiki_text"]:
        sources.append(f"WIKIPEDIA ({research['wiki_url']}):\n{research['wiki_text']}")
    if research["spotrac_text"]:
        sources.append(f"SPOTRAC ({research['spotrac_url']}):\n{research['spotrac_text']}")
    if data.get("contract_notes"):
        sources.append(f"DEAL DOCUMENTS (contract notes):\n{data['contract_notes']}")

    who = ", ".join(
        str(part) for part in (
            name, data.get("sport"), data.get("team"), data.get("league"),
        ) if part
    )
    message = client.messages.create(
        model=EXTRACTION_MODEL,
        max_tokens=700,
        system=_CLAUDE_CODE_SYSTEM,
        messages=[{
            "role": "user",
            "content": SPONSORSHIP_PROMPT.format(who=who, sources="\n\n".join(sources)),
        }],
    )
    text = "".join(b.text for b in message.content if b.type == "text").strip()
    return text or None


def extract_documents(docs: list[UploadedDoc]) -> Extraction:
    """Send documents to Claude and parse the structured response.

    Authenticates STRICTLY with a Claude subscription usage token (OAuth),
    never an API key. Generate the token with `claude setup-token` and put it
    in .env as CLAUDE_CODE_OAUTH_TOKEN (ANTHROPIC_AUTH_TOKEN also works).

    Raises RuntimeError if no usage token is set or the response can't be parsed.
    """
    token = usage_token()
    if not token:
        raise RuntimeError(
            "No Claude usage token is set. Run `claude setup-token` and put the "
            "token in .env as CLAUDE_CODE_OAUTH_TOKEN. Extraction uses your Claude "
            "subscription usage, not a pay-per-token API key."
        )

    try:
        import anthropic
    except ImportError as exc:
        raise RuntimeError(
            "The 'anthropic' package is not installed. Run: pip install -e '.[dev]'"
        ) from exc

    # Strictly usage, never an API key: drop any stray ANTHROPIC_API_KEY so the
    # SDK can't fall back to it and send both credentials (which the API rejects).
    os.environ.pop("ANTHROPIC_API_KEY", None)
    client = anthropic.Anthropic(
        auth_token=token,
        default_headers={"anthropic-beta": _OAUTH_BETA_HEADER},
    )

    content: list[dict] = [
        {
            "type": "document",
            "source": {"type": "base64", "media_type": d.mime, "data": d.b64},
        }
        for d in docs
    ]
    content.append({"type": "text", "text": PROMPT})

    try:
        message = client.messages.create(
            model=EXTRACTION_MODEL,
            max_tokens=3000,
            system=_CLAUDE_CODE_SYSTEM,
            messages=[{"role": "user", "content": content}],
        )
    except anthropic.AuthenticationError as exc:
        raise RuntimeError(
            "Claude rejected the usage token. Regenerate it with `claude setup-token`, "
            "update CLAUDE_CODE_OAUTH_TOKEN in .env, and restart the backend."
        ) from exc
    except anthropic.APIError as exc:
        raise RuntimeError(f"Claude API error during extraction: {exc}") from exc

    raw = "".join(block.text for block in message.content if block.type == "text")
    clean = raw.strip()
    if clean.startswith("```"):
        clean = clean.split("\n", 1)[1] if "\n" in clean else clean
        clean = clean.rsplit("```", 1)[0].strip()

    try:
        data = json.loads(clean)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Could not parse extraction response: {exc}") from exc

    # Belt-and-suspenders: re-mask the SSN server-side regardless of model output.
    if data.get("ssn_masked"):
        data["ssn_masked"] = mask_ssn(data["ssn_masked"])

    # Section V (Project Sponsorship) is athlete-centric: researched from
    # Wikipedia + Spotrac and composed by a second Claude call. Best-effort —
    # any failure keeps whatever narrative the documents provided.
    try:
        narrative = _compose_sponsorship(client, data)
        if narrative:
            data["sponsorship_narrative"] = narrative
    except Exception as exc:  # noqa: BLE001 - research must never break extraction
        logging.getLogger(__name__).warning("Sponsorship research failed: %s", exc)

    return Extraction(**data)
