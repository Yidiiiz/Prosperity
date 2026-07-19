"""Spec v2 section 7: the three universal gate genes (vol gate, trade-rate
throttle, active-hours mask), gate semantics in decide(), fill-window
persistence, and the immigration token bucket."""

import json
import random

from colony import agents, db, evolution, orchestrator, strategies
from colony.config import immigration_accrual, immigration_capacity
from tests.conftest import make_cfg, make_colony

ALL_HOURS = (1 << 24) - 1


def momentum_genome(**params):
    base = {
        "lookback": 5, "entry_z": 0.2, "exit_z": -2.0, "risk_fraction": 0.5,
        "hold_max": 20, "vol_gate_bps": 0, "max_trades_per_day": 500,
        "active_hours_mask": ALL_HOURS,
    }
    base.update(params)
    return {"archetype": "momentum", "params": base,
            "econ": {"child_seed_fraction": 0.4}, "genes": []}


RISING = [1_000_000 + 1_000 * i * i for i in range(30)]  # accelerating: z > 0


# ------------------------------------------------------------------ evolution

def test_new_genes_drawn_within_bounds():
    rng = random.Random(7)
    for _ in range(200):
        params = evolution.random_genome(rng)["params"]
        assert 0 <= params["vol_gate_bps"] <= 100
        assert 1 <= params["max_trades_per_day"] <= 500
        assert 1 <= params["active_hours_mask"] <= ALL_HOURS  # >= 1 bit set


def test_mask_mutation_flips_exactly_one_bit():
    rng = random.Random(11)
    mut_cfg = {"gene_flip_prob": 0.0, "archetype_hop_prob": 0.0}
    genome = momentum_genome()
    for _ in range(50):
        child = evolution.mutate(genome, 0.1, mut_cfg, rng)
        xor = child["params"]["active_hours_mask"] ^ genome["params"]["active_hours_mask"]
        assert bin(xor).count("1") == 1
        genome = child


def test_zero_mask_repairs_to_all_hours():
    genome = momentum_genome(active_hours_mask=0)
    assert evolution.repair(genome)["params"]["active_hours_mask"] == ALL_HOURS


def test_old_genome_gains_new_genes_on_mutation():
    rng = random.Random(3)
    old = momentum_genome()
    for key in ("vol_gate_bps", "max_trades_per_day", "active_hours_mask"):
        del old["params"][key]
    child = evolution.mutate(old, 0.1, {"gene_flip_prob": 0, "archetype_hop_prob": 0}, rng)
    params = child["params"]
    assert 0 <= params["vol_gate_bps"] <= 100
    assert 1 <= params["max_trades_per_day"] <= 500
    assert 1 <= params["active_hours_mask"] <= ALL_HOURS


# --------------------------------------------------------------------- gates

def test_hours_gate_blocks_opens_but_never_closes():
    genome = momentum_genome(active_hours_mask=1)  # hour 0 UTC only
    open_at_0 = strategies.decide(genome, RISING, 0, 0, 10_000_000_000, 0, utc_hour=0)
    assert open_at_0 is not None and open_at_0.side == "BUY"
    assert strategies.decide(genome, RISING, 0, 0, 10_000_000_000, 0, utc_hour=5) is None
    # closing is always allowed: hold_max exceeded sells in a blocked hour
    close = strategies.decide(genome, RISING, 3, 25, 10_000_000_000, 0, utc_hour=5)
    assert close is not None and close.side == "SELL"


def test_vol_gate_blocks_flat_tape():
    flat_rise = [1_000_000 + i for i in range(30)]  # +1 u/bar: z>0, CoV ~0 bps
    gated = momentum_genome(vol_gate_bps=100)
    assert strategies.decide(gated, flat_rise, 0, 0, 10_000_000_000, 0) is None
    always_on = momentum_genome(vol_gate_bps=0)
    assert strategies.decide(always_on, flat_rise, 0, 0, 10_000_000_000, 0) is not None


