"""Agent lifecycle: spawn, positions, equity, trade execution, death.

All money movements go through ledger.transfer. The in-memory AgentState
mirrors the agent_state table, which is flushed every tick by the
orchestrator so runs resume exactly.
"""

import json
from dataclasses import dataclass, field

from . import ledger
from .risk import buy_price_u, fee_u, sell_price_u

ASSET = "SIM"
TREASURY = "TREASURY"


def account_id(agent_id):
    return f"AGENT:{agent_id}"


@dataclass
class AgentState:
    id: str
    genome: dict
    generation: int
    born_tick: int
    birth_seed: int
    baseline: int
    debt: int
    lots: int = 0
    hold: int = 0
    ever_traded: bool = False
    peak_equity: int = 0
    first_snap_equity: int | None = None
    last_birth_tick: int | None = None
    queue_since: int | None = None
    pending_side: str | None = None  # unfilled order awaiting next bar (v2 2.3)
    pending_lots: int = 0
    fills: list = field(default_factory=list)  # fill utcs, rolling 24h (v2 7.1)
    dirty: bool = True


def cash(con, agent):
    return ledger.balance(con, account_id(agent.id))


def equity(con, agent, price):
    return cash(con, agent) + agent.lots * price


def spawn(con, tick, agent_id_str, genome, generation, parents, funders, debt):
    """Create an agent: account, seed transfer(s), rows. Caller wraps in a
    transaction (or savepoint — births must be atomic, spec 3.4)."""
    ledger.create_account(con, account_id(agent_id_str), "AGENT")
    seed = 0
    for funder_account, amount, memo in funders:
        ledger.transfer(con, tick, funder_account, account_id(agent_id_str), amount, memo)
        seed += amount
    con.execute(
        "INSERT INTO agents (id, genome_json, generation, parent_a, parent_b, born_tick, debt_u)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (agent_id_str, json.dumps(genome), generation, parents[0], parents[1], tick, debt),
    )
    con.execute(
        "INSERT INTO positions (agent_id, asset, lots) VALUES (?, ?, 0)", (agent_id_str, ASSET)
    )
    agent = AgentState(
        id=agent_id_str,
        genome=genome,
        generation=generation,
        born_tick=tick,
        birth_seed=seed,
        baseline=seed,
        debt=debt,
        peak_equity=seed,
    )
    save_state(con, agent)
    return agent


def save_state(con, agent, final_equity=None):
    con.execute(
        "INSERT OR REPLACE INTO agent_state (agent_id, birth_seed_u, baseline_u,"
        " peak_equity_u, first_snap_equity_u, hold_ticks, ever_traded,"
        " last_birth_tick, queue_since, pending_side, pending_lots, fills_json,"
        " final_equity_u)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            agent.id, agent.birth_seed, agent.baseline, agent.peak_equity,
            agent.first_snap_equity, agent.hold, int(agent.ever_traded),
            agent.last_birth_tick, agent.queue_since, agent.pending_side,
            agent.pending_lots, json.dumps(agent.fills), final_equity,
        ),
    )
    agent.dirty = False


def load_living(con):
    """Rebuild in-memory state for all living agents from the database."""
    rows = con.execute(
        """
        SELECT a.id, a.genome_json, a.generation, a.born_tick, a.debt_u,
               s.birth_seed_u, s.baseline_u, s.peak_equity_u,
               s.first_snap_equity_u, s.hold_ticks, s.ever_traded,
               s.last_birth_tick, s.queue_since, s.pending_side, s.pending_lots,
               s.fills_json, COALESCE(p.lots, 0) AS lots
        FROM agents a
        JOIN agent_state s ON s.agent_id = a.id
        LEFT JOIN positions p ON p.agent_id = a.id AND p.asset = ?
        WHERE a.died_tick IS NULL
        """,
        (ASSET,),
    ).fetchall()
    living = {}
    for row in rows:
        living[row["id"]] = AgentState(
            id=row["id"],
            genome=json.loads(row["genome_json"]),
            generation=row["generation"],
            born_tick=row["born_tick"],
            birth_seed=row["birth_seed_u"],
            baseline=row["baseline_u"],
            debt=row["debt_u"],
            lots=row["lots"],
            hold=row["hold_ticks"],
            ever_traded=bool(row["ever_traded"]),
            peak_equity=row["peak_equity_u"],
            first_snap_equity=row["first_snap_equity_u"],
            last_birth_tick=row["last_birth_tick"],
            queue_since=row["queue_since"],
            pending_side=row["pending_side"],
            pending_lots=row["pending_lots"],
            fills=json.loads(row["fills_json"]),
            dirty=False,
        )
    return living


