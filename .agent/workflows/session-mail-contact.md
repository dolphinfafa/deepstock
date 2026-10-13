# Deepstock session contact

The October 12 authorization enables one reminder after 30 minutes without a
reply to the latest completed Deepstock session message. The sender display
name is `deepstock`, and the recipient is the locally configured owner.
The verified October 13 replies add immediate results for email-origin tasks
and separate scheduled-task results; see [scheduled-task-mail.md](scheduled-task-mail.md).

The subsequent October 13 incremental-discovery plan explicitly authorizes
read-only access to every selectable shared-mailbox folder, including spam and
deleted mail. This supersedes the former INBOX-only association/token-search
restriction. Other agents' messages receive local header classification only.
Only confirmed, unprocessed Deepstock owner replies have their bodies fetched
or their text submitted to the model. Mail and credentials remain server-only.

## Receiving and attribution

The existing `deepstock-session-mail.service` checks mail every 30 seconds,
independently of Codex availability. It lists all folders and selects each with
IMAP EXAMINE (readonly=True). It fetches minimal From, Message-ID, In-Reply-To,
References and Subject headers plus UID and size using BODY.PEEK. No messages
are marked read, moved, deleted or expunged. Subjects are only a local hint;
full subjects and other senders' addresses are not saved in the classification
store. Reply-header and subject search indexes are no longer required.

Initial history is scanned in batches of 100 headers per folder per cycle.
Each folder has a durable UIDVALIDITY, UID high-water mark and history boundary.
An explicit bounded UID range discovers later arrivals; a successful batch
commits its header records and cursor together. Interruptions resume from the
last committed batch. UIDVALIDITY changes reset that folder's cursor and
locations; Message-ID deduplication survives. Copied/moved messages share a
single classification and body-processing record across folders. Missing
Message-IDs remain pending; conflicting identities are quarantined.

Routing starts with outbound reminder/completion/scheduled Message-IDs saved
before SMTP and already confirmed owner reply chains. Validate exactly one
From address equal to DEEPSTOCK_SESSION_EMAIL_TO. Prefer In-Reply-To; only when
that header is absent use the nearest (last) References parent. All direct
parents must be confirmed. Multiple confirmed links pointing to different
sessions remain pending even if one is a direct parent. A historical Deepstock
reference cannot override an unconfirmed nearest parent or another agent's
reply. Subject-only matches, unknown parents, invalid identities and cycles
retain reasons for local review. Owner hints never bypass header validation.

Out-of-order chains are resolved from stored headers when their parents become
known, without repeated header/body downloads. Initial history recovers only
unprocessed replies with this explicit stored Deepstock association evidence.
Confirmed bodies are bounded to 256 KiB and resulting text to 24,000 characters.
Full-message identity is checked again after BODY.PEEK retrieval. Body failures
retain retry state with a 60-second backoff; queued/rejected bodies are not read
again. An exact repeat of sufficiently substantial quoted parent text is
removed using saved outbound/reply text. New and inline replies, unquoted text
and uncertain quotations survive; HTML is converted without executing it.

## Queue and existing sending rules

Confirmed replies first enter the private SQLite queue. Codex being offline
never prevents receiving. Busy/error/unavailable sessions preserve the queue;
archived/deleted sessions remain suspended. Only an eligible idle primary
CLI/VSCode session in this exact workspace can receive a reply. An unloaded
session is resumed and its status rechecked. At most one email turn starts per
session per cycle, in queue order, through the existing turn/start interface.
The stable client message ID is derived from the reply Message-ID. Model and
permission settings are inherited. Quoted/forwarded email carries no system or
developer authority. A submission claim is committed before RPC; a crash,
disconnect or ambiguous response never triggers an automatic replay.

