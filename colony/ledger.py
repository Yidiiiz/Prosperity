"""Double-entry ledger: the ONLY way money moves.

Every movement is one ledger row (debit account -> credit account) plus a
balance-cache update in the same transaction. Balances are periodically
verified against the ledger; any drift halts the simulation.
"""


class LedgerError(Exception):
    pass


class InsufficientFunds(LedgerError):
    pass


class AccountingError(LedgerError):
    pass


def create_account(con, account_id, kind):
    if kind not in ("TREASURY", "AGENT", "ARENA"):
        raise LedgerError(f"unknown account kind {kind!r}")
    con.execute("INSERT INTO accounts (id, kind) VALUES (?, ?)", (account_id, kind))
    con.execute("INSERT INTO balances (account_id, balance_u) VALUES (?, 0)", (account_id,))


def balance(con, account_id):
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


def verify_invariants(con, initial_treasury_u):
    """THE conservation invariant (spec 4.4). Raises AccountingError on failure.

    The treasury's initial capitalization is created at init directly in the
    balance table (money creation, not movement), so its ledger-derived
    balance carries that base; every other account derives purely from the
    ledger. Together: SUM(all balances) == initial_treasury_u, forever.
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
    for account_id, kind, bal, credits, debits in rows:
        base = initial_treasury_u if account_id == "TREASURY" else 0
        if bal != base + credits - debits:
            raise AccountingError(
                f"balance cache drift on {account_id}: cached {bal},"
                f" ledger says {base + credits - debits}"
            )
        if bal < 0 and kind != "ARENA":
            raise AccountingError(f"non-ARENA account {account_id} is negative: {bal}")
