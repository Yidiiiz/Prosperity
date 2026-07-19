"""SQLite schema, connections, transactions. Raw sqlite3, WAL mode, no ORM.

v2: the ledger unit is the micro-dollar (u = 1/1,000,000 dollar, int64).
PRAGMA user_version = 2 stamps every v2 database; opening any other version
refuses — there is no migration path from v1 (the money unit changed,
BUILD_SPEC_V2 section 1.7). Old colonies are archives; new colonies are
fresh inits.
"""

import sqlite3
from contextlib import contextmanager

SCHEMA_VERSION = 2

SCHEMA = """
-- Every account that can hold cash. kind: TREASURY | AGENT | ARENA
CREATE TABLE IF NOT EXISTS accounts (
  id   TEXT PRIMARY KEY,
  kind TEXT NOT NULL
);

-- The single source of truth for money. Append-only. amount_u > 0 always.
CREATE TABLE IF NOT EXISTS ledger (
  seq            INTEGER PRIMARY KEY AUTOINCREMENT,
  tick           INTEGER NOT NULL,
  debit_account  TEXT NOT NULL REFERENCES accounts(id),
  credit_account TEXT NOT NULL REFERENCES accounts(id),
  amount_u   INTEGER NOT NULL CHECK (amount_u > 0),
  memo           TEXT NOT NULL
);

-- Cached balances, updated in the same transaction as each ledger insert.
CREATE TABLE IF NOT EXISTS balances (
  account_id    TEXT PRIMARY KEY REFERENCES accounts(id),
  balance_u INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS agents (
  id           TEXT PRIMARY KEY,
  genome_json  TEXT NOT NULL,
  generation   INTEGER NOT NULL,
  parent_a     TEXT,
  parent_b     TEXT,
  born_tick    INTEGER NOT NULL,
  debt_u   INTEGER NOT NULL DEFAULT 0,
  died_tick    INTEGER,
  death_cause  TEXT
);

-- Runtime state per agent, persisted every tick so runs resume exactly.
CREATE TABLE IF NOT EXISTS agent_state (
  agent_id                TEXT PRIMARY KEY REFERENCES agents(id),
  birth_seed_u        INTEGER NOT NULL,
  baseline_u          INTEGER NOT NULL,
  peak_equity_u       INTEGER NOT NULL,
  first_snap_equity_u INTEGER,
  hold_ticks              INTEGER NOT NULL,
  ever_traded             INTEGER NOT NULL,
  last_birth_tick         INTEGER,
  queue_since             INTEGER,
  pending_side            TEXT,
  pending_lots            INTEGER NOT NULL DEFAULT 0,
  final_equity_u          INTEGER
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
  utc         INTEGER NOT NULL,
  agent_id    TEXT NOT NULL,
  side        TEXT NOT NULL CHECK (side IN ('BUY','SELL')),
  lots        INTEGER NOT NULL,
  price_u  INTEGER NOT NULL,
  fee_u    INTEGER NOT NULL,
  spread_u INTEGER NOT NULL DEFAULT 0
);

-- Periodic equity snapshots (every snapshot_every ticks and at death).
CREATE TABLE IF NOT EXISTS snapshots (
  tick         INTEGER NOT NULL,
  agent_id     TEXT NOT NULL,
  cash_u   INTEGER NOT NULL,
  equity_u INTEGER NOT NULL,
  PRIMARY KEY (tick, agent_id)
);

-- Aggregate time series written every snapshot_every ticks.
CREATE TABLE IF NOT EXISTS colony_metrics (
  tick              INTEGER PRIMARY KEY,
  utc               INTEGER NOT NULL,
  treasury_u    INTEGER NOT NULL,
  colony_wealth_u INTEGER NOT NULL,
  arena_u       INTEGER NOT NULL,
  population        INTEGER NOT NULL,
  births_cum        INTEGER NOT NULL,
  deaths_cum        INTEGER NOT NULL,
  price_u       INTEGER NOT NULL,
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


class SchemaVersionError(Exception):
    pass


class Connection(sqlite3.Connection):
    """sqlite3.Connection that allows attributes (the in-memory mirrors)."""

    balances = None  # account_id -> balance_u mirror, attached by ledger


def check_version(con, path):
    """Refuse any initialized database whose schema version is not v2."""
    if not con.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table'").fetchone()[0]:
        return  # brand-new file: init_schema will stamp it
    version = con.execute("PRAGMA user_version").fetchone()[0]
    if version != SCHEMA_VERSION:
        raise SchemaVersionError(
            f"{path} has schema version {version}, this build requires {SCHEMA_VERSION}."
            " v1 databases cannot be migrated (the money unit changed);"
            " keep the old file as an archive and `colony init` a fresh database."
        )


def connect(path, readonly=False):
    """Open a connection. Writers get WAL mode and explicit transactions."""
    if readonly:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, factory=Connection)
    else:
        con = sqlite3.connect(path, isolation_level=None, factory=Connection)
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA synchronous=NORMAL")
        con.execute("PRAGMA foreign_keys=ON")
    con.row_factory = sqlite3.Row
    check_version(con, path)
    return con


def init_schema(con):
    con.executescript(SCHEMA)
    con.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")


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
