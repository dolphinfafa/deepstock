"""Fake transport only; these tests must never contact or email a provider."""
from dataclasses import replace
import json
from email.parser import BytesParser

import pytest

from scripts import send_norgate_membership_inquiry as sender


class FakeSMTP:
    instances = []
    failure = None
    refused = {}

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.tls = False
        self.message = None
        self.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        pass

    def starttls(self, *, context):
        assert context
        self.tls = True

    def login(self, username, password):
        assert username == "test@example.test" and password == "synthetic-password"

    def send_message(self, message, *, from_addr, to_addrs):
        self.message = message
        assert from_addr == "test@example.test" and to_addrs == [sender.RECIPIENT]
        if self.failure:
            raise self.failure
        return self.refused


@pytest.fixture()
def transport(monkeypatch):
    FakeSMTP.instances = []
    FakeSMTP.failure = None
    FakeSMTP.refused = {}
    monkeypatch.setattr(sender.smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(sender.smtplib, "SMTP_SSL", FakeSMTP)
    return replace(sender.settings, smtp_host="smtp.example.test", smtp_from="test@example.test",
                   smtp_username="test@example.test", smtp_password="synthetic-password", smtp_security="ssl")


def test_preview_contains_only_reviewed_request_not_internal_evidence():
    subject, body = sender.request_content()
    assert "NONE vs ALLMARKETDAYS" in subject
    assert body.startswith("Hello Norgate Data support,") and body.rstrip().endswith("Thank you.")
    assert "BIGGQ" in body and "168" in body and "import norgatedata" in body
    assert "Internal evidence references" not in body and "c8eb883" not in body
    assert "artifacts/" not in body and ".env" not in body


@pytest.mark.parametrize("security", ["ssl", "starttls"])
def test_manual_send_is_single_recipient_attachment_free_and_never_repeated(tmp_path, transport, security):
    output = tmp_path / "one-attempt"
    receipt = sender.send_once(output, replace(transport, smtp_security=security))
    assert receipt["status"] == "smtp_accepted_delivery_unconfirmed"
    assert receipt["recipient"] == sender.RECIPIENT and receipt["attachment_count"] == 0
    assert "test@example.test" not in json.dumps(receipt) and "synthetic-password" not in json.dumps(receipt)
    parsed = BytesParser().parsebytes((output / "message.eml").read_bytes())
    assert not parsed.is_multipart() and parsed["To"] == sender.RECIPIENT
    assert FakeSMTP.instances[0].tls is (security == "starttls")
    with pytest.raises(FileExistsError):
        sender.send_once(output, transport)
    assert len(FakeSMTP.instances) == 1


def test_ambiguous_timeout_retains_attempt_and_is_not_retried(tmp_path, transport):
    output = tmp_path / "one-attempt"
    FakeSMTP.failure = TimeoutError("synthetic-password must not appear in receipt")
    with pytest.raises(RuntimeError, match="inspect receipt"):
        sender.send_once(output, transport)
    receipt = json.loads((output / "receipt.json").read_text())
    assert receipt["status"] == "submission_outcome_unknown"
    assert receipt["transport_error_type"] == "TimeoutError"
    assert "synthetic-password" not in json.dumps(receipt)
    with pytest.raises(FileExistsError):
        sender.send_once(output, transport)
    assert len(FakeSMTP.instances) == 1


def test_refused_recipient_never_claims_smtp_acceptance(tmp_path, transport):
    output = tmp_path / "one-attempt"
    FakeSMTP.refused = {sender.RECIPIENT: (550, b"synthetic refusal")}
    with pytest.raises(RuntimeError, match="inspect receipt"):
        sender.send_once(output, transport)
    assert json.loads((output / "receipt.json").read_text())["status"] == "recipient_refused"


def test_insecure_smtp_blocked_before_creating_an_attempt(tmp_path, transport):
    output = tmp_path / "one-attempt"
    with pytest.raises(ValueError, match="Encrypted SMTP"):
        sender.send_once(output, replace(transport, smtp_security="plain"))
    assert not output.exists() and not FakeSMTP.instances
