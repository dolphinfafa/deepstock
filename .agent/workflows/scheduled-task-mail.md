# Scheduled task result mail

The verified October13 owner reply to the08:28 result email explicitly
requests a separate result email when scheduled tasks complete. This extends
the session-contact scope; it does not authorize strategy promotion or orders.

`scripts/run_scheduled_task.py` invokes the original approved command once,
using the same dedicated interpreter, cwd and arguments. All existing cron
times, waits, quota rules and the Windows07:30 trigger stay fixed. The wrapper
retains the original exit code; mail/reporting failures never rerun research or
change task success. A bounded stdout tail is used locally to extract the last
JSON summary for legacy tasks. October21 auction commands instead emit a marked
single-line `DEEPSTOCK_RESULT_V1` aggregate JSON envelope when invoked by the
wrapper (`DEEPSTOCK_SCHEDULED_SUMMARY=1`), capped at16KiB. Full report files and
ordinary CLI output remain available locally. This prevents a large report's
opening JSON from falling outside the128KiB tail. Marked malformed output fails
closed, and lock skips with string date lists remain `skipped_locked`.
Raw stdout/stderr, prices, securities, trades and credentials are
never emailed or uploaded by this flow. Original task logging stays local.

Receipts contain only a random execution ID, allowlisted task/node/status,
timezone-aware start/end, exit code, data date and approved numeric/boolean
aggregates. Failure, quota wait, missed signal cutoff and no-op statuses remain
distinct. A successful command is task completion, not strategy validation.
Aggregate receipts also expose pending/recovered announcement identity counts
and a no-progress flag for minute backfill. An incomplete attempted date returns
`input_blocked`, not a successful backfill; quota waits remain distinct.
Private SQLite outbox `artifacts/scheduled-mail/private/outbox.sqlite3` is
ignored by Git and protected with0700/0600 on the server.

The owner was asked whether high-frequency successful collection/ingestion
should each send mail. Pending a reply, the stated working assumption is
`results`: report, backfill, backup, signal/fill/settlement and Windows defensive
observation completions each get a separate message; failed/blocked/skipped
high-frequency invocations also get messages. Only completed successful
`auction-collect`/`app-ingest` invocations are suppressed. `all` is available
if the owner prefers every invocation. No previous executions are mailed
retroactively. This assumption must not be represented as an explicit owner
selection.

Server tasks queue locally. The existing session-mail service drains that
queue every poll, independently of Codex socket or IMAP availability. Windows
keeps its scheduler action, now invoking the same core pipeline through the
receipt wrapper. The October13 complete-route follow-up adds a separate
ADX task at07:45China while preserving the defensive07:30action. Windows
The later authorized Granville prospective scope adds
Deepstock_Granville_Shadow at08:10China, preserving both prior tasks. Windows
publishes only allowlisted defensive-observation, adx-shadow and granville-shadow receipts to
`/api/agent/scheduled-results` with the existing node token. The endpoint
requires node authentication, rejects extra fields/nonfinite stats/unexpected
tasks/future timestamps and immutable-ID conflicts. Identical receipts are
idempotent. A transport failure retains the local receipt for publication at
the next scheduled execution, without rerunning the earlier task.

Enable only on the server in local `.env`:

```text
DEEPSTOCK_SCHEDULED_EMAIL_ENABLED=true
DEEPSTOCK_SCHEDULED_EMAIL_SCOPE=results
DEEPSTOCK_SCHEDULED_EMAIL_THREAD_ID=<the owner's originating primary session>
```

The existing SMTP recipient remains the locally configured session owner;
From/Reply-To display name remains `deepstock`. Each result has a separate
subject and unpredictable token. Contact state stores its originating session,
Message-ID and durable claim before SMTP. A crash or ambiguous SMTP outcome
never automatically resends. Replies follow the existing strict owner/nearest
parent checks. The later October13 authorization permits local read-only
all-folder header scans; only confirmed unprocessed Deepstock replies download
bodies, as documented in session-mail-contact.md. Original session reminders and
email-origin completion results retain their existing rules.

Before deployment, back up private contact state and crontab. Follow server
edit/test → GitHub → quantitative clean ff-only pull/test; never edit the
Windows checkout or transfer mail secrets/state. Reinstall only the managed
server command blocks, verifying identical timing fields and retained unrelated
jobs. Restart the existing server services; install no second mail receiver.

The receipt wrapper must start to capture a task outcome. A machine/interpreter
outage that prevents startup, or a killed process before receipt persistence,
does not produce a completion receipt. This flow does not claim an independent
machine-availability monitor.

Verified deploymentOctober13: server434/full tests, native432/full tests with
2 Unix-only skips; frontend build passed. Both checkouts cleanly synchronized
throughGitHub. Backups passed;17 server cron command lines updated with all
timing fields unchanged, Windows action/time retained. The actual09:38 fill
check (nothing pending, exit0) produced one separate SMTP-accepted message
at09:38:06CST, while successful high-frequency ingestion remained quiet.
Production node auth/schema/login/logout/restricted-preview and original
Granville report/metric invariance checks passed. At that deployment,
provider-side automatic reply discovery still failed and was explicitly reported as unreliable. The later incremental-discovery upgrade
replaces provider reply/subject searching with local UID-based classification.
