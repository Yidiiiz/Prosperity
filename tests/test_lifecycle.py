import json

import pytest

from colony import agents, db, ledger
from tests.conftest import make_cfg, make_colony

CRASH_ARENA = {"regimes": [{"kind": "crash", "ticks": 5000, "drift_bps": -80, "vol_bps": 200}]}
BULL_ARENA = {"regimes": [{"kind": "trend_up", "ticks": 5000, "drift_bps": 40, "vol_bps": 60}]}


def death_causes(con):
    return dict(
        con.execute(
            "SELECT death_cause, COUNT(*) FROM agents WHERE died_tick IS NOT NULL GROUP BY 1"
        ).fetchall()
    )


def test_sitters_die_of_stagnation_and_never_survive_past_grace(tmp_path):
    cfg = make_cfg()
    con, orch = make_colony(tmp_path, cfg)
    orch.run(cfg["stagnation_ticks"] + 50)
    rows = con.execute(
        "SELECT genome_json, born_tick, died_tick, death_cause FROM agents"
    ).fetchall()
    sitters = [r for r in rows if json.loads(r["genome_json"])["archetype"] == "sitter"]
    assert sitters
    for row in sitters:
        assert row["died_tick"] is not None
        assert row["death_cause"] == "stagnation"
        assert row["died_tick"] - row["born_tick"] <= cfg["stagnation_ticks"] + 1


def test_bankruptcy_is_full_liquidation_with_residue(tmp_path):
    cfg = make_cfg()
    con, orch = make_colony(tmp_path, cfg)
    aid = sorted(orch.agents)[0]
    agent = orch.agents[aid]
    with db.tx(con):
        # commit nearly everything at $2.00, then mark to market at $0.18:
        # equity = 8_020_000 + 495*180_000 = 97_120_000 u <= death_floor -> bankrupt
        agents.buy(con, 1, 0, agent, 495, 2_000_000, cfg["fee_bps"], "ARENA:petri")
        orch._death_phase(1, 0, 180_000)
    row = con.execute("SELECT died_tick, death_cause FROM agents WHERE id = ?", (aid,)).fetchone()
    assert row["death_cause"] == "bankrupt" and row["died_tick"] == 1
    # full liquidation: no lots left, estate swept to treasury, fee charged on the sale
    assert con.execute("SELECT lots FROM positions WHERE agent_id = ?", (aid,)).fetchone()[0] == 0
    assert ledger.balance(con, f"AGENT:{aid}") == 0
    residue = con.execute(
        "SELECT amount_u FROM ledger WHERE debit_account = ? AND memo = 'death_residue:bankrupt'",
        (f"AGENT:{aid}",),
    ).fetchone()[0]
    assert residue == 8_020_000 + 495 * 180_000 - 178_200  # cash + proceeds - sale fee
    assert aid not in orch.agents
    ledger.verify_invariants(con, cfg["initial_treasury_u"])


def test_rent_shortfall_forces_full_liquidation(tmp_path):
    cfg = make_cfg()
    con, orch = make_colony(tmp_path, cfg)
    aid = sorted(orch.agents)[0]
    agent = orch.agents[aid]
    with db.tx(con):
        agents.buy(con, 1, 0, agent, 495, 2_000_000, cfg["fee_bps"], "ARENA:petri")
        # drain remaining cash below rent (in-simulation: pay it to the arena)
        ledger.transfer(con, 1, f"AGENT:{aid}", "ARENA:petri", 8_000_000, "fee")
    assert agents.cash(con, agent) == 20_000
    with db.tx(con):
        orch._live_phase(2, 0, 2_000_000)
    # position force-sold in one sale, rent paid, agent survived
    assert agent.lots == 0
    assert agents.cash(con, agent) > 0
    rent_rows = con.execute(
        "SELECT COUNT(*) FROM ledger WHERE debit_account = ? AND memo = 'rent'", (f"AGENT:{aid}",)
    ).fetchone()[0]
    assert rent_rows == 1
    ledger.verify_invariants(con, cfg["initial_treasury_u"])


def test_old_age_death(tmp_path):
    cfg = make_cfg(lifecycle={"max_age_days": 130, "breed_cooldown_days": 10})
    con, orch = make_colony(tmp_path, cfg)
    orch.run(140)
    rows = con.execute("SELECT born_tick, died_tick, death_cause FROM agents").fetchall()
    old = [r for r in rows if r["death_cause"] == "old_age"]
    assert old
    for row in old:
        assert row["died_tick"] - row["born_tick"] == 130


def test_breeding_produces_funded_children(tmp_path):
    cfg = make_cfg(arena=BULL_ARENA)
    con, orch = make_colony(tmp_path, cfg)
    orch.run(900)
    children = con.execute(
        "SELECT * FROM agents WHERE parent_a IS NOT NULL"
    ).fetchall()
    assert children, "a strong bull market must produce births"
    for child in children:
        assert child["debt_u"] == 0  # parent-funded children owe nothing
        seeds = con.execute(
            "SELECT amount_u FROM ledger WHERE credit_account = ? AND memo = 'child_seed'",
            (f"AGENT:{child['id']}",),
        ).fetchall()
        assert seeds and sum(s[0] for s in seeds) > 2 * cfg["death_floor_u"]
    # parents' baselines were reset below their prior cash (mitosis)
    a_parent = con.execute(
        "SELECT s.baseline_u, s.birth_seed_u FROM agent_state s"
        " JOIN agents a ON a.id = s.agent_id WHERE a.id = ?",
        (children[0]["parent_a"],),
    ).fetchone()
    assert a_parent["baseline_u"] > 0
    ledger.verify_invariants(con, cfg["initial_treasury_u"])


