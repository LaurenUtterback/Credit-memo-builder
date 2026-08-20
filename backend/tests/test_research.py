"""Spotrac player-page reading (research.py).

2026-08-20: a single-match Spotrac search redirects STRAIGHT to the player
page instead of listing results. The old code only hunted for result links to
click, found none, and silently returned nothing — the UI showed "Spotrac
page could not be retrieved" while the page had in fact loaded. spotrac_lookup
now reads whatever player page it lands on via _read_player_page; these lock
that helper's league check and chrome-trimming.
"""

from app.research import _read_player_page, _SPOTRAC_MAX_CHARS


class _FakePage:
    """Stands in for a Playwright page: just a URL and a body text."""

    def __init__(self, url, body):
        self.url = url
        self._body = body

    def inner_text(self, selector):
        assert selector == "body"
        return self._body


_BODY = ("HOME NFL NBA MLB Trending: Some Other Player\n"
         "Jalen Two-Rivers\nGotham Knights, Outside Linebacker\n"
         "Contract Details\n2026 CAP HIT $4,772,747")


def test_reads_the_loaded_player_page_and_trims_site_chrome():
    page = _FakePage(
        "https://www.spotrac.com/nfl/player/_/id/0/jalen-two-rivers",
        _BODY)
    text, url = _read_player_page(page, "Jalen Two-Rivers", "nfl")
    assert "Trending" not in text                       # nav/trending trimmed
    assert text.startswith("Contract Details")          # the later marker wins
    assert "CAP HIT" in text
    assert url == page.url


def test_a_league_mismatch_means_a_different_athlete():
    # Same surname, wrong league — the league-scoped URL is the tell.
    page = _FakePage(
        "https://www.spotrac.com/nba/player/_/id/1/jalen-two-rivers",
        _BODY)
    assert _read_player_page(page, "Jalen Two-Rivers", "nfl") is None


def test_no_known_league_accepts_any_player_page():
    page = _FakePage(
        "https://www.spotrac.com/nfl/player/_/id/0/jalen-two-rivers",
        _BODY)
    assert _read_player_page(page, "Jalen Two-Rivers", None) is not None


def test_text_is_capped():
    page = _FakePage(
        "https://www.spotrac.com/nfl/player/_/id/0/jalen-two-rivers",
        "Contract Details " + "x" * (_SPOTRAC_MAX_CHARS * 2))
    text, _ = _read_player_page(page, "Jalen Two-Rivers", "nfl")
    assert len(text) <= _SPOTRAC_MAX_CHARS
