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
    research.py          Wikipedia + Spotrac research on the athlete (Section V)
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
9. Salary used everywhere is the GUARANTEED portion of compensation only —
   the guaranteed base salary PLUS any bonus that is guaranteed and paid every
   year of the contract (annual signing-bonus installments, guaranteed yearly
   roster bonuses). Non-guaranteed incentives, one-time bonuses, and
   endorsements stay excluded. When bonus/signing-bonus installments differ by
   season, the amount added is the one scheduled for that SPECIFIC season —
   never an average, never another season's installment. Lauren's reference
   example (baked into the extraction prompt as a worked example): remaining
   contract value $39,500,000, base salary $1,000,000, guaranteed bonus
   scheduled for the season $9,000,000 → salary = $10,000,000 (base + bonus;
   never the base alone, never the remaining contract value, and the bonus is
   never double-counted in other_income). A second worked example in the
   prompt (from a real dated-installment schedule Lauren supplied 2026-07-06:
   $60M signing bonus paid July 2022–2029 in installments ranging $5.5M–$9.5M)
   shows how to pick the installment whose payment date falls in the season
   being underwritten. The total remaining contract value
   is captured separately (`contract_remaining`) and rendered in Section VII
   (sentence + "Total Contract Remaining" table row). (Changed 2026-07-06:
   annual guaranteed bonuses used to be excluded; now they are added into
   salary.)
10. LTC (Loan-to-Contract) = loan amount / guaranteed earnings, where
    guaranteed earnings is the TOTAL REMAINING contract value when extracted
    (`contract_remaining`), else the guaranteed season salary. Section I's
    "advance against $X in guaranteed salary" figure uses the same basis
    (Lauren, 2026-07-06). The cash flow still runs on the season salary.
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
16. The memo phrases the borrower as "a Professional <sport> player", so the
    sport value is normalized (`calc.normalize_sport`) to drop a leading
    "professional". As a source-agnostic backstop, `_dedupe_professional` (memo.py)
    collapses any consecutive duplicate "professional" in the rendered HTML
    (e.g. from a captured narrative). The memo must never render
    "Professional Professional ...".

The Alvarado reference deal: $12,267,600 assets, $10,373,361 total liabilities,
$1,894,239 net worth, facility (incl. interest) $2,703,754, LTC 27.8%.

## The extraction prompt

`extraction.py` holds the prompt sent to Claude. Its instructions about
guaranteed-compensation-only salary (base + guaranteed annual bonuses),
capturing every liability/expenditure line verbatim, auto-loan folding, and SSN
redaction are load-bearing. Keep them consistent with the rules above.

## Section V — Project Sponsorship research

Section V describes the ATHLETE and their playing career ONLY (background,
league career, national/country team play when applicable) — it must never
mention the loan, facility, funds, or use of proceeds, and it must contain NO
financial information at all (no salaries, bonuses, contract values, or career
earnings — that detail lives in Section VII Contract Analysis). Lauren,
2026-07-06. After document
extraction, `extraction.py`
`_compose_sponsorship` makes a second Claude call that writes the narrative
from public sources gathered by `research.py`:
- Wikipedia via httpx (needs the descriptive User-Agent — Wikimedia 403s
  browser-imitating UAs).
- Spotrac via the same Playwright headless Chromium used for PDF export, with a
  real-browser UA (Spotrac's CloudFront 403s plain HTTP clients and the default
  HeadlessChrome UA). The API's web_search server tool is NOT available to
  subscription OAuth tokens — don't try to switch to it.
Research is best-effort: any failure keeps the document-derived
`sponsorship_narrative` (and memo.py's one-line fallback below that). It must
never make /api/extract fail.

## Running locally

Easiest: double-click **`Start Builder.bat`** in the repo root — it launches the
backend and frontend in two minimized windows and opens http://localhost:5173.
Keep those windows open while using the app; there is no auto-start on reboot.

Manual:
Backend:  see `backend/README.md` (uvicorn on :8000)
Frontend: `cd frontend && npm install && npm run dev` (Vite on :5173, proxies /api)

Note: the app is at **http://localhost:5173** (Vite binds the `localhost`/IPv6
name, not `127.0.0.1` — `127.0.0.1:5173` can refuse the connection).

## Conventions

- Business logic lives in the backend, not the frontend. The frontend only
  collects input, calls the API, and displays results.
