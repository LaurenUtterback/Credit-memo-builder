"""Send the Participation Agreement out for signature via DocuSign.

Auth is the JWT grant (server-to-server): the app signs a JWT with the RSA
private key generated in DocuSign's "Apps & Keys" console, exchanges it for an
access token, and impersonates the sending user (one-time consent required —
``consent_url()`` builds the link). No secrets are hard-coded: everything comes
from .env (the repo is public):

    DOCUSIGN_INTEGRATION_KEY   the app's client id (GUID)
    DOCUSIGN_USER_ID           the sending user's API user id (GUID)
    DOCUSIGN_ACCOUNT_ID        optional — API account id; default account otherwise
    DOCUSIGN_ENV               "demo" (default) or "production"
    DOCUSIGN_PRIVATE_KEY_FILE  optional — path to the RSA private key
                               (default: <project root>/docusign_private_key.txt,
                               which is git-ignored)
    DOCUSIGN_PRIVATE_KEY       optional — the PEM inline (\\n-escaped) instead of a file

Signature fields are placed automatically: ``find_sign_tabs`` locates every
"By:  ______ (SEAL)" line in the rendered PDF (agreement signature page and the
Exhibit B Participation Certificate) and assigns them alternately to the Lender
and the Participant — the templates always print the Lender block above the
Participant block. Both recipients get routingOrder 1 (emailed at once), which
matches how SRC's executed PAs were routed.
"""

from __future__ import annotations

import base64
import io
import json
import os
import re
import time
from pathlib import Path
from urllib.parse import urlencode

import httpx
from pypdf import PdfReader

_AUTH_HOSTS = {"demo": "account-d.docusign.com", "production": "account.docusign.com"}
_DEFAULT_CONSENT_REDIRECT = "https://developers.docusign.com/platform/auth/consent"
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


class EsignError(RuntimeError):
    """A DocuSign step failed — the message is safe to show in the UI."""


class EsignNotConfigured(EsignError):
    """The DOCUSIGN_* environment values are missing or incomplete."""


# --- configuration -----------------------------------------------------------

def _cfg() -> dict:
    env = (os.environ.get("DOCUSIGN_ENV") or "demo").strip().lower()
    if env not in _AUTH_HOSTS:
        env = "demo"
    return {
        "integration_key": (os.environ.get("DOCUSIGN_INTEGRATION_KEY") or "").strip(),
        "user_id": (os.environ.get("DOCUSIGN_USER_ID") or "").strip(),
        "account_id": (os.environ.get("DOCUSIGN_ACCOUNT_ID") or "").strip(),
        "env": env,
        "key_file": os.environ.get("DOCUSIGN_PRIVATE_KEY_FILE")
        or str(_PROJECT_ROOT / "docusign_private_key.txt"),
    }


def _private_key_pem() -> str | None:
    inline = os.environ.get("DOCUSIGN_PRIVATE_KEY") or ""
    if "BEGIN" in inline:
        return inline.replace("\\n", "\n")
    path = Path(_cfg()["key_file"])
    if path.is_file():
        text = path.read_text(encoding="utf-8", errors="ignore")
        if "BEGIN" in text:
            return text
    return None


def status() -> dict:
    """For the UI: is one-click sending configured, and against which system?"""
    cfg = _cfg()
    ready = bool(cfg["integration_key"] and cfg["user_id"] and _private_key_pem())
    return {"provider": "docusign", "ready": ready, "mode": cfg["env"]}


def consent_url(cfg: dict | None = None) -> str:
    """One-time consent link the sending user must open and Accept."""
    cfg = cfg or _cfg()
    redirect = os.environ.get("DOCUSIGN_REDIRECT_URI") or _DEFAULT_CONSENT_REDIRECT
    query = urlencode({
        "response_type": "code",
        "scope": "signature impersonation",
        "client_id": cfg["integration_key"],
        "redirect_uri": redirect,
    })
    return f"https://{_AUTH_HOSTS[cfg['env']]}/oauth/auth?{query}"