The latest completed post-activation Codex-origin final message starts the
30-minute timer; a later Codex turn cancels it. Each turn gets at most one
reminder, not repeating mail every 30 minutes. Email-origin completion turns
are tracked across pages and send their final answer immediately in the same
mail thread, with no delayed reminder. Separate scheduled results retain their
existing scope, recipient, timing and durable receipts. Every SMTP claim is
saved before submission; sending/failed_or_ambiguous outcomes never retry
without explicit resolution. Existing subject tokens remain in outgoing mail
for compatibility but are not used to attribute incoming replies.

## State and operation

Credentials and addresses remain in local ignored .env. Existing
DEEPSTOCK_SMTP_* and DEEPSTOCK_IMAP_* values supply encrypted, certificate-
validated SMTP/IMAP. DEEPSTOCK_SESSION_EMAIL_ENABLED=true enables the bridge;
DEEPSTOCK_CODEX_SOCKET pins the current-user local Unix control socket. There
is no second receiver, polling timer, public listener or model API key.

The private store is artifacts/session-contact/private/state.sqlite3, with
0700 directories and 0600 files. Before upgrading an existing database, the
migration creates a timestamped SQLite backup and verifies its integrity.
Legacy replies are seeded as body-processed records, preserving their queue,
turn mappings, client identifiers and submission states. The two previously
adopted replies must never be downloaded or submitted again.

mail_folders stores cursors/errors, mail_messages stores classification,
confirmation reasons and body state, and mail_locations stores folder/UID
occurrences. outbound_text saves the exact outgoing text for conservative
quote removal. These augment the existing reminders/replies/completions and
scheduled_messages tables; the app/market databases are separate.

status.json reports local_uid_scan as the discovery method, folder/history/error
counts, pending confirmations, body retries, queued replies and SMTP/RPC
ambiguity. It does not claim a live new-turn test merely from successful polling.
Errors report class names only, never provider exception text or message data.

```bash
# Config/socket/history check; no SMTP or IMAP calls.
conda run --no-capture-output -n deepstock python -m scripts.run_session_mail_bridge --check
systemctl --user status deepstock-session-mail.service
```

Deploy with server tests -> GitHub -> clean quantitative-computer ff-only pull
and native validation. Runtime mail/state/backups stay only on the server.
Restart the existing service after validation. Preserve all research, broker,
auction and scheduled-task settings and trading gates.

## Verification history

Before this upgrade, the provider missed both reply-header queries and the
unpredictable subject token on two real owner replies. Exact owner-supplied IDs
recovered them; both were adopted into their existing active turns, avoiding
duplicate tasks. Two immediate result emails were SMTP-accepted. These are
historical recovery facts, not successful automatic discovery evidence.

The new synthetic tests cover history/incremental scanning, transactional
rollback/restart, UIDVALIDITY changes, cross-folder copies/moves, out-of-order
chains, same/different-session parents, foreign senders, identity conflicts,
body failures, legacy migration, conservative quoting and offline/busy/archive
queue behavior. Live verification outcomes are recorded in milestone/2026-10-13.md
only after they occur. A fresh owner's automatic session injection requires a
new actual reply; existing processed replies are not replayed to test it.


October13 first live automatic injection verified: the owner's new reply to
an unanswered-session reminder was discovered by the running UID scanner,
classified to its original session, and delivered through one new Codex turn
without an owner-supplied hint. Private state shows delivered, exactly one
reply-turn link, and a queued/body-processed classification. Later service
cycles show zero repeat receipt/submission; both previously adopted replies
remain preserved. This closes the fresh-reply receiving/routing check.

October13 native deployment validation completed after explicitly passing the
existing key to both the FRPS jump host and Windows target. The clean
quantitative checkout fast-forwarded from8f77a04 to6a20885 throughGitHub.
Dedicated WindowsPython3.12.14 full regression passed463 tests, with2 Unix-only
skips and159 existing warnings, in44.74s; the checkout remained clean. No mail,
credentials or server-private state were transferred. Together with server465
tests and the real original-session injection, this completes the planned
incremental-discovery implementation and deployment verification.
