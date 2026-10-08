from concurrent.futures import ThreadPoolExecutor
import pytest
from proton_rag.budget import Ledger, BudgetError


def test_concurrent_reservations_cannot_exceed_cap(tmp_path):
    path = tmp_path / "budget.db"
    Ledger(path, initialize=True)

    def reserve(_):
        try:
            Ledger(path, initialize=True).reserve(1_000_000)
            return True
        except BudgetError:
            return False

    with ThreadPoolExecutor(max_workers=12) as pool:
        assert sum(pool.map(reserve, range(20))) == 5
    assert Ledger(path, initialize=True).used() == 5_000_000


def test_rollover_retains_unknown_and_releases_settled_prior_day(tmp_path):
    day = ["2026-10-07"]
    ledger = Ledger(tmp_path / "budget.db", clock=lambda: day[0], initialize=True)
    known = ledger.reserve(1_000_000)
    ledger.settle(known, "0.1")
    ledger.reserve(2_000_000)
    day[0] = "2026-10-08"
    assert ledger.used() == 2_000_000
    with pytest.raises(BudgetError):
        ledger.reserve(3_000_001)


def test_unexpected_overrun_latches_closed(tmp_path):
    ledger = Ledger(tmp_path / "budget.db", initialize=True)
    token = ledger.reserve(1000)
    ledger.settle(token, "0.01")
    with pytest.raises(BudgetError):
        ledger.reserve(1)


@pytest.mark.parametrize("cost", ["NaN", "Infinity", "-1"])
def test_invalid_usage_does_not_release_reservation(tmp_path, cost):
    ledger = Ledger(tmp_path / "budget.db", initialize=True)
    token = ledger.reserve(1000)
    with pytest.raises(BudgetError):
        ledger.settle(token, cost)
    assert ledger.used() == 1000


def test_missing_ledger_fails_closed(tmp_path):
    with pytest.raises(BudgetError):
        Ledger(tmp_path / "missing.db")


def test_empty_file_is_not_a_known_budget(tmp_path):
    path = tmp_path / "budget.db"
    path.touch()
    with pytest.raises(BudgetError):
        Ledger(path)
