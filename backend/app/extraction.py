"""Document extraction via the Anthropic API.

This is the security-critical reason the project moved to a backend: the
credential lives here in an environment variable and never reaches the browser.

Extraction authenticates STRICTLY with a Claude subscription usage token
(an OAuth token), never a pay-per-token API key. See `extract_documents`.

The extraction PROMPT is the second authoritative artifact in this project
(alongside calculations.py). Its rules about guaranteed-salary-only, capturing
every liability and expenditure line, SSN redaction, and auto-loan folding are
load-bearing — keep them in sync with the rules documented in calculations.py.
"""

from __future__ import annotations

import json
import os

from .models import Extraction, UploadedDoc
from .calculations import mask_ssn

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

Rules: assets and liabilities are arrays of {label, amount}. other_expenses is array of {label, amount}. Use 0 for missing numbers, null for missing strings. Include ALL non-zero line items. sport is the sport name ONLY (e.g. "Ice Hockey", "Basketball", "Football"); never prefix it with the word "professional" — the memo already phrases it as "a professional <sport> player". CRITICAL — the liabilities array must include EVERY liability line item EXACTLY as the uploaded Personal Financial Statement lists them, with their exact labels and amounts, omitting NOTHING: all Notes Payable categories (e.g., "Notes Payable: Contract Based", "Notes Payable to: others"), mortgage debt, credit card debt, taxes payable, and every other liability shown. Never merge, rename, or drop a liability row (the ONLY exception: auto loan balances fold into Notes Payable to: others as instructed below). CRITICAL — capture the PROPOSED facility's deal terms exactly as the documents state them (a term sheet, approval, or commitment line such as "Loan Amount: $4,435,000", "Origination: 3%", "Rate: 13.5%", "Term: 6 months"): loan_amount is the loan / proposed facility PRINCIPAL as a numeric dollar amount (e.g. "$4.435M" -> 4435000, "$4,435,000" -> 4435000); interest_rate_pct is the facility's annual interest rate as a percent NUMBER (e.g. "13.5%" -> 13.5); origination_fee_pct is the origination/upfront/lender fee as a percent NUMBER (e.g. "3%" or "Origination: 3%" -> 3). Use 0 for any of these not stated in the documents. loan_term_months is the term/duration of the PROPOSED facility expressed in WHOLE months, as stated in the deal documents (e.g. a term sheet line such as "Term: 6 months", a stated loan period, or a "6 mo." note). If the term is stated in years, convert to months (1 year = 12 months). If the documents give only funding and maturity dates with no explicitly stated term, return 0 (the memo computes the span from the dates). Use 0 if no term is stated. facility_total_due is the proposed facility's TOTAL amount due (principal + interest) if any amortization schedule, payoff figure, or proposed-facility liability appears in the documents; otherwise 0. repayment_schedule is the proposed facility's repayment/amortization schedule WHEN the documents contain one (e.g., a payment/amortization table for the new loan): return an array with one object per scheduled payment, IN ORDER, each {"date","interest","principal","total"} where date is the payment date copied as shown (a short string such as "15-Jul-26"), interest and principal are that payment's dollar amounts (use 0 where a column is blank or zero — typically principal is 0 until the final balloon payment), and total is that payment's interest + principal. Copy the figures EXACTLY as the schedule lists them; do not recompute, round, or reorder. Capture ONLY the proposed facility's own schedule (not schedules for the borrower's other/existing debts). If the documents contain no repayment schedule for the proposed facility, return an empty array []. CRITICAL — uses_of_funds is the proposed facility's disbursement / sources-and-uses / settlement breakdown WHEN the documents contain one (e.g. a "Disbursement", "Uses of Funds", or settlement-statement table showing how the gross loan is applied): capture EVERY line provided, omitting NOTHING, as an object {"gross_loan_amount":0,"deductions":[{"label","amount"}],"additional_costs":[{"label","amount"}]}. gross_loan_amount is the full proposed facility (gross) amount. deductions are the fees and payoffs subtracted from the gross loan to reach the amount "to be disbursed to Borrower" — e.g. origination fee, underwriting fee, payoffs of existing loans (such as a "Bank Payments thru <date>" payoff line), and legal/closing costs. additional_costs are amounts funded from the loan and carved out of the to-Borrower figure to reach the NET disbursed — e.g. Death & Disgrace (DDD) insurance premium and Interest Reserve. Copy each label as shown and give every amount as a POSITIVE dollar magnitude (do not use negatives or parentheses). Do NOT include any subtotal/total lines themselves (e.g. "To be disbursed to Borrower", "Net to be Disbursed to Borrower") — those are recomputed. If the documents contain no disbursement breakdown, return null. Auto/vehicle loan balances must NOT appear as a separate row in the liabilities array — they are already included in "Notes Payable to: others"; if a document lists an auto loan balance not already within notes payable, fold it into the "Notes Payable to: others" amount instead of listing it separately. (Monthly auto payments still go in auto_payments for the cash flow.) CRITICAL — salary must be ONLY the GUARANTEED portion of the current/upcoming season's contract compensation: exclude signing bonuses, non-guaranteed years, performance/roster bonuses, incentives, options, and endorsement income. If the contract distinguishes total compensation from guaranteed compensation, always use the guaranteed figure. In contract_notes, summarize the contract structure (total value, term, guaranteed amount, current season base salary) and state explicitly which portion is guaranteed. CRITICAL — capture EVERY line item from the Annual Expenditures section of the PFS, omitting NOTHING: map mortgage payments to mortgage_payments, automobile payments to auto_payments, insurance premiums to insurance, alimony/child support to alimony, student loans to student_loans, interest & principal on loans to interest_principal_loans, HOA dues to hoa_payments, and EVERY other expenditure line item (with its exact label and amount) into the other_expenses array. Also capture insurance premiums and alimony/child support whenever they appear in ANY other document. ssn_masked must NEVER contain a full Social Security or Tax ID number — return ONLY the last 4 digits formatted exactly as "XXX-XX-1234". If a full SSN appears in any document, redact it to that format. Do not echo a full SSN anywhere in your output."""


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

    return Extraction(**data)
