"""Agent lifecycle: spawn, positions, equity, trade execution, death.

All money movements go through ledger.transfer. The in-memory AgentState
mirrors the agent_state table, which is flushed every tick by the
orchestrator so runs resume exactly.
"""

import json
from dataclasses import dataclass

from . import ledger
from .risk import fee_cents

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
        "INSERT INTO agents (id, genome_json, generation, parent_a, parent_b, born_tick, debt_cents)"
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
        "INSERT OR REPLACE INTO agent_state (agent_id, birth_seed_cents, baseline_cents,"
        " peak_equity_cents, first_snap_equity_cents, hold_ticks, ever_traded,"
        " last_birth_tick, queue_since, final_equity_cents) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            agent.id, agent.birth_seed, agent.baseline, agent.peak_equity,
            agent.first_snap_equity, agent.hold, int(agent.ever_traded),
            agent.last_birth_tick, agent.queue_since, final_equity,
        ),
    )
    agent.dirty = False


def load_living(con):
    """Rebuild in-memory state for all living agents from the database."""
    rows = con.execute(
        """
        SELECT a.id, a.genome_json, a.generation, a.born_tick, a.debt_cents,
               s.birth_seed_cents, s.baseline_cents, s.peak_equity_cents,
               s.first_snap_equity_cents, s.hold_ticks, s.ever_traded,
               s.last_birth_tick, s.queue_since, COALESCE(p.lots, 0) AS lots
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
            birth_seed=row["birth_seed_cents"],
            baseline=row["baseline_cents"],
            debt=row["debt_cents"],
            lots=row["lots"],
            hold=row["hold_ticks"],
            ever_traded=bool(row["ever_traded"]),
            peak_equity=row["peak_equity_cents"],
            first_snap_equity=row["first_snap_equity_cents"],
            last_birth_tick=row["last_birth_tick"],
            queue_since=row["queue_since"],
            dirty=False,
        )
    return living


def _set_lots(con, agent, lots):
    agent.lots = lots
    con.execute(
        "UPDATE positions SET lots = ? WHERE agent_id = ? AND asset = ?",
        (lots, agent.id, ASSET),
    )


def buy(con, tick, agent, lots, price, fee_bps, arena_account):
    cost = lots * price
    fee = fee_cents(cost, fee_bps)
    ledger.transfer(con, tick, account_id(agent.id), arena_account, cost, "buy")
    ledger.transfer(con, tick, account_id(agent.id), arena_account, fee, "fee")
    _set_lots(con, agent, agent.lots + lots)
    con.execute(
        "INSERT INTO trades (tick, agent_id, side, lots, price_cents, fee_cents)"
        " VALUES (?, ?, 'BUY', ?, ?, ?)",
        (tick, agent.id, lots, price, fee),
    )
    agent.hold = 0
    agent.ever_traded = True
    agent.dirty = True


def sell(con, tick, agent, lots, price, fee_bps, arena_account):
    proceeds = lots * price
    fee = fee_cents(proceeds, fee_bps)
    ledger.transfer(con, tick, arena_account, account_id(agent.id), proceeds, "sell")
    ledger.transfer(con, tick, account_id(agent.id), arena_account, fee, "fee")
    _set_lots(con, agent, agent.lots - lots)
    con.execute(
        "INSERT INTO trades (tick, agent_id, side, lots, price_cents, fee_cents)"
        " VALUES (?, ?, 'SELL', ?, ?, ?)",
        (tick, agent.id, lots, price, fee),
    )
    agent.ever_traded = True
    agent.dirty = True


def sell_all(con, tick, agent, price, fee_bps, arena_account):
    if agent.lots > 0:
        sell(con, tick, agent, agent.lots, price, fee_bps, arena_account)


def die(con, tick, agent, cause, price, fee_bps, arena_account):
    """Death is a full liquidation (spec 3.9): sell everything, sweep the
    residue to the treasury, archive the fossil. Returns the final equity."""
    sell_all(con, tick, agent, price, fee_bps, arena_account)
    residue = cash(con, agent)
    if residue > 0:
        ledger.transfer(
            con, tick, account_id(agent.id), TREASURY, residue, f"death_residue:{cause}"
        )
    con.execute(
        "UPDATE agents SET died_tick = ?, death_cause = ?, debt_cents = ? WHERE id = ?",
        (tick, cause, agent.debt, agent.id),
    )
    con.execute(
        "INSERT OR REPLACE INTO snapshots (tick, agent_id, cash_cents, equity_cents)"
        " VALUES (?, ?, ?, ?)",
        (tick, agent.id, residue, residue),
    )
    save_state(con, agent, final_equity=residue)
    return residue
