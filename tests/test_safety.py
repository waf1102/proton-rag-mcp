from unittest.mock import Mock
import pytest
from proton_rag.mailbox import TestMailbox as Mailbox, BoundaryError
from proton_rag.budget import Ledger, BudgetError


@pytest.mark.parametrize(
    "folder", ["INBOX", "test", "Folders/Test", "Folders/test/sub", "Folders/test\r\nEXPUNGE"]
)
def test_forbidden_folder_fails_before_network(folder):
    wire = Mock()
    with pytest.raises(BoundaryError):
        Mailbox(wire, folder)
    assert not wire.mock_calls


def test_cap_survives_new_process_and_unknown_usage(tmp_path):
    path = tmp_path / "spend.db"
    a = Ledger(path, initialize=True)
    a.reserve(4_000_000)
    with pytest.raises(BudgetError):
        Ledger(path, initialize=True).reserve(1_000_001)
    assert Ledger(path, initialize=True).used() == 4_000_000
