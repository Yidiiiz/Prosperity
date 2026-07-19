"""Venue model and fill delay (spec v2 2.2 / 2.3): spread charged even on
paper and rounded against the agent; taker fees with a configurable floor;
decisions at row N execute at row N+1's price via one pending order per
agent, re-risk-checked at fill time and durable across restarts."""

import pytest

from colony import agents, db, ledger, orchestrator, strategies
from colony.config import ConfigError
from colony.risk import buy_price_u, fee_u, sell_price_u
from tests.conftest import make_cfg, make_colony
from tests.test_determinism import ledger_hash

SPREAD_VENUE = {"taker_bps": 0, "maker_bps": 0, "spread_bps": 2, "min_fee_u": 0}


def test_spread_fill_prices_round_against_the_agent():
    assert buy_price_u(1_000_000, SPREAD_VENUE) == 1_000_100
    assert sell_price_u(1_000_000, SPREAD_VENUE) == 999_900
    # fractional results round UP for buys, DOWN for sells
    assert buy_price_u(3, SPREAD_VENUE) == 4
    assert sell_price_u(3, SPREAD_VENUE) == 2
    flat = dict(SPREAD_VENUE, spread_bps=0)
    assert buy_price_u(7, flat) == sell_price_u(7, flat) == 7


def test_taker_fee_and_min_fee_floor():
    venue = {"taker_bps": 10, "maker_bps": 0, "spread_bps": 0, "min_fee_u": 0}
    assert fee_u(1_000_000, venue) == 1_000
    assert fee_u(100, venue) == 0  # no integer-floor predator by default
    floored = dict(venue, min_fee_u=50)
    assert fee_u(100, floored) == 50  # a real venue's minimum, modeled honestly


def test_spread_cost_flows_to_the_arena(tmp_path):
    cfg = make_cfg(venue={"spread_bps": 2, "taker_bps": 0})
    con, orch = make_colony(tmp_path, cfg)
    aid = sorted(orch.agents)[0]
    agent = orch.agents[aid]
    with db.tx(con):
        agents.buy(con, 1, 0, agent, 10, 2_000_000, cfg["venue"], "ARENA:petri")
    fill = buy_price_u(2_000_000, cfg["venue"])
    assert fill == 2_000_200
    row = con.execute("SELECT price_u, spread_u, fee_u FROM trades").fetchone()
    assert row["price_u"] == fill
    assert row["spread_u"] == 10 * 200
    assert ledger.balance(con, "ARENA:petri") == 10 * fill
    with db.tx(con):
        agents.sell(con, 2, 0, agent, 10, 2_000_000, cfg["venue"], "ARENA:petri")
    # a flat round-trip loses exactly the full spread
    assert agents.cash(con, agent) == 1_000_000_000 - 10 * (fill - sell_price_u(2_000_000, cfg["venue"]))
    ledger.verify_invariants(con, cfg["initial_treasury_u"])


def test_zero_fee_posts_no_ledger_row(tmp_path):
    cfg = make_cfg(venue={"taker_bps": 10})
    con, orch = make_colony(tmp_path, cfg)
    agent = orch.agents[sorted(orch.agents)[0]]
    with db.tx(con):
        agents.buy(con, 1, 0, agent, 1, 100, cfg["venue"], "ARENA:petri")  # fee rounds to 0
    assert con.execute("SELECT COUNT(*) FROM ledger WHERE memo = 'fee'").fetchone()[0] == 0
    assert con.execute("SELECT fee_u FROM trades").fetchone()[0] == 0
    ledger.verify_invariants(con, cfg["initial_treasury_u"])


def force_buy_decide(lots):
    def fake(genome, history, held, hold, equity, fee_bps, utc_hour=0, trades_24h=0):
        return strategies.Decision("BUY", lots) if held == 0 else None
    return fake


