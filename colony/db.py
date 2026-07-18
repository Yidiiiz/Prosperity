"""SQLite schema, connections, transactions. Raw sqlite3, WAL mode, no ORM."""

import sqlite3
from contextlib import contextmanager

SCHEMA = """
-- Every account that can hold cash. kind: TREASURY | AGENT | ARENA
CREATE TABLE IF NOT EXISTS accounts (
  id   TEXT PRIMARY KEY,
  kind TEXT NOT NULL
);

-- The single source of truth for money. Append-only. amount_cents > 0 always.
CREATE TABLE IF NOT EXISTS ledger (
  seq            INTEGER PRIMARY KEY AUTOINCREMENT,
  tick           INTEGER NOT NULL,
  debit_account  TEXT NOT NULL REFERENCES accounts(id),
  credit_account TEXT NOT NULL REFERENCES accounts(id),
  amount_cents   INTEGER NOT NULL CHECK (amount_cents > 0),
  memo           TEXT NOT NULL
);

-- Cached balances, updated in the same transaction as each ledger insert.
CREATE TABLE IF NOT EXISTS balances (
  account_id    TEXT PRIMARY KEY REFERENCES accounts(id),
  balance_cents INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS agents (
  id           TEXT PRIMARY KEY,
  genome_json  TEXT NOT NULL,
  generation   INTEGER NOT NULL,
  parent_a     TEXT,
  parent_b     TEXT,
  born_tick    INTEGER NOT NULL,
  debt_cents   INTEGER NOT NULL DEFAULT 0,
  died_tick    INTEGER,
  death_cause  TEXT
);

-- Runtime state per agent, persisted every tick so runs resume exactly.
CREATE TABLE IF NOT EXISTS agent_state (
  agent_id                TEXT PRIMARY KEY REFERENCES agents(id),
  birth_seed_cents        INTEGER NOT NULL,
  baseline_cents          INTEGER NOT NULL,
  peak_equity_cents       INTEGER NOT NULL,
  first_snap_equity_cents INTEGER,
  hold_ticks              INTEGER NOT NULL,
  ever_traded             INTEGER NOT NULL,
  last_birth_tick         INTEGER,
  queue_since             INTEGER,
  final_equity_cents      INTEGER
);

CREATE TABLE IF NOT EXISTS positions (
  agent_id TEXT NOT NULL REFERENCES agents(id),
  asset    TEXT NOT NULL,
  lots     INTEGER NOT NULL,
  PRIMARY KEY (agent_id, asset)
);

CREATE TABLE IF NOT EXISTS trades (
  seq         INTEGER PRIMARY KEY AUTOINCREMENT,
  tick        INTEGER NOT NULL,
  agent_id    TEXT NOT NULL,
  side        TEXT NOT NULL CHECK (side IN ('BUY','SELL')),
  lots        INTEGER NOT NULL,
  price_cents INTEGER NOT NULL,
  fee_cents   INTEGER NOT NULL
);

-- Periodic equity snapshots (every snapshot_every ticks and at death).
CREATE TABLE IF NOT EXISTS snapshots (
  tick         INTEGER NOT NULL,
  agent_id     TEXT NOT NULL,
  cash_cents   INTEGER NOT NULL,
  equity_cents INTEGER NOT NULL,
  PRIMARY KEY (tick, agent_id)
);

-- Aggregate time series written every snapshot_every ticks.
CREATE TABLE IF NOT EXISTS colony_metrics (
  tick              INTEGER PRIMARY KEY,
  treasury_cents    INTEGER NOT NULL,
  colony_wealth_cents INTEGER NOT NULL,
  arena_cents       INTEGER NOT NULL,
  population        INTEGER NOT NULL,
  births_cum        INTEGER NOT NULL,
  deaths_cum        INTEGER NOT NULL,
  price_cents       INTEGER NOT NULL,
  regime_kind       TEXT NOT NULL,
  share_momentum    REAL NOT NULL,
  share_mean_revert REAL NOT NULL,
  share_sitter      REAL NOT NULL,
  diversity         REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  started_at  TEXT NOT NULL,
  config_json TEXT NOT NULL,
  last_tick   INTEGER NOT NULL DEFAULT 0,
  state_json  TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_ledger_debit  ON ledger (debit_account);
CREATE INDEX IF NOT EXISTS idx_ledger_credit ON ledger (credit_account);
CREATE INDEX IF NOT EXISTS idx_trades_agent  ON trades (agent_id);
"""


def connect(path, readonly=False):
    """Open a connection. Writers get WAL mode and explicit transactions."""
    if readonly:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    else:
        con = sqlite3.connect(path, isolation_level=None)
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA synchronous=NORMAL")
        con.execute("PRAGMA foreign_keys=ON")
    con.row_factory = sqlite3.Row
    return con


def init_schema(con):
    con.executescript(SCHEMA)


@contextmanager
def tx(con):
    """Explicit transaction: BEGIN IMMEDIATE ... COMMIT, rollback on error."""
    con.execute("BEGIN IMMEDIATE")
    try:
        yield
        con.execute("COMMIT")
    except BaseException:
        con.execute("ROLLBACK")
        raise


@contextmanager
def savepoint(con, name):
    """Nested atomic unit inside an open transaction (used for births)."""
    con.execute(f"SAVEPOINT {name}")
    try:
        yield
        con.execute(f"RELEASE {name}")
    except BaseException:
        con.execute(f"ROLLBACK TO {name}")
        con.execute(f"RELEASE {name}")
        raise