def test_atomic_birth_rolls_back_on_injected_crash(tmp_path, monkeypatch):
    cfg = make_cfg(arena=BULL_ARENA)
    con, orch = make_colony(tmp_path, cfg)
    orch.run(5)
    aid = sorted(orch.agents)[0]
    agent = orch.agents[aid]
    agent.baseline = 1_000  # force breeding eligibility
    agent.debt = 0
    agent.queue_since = orch.tick - 20  # solo patience long elapsed
    orch.queue = [aid]

    real_spawn = agents.spawn

    def exploding_spawn(*args, **kwargs):
        real_spawn(*args, **kwargs)  # seed transfers + rows land first...
        raise RuntimeError("injected crash mid-birth")

    monkeypatch.setattr("colony.agents.spawn", exploding_spawn)
    ledger_rows_before = con.execute("SELECT COUNT(*) FROM ledger").fetchone()[0]
    agents_before = con.execute("SELECT COUNT(*) FROM agents").fetchone()[0]
    with pytest.raises(RuntimeError):
        with db.tx(con):
            orch._breeding_phase(orch.tick + 1, orch.arena.price())
    # either both the seed transfer and the agent row, or neither: here, neither
    assert con.execute("SELECT COUNT(*) FROM ledger").fetchone()[0] == ledger_rows_before
    assert con.execute("SELECT COUNT(*) FROM agents").fetchone()[0] == agents_before
    assert con.execute(
        "SELECT COUNT(*) FROM ledger WHERE memo = 'child_seed'"
    ).fetchone()[0] == 0
    ledger.verify_invariants(con, cfg["initial_treasury_u"])


def test_quota_sweep_never_dips_below_baseline(tmp_path):
    cfg = make_cfg()
    con, orch = make_colony(tmp_path, cfg)
    aid = sorted(orch.agents)[0]
    agent = orch.agents[aid]
    assert agent.debt == int(cfg["repay_multiple"] * cfg["gen0_seed_u"])
    # give the agent a surplus above baseline, in-simulation money (from arena)
    with db.tx(con):
        ledger.transfer(con, 1, "ARENA:petri", f"AGENT:{aid}", 4_000, "sell")
        orch._quota_sweep(1)
    assert agents.cash(con, agent) == agent.baseline
    assert agent.debt == int(cfg["repay_multiple"] * cfg["gen0_seed_u"]) - 4_000
    # at baseline exactly: nothing more is swept
    with db.tx(con):
        orch._quota_sweep(2)
    assert agents.cash(con, agent) == agent.baseline
    ledger.verify_invariants(con, cfg["initial_treasury_u"])


def test_agent_with_debt_never_enters_breeding_queue(tmp_path):
    cfg = make_cfg()
    con, orch = make_colony(tmp_path, cfg)
    aid = sorted(orch.agents)[0]
    agent = orch.agents[aid]
    agent.baseline = 1_000  # cash (100k) far above repro threshold
    assert agent.debt > 0
    with db.tx(con):
        orch._breeding_phase(1, orch.arena.price())
    assert aid not in orch.queue
    agent.debt = 0
    with db.tx(con):
        orch._breeding_phase(2, orch.arena.price())
    assert aid in orch.queue


def test_only_house_funded_agents_carry_debt(tmp_path):
    cfg = make_cfg(arena=BULL_ARENA)
    con, orch = make_colony(tmp_path, cfg)
    orch.run(900)
    rows = con.execute("SELECT parent_a, debt_u FROM agents").fetchall()
    for row in rows:
        if row["parent_a"] is not None:
            assert row["debt_u"] == 0
    # and the treasury only ever receives via the three sanctioned channels
    memos = {
        r[0]
        for r in con.execute(
            "SELECT DISTINCT memo FROM ledger WHERE credit_account = 'TREASURY'"
        )
    }
    for memo in memos:
        assert memo in ("rent", "debt_repay") or memo.startswith("death_residue:")


def test_immigration_holds_population_floor(tmp_path):
    cfg = make_cfg(arena=CRASH_ARENA, population_floor=8)
    con, orch = make_colony(tmp_path, cfg)
    orch.run(600)
    # the crash killed most of gen-0, but immigration refilled to the floor
    assert len(orch.agents) >= cfg["population_floor"]
    immigrants = con.execute(
        "SELECT COUNT(*) FROM ledger WHERE memo = 'immigrant_seed'"
    ).fetchone()[0]
    assert immigrants > 0
    # immigrants are house-funded: they carry the seed-repayment quota
    debtors = con.execute(
        "SELECT COUNT(*) FROM agents WHERE born_tick > 0 AND parent_a IS NULL AND debt_u > 0"
    ).fetchone()[0]
    assert debtors > 0
    ledger.verify_invariants(con, cfg["initial_treasury_u"])


def test_population_never_exceeds_cap_plus_elites(tmp_path):
    cfg = make_cfg(arena=BULL_ARENA, max_population=20)
    con, orch = make_colony(tmp_path, cfg)
    peak = 0
    for _ in range(900):
        orch.step()
        peak = max(peak, len(orch.agents))
    assert peak <= cfg["max_population"] + cfg["elitism_top_k"]
