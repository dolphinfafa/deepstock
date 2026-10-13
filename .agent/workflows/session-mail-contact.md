# Deepstock session contact

The October 12 user explicitly authorized a persistent rule: when they have not
replied to the latest Deepstock session message for 30 minutes, email their
configured owner address, identifying the sender as `deepstock`. They may
reply through Codex or email. The sender mailbox is shared with other agents;
only replies to this assistant's own emails may be inspected. This consent
persists across turns and supersedes the earlier test-only email scope for
this specific reminder/reply flow. It does not authorize unrelated sends.
The verified October 13 owner reply adds: Codex-origin messages retain the
30-minute unanswered reminder; an email-origin task receives its completed
result immediately by email, once, in the same thread.

## Behavior

The server-only `deepstock-session-mail.service` polls the existing local Codex
control socket every 30 seconds. Only primary CLI/VSCode sessions with cwd
exactly this Deepstock workspace are considered; child agents, other projects
and ephemeral sessions are excluded. The latest completed turn's final
assistant message starts the 30-minute timer. Progress updates and active turns
do not create reminders. Only messages completed after bridge activation are
eligible, so old history is never emailed retroactively.

One email is submitted per completed turn, addressed only to
`DEEPSTOCK_SESSION_EMAIL_TO`. Both From and Reply-To identify `deepstock` and
the locally configured sender mailbox. Email contains the session identifier
and the latest final message (bounded to 6000 characters), with no transcript,
tool outputs or attachments. The newest Codex user turn cancels the old timer;
a newly completed assistant turn gets a new timer. No reply means one reminder,
not repeated 30-minute emails. SMTP claim is committed before submission;
timeouts/crashes never automatically resubmit an ambiguous send. New outbound
messages include a stored unpredictable ASCII `ds-...` subject token tied to
their Message-ID, because this provider does not reliably search reply headers.

Email-origin turn IDs are persisted separately. After the corresponding turn
completes, its final answer is sent at the next poll (no 30-minute wait), with
In-Reply-To pointing to the owner reply and References retaining the chain.
It does not also get a delayed reminder. Completed email turns are located
even if newer Codex turns precede them. Busy sessions queue incoming email
until idle; they are not interrupted. Completion SMTP claims use the same
no-automatic-retry rule. Replies to completion emails are also eligible.

Replies are routed to the exact original session using `turn/start` and a
stable client message identifier. This uses the existing model/permissions;
the bridge never overrides them. A previously unloaded active session may be
resumed, but archived/deleted sessions are left suspended. Email text is
ordinary user input, with quoted/forwarded material carrying no higher
instruction authority. Sending and RPC submission ambiguity remain visible in
local state and are never silently replayed. Live gates/order governance remain.

## Shared-mailbox restriction

- Generate an unpredictable Message-ID for each Deepstock reminder and retain
  the exact originating session/turn mapping before SMTP.
- Select INBOX read-only. Candidate discovery is limited to server-side HEADER
  In-Reply-To/References matches for stored outbound IDs, the exact unpredictable
  subject token actually mailed for that outbound message, or an exact reply
  Message-ID supplied by the owner and pinned to an existing outbound parent.
  A generic subject, sender or time search is forbidden, as are ALL, header
  sweeps, other-folder scans and full-inbox downloads. Subject tokens never
  substitute for reply association: owner and nearest-parent validation still
  precede every body fetch. Legacy mail has no retroactively invented token.
- Fetch only the matching candidate's minimal reply/sender headers and size;
  verify the single configured owner sender and the nearest reply parent.
  A reply directed to another agent is rejected even if our ID appears earlier
  in its References. Fetch BODY.PEEK only after this check succeeds, then
  revalidate the complete message's identity. No marking, moving or deletion.
- Read no other messages' bodies or attachments. Do not inspect provider
  support mail or another agent's traffic under this authorization. Plain-text
  and HTML user replies are converted to bounded text without executing HTML.
- Unsupported reply-header search may use only the narrow identities above;
  if all authorized searches fail, collection stops. No broad fallback.
  Repeated UID appearances/message identifiers are deduplicated.

## Configuration and operation

