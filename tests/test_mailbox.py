from unittest.mock import Mock
import pytest
from proton_rag.mailbox import TestMailbox as Mailbox, SnapshotError


def wire(raw=b"Subject: fixture\r\n\r\nCobalt Tuesday"):
    m = Mock()
    m.select.return_value = ("OK", [b"1"])
    m.response.return_value = ("UIDVALIDITY", [b"123"])
    m.uid.side_effect = [
        ("OK", [b"7"]),
        ("OK", [f"1 (UID 7 RFC822.SIZE {len(raw)})".encode()]),
        ("OK", [(b"1 (UID 7 BODY[] {1}", raw), b")"]),
        ("OK", [b"7"]),
    ]
    return m


def test_peek_readonly_and_logout():
    m = wire()
    mailbox = Mailbox(m)
    snapshot = mailbox.snapshot()
    assert snapshot.validity == "123" and "7" in snapshot.messages
    m.select.assert_called_once_with('"Folders/test"', readonly=True)
    assert m.uid.call_args_list[2].args == ("FETCH", b"7", "(UID BODY.PEEK[])")
    mailbox.close()
    m.logout.assert_called_once()
    assert not m.close.called


@pytest.mark.parametrize("fault", ["search", "fetch", "changed", "count", "validity"])
def test_unreliable_snapshot_rejected(fault):
    m = wire()
    if fault == "search":
        m.uid.side_effect = [("NO", [])]
    elif fault == "fetch":
        m.uid.side_effect = [("OK", [b"7"]), ("OK", [b"1 (UID 7 RFC822.SIZE 10)"]), ("NO", [])]
    elif fault == "changed":
        m.uid.side_effect = [
            ("OK", [b"7"]),
            ("OK", [b"1 (UID 7 RFC822.SIZE 0)"]),
            ("OK", [(b"1 (UID 7 BODY[] {0}", b"")]),
            ("OK", [b"7 8"]),
        ]
    elif fault == "count":
        m.select.return_value = ("OK", [b"2"])
    else:
        m.response.return_value = ("UIDVALIDITY", [None])
    with pytest.raises(SnapshotError):
        Mailbox(m).snapshot()
