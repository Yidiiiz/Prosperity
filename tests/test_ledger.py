import random

import pytest

from colony import db, ledger


@pytest.fixture
def con(tmp_path):
    con = db.connect(tmp_path / "test.db")
    db.init_schema(con)
    with db.tx(con):
        ledger.create_account(con, "TREASURY", "TREASURY")
        con.execute(
            "UPDATE balances SET balance_cents = ? WHERE account_id = 'TREASURY'", (1_000_000,)
        )
        ledger.create_account(con, "ARENA:petri", "ARENA")
        ledger.create_account(con, "AGENT:000001", "AGENT")
        ledger.create_account(con, "AGENT:000002", "AGENT")
    return con


def test_simple_transfer_moves_money(con):
    with db.tx(con):
        ledger.transfer(con, 1, "TREASURY", "AGENT:000001", 500, "seed")
    assert ledger.balance(con, "TREASURY") == 999_500
    assert ledger.balance(con, "AGENT:000001") == 500


def test_agent_overdraw_rejected(con):
    with db.tx(con):
        ledger.transfer(con, 1, "TREASURY", "AGENT:000001", 100, "seed")
    with pytest.raises(ledger.InsufficientFunds):
        with db.tx(con):
            ledger.transfer(con, 2, "AGENT:000001", "TREASURY", 101, "rent")
    # the failed transaction left nothing behind
    assert ledger.balance(con, "AGENT:000001") == 100
    ledger.verify_invariants(con, 1_000_000)


def test_treasury_overdraw_rejected(con):
    with pytest.raises(ledger.InsufficientFunds):
        with db.tx(con):
            ledger.transfer(con, 1, "TREASURY", "AGENT:000001", 1_000_001, "seed")


def test_arena_may_go_negative(con):
    with db.tx(con):
        ledger.transfer(con, 1, "ARENA:petri", "AGENT:000001", 250, "sell")
    assert ledger.balance(con, "ARENA:petri") == -250
    ledger.verify_invariants(con, 1_000_000)


def test_bad_amounts_rejected(con):
    for amount in (0, -5, 1.5):
        with pytest.raises(ledger.LedgerError):
            with db.tx(con):
                ledger.transfer(con, 1, "TREASURY", "AGENT:000001", amount, "seed")


def test_unknown_accounts_rejected(con):
    with pytest.raises(ledger.LedgerError):
        with db.tx(con):
            ledger.transfer(con, 1, "NOPE", "AGENT:000001", 1, "x")
    with pytest.raises(ledger.LedgerError):
        with db.tx(con):
            ledger.transfer(con, 1, "TREASURY", "NOPE", 1, "x")
    ledger.verify_invariants(con, 1_000_000)


def test_cache_matches_ledger_sums(con):
    with db.tx(con):
        ledger.transfer(con, 1, "TREASURY", "AGENT:000001", 10_000, "seed")
        ledger.transfer(con, 1, "AGENT:000001", "ARENA:petri", 4_000, "buy")
        ledger.transfer(con, 2, "ARENA:petri", "AGENT:000001", 6_000, "sell")
        ledger.transfer(con, 2, "AGENT:000001", "TREASURY", 20, "rent")
    ledger.verify_invariants(con, 1_000_000)
    # corrupt the cache: verification must catch it
    con.execute("UPDATE balances SET balance_cents = balance_cents + 1 WHERE account_id = 'AGENT:000001'")
    with pytest.raises(ledger.AccountingError):
        ledger.verify_invariants(con, 1_000_000)


def test_conservation_under_random_transfers(con):
    rng = random.Random(7)
    accounts = ["TREASURY", "ARENA:petri", "AGENT:000001", "AGENT:000002"]
    with db.tx(con):
        for tick in range(1_000):
            debit, credit = rng.sample(accounts, 2)
            amount = rng.randint(1, 5_000)
            try:
                ledger.transfer(con, tick, debit, credit, amount, "fuzz")
            except ledger.InsufficientFunds:
                pass
    ledger.verify_invariants(con, 1_000_000)
    total = con.execute("SELECT SUM(balance_cents) FROM balances").fetchone()[0]
    assert total == 1_000_000
