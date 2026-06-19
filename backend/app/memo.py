"""Memo rendering.

Produces the credit memo as HTML (via the Jinja2 template that mirrors the
original design pixel-for-pixel), and exports it to PDF and Word.

- HTML  : Jinja2 template in templates/memo.html.j2
- PDF   : WeasyPrint renders the print-styled HTML to a true PDF server-side
- Word  : HTML-wrapped .doc that Microsoft Word opens with formatting intact
"""

from __future__ import annotations

import os
import re
from datetime import date, datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .models import DealTerms, Extraction
from . import calculations as calc

_TEMPLATE_DIR = Path(__file__).parent / "templates"
_LOGO_PATH = Path(__file__).parent / "logo.txt"

_env = Environment(
    loader=FileSystemLoader(str(_TEMPLATE_DIR)),
    autoescape=select_autoescape(enabled_extensions=()),  # template is trusted HTML
)

# PDF page furniture. Chromium draws these in the page MARGIN (not the body), so
# the footer can never overlap content, and its <span class="pageNumber"> /
# <span class="totalPages"> tokens are filled with the real, correct page numbers
# for however many physical pages the deal happens to span. The in-body
# ``.pg-footer`` is hidden in print (see @media print in memo.html.j2) so it does
# not double up. An empty header template suppresses Chromium's default
# date/title header.
_PDF_HEADER_TEMPLATE = "<div></div>"
_PDF_FOOTER_TEMPLATE = (
    '<div style="width:100%;box-sizing:border-box;padding:0 0.75in;margin:0;'
    'font-family:Arial,Helvetica,sans-serif;font-size:8px;line-height:1;'
    'letter-spacing:0.08em;color:#8a8a8a;text-transform:uppercase;'
    'display:flex;justify-content:space-between;align-items:center;">'
    "<span>South River Capital &mdash; Credit Memorandum</span>"
    '<span>Page <span class="pageNumber"></span> of <span class="totalPages"></span></span>'
    "</div>"
)


def _money(n) -> str:
    if n is None:
        return "—"
    try:
        n = float(n)
    except (TypeError, ValueError):
        return "—"
    if n == 0:
        return "$0"
    return "$" + f"{abs(round(n)):,}"


def _signed(n) -> str:
    if n is None:
        return "—"
    return f"({_money(abs(n))})" if n < 0 else _money(n)


def _fmt_long(d: date | None) -> str:
    # Build the no-leading-zero day from the date's integer fields instead of the
    # glibc/BSD-only "%-d" strftime code, which raises ValueError on Windows.
    return f"{d.strftime('%B')} {d.day}, {d.year}" if d else "[Maturity Date]"


def _fmt_short(d: date | None) -> str:
    return d.strftime("%m/%d/%Y") if d else "[Date]"


def _cf_rows_html(cf: dict) -> str:
    rows = [
        ("1", "Contract Receivable (Guaranteed Salary)", cf["salary_income"], ""),
    ]
    if cf["other_income"]:
        rows.append(("2", "Other Income (Annual Income Section)", cf["other_income"], ""))
    rows += [
        ("", "Gross Cash Flow", cf["income"], "total"),
        ("3", "Less: Taxes @ 45%", -cf["taxes"], ""),
        ("4", "Less: Ordinary Living Expenses @ 10%", -cf["living"], ""),
        ("", "Total Deductions", -(cf["taxes"] + cf["living"]), "total"),
        ("", "Available for Debt", cf["avail"], "total"),
        ("5", "Less: Proposed Facility", -cf["proposed_ds"], ""),
    ]
    for di in cf["debt_items"]:
        rows.append(("", "Less: " + di["label"], -di["amt"], ""))
    rows += [
        ("", "Total Debt", -cf["total_ds"], "total"),
        ("", "Net Cash Flow", cf["net_cf"], "grand"),
    ]
    return "".join(
        f'<tr class="{cls}"><td>{n}</td><td>{label}</td>'
        f'<td class="num-col">{_signed(amt)}</td></tr>'
        for n, label, amt, cls in rows
    )