def test_fill_delay_defers_execution_to_the_next_bar(tmp_path, monkeypatch):
    cfg = make_cfg(venue={"fill_delay_ticks": 1})
    con, orch = make_colony(tmp_path, cfg)
    monkeypatch.setattr(strategies, "decide", force_buy_decide(3))
    orch.step()
    # tick 1: every decision became a pending order, nothing filled
    assert con.execute("SELECT COUNT(*) FROM trades").fetchone()[0] == 0
    assert all(a.pending_side == "BUY" for a in orch.agents.values())
    orch.step()
    # tick 2: pending orders filled FIRST, at tick 2's price
    fill = buy_price_u(orch.arena.price(), cfg["venue"])
    rows = con.execute("SELECT DISTINCT tick, price_u FROM trades").fetchall()
    assert rows and all(r["tick"] == 2 and r["price_u"] == fill for r in rows)


def test_pending_order_cancelled_when_no_longer_affordable(tmp_path, monkeypatch):
    cfg = make_cfg(venue={"fill_delay_ticks": 1})
    con, orch = make_colony(tmp_path, cfg)
    monkeypatch.setattr(strategies, "decide", force_buy_decide(400))
    orch.step()
    aid = sorted(orch.agents)[0]
    agent = orch.agents[aid]
    assert agent.pending_side == "BUY"
    with db.tx(con):  # drain the agent below one lot before the fill bar
        ledger.transfer(con, 1, f"AGENT:{aid}", "ARENA:petri", agents.cash(con, agent) - 1000,
                        "fee")
    orch.step()
    assert con.execute(
        "SELECT COUNT(*) FROM trades WHERE agent_id = ?", (aid,)
    ).fetchone()[0] == 0
    assert agent.pending_side is None  # cancelled, not retried
    ledger.verify_invariants(con, cfg["initial_treasury_u"])


def test_pending_order_shrinks_to_current_caps(tmp_path, monkeypatch):
    cfg = make_cfg(venue={"fill_delay_ticks": 1})
    con, orch = make_colony(tmp_path, cfg)
    monkeypatch.setattr(strategies, "decide", force_buy_decide(390))
    orch.step()
    aid = sorted(orch.agents)[0]
    agent = orch.agents[aid]
    with db.tx(con):  # halve the cash: 390 lots no longer fit the caps
        ledger.transfer(con, 1, f"AGENT:{aid}", "ARENA:petri", 500_000_000, "fee")
    orch.step()
    filled = con.execute(
        "SELECT lots FROM trades WHERE agent_id = ?", (aid,)
    ).fetchone()
    assert filled is not None and 0 < filled[0] < 390  # shrunk, not cancelled
    ledger.verify_invariants(con, cfg["initial_treasury_u"])


def test_pending_orders_survive_restart_byte_identically(tmp_path):
    cfg = make_cfg(venue={"fill_delay_ticks": 1})
    con_a, orch_a = make_colony(tmp_path, cfg, "a.db")
    orch_a.run(120)
    con_b, orch_b = make_colony(tmp_path, cfg, "b.db")
    orch_b.run(61)  # stop with pending orders likely in flight
    con_b.close()
    con_b = db.connect(tmp_path / "b.db")
    resumed = orchestrator.Orchestrator(con_b)
    resumed.run(59)
    assert ledger_hash(con_a) == ledger_hash(con_b)


def test_fill_delay_zero_only_for_petri(tmp_path):
    from tests.test_replay import trend_closes, write_csv
    path = write_csv(tmp_path / "p.csv", trend_closes(10))
    with pytest.raises(ConfigError):
        make_cfg(arena={"kind": "replay", "name": "x", "csv": path, "tick_seconds": 86_400},
                 venue={"fill_delay_ticks": 0})
    make_cfg(venue={"fill_delay_ticks": 0})  # petri: allowed
    with pytest.raises(ConfigError):
        make_cfg(venue={"fill_delay_ticks": 2})


def test_venue_keys_validated():
    for key in ("taker_bps", "maker_bps", "spread_bps", "min_fee_u"):
        with pytest.raises(ConfigError):
            make_cfg(venue={key: -1})
        with pytest.raises(ConfigError):
            make_cfg(venue={key: 1.5})
