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

import json
import logging
import subprocess
import sys
from pathlib import Path

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
# Generous because the text now feeds TWO consumers: the Section V narrative
# and the guaranteed-salary cross-check. The year-by-year contract tables
# (base salary + what portion is guaranteed) sit below the page's summary
# block and were cut off at the old 6000-char cap.
_SPOTRAC_MAX_CHARS = 14000


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

    # Prefer a title carrying the borrower's FULL name; fall back to the last
    # name. A hit sharing neither means the search matched something unrelated,
    # and feeding it to the memo is worse than saying nothing.
    tokens = name.lower().split()
    last_name = tokens[-1]
    title = next(
        (h["title"] for h in hits if all(t in h["title"].lower() for t in tokens)),
        None,
    ) or next((h["title"] for h in hits if last_name in h["title"].lower()), None)
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
    tokens = name.lower().split()
    last_name = tokens[-1]

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
            # Full-name matches first: a sibling or teammate sharing the
            # surname (e.g. Luke Hughes vs Jack Hughes, both NJ Devils) can
            # otherwise outrank the borrower in Spotrac's result order and
            # would pass the league check below.
            full = [c for c in matches
                    if all(t in c["text"].lower() for t in tokens)]
            ordered = full + [c for c in matches if c not in full]

            for cand in ordered[:4]:
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


def _backend_python() -> str:
    """The venv's own python.exe — never sys.executable.

    The live server is a re-exec'd child whose sys.executable points at the
    BASE Python312 install (see run_local.py), which has no playwright; the
    venv interpreter next to the app does.
    """
    venv = Path(__file__).resolve().parents[1] / ".venv" / "Scripts" / "python.exe"
    return str(venv) if venv.exists() else sys.executable


def _spotrac_lookup_subprocess(name: str, league: str | None,
                               sport: str | None) -> tuple[str | None, str | None]:
    """spotrac_lookup, run in a FRESH python process.

    A long-lived backend eventually starts failing Playwright's launch with a
    false "Executable doesn't exist" (2026-07-10, 07-22, 08-19 — the exe is
    present and untouched each time; root cause unknown). A fresh process has
    never shown it, so on any in-process failure the same lookup is retried
    once via ``python -m app.research`` (the __main__ block below).
    """
    proc = subprocess.run(
        [_backend_python(), "-m", "app.research", name, league or "", sport or ""],
        cwd=str(Path(__file__).resolve().parents[1]),
        capture_output=True, text=True, encoding="utf-8", timeout=180,
    )
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or "").strip()[-400:]
                           or f"subprocess exit {proc.returncode}")
    data = json.loads(proc.stdout.strip().splitlines()[-1])
    return data.get("text"), data.get("url")


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
        logger.warning("Spotrac lookup failed for %s: %s — retrying in a "
                       "fresh process", name, exc)
        try:
            out["spotrac_text"], out["spotrac_url"] = _spotrac_lookup_subprocess(
                name, league, sport)
        except Exception as exc2:  # noqa: BLE001
            logger.warning("Spotrac fresh-process lookup also failed for %s: %s",
                           name, exc2)

    return out


if __name__ == "__main__":
    # Subprocess entry for _spotrac_lookup_subprocess:
    #   python -m app.research <name> [league] [sport]
    # Prints one JSON line {"text": ..., "url": ...} to stdout (forced UTF-8 —
    # player pages carry accented names the Windows console codepage rejects).
    sys.stdout.reconfigure(encoding="utf-8")
    _name = sys.argv[1]
    _league = sys.argv[2] if len(sys.argv) > 2 else ""
    _sport = sys.argv[3] if len(sys.argv) > 3 else ""
    _text, _url = spotrac_lookup(_name, _league or None, _sport or None)
    print(json.dumps({"text": _text, "url": _url}))
