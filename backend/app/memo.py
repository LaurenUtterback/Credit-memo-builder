"""Memo rendering.

Produces the credit memo as HTML (via the Jinja2 template that mirrors the
original design pixel-for-pixel), and exports it to PDF and Word.

- HTML  : Jinja2 template in templates/memo.html.j2
- PDF   : WeasyPrint renders the print-styled HTML to a true PDF server-side
- Word  : HTML-wrapped .doc that Microsoft Word opens with formatting intact
"""

from __future__ import annotations

import os
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
    return d.strftime("%B %-d, %Y") if d else "[Maturity Date]"


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


def _amort_html(amort: dict) -> str:
    out = []
    for r in amort["rows"]:
        cls = "grand" if r.get("is_balloon") else ""
        def cell(v):
            if v is None:
                return "—"
            return _money(v) if r.get("is_balloon") else "$0"
        out.append(
            f'<tr class="{cls}"><td>{r["num"]}</td><td>{r["date"]}</td>'
            f'<td class="num-col">{cell(r["principal"])}</td>'
            f'<td class="num-col">{cell(r["interest"])}</td>'
            f'<td class="num-col">{cell(r["payment"])}</td>'
            f'<td class="num-col">{_money(r["balance"])}</td></tr>'
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


def render_html(terms: DealTerms, ed: Extraction | None, filenames: list[str] | None = None) -> str:
    """Render the full credit memo as an HTML string."""
    filenames = filenames or []
    loan = terms.loan or 0
    rate = terms.rate or 0
    fee = terms.fee or 0
    salary = terms.salary or (ed.salary if ed else 0) or 0

    amort = None
    if terms.fund and terms.mat and loan and rate:
        amort = calc.calc_amort(loan, rate, terms.fund, terms.mat)

    cf = calc.build_cash_flow(ed, amort, loan, salary)
    facility_due = calc.facility_total(ed, amort, loan)
    ltc = calc.calc_ltc(loan, salary)

    amort_for_tpl = amort or {"rows": [], "interest": 0, "balloon": 0, "months": 0}

    context = {
        "logo": _LOGO_PATH.read_text().strip(),
        "name": terms.name or "[Borrower Name]",
        "team": terms.team or "[Team Name]",
        "league": terms.league or "[League]",
        "sport": terms.sport or "[sport]",
        "addr": terms.addr or "[Address]",
        "phone": terms.phone or "[Phone]",
        "dob": terms.dob or "[DOB]",
        "ssn": calc.mask_ssn(terms.ssn) or "[XXX-XX-####]",
        "dl": terms.dl or "[DL#]",
        "agent": terms.agent or "",
        "rate": rate,
        "fee": fee,
        "fee_amt": _money(loan * fee / 100) if loan and fee else "[Fee]",
        "net_borrower": _money(loan - loan * fee / 100) if loan and fee else "[Net]",
        "mat_fmt": _fmt_long(terms.mat),
        "md_fmt": _fmt_short(date.today()),
        "loan_type": terms.loan_type,
        "months": amort_for_tpl["months"],
        "loan_money": _money(loan),
        "salary_money": _money(salary),
        "interest_money": _money(amort_for_tpl["interest"]),
        "ltc": f"{ltc:.1f}",
        "sponsor_text": (ed.sponsorship_narrative if ed and ed.sponsorship_narrative
                         else f"{terms.name or '[Borrower Name]'} is a professional "
                              f"{terms.sport or '[sport]'} player for the "
                              f"{terms.team or '[Team Name]'} of the {terms.league or '[League]'}."),
        "credit_text": (ed.credit_notes if ed and ed.credit_notes
                        else "Credit report reviewed. No bankruptcies, no judgments, no tax liens on file."),
        "contract_notes": ed.contract_notes if ed else "",
        "contract_text": ed.contract_notes if ed else "",
        "cf_html": _cf_rows_html(cf),
        "pfs_html": _pfs_html(ed, facility_due, salary),
        "amort_html": _amort_html(amort_for_tpl),
        "doc_list_html": _doc_list_html(filenames),
    }

    return _env.get_template("memo.html.j2").render(**context)


def render_pdf(html: str) -> bytes:
    """Render the memo HTML to PDF using WeasyPrint."""
    try:
        from weasyprint import HTML  # imported lazily; heavy native dep
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "WeasyPrint is not installed or its system libraries are missing. "
            "See backend/README for setup, or use the browser print-to-PDF route."
        ) from exc
    return HTML(string=html).write_pdf()


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
