# CLAUDE.md — project guide for Claude Code

This file is read automatically by Claude Code. It explains how the project is
laid out, where the important logic lives, and the rules that must not regress.

## What this is

A web app that builds credit memorandums for professional-athlete loans at South
River Capital. A user uploads deal documents (contract, PFS, credit report),
Claude extracts the structured data, the user confirms deal terms, and the app
generates a formatted credit memo that can be downloaded as HTML, PDF, or Word.

It was ported from a single React component into a Python (FastAPI) backend and
a Vue 3 frontend. The port moved the Anthropic API call server-side so the API
key is never exposed in the browser.

## Layout

```
backend/                 FastAPI + the authoritative business logic
  app/
    calculations.py      ALL underwriting math and rules (the crown jewels)
    models.py            Pydantic models — the API contract
    extraction.py        Anthropic document extraction (holds the prompt)
    memo.py              Renders the memo (Jinja2 HTML, PDF, Word)
    main.py              FastAPI routes
    templates/memo.html.j2   the memo design (HTML/CSS)
    logo.txt             base64 logo data URI
  tests/
    test_calculations.py LOCKS IN the rules using the Alvarado reference deal
frontend/                Vue 3 + Vite UI
  src/
    App.vue              the whole flow (upload → terms → generate → export)
    lib/api.js           the only place that talks to the backend
```

## The rules that must not regress

These are encoded in `tests/test_calculations.py`. Run `pytest` before and after
any change to `calculations.py` or `memo.py`. If a test breaks, either the change
is wrong or a rule genuinely changed — in which case update the test deliberately,
never silently.

1. Taxes are ALWAYS 45% of gross income (salary + other income). Never from docs.
2. Ordinary living expenses are ALWAYS 10% of gross income.
3. In the Guarantor Analysis cash flow, the Proposed Facility line is the loan
   PRINCIPAL only.
4. On the PFS, the Proposed Facility is loan + interest (full amount due).
   Interest comes from the amortization schedule; if rate/dates aren't set, it
   falls back to a facility total stated in the documents.
5. Net Worth = Total Assets − Total Liabilities. Always calculated, never copied.
6. Total Liabilities includes the Proposed Facility (loan + interest).
7. Alimony / child support is a cash-flow item ONLY. Never a PFS liability.
8. Auto loan balances are never a separate PFS liability (they live inside
   "Notes Payable to: others"). Monthly auto PAYMENTS still appear in the cash flow.
9. Salary used everywhere is the GUARANTEED portion of compensation only.
10. LTC (Loan-to-Contract) = loan amount / guaranteed earnings.
11. The memo must NOT contain the phrase "general business purposes".
12. SSN/Tax ID is only ever stored/shown as the last 4 digits (XXX-XX-1234).
13. Taxes are NEVER a PFS liability. Even when the PFS reports an estimated tax
    figure (e.g. "Taxes (Est of 35% of ...)"), it is excluded from Total
    Liabilities and from Net Worth.
14. Section VI (Uses of Funds) reproduces EVERY disbursement line provided in the
    documents (fees, payoffs, closing costs, insurance, interest reserve, ...).
    The "To be disbursed to Borrower" and "Net to be Disbursed to Borrower"
    subtotals are always recomputed from the lines, never copied. With no
    breakdown in the documents it falls back to gross loan less the origination
    fee from the deal terms. Captured by `uses_of_funds` (extraction.py) and
    rendered by `calc_uses_of_funds` / `_uses_of_funds_html`.
15. The loan term in months (Section II Action Request) prefers the term stated
    in the documents (a term sheet's "Term: N months"), falling back to the
    funding-to-maturity span. Captured by `loan_term_months` (extraction.py) and
    resolved by `calc.loan_term_months`.

The Alvarado reference deal: $12,267,600 assets, $10,373,361 total liabilities,
$1,894,239 net worth, facility (incl. interest) $2,703,754, LTC 27.8%.

## The extraction prompt

`extraction.py` holds the prompt sent to Claude. Its instructions about
guaranteed-salary-only, capturing every liability/expenditure line verbatim,
auto-loan folding, and SSN redaction are load-bearing. Keep them consistent with
the rules above.

## Running locally

Backend:  see `backend/README.md` (uvicorn on :8000)
Frontend: `cd frontend && npm install && npm run dev` (Vite on :5173, proxies /api)

## Conventions

- Business logic lives in the backend, not the frontend. The frontend only
  collects input, calls the API, and displays results.
- All network calls go through `frontend/src/lib/api.js`.
- Add a test for any new rule or any bug you fix in calculations.
- Never commit `.env` or any API key.