def test_trade_throttle_blocks_opens_at_the_cap():
    genome = momentum_genome(max_trades_per_day=3)
    assert strategies.decide(genome, RISING, 0, 0, 10_000_000_000, 0, trades_24h=2) is not None
    assert strategies.decide(genome, RISING, 0, 0, 10_000_000_000, 0, trades_24h=3) is None
    # the throttle never blocks a close either
    close = strategies.decide(genome, RISING, 3, 25, 10_000_000_000, 0, trades_24h=3)
    assert close is not None and close.side == "SELL"


def test_neutral_genes_are_a_no_op_at_every_hour_and_count():
    """The Petri regression bar (spec v2 build order 8): gates at neutral
    defaults change nothing about a v1 decision."""
    genome = momentum_genome()  # neutral: gate 0, 500/day, all hours
    baseline = strategies.decide(genome, RISING, 0, 0, 10_000_000_000, 0)
    assert baseline is not None
    for hour in range(24):
        for trades in (0, 250, 499):
            d = strategies.decide(genome, RISING, 0, 0, 10_000_000_000, 0, hour, trades)
            assert d == baseline


# --------------------------------------------------- fills window persistence

def test_fills_persist_and_roll(tmp_path):
    # hour bars so the rolling 24h window spans 24 ticks (at day bars it
    # correctly holds only the current bar's fills)
    cfg = make_cfg(gen0_population=12, max_population=16, population_floor=3,
                   arena={"tick_seconds": 3_600})
    con, orch = make_colony(tmp_path, cfg)
    orch.run(400)
    assert con.execute("SELECT COUNT(*) FROM trades").fetchone()[0] > 0
    utc_now = orch.arena.utc()
    reloaded = agents.load_living(con)
    for aid, agent in orch.agents.items():
        # in-memory fills are EXACTLY this agent's trade utcs inside (now-24h, now]
        want = [u for (u,) in con.execute(
            "SELECT utc FROM trades WHERE agent_id = ? AND utc > ? ORDER BY tick",
            (aid, utc_now - 86_400))]
        assert agent.fills == want
        # persisted fills may carry stale entries (pruning doesn't dirty the
        # row); they are a superset that re-prunes deterministically on load
        pruned = [u for u in reloaded[aid].fills if u > utc_now - 86_400]
        assert pruned == agent.fills
    con.close()


# --------------------------------------------------------- immigration budget

def test_budget_capacity_and_accrual_convert_like_rent():
    cfg = make_cfg()  # default 2,000 bps on a $20,000 treasury, day ticks
    assert cfg["immigration_budget_apr_bps"] == 2_000
    assert immigration_capacity(cfg) == 4_000_000_000  # 20% of treasury
    assert immigration_accrual(cfg) == 20_000_000_000 * 2_000 * 86_400 // (10_000 * 31_536_000)


def test_immigration_stops_when_the_budget_is_exhausted(tmp_path):
    # everyone dies bankrupt each tick; immigrants burn the budget, then the
    # population honestly sits below the floor (spec v2 7.3)
    cfg = make_cfg(
        gen0_population=4, population_floor=4, max_population=8,
        death_floor_u=999_999_999, reserve_floor_u=999_999_999,
    )
    con, orch = make_colony(tmp_path, cfg)
    assert orch.imm_tokens == immigration_capacity(cfg)  # starts full
    orch.run(6)
    assert orch.imm_tokens < cfg["gen0_seed_u"]  # exhausted
    assert len(orch.agents) < cfg["population_floor"]  # the honest signal
    immigrants = con.execute(
        "SELECT COUNT(*) FROM agents WHERE born_tick > 0 AND parent_a IS NULL"
    ).fetchone()[0]
    assert immigrants == 4  # exactly one year's budget of seeds
    # the bucket survives a resume
    orch2 = orchestrator.Orchestrator(con)
    assert orch2.imm_tokens == orch.imm_tokens
    con.close()


def test_budget_never_exceeds_capacity(tmp_path):
    cfg = make_cfg(gen0_population=6, population_floor=2)
    con, orch = make_colony(tmp_path, cfg)
    orch.run(10)  # no deaths: accrual must cap at capacity, not grow
    assert orch.imm_tokens == immigration_capacity(cfg)
    con.close()