- All network calls go through `frontend/src/lib/api.js`.
- Add a test for any new rule or any bug you fix in calculations.
- Never commit `.env` or any API key.

## Participation Agreement Builder

A second tool lives in the same app (tab switch in `App.vue`, `view === 'pa'`).
It generates South River's loan **Participation Agreement** — Lender is always
fixed (South River Capital LLC / James Plack). A dropdown chooses the form:
  * **Brookridge Participation Agreement** — Participant defaults to Brookridge
    Opportunistic Credit Fund, LP; Brookridge's clause set; Key Terms include
    Total Loan Amount, Participant's Loan Amount, Origination Fee $.
  * **Participation Agreement (standard form)** — Participant blank by default;
    adds the Lead-Lender disclaimer, Participant rep (c), an Arbitration section,
    and different default-interest language; Key Terms use Purchase Price,
    Application & Administration Fees, and late fees as "% × Participation %".
Drop the deal documents, Claude extracts the loan terms, the user confirms every
field, and it produces the filled agreement as Word **and** PDF (agreement +
Exhibit A Key Terms + Exhibit B Participation Certificate). The chosen form is
sent as `agreement_type` ("brookridge" | "standard") on the /api/pa requests.

Backend pieces:
- `app/pa_models.py` — `PAExtraction` (what Claude pulls from docs), `PATerms`
  (the exact strings injected into the template, one per placeholder), and the
  `Breakdown*` models.
- `app/pa_extraction.py` — reuses the SAME Claude usage-token (OAuth) auth as
  `extraction.py`; only the prompt differs (loan/participation deal fields).
- `app/pa_breakdown.py` — parses the deal's **Participant Breakdown .xlsx**
  (openpyxl) into deal info + per-participant terms. It RECOMPUTES the sheet's
  formulas itself (Participation % = Participant $ / Loan amount; Points $ =
  Points % × Participant $; Late-fee share = Participation % × 50%) so it works
  even without Excel's cached values. Emails come from the lookup sheet.
- `app/pa_agreement.py` — fills the template with **docxtpl** (pure Python) and
  converts the .docx to PDF with **LibreOffice headless** (`find_soffice()`).
- Routes: `POST /api/pa/extract`, `/api/pa/breakdown`, `/api/pa/docx`, `/api/pa/pdf`.
- Frontend: `frontend/src/PaBuilder.vue` (uses the app's existing global CSS).
  Drop the breakdown .xlsx → pick a participant → the Key Terms (participation %,
  points %/$, interest, late-fee share, amount, email) auto-fill (mapped per
  form: brookridge → participant_loan_amount + origination_fee_amount; standard
  → purchase_price). "Recalculate" applies the same formulas to manual entry.
  `App.vue` passes the Credit Memo's `terms` to `PaBuilder` as `:memo-terms`, and a
  **"Pull deal info from Credit Memo"** button copies borrower, loan amount,
  interest rate, origination fee, and the funding date (→ agreement_date) over —
  so a typical flow is: build the memo → pull → drop the breakdown → generate.

Templates — `app/templates/participation_agreement_{brookridge,standard}.docx`:
- Carry the SRC logo (blue compass — decoded from `app/logo.txt`) centered in a
  FIRST-page-only Word header, added by the build script; body layout and
  pages 2+ are untouched.
- Hold ONLY `{{ placeholders }}`, never deal data. Built by
  `tools/build_pa_template.py` (config-driven; one config per form) from
  `tools/_pa_struct_{brookridge,standard}.json` — faithful paragraph-level
  captures (text, auto-number strings, alignment, bold spans) of the source
  docs. Re-run after editing: `.venv\Scripts\python.exe tools\build_pa_template.py`.
- BROOKRIDGE: reproduced verbatim, including its numbering and internal
  cross-references ("Section 6.2", "9.3", …) and their quirks — do NOT "fix" them.
- STANDARD: clause WORDING is verbatim, but the source's numbering was internally
  inconsistent (auto-numbers 1)/a)/i) that didn't match its own "Section 3.2(b)"/
  "6.1"/"9.3" cross-references, plus a duplicated Section 9). Per the user's
  decision it is NORMALIZED to the decimal scheme the clauses cross-reference
  (8 Notices, 9 Arbitration, 10 Miscellaneous) with the one stale reference
  corrected ("9.3" → "10.3"). The numbering is encoded as an explicit `numbered`
  map in the STANDARD config, not taken from the source's auto-numbers.
