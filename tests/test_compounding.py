"""spec v3 5.4 / 10.4: the compounding ratchet — surplus above high-water
redeploys into the immigration bucket, capped, one-way, conservation exact,
state survives resume."""

from colony import db, ledger, orchestrator
from tests.conftest import make_cfg, make_colony


def inject_extraction(con, orch, amount_u):
    """Stand-in for market profit: the arena account may go negative, so a
    sell-side inflow to the treasury models audited extraction exactly."""
    with db.tx(con):
        ledger.transfer(con, orch.tick, orch.arena_account, "TREASURY",
                        amount_u, "sell")


def test_surplus_redeploys_and_ratchet_turns_one_way(tmp_path):
    cfg = make_cfg()
    con, orch = make_colony(tmp_path, cfg)
    base = orch.imm_capacity
    deployed = cfg["gen0_population"] * cfg["gen0_seed_u"]
    assert orch.high_water == cfg["initial_treasury_u"]

    # below high-water (gen-0 seeds are deployed): nothing redeploys
    tokens = orch.imm_tokens
    orch._reinvest()
    assert orch.imm_tokens == tokens and orch.high_water == cfg["initial_treasury_u"]

    # extraction lifts treasury above initial: half the headroom redeploys
    surplus = 1_000_000_000
    inject_extraction(con, orch, deployed + surplus)
    orch._reinvest()
    assert orch.imm_tokens == tokens + surplus * 5_000 // 10_000
    assert orch.high_water == cfg["initial_treasury_u"] + surplus

    # same treasury again: the ratchet already turned, nothing more moves
    tokens = orch.imm_tokens
    orch._reinvest()
    assert orch.imm_tokens == tokens

    # a drawdown redeploys nothing and the mark stays up
    with db.tx(con):
        ledger.transfer(con, orch.tick, "TREASURY", orch.arena_account,
                        surplus, "buy")
    orch._reinvest()
    assert orch.imm_tokens == tokens
    assert orch.high_water == cfg["initial_treasury_u"] + surplus

    # conservation is exact throughout (tokens are budget, not money)
    ledger.verify_invariants(con, cfg["initial_treasury_u"])


def test_bucket_cap_holds_at_4x_base_capacity(tmp_path):
    cfg = make_cfg()
    con, orch = make_colony(tmp_path, cfg)
    inject_extraction(con, orch,
                      cfg["gen0_population"] * cfg["gen0_seed_u"]
                      + 100 * orch.imm_capacity)
    orch._reinvest()
    assert orch.imm_tokens == 4 * orch.imm_capacity


def test_accrual_never_clamps_reinvested_tokens(tmp_path):
    cfg = make_cfg()
    con, orch = make_colony(tmp_path, cfg)
    orch.imm_tokens = 3 * orch.imm_capacity  # as if reinvested
    orch.step()
    assert orch.imm_tokens >= 2 * orch.imm_capacity  # not clamped to base
    # and normal accrual still tops up a drained bucket
    orch.imm_tokens = 0
    orch.step()
    assert 0 < orch.imm_tokens <= orch.imm_capacity


def test_day_boundary_triggers_redeploy_in_the_loop(tmp_path):
    cfg = make_cfg()  # Petri day bars: every tick is a UTC-day boundary
    con, orch = make_colony(tmp_path, cfg)
    inject_extraction(con, orch,
                      cfg["gen0_population"] * cfg["gen0_seed_u"] + 500_000_000)
    tokens = orch.imm_tokens
    orch.step()
    assert orch.imm_tokens > tokens  # the boundary fired inside the loop
    assert orch.high_water > cfg["initial_treasury_u"]
    ledger.verify_invariants(con, cfg["initial_treasury_u"])


def test_ratchet_state_survives_resume_byte_identically(tmp_path):
    cfg = make_cfg()
    con, orch = make_colony(tmp_path, cfg)
    inject_extraction(con, orch,
                      cfg["gen0_population"] * cfg["gen0_seed_u"] + 750_000_000)
    orch.run(5)
    tokens, mark = orch.imm_tokens, orch.high_water
    assert mark > cfg["initial_treasury_u"]
    con.close()
    con = db.connect(tmp_path / "colony.db")
    resumed = orchestrator.Orchestrator(con)
    assert resumed.imm_tokens == tokens
    assert resumed.high_water == mark
    con.close()