def _set_lots(con, agent, lots):
    agent.lots = lots
    con.execute(
        "UPDATE positions SET lots = ? WHERE agent_id = ? AND asset = ?",
        (lots, agent.id, ASSET),
    )


def buy(con, tick, utc, agent, lots, price, venue, arena_account):
    """Market BUY at the venue's fill price (spread charged, rounded against
    the agent); taker fee on notional. 0-amount transfers are skipped (#27)."""
    fill = buy_price_u(price, venue)
    cost = lots * fill
    fee = fee_u(cost, venue)
    ledger.transfer(con, tick, account_id(agent.id), arena_account, cost, "buy")
    if fee > 0:
        ledger.transfer(con, tick, account_id(agent.id), arena_account, fee, "fee")
    _set_lots(con, agent, agent.lots + lots)
    con.execute(
        "INSERT INTO trades (tick, utc, agent_id, side, lots, price_u, fee_u, spread_u)"
        " VALUES (?, ?, ?, 'BUY', ?, ?, ?, ?)",
        (tick, utc, agent.id, lots, fill, fee, lots * (fill - price)),
    )
    agent.hold = 0
    agent.ever_traded = True
    agent.fills.append(utc)
    agent.dirty = True


def sell(con, tick, utc, agent, lots, price, venue, arena_account):
    """Market SELL at the venue's fill price (spread charged, rounded against
    the agent); taker fee on notional. 0-amount transfers are skipped (#27)."""
    fill = sell_price_u(price, venue)
    proceeds = lots * fill
    fee = fee_u(proceeds, venue)
    if proceeds > 0:
        ledger.transfer(con, tick, arena_account, account_id(agent.id), proceeds, "sell")
    if fee > 0:
        ledger.transfer(con, tick, account_id(agent.id), arena_account, fee, "fee")
    _set_lots(con, agent, agent.lots - lots)
    con.execute(
        "INSERT INTO trades (tick, utc, agent_id, side, lots, price_u, fee_u, spread_u)"
        " VALUES (?, ?, ?, 'SELL', ?, ?, ?, ?)",
        (tick, utc, agent.id, lots, fill, fee, lots * (price - fill)),
    )
    agent.ever_traded = True
    agent.fills.append(utc)
    agent.dirty = True


def sell_all(con, tick, utc, agent, price, venue, arena_account):
    if agent.lots > 0:
        sell(con, tick, utc, agent, agent.lots, price, venue, arena_account)


def die(con, tick, utc, agent, cause, price, venue, arena_account):
    """Death is a full liquidation (spec 3.9): sell everything, sweep the
    residue to the treasury, archive the fossil. Returns the final equity."""
    sell_all(con, tick, utc, agent, price, venue, arena_account)
    residue = cash(con, agent)
    if residue > 0:
        ledger.transfer(
            con, tick, account_id(agent.id), TREASURY, residue, f"death_residue:{cause}"
        )
    con.execute(
        "UPDATE agents SET died_tick = ?, death_cause = ?, debt_u = ? WHERE id = ?",
        (tick, cause, agent.debt, agent.id),
    )
    con.execute(
        "INSERT OR REPLACE INTO snapshots (tick, agent_id, cash_u, equity_u)"
        " VALUES (?, ?, ?, ?)",
        (tick, agent.id, residue, residue),
    )
    save_state(con, agent, final_equity=residue)
    return residue
