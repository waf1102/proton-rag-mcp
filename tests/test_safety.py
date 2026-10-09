from unittest.mock import Mock
from proton_rag.mailbox import Mailbox


def test_mailbox_has_no_mutation_interface():
    box = Mailbox(Mock())
    for name in ["copy", "move", "store", "delete", "expunge", "append"]:
        assert not hasattr(box, name)