def _pfs_html(ed: Extraction | None, facility_due: float, salary: float) -> str:
    bs = calc.calc_balance_sheet(ed, facility_due)
    out = []
    if ed and ed.total_assets:
        for a in ed.assets:
            if a.amount:
                out.append(f'<tr><td>{a.label}</td><td class="num-col">{_money(a.amount)}</td></tr>')
        out.append(f'<tr class="total"><td>Total Assets</td><td class="num-col">{_money(bs["assets_total"])}</td></tr>')
        out.append('<tr><td>Liabilities</td><td></td></tr>')
        if facility_due:
            out.append(f'<tr><td><em>Proposed Facility (includes interest)</em></td>'
                       f'<td class="num-col"><em>{_money(facility_due)}</em></td></tr>')
        for l in bs["liab_items"]:
            if l.amount:
                out.append(f'<tr><td>{l.label}</td><td class="num-col">{_money(l.amount)}</td></tr>')
        out.append(f'<tr class="total"><td>Total Liabilities (incl. Proposed Facility)</td>'
                   f'<td class="num-col">{_money(bs["total_liab"])}</td></tr>')
        out.append(f'<tr class="grand"><td>Net Worth (Total Assets &minus; Total Liabilities)</td>'
                   f'<td class="num-col">{_money(bs["net_worth"])}</td></tr>')
    else:
        out.append(
            f'<tr><td>Contract Receivable</td><td class="num-col">{_money(salary)}</td></tr>'
            f'<tr class="total"><td>Total Assets</td><td class="num-col">{_money(salary)}</td></tr>'
            f'<tr><td>Liabilities</td><td></td></tr>'
            f'<tr><td><em>Proposed Facility (includes interest)</em></td>'
            f'<td class="num-col"><em>{_money(facility_due)}</em></td></tr>'
            f'<tr class="total"><td>Total Liabilities (incl. Proposed Facility)</td>'
            f'<td class="num-col">{_money(facility_due)}</td></tr>'
            f'<tr class="grand"><td>Net Worth (Total Assets &minus; Total Liabilities)</td>'
            f'<td class="num-col">{_money(salary - facility_due)}</td></tr>'
        )
    return "".join(out)


def _uses_of_funds_html(uof: dict) -> str:
    """Render Section VI's disbursement waterfall from calc.calc_uses_of_funds.

    Gross Loan Amount, each deduction shown as a parenthesized reduction, the
    "To be disbursed to Borrower" subtotal, then (when the documents provide
    them) the additional costs carved out of that figure and the final "Net to
    be Disbursed to Borrower". Mirrors South River's standard disbursement table.
    """
    out = [
        f'<tr class="total"><td>Gross Loan Amount</td>'
        f'<td class="num-col">{_money(uof["gross"])}</td></tr>'
    ]
    for d in uof["deductions"]:
        out.append(f'<tr><td>{d["label"]}</td>'
                   f'<td class="num-col">({_money(d["amount"])})</td></tr>')
    out.append(
        f'<tr class="total"><td><em>To be disbursed to Borrower (Est)</em></td>'
        f'<td class="num-col"><em>{_money(uof["to_borrower"])}</em></td></tr>'
    )
    if uof["additional_costs"]:
        for a in uof["additional_costs"]:
            out.append(f'<tr><td>{a["label"]}</td>'
                       f'<td class="num-col">{_money(a["amount"])}</td></tr>')
        out.append(
            f'<tr class="grand"><td>Net to be Disbursed to Borrower (Est)</td>'
            f'<td class="num-col">{_money(uof["net_to_borrower"])}</td></tr>'
        )
    return "".join(out)


def _repayment_html(rows: list[dict], totals: dict) -> str:
    """Render Section X's repayment table — one row per scheduled payment
    (Payment | Date | Interest | Principal | Total) followed by a totals row.

    ``rows`` come from the uploaded documents when present, otherwise from
    calc.calc_repayment_schedule. An empty schedule yields a placeholder row.
    """
    if not rows:
        return ('<tr><td colspan="5" style="text-align:center;color:#777">'
                'Repayment schedule unavailable — include it in the uploaded '
                'documents, or set the loan amount, rate, and funding/maturity '
                'dates.</td></tr>')
    out = []
    for r in rows:
        out.append(
            f'<tr><td>{r["num"]}</td><td>{r["date"]}</td>'
            f'<td class="num-col">{_money(r["interest"])}</td>'
            f'<td class="num-col">{_money(r["principal"])}</td>'
            f'<td class="num-col">{_money(r["total"])}</td></tr>'
        )
    out.append(
        f'<tr class="total"><td></td><td></td>'
        f'<td class="num-col">{_money(totals.get("total_interest", 0))}</td>'
        f'<td class="num-col">{_money(totals.get("total_principal", 0))}</td>'
        f'<td class="num-col">{_money(totals.get("total_payment", 0))}</td></tr>'
    )
    return "".join(out)


