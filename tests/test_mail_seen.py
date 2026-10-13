"""Flags are written only after attributable processing, never during discovery."""
import pytest
from deepstock import mail_seen
from deepstock.session_mail import poll_replies
from test_session_mail import config, db, prepare, mail, Mailbox


class FlagsMailbox(Mailbox):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.flags, self.writes, self.fail_store = {}, [], False

    def select(self, folder, readonly=False):
        self.current = folder
        return 'OK', [b'1']

    def uid(self, operation, *args):
        if operation == 'store':
            self.writes.append((self.current, *args))
            if self.fail_store:
                return 'NO', []
            self.flags[(self.current, args[0])] = '\\Seen \\Flagged'
            return 'OK', []
        if args[-1] == '(UID FLAGS)':
            return 'OK', [f"1 (UID {args[0]} FLAGS ({self.flags.get((self.current,args[0]), '')}))".encode()]
        return super().uid(operation, *args)


def processed(db):
    db.execute('INSERT INTO mail_processed VALUES (?,?,?,?,?)',
               ('<owner-reply@example.test>', 'work', 'agentMessage', 'answer', 1))
    db.commit()


def test_queue_is_unread_then_copies_are_marked_once_after_processing(db, config):
    prepare(db, config)
    mailbox = FlagsMailbox({}, folders={'"INBOX"': {b'1': mail()}, '"Spam"': {b'5': mail()}})
    poll_replies(db, config, mailbox)
    assert mail_seen.mark_processed(db, config, mailbox, 1) == 0
    assert mailbox.writes == []
    processed(db)
    assert mail_seen.mark_processed(db, config, mailbox, 2) == 2
    assert len(mailbox.body_fetches) == 1
    mailbox.flags.clear()  # User manually marks unread again: respect that.
    assert mail_seen.mark_processed(db, config, mailbox, 100) == 0
    assert len(mailbox.writes) == 2
    assert all(call[2:] == ('+FLAGS.SILENT', '(\\Seen)') for call in mailbox.writes)


def test_failed_write_retries_without_task_or_body_replay(db, config):
    prepare(db, config)
    mailbox = FlagsMailbox({b'1': mail()})
    poll_replies(db, config, mailbox)
    processed(db)
    mailbox.fail_store = True
    assert mail_seen.mark_processed(db, config, mailbox, 1) == 0
    mailbox.fail_store = False
    assert mail_seen.mark_processed(db, config, mailbox, 30) == 0
    assert mail_seen.mark_processed(db, config, mailbox, 62) == 1
    assert mailbox.body_fetches == [b'1']


def test_uid_reset_and_identity_mismatch_never_flags_wrong_mail(db, config):
    prepare(db, config)
    mailbox = FlagsMailbox({b'1': mail()})
    poll_replies(db, config, mailbox)
    processed(db)
    mailbox.validities['"INBOX"'] = 2
    assert mail_seen.mark_processed(db, config, mailbox, 1) == 0
    assert mailbox.writes == []
    poll_replies(db, config, mailbox)
    mailbox.values[b'1'] = mail(sender='someone@example.test')
    assert mail_seen.mark_processed(db, config, mailbox, 100) == 0
    assert mailbox.writes == []


@pytest.mark.parametrize('item,expected', [
    ({'type':'agentMessage','text':'已开始检查','phase':'commentary'}, True),
    ({'type':'reasoning','text':'thinking'}, False),
    ({'type':'plan','text':'plan'}, False),
    ({'type':'commandExecution','status':'inProgress'}, False),
    ({'type':'fileChange','status':'declined'}, False),
    ({'type':'commandExecution','status':'failed'}, True),
    ({'type':'mcpToolCall','status':'completed'}, True),
])
def test_processing_trigger(item, expected):
    assert mail_seen.processing_evidence(item) is expected


def test_only_mapped_turn_items_trigger_and_pagination_is_supported(db, config):
    prepare(db, config)
    poll_replies(db, config, FlagsMailbox({b'1':mail()}))
    db.execute('INSERT INTO reply_turns VALUES (?,?)', ('<owner-reply@example.test>', 'work'))
    db.commit()
    class Client:
        def request(self, method, params):
            assert method == 'thread/items/list'
            if 'cursor' not in params:
                return {'data':[{'turnId':'other','item':{'type':'agentMessage','text':'other'}}], 'nextCursor':'p2'}
            return {'data':[{'turnId':'work','item':{'id':'done','type':'commandExecution','status':'completed'}}]}
    mail_seen.observe_processing(db, Client())
    assert db.execute('SELECT evidence_type FROM mail_processed').fetchone()[0] == 'commandExecution'
