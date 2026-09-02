"""Spotrac player-page reading (research.py).

2026-08-20: a single-match Spotrac search redirects STRAIGHT to the player
page instead of listing results. The old code only hunted for result links to
click, found none, and silently returned nothing — the UI showed "Spotrac
page could not be retrieved" while the page had in fact loaded. spotrac_lookup
now reads whatever player page it lands on via _read_player_page; these lock
that helper's league check and chrome-trimming.
"""

from app.research import (_core_tokens, _pick_wiki_title, _read_player_page,
                          _search_names, _SPOTRAC_MAX_CHARS)


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


# 2026-09-02: the extraction reads the contract's LEGAL name ("Jalen A.
# Two-Rivers Jr."), but Spotrac and Wikipedia index the common one ("Jalen
# Two-Rivers Jr."), so the middle initial turned both searches into zero
# results on a real deal and the guaranteed-salary check silently fell back
# to the documents. The lookups now retry with simplified name variants.


def test_search_names_tries_legal_then_common_then_bare():
    assert _search_names("Jalen A. Two-Rivers Jr.") == [
        "Jalen A. Two-Rivers Jr.",   # as extracted
        "Jalen Two-Rivers Jr.",      # middle initial dropped
        "Jalen Two-Rivers",          # suffix dropped too
    ]


def test_search_names_for_a_plain_name_is_just_the_name():
    # No initials or suffix — no pointless retry queries.
    assert _search_names("Jalen Two-Rivers") == ["Jalen Two-Rivers"]


def test_core_tokens_surname_is_never_the_suffix():
    # "jr." as the surname test would match ANY junior on a results page.
    assert _core_tokens("Jalen A. Two-Rivers Jr.") == ["jalen", "two-rivers"]
    assert _core_tokens("Jalen Two-Rivers III") == ["jalen", "two-rivers"]


def test_wiki_title_prefers_the_suffix_over_the_fathers_page():
    hits = [{"title": "Jalen Two-Rivers"}, {"title": "Jalen Two-Rivers Jr."}]
    strong, _ = _pick_wiki_title(hits, "Jalen A. Two-Rivers Jr.")
    assert strong == "Jalen Two-Rivers Jr."


def test_wiki_junk_hits_are_only_a_weak_match():
    # The legal-name search can return exactly this junk while the common-name
    # search finds the athlete — junk must never stop the variant loop.
    hits = [{"title": "Two-Rivers (surname)"}, {"title": "Jalen (name)"}]
    strong, weak = _pick_wiki_title(hits, "Jalen A. Two-Rivers Jr.")
    assert strong is None
    assert weak == "Two-Rivers (surname)"


def test_player_page_trim_finds_the_common_name_for_a_legal_one():
    # The page prints "Jalen Two-Rivers"; the extracted legal name carries a
    # middle initial and suffix the page never shows. The trim must still
    # find the player content (no "Contract Details" marker here on purpose).
    page = _FakePage(
        "https://www.spotrac.com/nfl/player/_/id/0/jalen-two-rivers",
        "HOME NFL NBA Trending: Some Other Player\n"
        "Jalen Two-Rivers\nGotham Knights, Outside Linebacker\n"
        "2026 CAP HIT $4,772,747")
    text, _ = _read_player_page(page, "Jalen A. Two-Rivers Jr.", "nfl")
    assert text.startswith("Jalen Two-Rivers")
    assert "Trending" not in text
