"""An API failure must name the files it was carrying, and say what to do.

Chasing a 500 on 2026-08-06 there was no record of what the failing request
contained — only an HTTP status. These lock in the manifest and the messages.
"""

import base64
import logging

import pytest

from app.models import UploadedDoc
from app import extraction


def _doc(filename: str, size: int = 300, mime: str = "") -> UploadedDoc:
    return UploadedDoc(filename=filename, mime=mime,
                       b64=base64.b64encode(b"x" * size).decode())


class _FakeError(Exception):
    """Stands in for anthropic.APIError, which carries these attributes."""
    def __init__(self, message, status_code=None, request_id=None):
        super().__init__(message)
        self.status_code = status_code
        self.request_id = request_id


def test_manifest_names_every_file(caplog):
    docs = [_doc("PFS.pdf"), _doc("terms.docx")]
    content = [
        {"type": "document", "source": {"data": docs[0].b64}},
        {"type": "text", "text": "Loan Amount | $2,025,000"},
    ]
    with caplog.at_level(logging.INFO, logger="app.extraction"):
        extraction.log_request_manifest("extraction", docs, content)
    logged = caplog.text
    assert "PFS.pdf" in logged and "terms.docx" in logged
    assert "2 document(s)" in logged


def test_manifest_sizes_a_converted_file_by_its_text_not_its_upload(caplog):
    # An 8 MB .docx becomes a few KB of text — the manifest must not imply the
    # request was 8 MB, or it points the next investigation at the wrong file.
    big = _doc("Closing Packet.docx", size=8_000_000)
    content = [{"type": "text", "text": "short extracted text"}]
    with caplog.at_level(logging.INFO, logger="app.extraction"):
        extraction.log_request_manifest("extraction", [big], content)
    assert "of text" in caplog.text
    assert "~0.0 MB sent" in caplog.text


def test_oversized_request_is_warned_about(caplog):
    docs = [_doc("huge.pdf", size=40)]
    content = [{"type": "text", "text": "y" * 20_000_000}]
    with caplog.at_level(logging.INFO, logger="app.extraction"):
        extraction.log_request_manifest("extraction", docs, content)
    assert any(r.levelno == logging.WARNING for r in caplog.records)
    assert "fewer documents" in caplog.text


@pytest.mark.parametrize("status", [500, 502, 503, 529])
def test_server_errors_are_explained_as_anthropic_side(status):
    msg = extraction.describe_api_error(
        _FakeError("Internal server error", status_code=status,
                   request_id="req_abc123"), "extraction")
    assert "not with your documents" in msg
    assert "req_abc123" in msg
    assert "fewer documents" in msg          # the one thing she can act on


def test_error_without_a_request_id_still_reads_cleanly():
    msg = extraction.describe_api_error(
        _FakeError("Internal server error", status_code=500), "extraction")
    assert "unknown" in msg


def test_non_server_errors_keep_the_original_detail():
    msg = extraction.describe_api_error(
        _FakeError("media_type must be application/pdf", status_code=400,
                   request_id="req_z"), "extraction")
    assert "media_type" in msg
    assert "req_z" in msg


def test_retries_are_configured_above_the_sdk_default():
    # The SDK's default of 2 was not enough: uploads that failed succeeded on a
    # manual retry moments later.
    assert extraction._MAX_RETRIES > 2

    captured = {}

    class _FakeAnthropic:
        def Anthropic(self, **kwargs):        # noqa: N802 - mimics the SDK
            captured.update(kwargs)
            return object()

    extraction.build_client(_FakeAnthropic(), "sk-ant-oat01-test")
    assert captured["max_retries"] == extraction._MAX_RETRIES
    assert captured["auth_token"] == "sk-ant-oat01-test"
