"""Document extraction for the Loan Documents builder's Team & Contract fields.

Reuses the same Claude subscription usage-token (OAuth) authentication as the
credit-memo extraction (see ``extraction.py``) — never a pay-per-token API key.
The prompt is tuned to pull the details the closing package needs about the
athlete's employer and playing contract: the team's name and mailing address
(the Payment Direction Letter is mailed there), the league, and how the
contract should be referenced (title + "as of" date) in the Loan & Security
Agreement's Contract definition.
"""

from __future__ import annotations

import json
import os
from typing import Optional

from pydantic import BaseModel

from .models import UploadedDoc
# Reuse the exact, proven auth pieces from the credit-memo extraction.
from .extraction import usage_token, _OAUTH_BETA_HEADER, _CLAUDE_CODE_SYSTEM

EXTRACTION_MODEL = os.environ.get("EXTRACTION_MODEL", "claude-sonnet-4-6")


class TeamContractExtraction(BaseModel):
    """What Claude pulls out of the uploaded contract / deal documents."""

    player_name: Optional[str] = None
    team_name: Optional[str] = None
    team_street: Optional[str] = None
    team_city_state_zip: Optional[str] = None
    league: Optional[str] = None
    contract_title: Optional[str] = None
    contract_date: Optional[str] = None   # ISO yyyy-mm-dd for the date picker
    notes: Optional[str] = None


PROMPT = """You are an analyst at South River Capital LLC assembling the CLOSING DOCUMENTS for a loan to a professional athlete. You have been given one or more deal documents, which may include the athlete's professional playing contract (or a summary/addendum of it), a term sheet, a South River credit memorandum, or other loan documents.

Extract the TEAM and CONTRACT details below. They are used two ways: (1) the Loan & Security Agreement defines the "Contract" as: the <contract_title> between <player> and <team> dated <contract date>; and (2) a Payment Direction Letter is MAILED to the team's front office at the address you extract.

Return ONLY raw JSON, no markdown, no backticks:

{"player_name":null,"team_name":null,"team_street":null,"team_city_state_zip":null,"league":null,"contract_title":null,"contract_date":null,"notes":null}

Rules:
- Use null for anything not stated in the documents. Do not invent values.
- player_name: the athlete who is party to the contract.
- team_name: the full club/team name employing the athlete, e.g. "Baltimore Orioles".
- team_street: the street line of the club's mailing address AS STATED in the documents (e.g. "333 West Camden Street"). team_city_state_zip: the rest of that address on one line (e.g. "Baltimore, MD 21201" or "Vancouver, BC V6B 4Y8, Canada"). The letter is physically mailed there, so only use an address that appears in the documents; if none does, return null for both and say so in notes.
- league: the league's usual abbreviation (MLB, NBA, NFL, NHL, MLS, WNBA, ...).
- contract_title: a short legal reference for the contract, preferring the document's own title prefixed with the league when helpful — e.g. "MLB Uniform Player's Contract", "NBA Standard Player Contract", or "MLB Professional Contract" when the papers just call it a player contract. Null if you cannot tell.
- contract_date: the date the contract was made / its "as of" date, formatted "YYYY-MM-DD". If the documents show several contract-related dates (signing date vs. effective date), prefer the "dated as of" date and mention the others in notes. Null if not stated.
- notes: one or two short sentences flagging anything missing, ambiguous, or conflicting (e.g. "Team address not shown in the documents", "Contract is an extension dated ..."). Null if nothing notable."""


def extract_documents(docs: list[UploadedDoc]) -> TeamContractExtraction:
    """Send the uploaded documents to Claude and parse the structured response.

    Authenticates strictly with the Claude subscription usage token (OAuth).
    Raises RuntimeError if no token is set or the response can't be parsed.
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
            max_tokens=1000,
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

    return TeamContractExtraction(**data)