- `pa_agreement.template_path(agreement_type)` selects the file (default brookridge).
- `tools/_*` is git-ignored: it can contain REAL deal data (borrower, amounts,
  loan #) captured from the sources, and this repo is public.

PDF export (LibreOffice):
- Located at runtime via `SOFFICE_PATH` env, then known install paths, then a
  no-admin copy under `%LOCALAPPDATA%\CreditMemoBuilder\libreoffice\program\
  soffice.exe`. The .docx download always works; only PDF needs LibreOffice.
- On this machine LibreOffice was extracted (no admin) with
  `msiexec /a <LibreOffice.msi> /qn TARGETDIR=%LOCALAPPDATA%\CreditMemoBuilder\
  libreoffice`. Word COM automation is NOT used (SaveAs hangs in headless/
  non-interactive sessions).

## Loan Documents Builder

A third tool (tab in `App.vue`, `view === 'loandocs'`) that generates South
River's athlete-loan CLOSING PACKAGE in the credit memo's visual design:
Business Entity Affidavit, Promissory Note (+ Exhibit A repayment schedule),
Loan and Security Agreement (+ Exhibit A definitions), Guaranty, Memo of
Settlement, UCC-1 Financing Statement (+ Exhibit A), and the Payment Direction
Letter to the team. Each document can be included/excluded per package.

Backend pieces:
- `app/loandocs_models.py` - `LoanDocTerms` (one field per placeholder),
  `SettlementLine`, `ScheduleRow`, `LoanDocsInclude`, `LoanDocsRequest`.
- `app/loandocs.py` - builds the Jinja context and renders
  `templates/loan_documents.html.j2`. PDF/Word go through the SAME pipeline as
  the memo (`memo.render_pdf` / `memo.render_word`, whose footer text is now a
  parameter). The Note's Exhibit A schedule uses the memo's
  `calc.calc_repayment_schedule` unless rows are supplied (pulled from the
  memo extraction). The Memo of Settlement's "To be disbursed to Borrower" is
  ALWAYS recomputed from the deduction lines, never copied.
- Routes: `POST /api/loandocs/html|pdf|word`, `GET /api/loandocs/defaults`.
- The Payment Direction Letter's receiving account (bank name, account no.,
  ABA, contact) comes from `SRC_BANK_*` / `SRC_ACCOUNT_NAME` in `.env` - the
  values must NEVER be hard-coded (public repo). `loandocs.bank_defaults()`
  reads them; the UI prefills from `/api/loandocs/defaults` and any field can
  be overridden per deal.
- Frontend: `frontend/src/LoanDocsBuilder.vue` - "Pull deal info from Credit
  Memo" copies borrower/team/league/loan/rate/fee/dates, derives Occupation
  from the sport, parses the memo's one-line address into the UCC-1's
  street/city/state/zip cells, seeds the settlement deductions from the memo's
  Uses of Funds, and carries the extraction's repayment rows into Exhibit A.