# --- JWT grant ---------------------------------------------------------------

def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def jwt_assertion(cfg: dict, pem: str, now: int | None = None) -> str:
    """Build the RS256-signed JWT DocuSign exchanges for an access token."""
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding

    now = int(time.time()) if now is None else now
    header = {"alg": "RS256", "typ": "JWT"}
    claims = {
        "iss": cfg["integration_key"],
        "sub": cfg["user_id"],
        "aud": _AUTH_HOSTS[cfg["env"]],
        "iat": now,
        "exp": now + 3600,
        "scope": "signature impersonation",
    }
    signing_input = _b64url(json.dumps(header).encode()) + "." + _b64url(json.dumps(claims).encode())
    key = serialization.load_pem_private_key(pem.encode(), password=None)
    signature = key.sign(signing_input.encode(), padding.PKCS1v15(), hashes.SHA256())
    return signing_input + "." + _b64url(signature)


# Cached token + resolved account (the token lives ~1 hour).
_cache: dict = {"key": None, "token": None, "exp": 0.0, "account_id": None, "base_uri": None}


def _auth() -> tuple[str, str, str]:
    """-> (access_token, account_id, base_uri). Raises EsignError with a UI-safe message."""
    cfg = _cfg()
    pem = _private_key_pem()
    if not (cfg["integration_key"] and cfg["user_id"] and pem):
        raise EsignNotConfigured(
            "DocuSign isn't connected yet — set DOCUSIGN_INTEGRATION_KEY, DOCUSIGN_USER_ID "
            "and the private key in .env, then restart the backend."
        )
    cache_key = (cfg["integration_key"], cfg["user_id"], cfg["env"], cfg["account_id"])
    if _cache["token"] and _cache["key"] == cache_key and time.time() < _cache["exp"] - 60:
        return _cache["token"], _cache["account_id"], _cache["base_uri"]

    host = _AUTH_HOSTS[cfg["env"]]
    resp = httpx.post(
        f"https://{host}/oauth/token",
        data={
            "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
            "assertion": jwt_assertion(cfg, pem),
        },
        timeout=30,
    )
    if resp.status_code != 200:
        if "consent_required" in resp.text:
            raise EsignError(
                "DocuSign needs a one-time consent: open this link, sign in as the sending "
                f"user, and click Accept — then try again. {consent_url(cfg)}"
            )
        raise EsignError(f"DocuSign sign-in failed ({resp.status_code}): {resp.text[:300]}")
    token = resp.json().get("access_token", "")

    info = httpx.get(
        f"https://{host}/oauth/userinfo",
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    )
    if info.status_code != 200:
        raise EsignError(f"DocuSign account lookup failed ({info.status_code}): {info.text[:300]}")
    accounts = info.json().get("accounts", [])
    if not accounts:
        raise EsignError("DocuSign returned no accounts for this user.")
    wanted = cfg["account_id"].lower()
    account = next(
        (a for a in accounts if wanted and str(a.get("account_id", "")).lower() == wanted),
        None,
    ) or next((a for a in accounts if a.get("is_default")), accounts[0])

    _cache.update(
        key=cache_key,
        token=token,
        exp=time.time() + float(resp.json().get("expires_in", 3600)),
        account_id=account["account_id"],
        base_uri=account["base_uri"].rstrip("/") + "/restapi",
    )
    return _cache["token"], _cache["account_id"], _cache["base_uri"]


# --- signature-line detection --------------------------------------------------

_SIGN_LINE = re.compile(r"^By[:.]?\s+_{6,}")


