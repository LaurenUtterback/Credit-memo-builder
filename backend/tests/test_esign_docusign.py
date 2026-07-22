"""The DocuSign send-for-signature step: tab detection, envelope shape, JWT.

No test talks to DocuSign — the network calls live in send_for_signature/_auth,
which are exercised only with real credentials. Everything testable without an
account (finding the signature lines, the envelope JSON, the signed assertion)
is locked here.
"""

import base64
import json

import pytest

from app import esign_docusign as ds
from app import pa_agreement
from app.pa_models import PATerms


def _demo_terms() -> PATerms:
    return PATerms(
        borrower_name="Test Borrower",
        participant_name="Sample Participant, LLC",
        participant_signatory_name="Jane Sample",
        participation_percentage="10.00%",
        total_loan_amount="$1,000,000.00",
    )


@pytest.mark.parametrize("agreement_type", ["brookridge", "standard"])
def test_find_sign_tabs_places_two_pairs(agreement_type):
    """Both forms carry 4 'By: ____' lines: signature page + Exhibit B, lender first."""
    if not pa_agreement.pdf_available():
        pytest.skip("LibreOffice not available")
    pdf = pa_agreement.render_pdf(_demo_terms(), agreement_type)
    tabs = ds.find_sign_tabs(pdf)
    assert len(tabs) == 4
    assert [t["party"] for t in tabs] == ["lender", "participant", "lender", "participant"]
    assert len({t["page"] for t in tabs}) == 2
    for t in tabs:
        assert 0 < t["x"] < 612 and 0 < t["y"] < t["page_height"]
    by_page: dict = {}
    for t in tabs:
        by_page.setdefault(t["page"], []).append(t)
    for pair in by_page.values():
        lender = next(t for t in pair if t["party"] == "lender")
        participant = next(t for t in pair if t["party"] == "participant")
        assert lender["y"] > participant["y"]  # lender block prints above the participant's


def test_find_sign_tabs_rejects_odd_counts():
    from pypdf import PdfWriter
    import io

    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    buf = io.BytesIO()
    writer.write(buf)
    with pytest.raises(ds.EsignError):
        ds.find_sign_tabs(buf.getvalue())


def test_envelope_payload_shape():
    tabs = [
        {"page": 8, "x": 288.1, "y": 607.3, "page_height": 792.0, "party": "lender"},
        {"page": 8, "x": 288.1, "y": 434.8, "page_height": 792.0, "party": "participant"},
    ]
    env = ds.build_envelope(
        b"%PDF-fake",
        "PA.pdf",
        {"name": "Jim Plack", "email": "lender@example.com"},
        {"name": "Jane Sample", "email": "jane@example.com"},
        "Please sign: Participation Agreement - Test Borrower",
        "",
        tabs,
    )
    assert env["status"] == "sent"
    assert base64.b64decode(env["documents"][0]["documentBase64"]) == b"%PDF-fake"
    signers = env["recipients"]["signers"]
    assert [s["routingOrder"] for s in signers] == ["1", "1"]  # both emailed at once
    lender, participant = signers
    assert lender["email"] == "lender@example.com"
    assert participant["email"] == "jane@example.com"
    assert len(lender["tabs"]["signHereTabs"]) == 1
    assert len(participant["tabs"]["signHereTabs"]) == 1
    tab = lender["tabs"]["signHereTabs"][0]
    assert tab["pageNumber"] == "8"
    assert tab["xPosition"] == str(int(288.1 + 30))
    assert tab["yPosition"] == str(int(792 - 607.3 - 24))
    draft = ds.build_envelope(
        b"x", "f.pdf",
        {"name": "a", "email": "a@b.c"}, {"name": "d", "email": "d@e.f"},
        "s", "", tabs, draft=True,
    )
    assert draft["status"] == "created"  # draft envelopes email no one


def test_jwt_assertion_round_trip():
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    cfg = {"integration_key": "int-key", "user_id": "user-guid", "env": "demo"}
    token = ds.jwt_assertion(cfg, pem, now=1_700_000_000)
    header_b64, claims_b64, signature = token.split(".")

    def unpad(segment: str) -> bytes:
        return base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4))

    assert json.loads(unpad(header_b64)) == {"alg": "RS256", "typ": "JWT"}
    claims = json.loads(unpad(claims_b64))
    assert claims["iss"] == "int-key"
    assert claims["sub"] == "user-guid"
    assert claims["aud"] == "account-d.docusign.com"
    assert claims["scope"] == "signature impersonation"
    assert claims["exp"] - claims["iat"] == 3600
    assert signature


def test_status_unconfigured(monkeypatch):
    for var in (
        "DOCUSIGN_INTEGRATION_KEY", "DOCUSIGN_USER_ID", "DOCUSIGN_ACCOUNT_ID",
        "DOCUSIGN_PRIVATE_KEY", "DOCUSIGN_ENV",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("DOCUSIGN_PRIVATE_KEY_FILE", "Z:/definitely/not/here.txt")
    assert ds.status() == {"provider": "docusign", "ready": False, "mode": "demo"}
