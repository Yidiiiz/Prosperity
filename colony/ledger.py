"""Double-entry ledger: the ONLY way money moves.

Every movement is one ledger row (debit account -> credit account) plus a
balance-cache update in the same transaction. Balances are periodically
verified against the ledger; any drift halts the simulation.

v2 (spec v2 section 4): a writer connection carries an in-memory balance
mirror (`con.balances`) so the hot path — `agents.cash()` several times per
agent per tick — issues zero SELECTs. The mirror is rebuilt from the
database at open (attach_mirror), updated inside `transfer` in the same
call as the SQL, rebuilt on any rollback (db.tx / db.savepoint), and
verified against the balances table on the invariant cadence: any
divergence is an AccountingError.
"""


class LedgerError(Exception):
    pass


class InsufficientFunds(LedgerError):
    pass


class AccountingError(LedgerError):
    pass


def attach_mirror(con):
    """Build the in-memory balance (and account-kind) mirror for a writer."""
    con.balances = dict(con.execute("SELECT account_id, balance_u FROM balances"))
    con.kinds = dict(con.execute("SELECT id, kind FROM accounts"))
    con.dirty_accounts = set()


def create_account(con, account_id, kind):
    if kind not in ("TREASURY", "AGENT", "ARENA"):
        raise LedgerError(f"unknown account kind {kind!r}")
    con.execute("INSERT INTO accounts (id, kind) VALUES (?, ?)", (account_id, kind))
    con.execute("INSERT INTO balances (account_id, balance_u) VALUES (?, 0)", (account_id,))
    if con.balances is not None:
        con.balances[account_id] = 0
        con.kinds[account_id] = kind


def genesis(con, account_id, amount_u):
    """Treasury genesis is money creation, not movement (#2): set the balance
    directly with no ledger row, keeping the mirror in step."""
    con.execute("UPDATE balances SET balance_u = ? WHERE account_id = ?",
                (amount_u, account_id))
    if con.balances is not None:
        con.balances[account_id] = amount_u


def balance(con, account_id):
    if con.balances is not None:
        try:
            return con.balances[account_id]
        except KeyError:
            raise LedgerError(f"unknown account {account_id}") from None
    row = con.execute(
        "SELECT balance_u FROM balances WHERE account_id = ?", (account_id,)
    ).fetchone()
    if row is None:
        raise LedgerError(f"unknown account {account_id}")
    return row[0]


def transfer(con, tick, debit, credit, amount_u, memo):
    """Single double-entry movement. Called inside an open transaction.

    Raises InsufficientFunds if the debit account is an AGENT or TREASURY
    account and would go below zero. ARENA accounts may go negative.
    """
    if not isinstance(amount_u, int) or amount_u <= 0:
        raise LedgerError(f"bad amount {amount_u!r} for memo {memo!r}")
    mirror = con.balances
    if mirror is not None:
        if debit not in mirror:
            raise LedgerError(f"unknown debit account {debit}")
        if credit not in mirror:
            raise LedgerError(f"unknown credit account {credit}")
        bal, kind = mirror[debit], con.kinds[debit]
    else:
        row = con.execute(
            "SELECT b.balance_u, a.kind FROM balances b"
            " JOIN accounts a ON a.id = b.account_id WHERE b.account_id = ?",
            (debit,),
        ).fetchone()
        if row is None:
            raise LedgerError(f"unknown debit account {debit}")
        bal, kind = row
    if kind != "ARENA" and bal - amount_u < 0:
        raise InsufficientFunds(f"{debit} holds {bal}, cannot pay {amount_u} ({memo})")
    if mirror is None:
        con.execute(
            "UPDATE balances SET balance_u = balance_u - ? WHERE account_id = ?",
            (amount_u, debit),
        )
        cur = con.execute(
            "UPDATE balances SET balance_u = balance_u + ? WHERE account_id = ?",
            (amount_u, credit),
        )
        if cur.rowcount != 1:
            raise LedgerError(f"unknown credit account {credit}")
    con.execute(
        "INSERT INTO ledger (tick, debit_account, credit_account, amount_u, memo)"
        " VALUES (?, ?, ?, ?, ?)",
        (tick, debit, credit, amount_u, memo),
    )
    if mirror is not None:
        # the table row is synced from the mirror at commit (db.flush_balances)
        mirror[debit] = bal - amount_u
        mirror[credit] += amount_u
        con.dirty_accounts.add(debit)
        con.dirty_accounts.add(credit)


def verify_fast(con, initial_treasury_u):
    """The per-cadence conservation check, O(accounts): mirror totals match
    the initial capitalization and no non-ARENA account is negative. The
    full O(ledger) audit (verify_invariants) runs at run boundaries,
    checkpoints, wind_down, and `colony verify`."""
    mirror = con.balances
    if mirror is None:
        total = con.execute("SELECT COALESCE(SUM(balance_u), 0) FROM balances").fetchone()[0]
        if total != initial_treasury_u:
            raise AccountingError(
                f"conservation violated: SUM(balances) = {total}, expected {initial_treasury_u}"
            )
        return
    total = sum(mirror.values())
    if total != initial_treasury_u:
        raise AccountingError(
            f"conservation violated: SUM(mirror) = {total}, expected {initial_treasury_u}"
        )
    kinds = con.kinds
    for account_id, bal in mirror.items():
        if bal < 0 and kinds[account_id] != "ARENA":
            raise AccountingError(f"non-ARENA account {account_id} is negative: {bal}")


def verify_invariants(con, initial_treasury_u):
    """THE conservation invariant (spec 4.4). Raises AccountingError on failure.

    The treasury's initial capitalization is created at init directly in the
    balance table (money creation, not movement), so its ledger-derived
    balance carries that base; every other account derives purely from the
    ledger. Together: SUM(all balances) == initial_treasury_u, forever.
    The in-memory mirror, when attached, must match the table exactly.
    """
    total = con.execute("SELECT COALESCE(SUM(balance_u), 0) FROM balances").fetchone()[0]
    if total != initial_treasury_u:
        raise AccountingError(
            f"conservation violated: SUM(balances) = {total}, expected {initial_treasury_u}"
        )
    rows = con.execute(
        """
        SELECT a.id, a.kind, b.balance_u,
               COALESCE(c.s, 0) AS credits, COALESCE(d.s, 0) AS debits
        FROM accounts a
        JOIN balances b ON b.account_id = a.id
        LEFT JOIN (SELECT credit_account AS k, SUM(amount_u) AS s FROM ledger GROUP BY 1) c
               ON c.k = a.id
        LEFT JOIN (SELECT debit_account AS k, SUM(amount_u) AS s FROM ledger GROUP BY 1) d
               ON d.k = a.id
        """
    ).fetchall()
    mirror = con.balances
    for account_id, kind, bal, credits, debits in rows:
        base = initial_treasury_u if account_id == "TREASURY" else 0
        if bal != base + credits - debits:
            raise AccountingError(
                f"balance cache drift on {account_id}: cached {bal},"
                f" ledger says {base + credits - debits}"
            )
        if bal < 0 and kind != "ARENA":
            raise AccountingError(f"non-ARENA account {account_id} is negative: {bal}")
        if mirror is not None and mirror.get(account_id) != bal:
            raise AccountingError(
                f"in-memory mirror drift on {account_id}:"
                f" mirror {mirror.get(account_id)}, table {bal}"
            )
    if mirror is not None and len(mirror) != len(rows):
        raise AccountingError("in-memory mirror has phantom accounts")
