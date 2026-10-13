"""Run the user-authorized Deepstock session mail contact rule."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import signal
import time

from deepstock.codex_session_client import CodexClient
from deepstock.session_mail import ContactConfig, send_contact, state_store, synchronize, tick
from deepstock.scheduled_mail import notify_pending


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--once', action='store_true')
    parser.add_argument('--check', action='store_true', help='Read-only Codex/config check; no SMTP or IMAP operations')
    args = parser.parse_args()
    if os.getenv('DEEPSTOCK_SESSION_EMAIL_ENABLED', '').lower() != 'true':
        raise ValueError('Session contact has not been explicitly enabled in .env')
    config = ContactConfig.load()
    socket_path = Path(os.environ['DEEPSTOCK_CODEX_SOCKET'])
    db = state_store(config.root / 'artifacts/session-contact/private/state.sqlite3')
    stopping = False

    def stop(*_):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    while not stopping:
        scheduled_sent = 0
        scheduled_error = None
        if not args.check and os.getenv('DEEPSTOCK_SCHEDULED_EMAIL_ENABLED', '').lower() == 'true':
            try:
                thread_id = os.environ['DEEPSTOCK_SCHEDULED_EMAIL_THREAD_ID']
                if not thread_id.strip():
                    raise ValueError('Scheduled email needs an originating session')
                scheduled_sent = notify_pending(db, config, send_contact, thread_id=thread_id,
                    scope=os.getenv('DEEPSTOCK_SCHEDULED_EMAIL_SCOPE', 'results'))
            except Exception as error:
                scheduled_error = type(error).__name__
        try:
            with CodexClient(socket_path) as codex:
                if args.check:
                    synchronize(db, config, codex)
                    result = {'status': 'checked', 'smtp_called': False, 'imap_called': False}
                else:
                    result = {'status': 'ok', **tick(db, config, codex)}
        except Exception as error:
            # Exception text can contain provider credentials or session data.
            result = {'status': 'deferred', 'error_type': type(error).__name__}
        result['at_utc'] = time.time()
        result['scheduled_sent'] = scheduled_sent
        if scheduled_error:
            result['scheduled_error_type'] = scheduled_error
        path = config.root / 'artifacts/session-contact/private/status.json'
        path.write_text(json.dumps(result) + '\n', encoding='utf-8')
        path.chmod(0o600)
        print(json.dumps(result), flush=True)
        if args.once or args.check:
            return 0 if result['status'] in ('checked', 'ok') else 1
        for _ in range(30):
            if stopping:
                break
            time.sleep(1)
    db.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