def _doc_list_html(filenames: list[str]) -> str:
    if filenames:
        return "".join(
            f'<tr><td class="k" style="width:40px;text-align:center">{chr(65 + i)}</td>'
            f'<td>{fn}</td></tr>'
            for i, fn in enumerate(filenames)
        )
    return (
        '<tr><td class="k" style="width:40px;text-align:center">A</td><td>League Contract and Pay Stub</td></tr>'
        '<tr><td class="k" style="text-align:center">B</td><td>Driver\'s License</td></tr>'
        '<tr><td class="k" style="text-align:center">C</td><td>Credit Report / LexisNexis Report</td></tr>'
        '<tr><td class="k" style="text-align:center">D</td><td>Personal Financial Statement (PFS)</td></tr>'
    )


_DUP_PROFESSIONAL_RE = re.compile(r"\bprofessional(?:\s+professional\b)+", re.I)


def _dedupe_professional(html: str) -> str:
    """Collapse any run of consecutive 'professional' words down to one.

    The memo phrases the borrower as "a Professional <sport> player", and the
    sport value is already normalized (calc.normalize_sport) so it can't carry
    its own "Professional". This is the final, source-agnostic guarantee: even
    if a narrative captured from the documents (sponsorship/contract notes)
    contains the doubled word, the rendered memo never reads
    "Professional Professional ...". Casing of the first word is preserved.
    """
    return _DUP_PROFESSIONAL_RE.sub(lambda m: m.group(0).split()[0], html)


def render_html(terms: DealTerms, ed: Extraction | None, filenames: list[str] | None = None) -> str:
    """Render the full credit memo as an HTML string."""
    filenames = filenames or []
    loan = terms.loan or 0
    rate = terms.rate or 0
    fee = terms.fee or 0
    salary = terms.salary or (ed.salary if ed else 0) or 0
    sport = calc.normalize_sport(terms.sport)

    amort = None
    if terms.fund and terms.mat and loan and rate:
        amort = calc.calc_amort(loan, rate, terms.fund, terms.mat)

    cf = calc.build_cash_flow(ed, amort, loan, salary)
    facility_due = calc.facility_total(ed, amort, loan)
    ltc = calc.calc_ltc(loan, salary)
    uof = calc.calc_uses_of_funds(ed.uses_of_funds if ed else None, loan, fee)

    amort_for_tpl = amort or {"rows": [], "interest": 0, "balloon": 0, "months": 0}

    # Section X repayment schedule: prefer the schedule captured from the uploaded
    # documents; otherwise compute one (interest paid monthly, principal balloon)
    # from the deal terms. Empty when neither is available -> placeholder row.
    if ed and ed.repayment_schedule:
        rep_rows = [
            {
                "num": i + 1,
                "date": r.date or "—",
                "interest": r.interest,
                "principal": r.principal,
                "total": r.total or (r.interest + r.principal),
            }
            for i, r in enumerate(ed.repayment_schedule)
        ]
        rep_totals = {
            "total_interest": sum(r["interest"] for r in rep_rows),
            "total_principal": sum(r["principal"] for r in rep_rows),
            "total_payment": sum(r["total"] for r in rep_rows),
        }
    elif terms.fund and terms.mat and loan and rate:
        rep_sched = calc.calc_repayment_schedule(loan, rate, terms.fund, terms.mat)
        rep_rows = rep_sched["rows"]
        rep_totals = rep_sched
    else:
        rep_rows, rep_totals = [], {}

    context = {
        "logo": _LOGO_PATH.read_text().strip(),
        "name": terms.name or "[Borrower Name]",
        "team": terms.team or "[Team Name]",
        "league": terms.league or "[League]",
        "sport": sport or "[sport]",
        "addr": terms.addr or "[Address]",
        "phone": terms.phone or "[Phone]",
        "dob": terms.dob or "[DOB]",
        "ssn": calc.mask_ssn(terms.ssn) or "[XXX-XX-####]",
        "dl": terms.dl or "[DL#]",
        "agent": terms.agent or "",
        "rate": rate,
        "fee": fee,
        "fee_amt": _money(loan * fee / 100) if loan and fee else "[Fee]",
        "mat_fmt": _fmt_long(terms.mat),
        "md_fmt": _fmt_short(date.today()),
        "loan_type": terms.loan_type,
        "months": calc.loan_term_months(ed, amort),
        "loan_money": _money(loan),
        "salary_money": _money(salary),
        "interest_money": _money(amort_for_tpl["interest"]),
        "ltc": f"{ltc:.1f}",
        "sponsor_text": (ed.sponsorship_narrative if ed and ed.sponsorship_narrative
                         else f"{terms.name or '[Borrower Name]'} is a Professional "
                              f"{sport or '[sport]'} player for the "
                              f"{terms.team or '[Team Name]'} of the {terms.league or '[League]'}."),
        "credit_text": (ed.credit_notes if ed and ed.credit_notes
                        else "Credit report reviewed. No bankruptcies, no judgments, no tax liens on file."),
        "contract_notes": ed.contract_notes if ed else "",
        "contract_text": ed.contract_notes if ed else "",
        "cf_html": _cf_rows_html(cf),
        "uses_html": _uses_of_funds_html(uof),
        "pfs_html": _pfs_html(ed, facility_due, salary),
        "repayment_html": _repayment_html(rep_rows, rep_totals),
        "doc_list_html": _doc_list_html(filenames),
    }

    html = _env.get_template("memo.html.j2").render(**context)
    return _dedupe_professional(html)


