# Mail connectivity verification

SMTP severe-alert delivery is implemented in `deepstock.web.alerts`. The first
October12 IMAP check was manual. The later user-authorized session contact
bridge in `session-mail-contact.md` separately installs reply-only reception;
it never opens a strategy/execution gate or grants access to provider replies.

The October12 user explicitly requested a test after updating local `.env`.
Server settings use `DEEPSTOCK_SMTP_*` and `DEEPSTOCK_ALERT_EMAIL_TO`. Manual
IMAP checks read `DEEPSTOCK_IMAP_HOST`, `DEEPSTOCK_IMAP_PORT` and
`DEEPSTOCK_IMAP_SECURITY`; separate `DEEPSTOCK_IMAP_USERNAME`/`PASSWORD` are
optional and otherwise use SMTP credentials. Credentials remain `.env` only;
never print them, commit them or copy them to the quantitative node.

Use certificate-validated SSL or STARTTLS. A send needs explicit current user
authorization; do not send additional tests merely to reproduce a completed
check. Send one identifiable, harmless test through the existing application
formatter/authentication/transport path to the configured alert recipient. If
that address differs from the IMAP mailbox, add a verification copy to the
configured receiving mailbox and disclose that in the result. Do not contact
other third parties or reuse the old one-time Norgate inquiry approval.

For that historical connectivity test, INBOX was read-only with BODY.PEEK and
bounded recent subject/from headers until the unique test identifier matches;
then the test body/sender were verified. Do not retrieve
unrelated message bodies, mark messages read, move/delete mail or expunge.
Aliyun enterprise IMAP in this check rejected SUBJECT/BODY/TEXT UID searches;
HEADER Subject search did not match the MIME-encoded test subject. UID SEARCH
ALL plus decoded bounded headers and a refreshed read-only mailbox view found
the message. Unsupported search is not evidence of failed mail delivery.
The later shared-mailbox restriction supersedes this header-sweep fallback:
only server-filtered replies to stored Deepstock Message-IDs may be inspected.
Never repeat the old whole-inbox header scan under the current authorization.

Separate SMTP acceptance, confirmed IMAP receipt of the verification copy and
unconfirmed delivery to an external notification address. Preserve an initial
failed/ambiguous receipt and any later read-only confirmation separately. Never
resend after SMTP acceptance or an ambiguous send outcome just to fix search.
Store local ignored receipts under `artifacts/mail-checks/<unique-id>/`; avoid
credentials, other message contents and licensed research in receipts.

The dashboard loads settings at process start. After a `.env` change, reload
`systemctl --user restart deepstock-dashboard.service` when needed for that
authorized configuration test, then verify service health and login/logout.
Do not change notification schedules, live/kill controls or email-test gate
settings merely because a manual transport check passed. The subsequently
explicit30-minute session-contact authorization is separate and documented.

## October12 verified result

SMTPsmtp.qiye.aliyun.com:465/SSL and IMAPimap.qiye.aliyun.com:993/SSL worked
with certificate validation. One test message submitted2026-10-12T14:52:42Z;
both configured notification recipient and mailbox verification copy accepted,
zero SMTP refusals. IMAP login/read-only INBOX succeeded. The initial120-second
HEADER search did not find the MIME subject; read-only decoded-header/body
confirmation verified the same message, UID4, at14:55:34Z without another send.
External notification inbox delivery was not independently observed.

Receipts are in
`artifacts/mail-checks/deepstock-mail-check-20261012T145237Z-02313d27e0d7/`:
`receipt.json` preserves initial search status; `readonly-search-receipt.json`
records confirmation; `verification.json` joins the evidence. No unrelated
mail bodies fetched. Dashboard restarted22:55:45CST to load the new settings;
HTTPS health and login/logout passed. Livefalse/killtrue andResearch-only
remain. No SMTP retry, permanent receiver, email schedule or gate change.