Template - `app/templates/loan_documents.html.j2`:
- GENERATED by `tools/build_loandocs_template.py` from
  `tools/_loandocs_struct.json`, a Word-COM capture (read-only; SaveAs hangs)
  of the executed example
  `Z:\SRC Shared\Servicing Tools\2. Executed Templates\Closing Documents\Loan
  documents Sports Template.docx`. COM text is authoritative (the source fills
  deal data via Word FIELDS that python-docx can't see); runs/page breaks come
  from python-docx, aligned by difflib. Re-run after editing:
  `.venv\Scripts\python.exe tools\build_loandocs_template.py` (re-capture with
  `tools\capture_loandocs.py` only if the source doc itself changes).
- Holds ONLY `{{ placeholders }}`. The build hard-fails if any deal-data token
  (borrower, amounts, dates, bank numbers) survives; `tests/test_loandocs.py`
  re-checks the committed file and every render.
- Clause wording is verbatim INCLUDING the source's numbering quirks (the
  LSA's MISCELLANEOUS/CONSENTS sections are numbered 7.3-7.19) - do not "fix"
  them. ONE deliberate correction: LSA 7.17 had paste-corrupted text
  ("...common la alifornia derpliance with y such Obligor..."), restored to
  the intended "...common law for disclosure...".
- `tools/` stays git-ignored: the struct json contains the real deal data
  captured from the executed example.

Amortization workbook upload (added 2026-07-08, second pass):
- `app/loandocs_sheet.py` + `POST /api/loandocs/settlement`: drop the deal's
  "Balloon *.xlsx" / "Fully Amortized *.xlsx" on the Loan Documents tab
  (Step 2) and it fills the Memo of Settlement and the Note's Exhibit A.
- The fee block is found by its LABELS, never coordinates (it moves between
  workbooks: G/H on Balloon, K/L on Fully Amortized): "Gross Loan Amount" ->
  fee/payoff lines below it -> "To be disbursed to Borrower (Est)". Some
  workbooks continue with lines carved out of the to-Borrower figure (e.g.
  DDD Insurance) down to "Net to be disbursed to Borrower (Est)" — those are
  returned as `post_lines` / `LoanDocTerms.settlement_post_lines` and the
  rendered Memo of Settlement then closes with a recomputed Net row (added
  2026-07-10). The post section is kept ONLY when the Net terminator row is
  found, so stray cells below a Net-less block are never swept in. Blank rows
  inside the block are skipped. The sheet's own disbursed/net figures are
  returned ONLY as cross-checks (the UI compares and warns); the rendered
  memo always recomputes both subtotals from the lines.
- The Exhibit A schedule comes from the "Sheet1" tab: header row of
  Payment Number | Payment Date | Principal | Interest | Total (Payment),
  rows until the first row without a payment number (the totals row). The
  main sheet's full amortization grid has a similar header but also
  "Beginning Balance" — that's the exclusion test. Rows are reflected
  VERBATIM (dates included), rounded to cents.
- A workbook-loaded schedule takes priority over rows pulled from the credit
  memo extraction; the UI has a "clear" link to fall back to the computed
  interest-monthly + balloon schedule. Locked by `tests/test_loandocs_sheet.py`.

Team & Contract extraction (added 2026-07-08, third pass):
- `app/loandocs_extraction.py` + `POST /api/loandocs/extract`: upload the
  player's contract (or any deal documents) in the Loan Documents tab's
  "Team & contract" group and Claude fills team name, team street address,
  team city/state/zip, league, contract title, and contract date. Same
  subscription usage-token auth as extraction.py / pa_extraction.py.
- Extracted values OVERWRITE the team/contract fields (the user uploaded the
  contract specifically for them); player_name fills Borrower name only when
  blank. The team address is only taken when it appears IN the documents (the
  Payment Direction Letter is mailed there) - the prompt forbids inventing it
  and surfaces a note instead, shown in the status line.
- contract_date returns ISO yyyy-mm-dd for the date picker.

Repayment structure dropdown (added 2026-07-08, fourth pass):
- `LoanDocTerms.amortization_type`: "balloon" (default) | "interest_only" |
  "fully_amortized". Drives BOTH the Note clause (d) opening sentence (the
  template's `{{ payment_structure_sentence }}`; the balloon wording is the
  source template's, verbatim - see `PAYMENT_SENTENCES` in loandocs.py) and
  the computed Exhibit A schedule (`_computed_rows`): balloon = $0 rows then
  principal + actual/365 interest via calc_amort (same engine as the memo's
  facility total); interest_only = the memo's Section X fallback (interest
  monthly, principal balloon); fully_amortized = level monthly payments with
  the last row retiring the remaining balance exactly.
- A schedule from the uploaded workbook / memo extraction still outranks the
  computed one. Uploading a workbook also sets the dropdown from the fee-block
  tab name (Balloon / Fully Amortized).

Insurance Policy dropdown (added 2026-07-08, fifth pass):
- `LoanDocTerms.has_insurance_policy` (default False) + a Yes/No dropdown in
  the tab. Swaps the LSA Exhibit A "Insurance Policy" definition via a Jinja
  conditional in the template ({% if has_insurance_policy %}):
  * Yes: "Insurance Policy means one or more policies of insurance, in a form
    and substance acceptable to the Lender, issued by insurers acceptable to
    the Lender." (wording supplied by Lauren 2026-07-08)
  * No (default): the sports template's waived wording VERBATIM, including
    its stray closing quote after "acceptable to Lender”" - do not tidy it.
- Note: the LSA rep 4.1(i) also says the insurance requirement "has been
  waived" and is NOT yet conditional - flagged to Lauren, awaiting direction.

- LSA rep 4.1(i) now follows the SAME has_insurance_policy dropdown (Lauren,
  2026-07-08): Yes = "The Insurance Policy is in full force and effect ...
  will not in any way be affected by, or terminate or lapse by reason of any
  of the transactions contemplated hereby."; No = the waived rep. Both texts
  verbatim as she supplied them (the No wording drops the source's trailing
  double period).

Credit memo upload (added 2026-07-08, sixth pass):
- `POST /api/loandocs/memo` + an uploader in the Loan Documents tab's Step 1
  (beside the live "Pull deal info" button), mirroring the PA tab's memo
  reader: drop a previously generated credit memorandum (PDF is best) and
  Claude returns the deal-level fields (`MemoDealExtraction` in
  loandocs_extraction.py; the shared `_ask_claude` helper serves both
  prompts). The memo's one-line "Address (Season)" is split into
  street/city/state/zip, the state is spelled out for the LSA text, and the
  occupation is phrased "Professional <Sport> Player".
- Like the PA reader, it fills ONLY empty fields - typed values are never
  overwritten. Memos usually lack a funding/closing date and loan number; the
  prompt says not to guess, and anything notable comes back in `notes`
  (shown in the status line).

No-team-contract checkbox (added 2026-07-09):
- `LoanDocTerms.no_team_contract` (default False) + a checkbox at the top of
  the tab's "Team & contract" group ("Athlete does not have a contract with a
  Team / employer"). When checked: the team/contract fields are disabled and
  the contract uploader is hidden; the cover page shows "None" for
  Team / Employer and Contract (a Jinja conditional in the build script's
  COVER block); and the Payment Direction Letter is dropped from the package
  — enforced server-side in `render_html` regardless of the include flag
  (the UI also unchecks and disables its checkbox, restoring it when
  unchecked). "Pull deal info" and the memo reader skip team/league while
  checked. Locked by `test_no_team_contract_drops_letter_and_blanks_cover`.
- The LSA/UCC clauses that reference the Contract (rep 4.1(e), section 5.12,
  the collateral items and the Contract / Borrower's Employer definitions)
  still render with blank underscores in this mode - swapping or omitting
  them needs verbatim no-contract wording from Lauren (same process as the
  insurance dropdown), flagged 2026-07-09.

## Closing Binder

A fourth tool (tab in `App.vue`, `view === 'binder'`) that merges the deal's
EXECUTED documents — uploaded as PDFs, usually scans of the signed set — into
one closing-binder PDF. Its structure follows an executed example binder the
user supplied (kept on the Z: drive — never name the deal here; this repo is
public), re-dressed in the credit memo's visual design:
- page 1: a cover page — borrower name, loan amount, "Closing Binder" (plus
  masthead with closing date / loan number);
- page 2 (+overflow): a TABLE OF CONTENTS with dot leaders and page ranges
  ("5-11"), headed "(Click on a section title to jump directly to the page)"
  — every row is a real /Link annotation that jumps to its section;
- optionally (default on) a title page in front of each document, in the
  loan-docs cover-sheet style; the TOC range includes it, and links/bookmarks
  target it (like the example);
- PDF outline bookmarks ("Cover", "Table of Contents", one per document).

Unlike the other builders it generates no clause text: the uploaded documents
pass through byte-for-byte.

Backend pieces:
- `app/binder_models.py` — `BinderDoc` (title + base64 PDF), `BinderInfo`
  (cover fields), `BinderRequest`.
- `app/binder.py` — `build_binder()`. The front matter renders from
  `templates/closing_binder.html.j2` through the same Playwright engine as
  the memo, in a custom pass (`_render_front`) that ALSO measures each
  `.toc-row`'s geometry via page.evaluate() before page.pdf(); pypdf then
  stitches everything together and turns the measured rows into link
  annotations. Quirks that must not regress:
  * Screen/print geometry sharing: the measured row offsets are valid in the
    PDF because the screen `.page` has the same 7in content width and .7in
    top offset as the printed page. If the template's margins/padding change,
    update `_MARGIN_TOP`/`_MARGIN_LEFT`/`_link_rect`.
  * Front-matter pagination is DETERMINISTIC: 1 cover + ceil(n/18) TOC pages
    (`_TOC_ROWS_PER_PAGE`, rows are single-line nowrap+ellipsis) + n title
    pages. `build_binder` raises if the render disagrees.
  * The front matter renders with `page_numbers=False` (a `memo.render_pdf` /
    `_pdf_footer_template` parameter) — Chromium's "Page X of N" would count
    only the front matter, not the merged documents.
  * pypdf bookmarks must be added AFTER their target page exists in the
    writer, or the destination is dead (resolves to page None). Links are
    written as direct `/Dest` arrays (pypdf `annotations.Link` with
    `target_page_index`), not /A GoTo actions.
  * This template's Jinja env has autoescape ON (unlike loandocs) because
    document titles are free-text user input.
- `app/binder_extraction.py` + `POST /api/binder/extract` — Step 1's
  document reader: drop any deal documents (credit memo, loan documents,
  term sheet; PDFs are best) and Claude returns the cover fields
  (`BinderInfoExtraction`: borrower_name, loan_amount, loan_number,
  closing_date ISO, notes). Same usage-token auth via the shared
  `_ask_claude` in loandocs_extraction.py. The prompt prefers an explicit
  Closing Date and says in `notes` which date it used; it never guesses a
  loan number.
- `POST /api/binder/sort` (same module) — Step 2's auto-sort: upload the
  SIGNED closing package (and separate insurance PDFs) and Claude returns
  every document's page range, which `_organize()` post-processes
  DETERMINISTICALLY into `BinderSortResult.sections`:
  * canonical order (affidavit, note, repayment schedule, LSA, settlement,
    UCC, direction letter), "other" sections after those, insurance ALWAYS
    last;
  * all ranges of the SAME known category merge into ONE section (Claude
    tends to report the LSA body and its Exhibit A separately), and all
    insurance files/ranges merge into one "Insurance Documents" section;
  * the GUARANTY is filed together with the LSA, directly under the
    "Loan and Security Agreement" title page — one section, one TOC row
    (`_MERGE_INTO`; the user's choice 2026-07-13, and the executed example
    has no separate Guaranty tab). The prompt still has Claude label
    "guaranty" ranges; the merge is a backend rule;
  * category "package_cover" (the package's own cover/index and per-document
    title sheets) is DROPPED — the binder adds its own cover and title
    pages; the prompt is emphatic that a title sheet is never part of the
    following document's span (Claude initially lumped them in);
  * pages Claude failed to assign are reported in `notes` (computed in
    Python from the page counts, not trusted from the model) so nothing
    disappears silently.
  Sections can therefore be PAGE RANGES of one file and can span several
  files — `BinderDoc.parts` (list of `BinderPart` with optional
  page_from/page_to, 1-indexed inclusive) carries that to `build_binder`,
  which validates ranges and parses each distinct upload only once. The
  single-file b64 form still works (the manual flow).
- Route: `POST /api/binder/pdf` (400 no docs, 422 unreadable/non-PDF upload
  naming the file, 501 renderer unavailable). PDF only — there is no Word
  export of a merged scan set.
- Tests: `tests/test_binder.py` (page math via the outline bookmarks, TOC
  link targets, filename fallback titles, non-PDF rejection).

Frontend: `frontend/src/ClosingBinderBuilder.vue`. Step 1 has BOTH the live
"Pull deal info from Loan Documents" button and a document uploader ("Read
deal documents") that fills ONLY empty fields — typed values are never
overwritten (PA/loandocs precedent), with the extraction's notes shown in
the status line. The pull reads the LOAN DOCUMENTS tab (not the memo — the
closing documents carry the binder's exact fields: borrower, loan amount,
loan number, closing date). To make that possible the Loan Documents tab's
`terms` now live in an App.vue-owned store (`loanDocsTerms`, passed down as
`terms-store`; the tab seeds missing keys from its `TERM_DEFAULTS` on first
mount) — which also means that tab's typed values now SURVIVE tab switches,
unlike the other tabs' local state.

Step 2's section rows are `{ uid, title, parts: [{file, from, to}] }` — a
manual add creates one whole-file part; "✨ Sort & organize with Claude"
sends every distinct file in the list to `/api/binder/sort` and REPLACES the
rows with the organized sections (source shown as "file.pdf p.3–8", multiple
parts joined with "+"; notes in the status line). Rows stay editable,
reorderable and removable afterwards. `binderPdf` base64-encodes each
distinct file only once however many sections it was split into. Step 2's PDF uploader
accumulates files (non-PDFs are skipped with a notice), each row has an
editable title (defaulting to the cleaned-up filename), ↑/↓ reordering and
remove; generation previews the binder inline and the download button reuses
the same blob.
