"""Manually authorized, one-message Norgate inquiry; never a scheduled sender."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from email import policy
from email.message import EmailMessage
from email.utils import format_datetime, make_msgid
import hashlib
import json
from pathlib import Path
import smtplib
import ssl

from deepstock.data.store import ROOT
from deepstock.web.config import settings


RECIPIENT = "support@norgatedata.com"
CONTACT_URL = "https://norgatedata.com/contact.php"
DRAFT = ROOT / "docs/research/norgate-membership-semantics-request.md"


def request_content(path=DRAFT):
    """Only the approved external request, never internal provenance or files."""
    draft = path.read_text(encoding="utf-8")
    prefix = "Suggested subject: "
    subjects = [line[len(prefix):] for line in draft.splitlines() if line.startswith(prefix)]
    if len(subjects) != 1 or draft.count("Hello Norgate Data support,") != 1 or draft.count("## Internal evidence references") != 1:
        raise ValueError("Expected reviewed request boundaries required")
    body = draft.split("Hello Norgate Data support,", 1)[1].split("## Internal evidence references", 1)[0]
    body = "Hello Norgate Data support," + body.rstrip() + "\n"
    if not body.rstrip().endswith("Thank you."):
        raise ValueError("Expected approved request ending required")
    if len(body) > 12000 or any(token in body for token in ("artifacts/", ".env", "Internal evidence references", "raw_sha256", "source_sha256")):
        raise ValueError("Internal evidence is not authorized for external email")
    return subjects[0], body


def build_message(config=settings, path=DRAFT):
    if not config.smtp_from:
        raise ValueError("Configured sender required")
    subject, body = request_content(path)
    message = EmailMessage()
    message["From"] = config.smtp_from
    message["To"] = RECIPIENT
    message["Subject"] = subject
    message["Date"] = format_datetime(datetime.now(timezone.utc))
    message["Message-ID"] = make_msgid()
    message.set_content(body)
    return message


def send_once(output, config=settings, path=DRAFT):
    """An existing attempt is never resent, even if SMTP outcome was ambiguous."""
    if not config.smtp_host or not config.smtp_from or bool(config.smtp_username) != bool(config.smtp_password):
        raise ValueError("Complete SMTP configuration required")
    if config.smtp_security not in ("ssl", "starttls"):
        raise ValueError("Encrypted SMTP required for external support inquiry")
    message = build_message(config, path)
    raw = message.as_bytes(policy=policy.SMTP)
    output.mkdir(parents=True, exist_ok=False)
    (output / "message.eml").write_bytes(raw)
    receipt_path = output / "receipt.json"
    receipt = {"status": "prepared_not_sent", "recipient": RECIPIENT, "contact_url": CONTACT_URL,
               "message_id": message["Message-ID"], "subject": message["Subject"],
               "request_body_sha256": hashlib.sha256(request_content(path)[1].encode()).hexdigest(),
               "message_sha256": hashlib.sha256(raw).hexdigest(), "attachment_count": 0,
               "started_at_utc": datetime.now(timezone.utc).isoformat(),
               "manual_authorization": "User explicitly approved sending the Norgate membership inquiry",
               "policy": "One attempt only; SMTP acceptance is not confirmed delivery or a provider answer"}

    def retain():
        receipt_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")

    retain()
    # Once this attempt is reserved, any exception requires human assessment;
    # never silently create a new output directory and send it again.
    receipt["status"] = "submission_outcome_unknown"
    retain()
    try:
        context = ssl.create_default_context()
        smtp_class = smtplib.SMTP_SSL if config.smtp_security == "ssl" else smtplib.SMTP
        kwargs = {"host": config.smtp_host, "port": config.smtp_port, "timeout": 20}
        if config.smtp_security == "ssl":
            kwargs["context"] = context
        with smtp_class(**kwargs) as client:
            if config.smtp_security == "starttls":
                client.starttls(context=context)
            if config.smtp_username:
                client.login(config.smtp_username, config.smtp_password)
            refused = client.send_message(message, from_addr=config.smtp_from, to_addrs=[RECIPIENT])
            if refused:
                receipt["status"] = "recipient_refused"
                retain()
                raise RuntimeError("Support recipient rejected; inspect local transport receipt")
            receipt.update(status="smtp_accepted_delivery_unconfirmed",
                           accepted_at_utc=datetime.now(timezone.utc).isoformat())
            retain()
    except Exception as error:
        # No exception string or sender/credential is emitted to logs or Git.
        receipt["transport_error_type"] = type(error).__name__
        retain()
        raise RuntimeError("SMTP operation failed; inspect receipt before any retry") from None
    print(json.dumps(receipt))
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--send", action="store_true", help="Only after explicit user approval; submits one email")
    parser.add_argument("--output-dir", type=Path, help="New ignored local outbox directory required for send")
    args = parser.parse_args()
    if args.send:
        if args.output_dir is None:
            parser.error("--output-dir required; preserve each attempt")
        send_once(args.output_dir)
    else:
        subject, body = request_content()
        print(json.dumps({"status": "preview_only_not_sent", "recipient": RECIPIENT,
                          "subject": subject, "body": body, "attachment_count": 0}))


if __name__ == "__main__":
    main()
