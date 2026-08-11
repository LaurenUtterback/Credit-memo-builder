"""The guaranteed-salary Spotrac cross-check (Step 2's verification line).

The verdict shown next to the Guaranteed salary field is computed
deterministically in build_salary_check, never by the model — these lock that
in, along with the check being harmless when Spotrac is unreachable and the
figure being verification-only (it lives on the Extraction model, never in
the memo's numbers).
"""

from types import SimpleNamespace

import pytest

from app import extraction
from app.models import Extraction, SalaryCheck


# --- the verdict matrix ------------------------------------------------------

def test_equal_figures_match():
    check = extraction.build_salary_check(1_650_000, 1_650_000, "https://x", "n")
    assert check["verdict"] == "match"


def test_figures_within_tolerance_match():
    # 0.1% of $2,350,000 is ~$2,350 — a $500 rounding difference is the same figure.
    check = extraction.build_salary_check(2_350_000, 2_349_500, None, "")
    assert check["verdict"] == "match"


def test_figures_outside_tolerance_mismatch():
    # The real failure this exists to catch: documents show the guaranteed
    # portion, Spotrac (or the docs) show the full season salary.
    check = extraction.build_salary_check(1_650_000, 2_350_000, None, "")
    assert check["verdict"] == "mismatch"


def test_spotrac_only_when_documents_gave_nothing():
    check = extraction.build_salary_check(0, 1_500_000, None, "")
    assert check["verdict"] == "spotrac_only"


def test_docs_only_when_spotrac_gave_nothing():
    check = extraction.build_salary_check(1_500_000, 0, None, "")
    assert check["verdict"] == "docs_only"


def test_unavailable_when_neither_gave_a_figure():
    check = extraction.build_salary_check(0, 0, None, "")
    assert check["verdict"] == "unavailable"


def test_tiny_salaries_use_the_dollar_floor():
    # Below the floor the percentage tolerance would be fractions of a dollar.
    assert extraction.build_salary_check(500, 501, None, "")["verdict"] == "match"
    assert extraction.build_salary_check(500, 600, None, "")["verdict"] == "mismatch"


def test_negative_or_none_inputs_are_clamped():
    check = extraction.build_salary_check(-5, None, None, "")
    assert check["verdict"] == "unavailable"
    assert check["spotrac_salary"] == 0


# --- the check itself --------------------------------------------------------

class _FakeClient:
    """Stands in for the Anthropic client; returns a canned reply."""

    def __init__(self, reply: str):
        self._reply = reply
        self.last_kwargs = None
        self.messages = self

    def create(self, **kwargs):
        self.last_kwargs = kwargs
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text=self._reply)])


def test_no_spotrac_page_yields_a_manual_check_note_without_calling_claude():
    # research.py could not fetch the page: the check must still return a
    # record (so the UI says the check did not run) and must not touch the API.
    out = extraction._check_salary_against_spotrac(
        client=None,  # would explode if used
        data={"salary": 1_650_000},
        research={"spotrac_text": None, "spotrac_url": None},
    )
    assert out["verdict"] == "docs_only"
    assert "spotrac.com" in out["note"]


def test_spotrac_reply_is_parsed_and_compared():
    client = _FakeClient(
        '```json\n{"spotrac_salary": 2350000, "season": "2026", '
        '"note": "Base salary per Spotrac."}\n```')
    out = extraction._check_salary_against_spotrac(
        client,
        data={"borrower_name": "Test Player", "salary": 1_650_000,
              "contract_remaining": 1_650_000},
        research={"spotrac_text": "Contract details ...",
                  "spotrac_url": "https://www.spotrac.com/nfl/player/x"},
    )
    assert out["verdict"] == "mismatch"
    assert out["spotrac_salary"] == 2_350_000
    assert out["season"] == "2026"
    assert out["spotrac_url"] == "https://www.spotrac.com/nfl/player/x"
    # the page text must actually be in the prompt that was sent
    sent = client.last_kwargs["messages"][0]["content"]
    assert "Contract details" in sent and "Test Player" in sent


def test_prompt_asks_for_the_cap_hit_not_the_base_salary():
    # Lauren, 2026-08-06: the Spotrac figure is the season's CAP HIT (base +
    # prorated signing bonus + counted bonuses), never the base salary alone.
    assert "CAP HIT" in extraction.SALARY_CHECK_PROMPT
    assert "NEVER return the base salary alone" in extraction.SALARY_CHECK_PROMPT


def test_prompt_template_formats_cleanly():
    # The JSON example inside the prompt uses doubled braces; a stray single
    # brace would make .format() raise at extraction time, not at import time.
    text = extraction.SALARY_CHECK_PROMPT.format(
        who="A", today="2026-08-06", doc_salary="1", doc_remaining="2",
        url="u", text="t")
    assert '{"spotrac_salary":0,"season":null,"note":null}' in text


# --- the model contract ------------------------------------------------------

def test_extraction_model_carries_the_check():
    ed = Extraction(salary=1.0, salary_check=extraction.build_salary_check(
        1.0, 1.0, "https://www.spotrac.com/x", "note"))
    assert isinstance(ed.salary_check, SalaryCheck)
    assert ed.salary_check.verdict == "match"
    # and old payloads without the field still validate
    assert Extraction(salary=1.0).salary_check is None
