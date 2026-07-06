"""Public-source research on the borrower athlete (Wikipedia + Spotrac).

Feeds Section V (Project Sponsorship): the memo's narrative describes the
ATHLETE — background, career path, contract history, career earnings — rather
than just the facility. This module only GATHERS source text; the narrative
itself is composed by a second Claude call in extraction.py.

How each source is fetched (verified working 2026-07-06):
- Wikipedia via httpx (always present: it is a dependency of the anthropic
  SDK). The Wikimedia API requires a DESCRIPTIVE User-Agent — a browser-
  imitating UA gets a 403.
- Spotrac sits behind CloudFront bot protection that 403s plain HTTP clients
  and Playwright's default HeadlessChrome UA. It is fetched with the same
  Playwright headless Chromium already installed for PDF export, with a
  real-browser UA on the context. The Claude API's web_search server tool is
  NOT an option here: subscription usage (OAuth) tokens get
  'web_search_tool_result_error: unavailable'.

Research is best-effort and must never break /api/extract: every failure is
swallowed (logged at WARNING) and callers fall back to whatever narrative the
deal documents provided.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Wikimedia asks API clients to identify themselves; fake browser UAs are blocked.
_WIKI_UA = "CreditMemoBuilder/1.0 (South River Capital; lauren@southrivercapital.com)"

# Spotrac's CloudFront rules block anything advertising itself as headless.
_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

_SPOTRAC_SEARCH = "https://www.spotrac.com/search?q={query}"

# League slugs Spotrac uses as the first URL segment of player pages
# (e.g. spotrac.com/nba/player/...). Used to verify a search hit is the right
# person when the extraction knows the borrower's league.
_LEAGUE_SLUGS = ("nba", "nfl", "mlb", "nhl", "wnba", "mls", "epl", "nwsl")
_SPORT_TO_SLUG = {
    "basketball": "nba",
    "football": "nfl",
    "baseball": "mlb",
    "hockey": "nhl",
    "soccer": "mls",
}

_WIKI_MAX_CHARS = 2500
_SPOTRAC_MAX_CHARS = 6000


def _league_slug(league: str | None, sport: str | None) -> str | None:
    """Best-effort Spotrac league slug from the extracted league/sport strings."""
    for value in (league, sport):
        if not value:
            continue
        lowered = value.lower()
        for slug in _LEAGUE_SLUGS:
            if slug in lowered.split() or lowered == slug:
                return slug
        for keyword, slug in _SPORT_TO_SLUG.items():
            if keyword in lowered:
                # Women's basketball is the one keyword collision that matters.
                return "wnba" if slug == "nba" and "women" in lowered else slug
    return None


def wiki_lookup(name: str, sport: str | None) -> tuple[str | None, str | None]:
    """Return (intro_text, article_url) for the athlete's Wikipedia page."""
    import httpx  # dependency of the anthropic SDK, so always installed

    headers = {"User-Agent": _WIKI_UA}
    search = httpx.get(
        "https://en.wikipedia.org/w/api.php",
        params={
            "action": "query", "list": "search", "format": "json",
            "srsearch": f"{name} {sport or ''}".strip(), "srlimit": 3,
        },
        headers=headers, timeout=20,
    )
    search.raise_for_status()
    hits = search.json().get("query", {}).get("search", [])
    if not hits:
        return None, None

    # The hit must at least share the borrower's last name — otherwise the
    # search matched something unrelated and feeding it to the memo is worse
    # than saying nothing.
    last_name = name.split()[-1].lower()
    title = next((h["title"] for h in hits if last_name in h["title"].lower()), None)
    if not title:
        return None, None

    extract_resp = httpx.get(
        "https://en.wikipedia.org/w/api.php",
        params={
            "action": "query", "prop": "extracts", "format": "json",
            "exintro": 1, "explaintext": 1, "redirects": 1, "titles": title,
        },
        headers=headers, timeout=20,
    )
    extract_resp.raise_for_status()
    pages = extract_resp.json().get("query", {}).get("pages", {})
    text = next(iter(pages.values()), {}).get("extract", "").strip()
    if not text:
        return None, None
    url = "https://en.wikipedia.org/wiki/" + title.replace(" ", "_")
    return text[:_WIKI_MAX_CHARS], url


def spotrac_lookup(name: str, league: str | None,
                   sport: str | None) -> tuple[str | None, str | None]:
    """Return (page_text, page_url) for the athlete's Spotrac player page."""
    from playwright.sync_api import sync_playwright  # imported lazily; heavy dep

    slug = _league_slug(league, sport)
    last_name = name.split()[-1].lower()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            ctx = browser.new_context(user_agent=_BROWSER_UA, locale="en-US")
            page = ctx.new_page()
            page.goto(_SPOTRAC_SEARCH.format(query=name.replace(" ", "%20")),
                      wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(2500)
            candidates = page.eval_on_selector_all(
                "a[href*='/player/']",
                "els => els.map(e => ({href: e.href, text: (e.innerText || '').trim()}))",
            )
            matches = [c for c in candidates if last_name in c["text"].lower()]

            for cand in matches[:4]:
                page.goto(cand["href"], wait_until="domcontentloaded", timeout=45000)
                page.wait_for_timeout(2500)
                # Player URLs are league-scoped (spotrac.com/<league>/player/...);
                # when we know the borrower's league, a mismatch means this is a
                # different athlete with the same surname — keep looking.
                if slug and f"/{slug}/" not in page.url:
                    continue
                text = page.inner_text("body")
                # Skip the site chrome (nav / trending lists) at the top of the
                # body; the player content starts at their name or the
                # "Contract Details" tab strip.
                lowered = text.lower()
                start = max(lowered.find(name.lower()), lowered.find("contract details"))
                return text[max(start, 0):][:_SPOTRAC_MAX_CHARS], page.url
        finally:
            browser.close()
    return None, None


def gather_athlete_research(name: str | None, sport: str | None = None,
                            league: str | None = None) -> dict:
    """Collect public source text about the athlete. Never raises."""
    out: dict = {"wiki_text": None, "wiki_url": None,
                 "spotrac_text": None, "spotrac_url": None}
    if not name or not name.strip():
        return out
    name = name.strip()

    try:
        out["wiki_text"], out["wiki_url"] = wiki_lookup(name, sport)
    except Exception as exc:  # noqa: BLE001 - research must never break extraction
        logger.warning("Wikipedia lookup failed for %s: %s", name, exc)

    try:
        out["spotrac_text"], out["spotrac_url"] = spotrac_lookup(name, league, sport)
    except Exception as exc:  # noqa: BLE001 - research must never break extraction
        logger.warning("Spotrac lookup failed for %s: %s", name, exc)

    return out
