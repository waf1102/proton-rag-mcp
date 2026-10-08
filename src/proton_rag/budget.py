"""Shared, transactional USD microdollar accounting, UTC reset, fail closed."""

import sqlite3
import uuid
from datetime import datetime, timezone
from decimal import Decimal, ROUND_CEILING

CAP = 5_000_000


class BudgetError(RuntimeError):
    pass


def microdollars(value):
    amount = Decimal(str(value))
    if not amount.is_finite() or amount < 0:
        raise BudgetError("Unknown cost")
    return int((amount * 1_000_000).to_integral_value(rounding=ROUND_CEILING))


class Ledger:
    def __init__(self, path, clock=None, initialize=False):
        from pathlib import Path

        if not initialize and not Path(path).is_file():
            raise BudgetError("Shared ledger missing; explicit initialization required")
        self.path = path
        self.clock = clock or (lambda: datetime.now(timezone.utc).date().isoformat())
        with self.connect() as db:
            if not initialize:
                tables = {
                    r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
                }
                if not {"reservations", "safety"} <= tables:
                    raise BudgetError("Shared ledger invalid; reconciliation required")
            db.execute(
                "CREATE TABLE IF NOT EXISTS reservations "
                "(id TEXT PRIMARY KEY, day TEXT, charged INTEGER, pending INTEGER)"
            )
            db.execute("CREATE TABLE IF NOT EXISTS safety (reason TEXT)")

    def connect(self):
        return sqlite3.connect(self.path, timeout=30, isolation_level="IMMEDIATE")

    def used(self):
        with self.connect() as db:
            # Unreconciled requests carry across midnight: a timeout can still be billed later.
            return db.execute(
                "SELECT COALESCE(SUM(charged),0) FROM reservations WHERE day=? OR pending=1",
                (self.clock(),),
            ).fetchone()[0]

    def reserve(self, amount):
        if type(amount) is not int or not 0 < amount <= CAP:
            raise BudgetError("Invalid reservation")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM safety").fetchone():
                raise BudgetError("Accounting requires reconciliation")
            used = db.execute(
                "SELECT COALESCE(SUM(charged),0) FROM reservations WHERE day=? OR pending=1",
                (self.clock(),),
            ).fetchone()[0]
            if used + amount > CAP:
                raise BudgetError("Daily budget exhausted")
            token = uuid.uuid4().hex
            db.execute("INSERT INTO reservations VALUES (?,?,?,1)", (token, self.clock(), amount))
        return token

    def settle(self, token, cost):
        # Do not release uncertain reservations, including cancellation, retries and missing usage.
        if cost is None:
            return
        billed = microdollars(cost)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT charged,pending FROM reservations WHERE id=?", (token,)
            ).fetchone()
            if not row or not row[1]:
                raise BudgetError("Unknown or settled reservation")
            if billed > row[0]:
                db.execute("INSERT INTO safety VALUES (?)", ("Cost exceeded conservative reserve",))
            # Keep full conservative debit for that day; actual costs can only increase it.
            db.execute(
                "UPDATE reservations SET charged=?,pending=0,day=? WHERE id=?",
                (max(row[0], billed), self.clock(), token),
            )