All account addresses/credentials stay in local `.env`, not Git. Existing
`DEEPSTOCK_SMTP_*` values supply encrypted SMTP; `DEEPSTOCK_IMAP_*` values
supply encrypted receiving, optionally using separate credentials. Defaults
fall back to SMTP credentials. `DEEPSTOCK_SESSION_EMAIL_ENABLED=true` and
`DEEPSTOCK_SESSION_EMAIL_TO` are the explicit local activation/recipient.
`DEEPSTOCK_CODEX_SOCKET` pins the existing current-user Unix control socket;
no new public listener or OpenAI API key is introduced.

Runtime state is separate from the app/market databases:
`artifacts/session-contact/private/state.sqlite3` and `status.json`. Directory
mode 0700 and files 0600 protect local reminder mappings/reply text. They are Git
ignored, not research inputs/catalog previews and never copied to Windows.

```bash
# Config/socket/history check only: no SMTP or IMAP call.
conda run --no-capture-output -n deepstock python -m scripts.run_session_mail_bridge --check
# Service status; local status.json reports progress/failure counts.
systemctl --user status deepstock-session-mail.service
# Stop the contact rule when explicitly requested.
systemctl --user disable --now deepstock-session-mail.service
```

The service template is `config/deepstock-session-mail.service`, enabled only
on this server. User lingering is already enabled. Mailbox or Codex connection
failure defers work and records an error class without secrets/message text.
The local Codex daemon must be available for reply-state checks; the worker
never guesses that a session is unanswered while the daemon is unavailable.
No research/broker/auction schedules or execution email-test gate are changed.

Installed and enabled at 2026-10-12 23:42:37 CST after commit `300545d` was
pushed and cleanly fast-forwarded on the quantitative computer. Windows
verification passed 32 tests, with 2 Unix-transport tests skipped, and left
the checkout clean. Initial service status was `ok`, observing 4 primary
sessions, with 0 mail submissions and 0 reply bodies read. Activation time
was set at service installation so the earlier read-only checks do not make
pre-activation messages eligible. Local deployment evidence is in the private
`activation.json`; no credentials, mail or runtime state were transferred.

## Verification and source

Server regression 401 tests/111.07s passed. The 34 targeted mail/transport
tests also passed. Synthetic tests cover the 30-minute
boundary, activation/no retroactive mail, single submission/restart, Codex
reply cancellation, partial/HTML reply handling, mailbox separation, other
agent parent rejection, size limits, duplicate email/RPC outcomes and archive
preservation. A fake Unix control socket tests masking, handshake, notifications,
ping and fragmented JSON-RPC responses. The real Codex config/socket check
passed; real IMAP reply-header queries were accepted with zero matches and
zero body fetches. This setup did not send a test/reminder email or start a
synthetic Codex turn. First real owner reply will exercise live routing; no
end-to-end owner reply is falsely claimed before it occurs.

OpenAI Docs was used to confirm the local session interface and supported
reply routing: [App Server](https://learn.chatgpt.com/docs/app-server).
The deployment uses its owner-only Unix socket and current CLI 0.162.1 schemas.
The real completed-turn summary exposes phase `final_answer`; the bridge
accepts that value as well as `final`. A read-only real-summary observation
confirmed the waiting state without SMTP/IMAP or starting a Codex turn.

October 13 actual reply verification: the owner supplied the specific reply's
Message-ID, In-Reply-To and References. Its parent matched the stored 00:18
reminder exactly. Message-ID lookup located one UID; reply-header searches
returned zero despite the fetched reply's correct headers. Only that verified
owner reply body was read. No shared-inbox header sweep occurred. The existing
task adopts this reply so it is not injected a second time into Codex.

The October 13 result was sent once at 08:28:12 CST, along that verified owner
reply's thread, with SMTP acceptance recorded privately. Final server regression
passed 414 tests; quantitative regression passed 45 with 2 Unix-only skips.
Existing service restarted successfully. The actual recovery and result send
are verified; discovery of the next newly tagged reply and automatic new-turn
routing still require a live owner reply. Do not claim those were exercised by
this deliberate adoption of the existing task.