def find_sign_tabs(pdf_bytes: bytes) -> list[dict]:
    """Locate every "By:  ______" signature line in the rendered agreement.

    Returns [{page (1-based), x, y, page_height, party}] in PDF points
    (origin bottom-left). Lines are assigned alternately lender/participant in
    reading order — the templates always print the Lender block first, both on
    the signature page and on the Exhibit B certificate.
    """
    reader = PdfReader(io.BytesIO(pdf_bytes))
    lines: list[dict] = []
    for page_index, page in enumerate(reader.pages):
        found: list[tuple[float, float]] = []

        def visit(text, cm, tm, font_dict, font_size, _found=found):
            if _SIGN_LINE.match(text.strip()):
                _found.append((float(tm[4]), float(tm[5])))

        page.extract_text(visitor_text=visit)
        found.sort(key=lambda point: -point[1])  # top of the page first
        height = float(page.mediabox.height)
        for x, y in found:
            lines.append({"page": page_index + 1, "x": x, "y": y, "page_height": height})

    if not lines or len(lines) % 2:
        raise EsignError(
            f"Could not place the signature fields: expected pairs of 'By: ____' lines, "
            f"found {len(lines)}. Send manually instead."
        )
    for i, line in enumerate(lines):
        line["party"] = "lender" if i % 2 == 0 else "participant"
    return lines


# --- envelope ------------------------------------------------------------------

def build_envelope(
    pdf_bytes: bytes,
    filename: str,
    lender_signer: dict,
    participant_signer: dict,
    subject: str,
    message: str,
    tabs: list[dict],
    draft: bool = False,
) -> dict:
    """The envelope JSON — pure function so tests can check it without network."""

    def sign_here(party: str) -> list[dict]:
        return [
            {
                "documentId": "1",
                "pageNumber": str(t["page"]),
                # DocuSign positions are top-left-origin pixels at 72dpi (= PDF
                # points). Shift right past "By:  " onto the underscores, and up
                # so the signature sits on the line.
                "xPosition": str(int(t["x"] + 30)),
                "yPosition": str(int(t["page_height"] - t["y"] - 24)),
                "scaleValue": "0.6",
            }
            for t in tabs
            if t["party"] == party
        ]

    signers = []
    for recipient_id, (party, signer) in enumerate(
        (("lender", lender_signer), ("participant", participant_signer)), start=1
    ):
        signers.append(
            {
                "email": signer["email"],
                "name": signer["name"],
                "recipientId": str(recipient_id),
                "routingOrder": "1",  # both at once — how SRC's executed PAs were routed
                "tabs": {"signHereTabs": sign_here(party)},
            }
        )
    return {
        "emailSubject": subject[:100],  # DocuSign's subject limit
        "emailBlurb": message,
        "documents": [
            {
                "documentBase64": base64.b64encode(pdf_bytes).decode(),
                "name": filename,
                "fileExtension": "pdf",
                "documentId": "1",
            }
        ],
        "recipients": {"signers": signers},
        "status": "created" if draft else "sent",
    }


def send_for_signature(
    pdf_bytes: bytes,
    filename: str,
    lender_signer: dict,
    participant_signer: dict,
    subject: str,
    message: str = "",
    draft: bool = False,
) -> dict:
    """Render-ready PDF in, envelope out. Raises EsignError with a UI-safe message."""
    tabs = find_sign_tabs(pdf_bytes)
    token, account_id, base_uri = _auth()
    payload = build_envelope(
        pdf_bytes, filename, lender_signer, participant_signer, subject, message, tabs, draft
    )
    resp = httpx.post(
        f"{base_uri}/v2.1/accounts/{account_id}/envelopes",
        json=payload,
        headers={"Authorization": f"Bearer {token}"},
        timeout=60,
    )
    if resp.status_code not in (200, 201):
        try:
            detail = resp.json().get("message") or resp.text[:300]
        except Exception:  # noqa: BLE001 - non-JSON error body
            detail = resp.text[:300]
        raise EsignError(f"DocuSign rejected the envelope ({resp.status_code}): {detail}")
    body = resp.json()
    return {
        "envelope_id": body.get("envelopeId", ""),
        "status": body.get("status", ""),
        "mode": _cfg()["env"],
    }