def render_pdf(html: str) -> bytes:
    """Render the memo HTML to PDF using headless Chromium via Playwright.

    Chromium's print engine honors the template's own ``@page`` rules and
    ``@media print`` CSS, so the PDF matches the on-screen preview. Playwright
    ships its own browser (installed once with ``python -m playwright install
    chromium``), so no system GTK/Pango/Cairo libraries are required — which is
    why this replaced WeasyPrint, whose native deps aren't available on Windows.
    """
    try:
        from playwright.sync_api import sync_playwright  # imported lazily; heavy dep
    except ImportError as exc:
        raise RuntimeError(
            "PDF export needs Playwright. Install it with: "
            "pip install playwright  then  python -m playwright install chromium"
        ) from exc

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            try:
                page = browser.new_page()
                page.set_content(html, wait_until="load")
                # Letter size with explicit margins so Chromium reserves the bottom
                # margin for the native footer (display_header_footer). print_background
                # keeps the navy/gold styling. The footer template carries the real
                # "Page X of N" so numbering is always correct, and lives in the margin
                # so it never prints over the body. Margins match the template's @page.
                pdf = page.pdf(
                    format="Letter",
                    print_background=True,
                    display_header_footer=True,
                    header_template=_PDF_HEADER_TEMPLATE,
                    footer_template=_PDF_FOOTER_TEMPLATE,
                    margin={"top": "0.7in", "bottom": "0.6in", "left": "0.75in", "right": "0.75in"},
                )
            finally:
                browser.close()
    except Exception as exc:  # browser missing, launch failure, render error
        raise RuntimeError(
            "PDF rendering failed. If this is the first run, install the browser "
            "with: python -m playwright install chromium"
        ) from exc
    return pdf


def render_word(html: str) -> bytes:
    """Wrap the memo HTML so Microsoft Word opens it as a .doc with formatting."""
    word_html = html.replace(
        "<head>",
        "<head><!--[if gte mso 9]><xml><w:WordDocument>"
        "<w:View>Print</w:View><w:Zoom>100</w:Zoom></w:WordDocument></xml><![endif]-->",
        1,
    ).replace(
        "</head>",
        "<style>@page{size:8.5in 11in;margin:0.6in;}</style></head>",
        1,
    )
    return ("\ufeff" + word_html).encode("utf-8")
