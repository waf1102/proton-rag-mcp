from unittest.mock import Mock
import pytest
from proton_rag.mailbox import Mailbox, SnapshotError, encode_folder, decode_folder


def test_folder_discovery_encoding_and_no_select():
    wire = Mock()
    name = encode_folder('Folders/旅行 "one"')
    quoted = name.replace(b"\\", b"\\\\").replace(b'"', b'\\"')
    wire.list.return_value = (
        "OK",
        [
            b'(\\HasNoChildren) "/" "INBOX"',
            b'(\\Noselect) "/" "Folders"',
            b'(\\HasNoChildren) "/" "' + quoted + b'"',
        ],
    )
    assert Mailbox(wire).folders() == ["INBOX", 'Folders/旅行 "one"']
    assert decode_folder(encode_folder("A&B/旅行")) == "A&B/旅行"


def test_inventory_exceeds_old_limit_and_fetch_uses_peek():
    wire = Mock()
    uids = b" ".join(str(i).encode() for i in range(1, 3002))
    wire.select.return_value = ("OK", [b"3001"])
    wire.response.return_value = ("UIDVALIDITY", [b"123"])
    wire.uid.side_effect = [
        ("OK", [uids]),
        ("OK", [b"1 (UID 7 RFC822.SIZE 4)"]),
        ("OK", [(b"1 (UID 7 BODY[] {4}", b"mail"), b")"]),
        ("OK", [uids]),
    ]
    box = Mailbox(wire)
    inv = box.inventory("INBOX")
    assert len(inv.uids) == 3001
    assert box.fetch("7") == b"mail"
    box.verify(inv)
    wire.select.assert_called_once_with(b'"INBOX"', readonly=True)
    assert wire.uid.call_args_list[2].args == ("FETCH", "7", "(UID BODY.PEEK[])")
    box.close()
    wire.logout.assert_called_once()
    wire.close.assert_not_called()


@pytest.mark.parametrize("name", ["bad\r\nEXPUNGE", "bad\x00name"])
def test_invalid_folder_rejected_before_selection(name):
    wire = Mock()
    with pytest.raises(ValueError):
        Mailbox(wire).inventory(name)
    assert not wire.mock_calls


def test_changed_inventory_refuses_reconciliation():
    wire = Mock()
    wire.select.return_value = ("OK", [b"1"])
    wire.response.return_value = ("UIDVALIDITY", [b"1"])
    wire.uid.side_effect = [("OK", [b"7"]), ("OK", [b"7 8"])]
    box = Mailbox(wire)
    inv = box.inventory("INBOX")
    with pytest.raises(SnapshotError):
        box.verify(inv)


def test_literal_folder_trailer_and_fetch_field_order():
    wire = Mock()
    wire.list.return_value = ("OK", [(b'(\\HasNoChildren) "/" {10}', b"Folders/Hi"), b""])
    box = Mailbox(wire)
    assert box.folders() == ["Folders/Hi"]
    wire.uid.side_effect = [
        ("OK", [b"1 (RFC822.SIZE 4 UID 7)"]),
        ("OK", [(b"1 (BODY[] {4}", b"mail"), b" UID 7)"]),
    ]
    assert box.fetch("7") == b"mail"
