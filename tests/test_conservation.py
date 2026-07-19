import random

from colony import agents, db, ledger
from tests.conftest import make_cfg, make_colony


def test_conservation_over_2000_tick_run(tmp_path):
    cfg = make_cfg(
        debug=False,
        arena={
            "regimes": [
                {"kind": "trend_up", "ticks": 700, "drift_bps": 12, "vol_bps": 60},
                {"kind": "mean_revert", "ticks": 700, "kappa": 0.15, "vol_bps": 200},
                {"kind": "crash", "ticks": 100, "drift_bps": -80, "vol_bps": 200},
            ]
        },
    )
    con, orch = make_colony(tmp_path, cfg)
    orch.run(2000)  # verifies every 100 ticks and at the end
    ledger.verify_invariants(con, cfg["initial_treasury_u"])
    total = con.execute("SELECT SUM(balance_u) FROM balances").fetchone()[0]
    assert total == cfg["initial_treasury_u"]
    # the colony saw real churn, not a vacuous pass
    assert orch.deaths_cum > 0
    assert con.execute("SELECT COUNT(*) FROM trades").fetchone()[0] > 0


def test_property_1000_random_valid_operations(tmp_path):
    """Random trades, rent, births and deaths preserve conservation exactly."""
    cfg = make_cfg()
    con, orch = make_colony(tmp_path, cfg)
    rng = random.Random(99)
    price = 200
    tick = 0
    with db.tx(con):
        for _ in range(1000):
            tick += 1
            living = sorted(orch.agents)
            if not living:
                break
            aid = rng.choice(living)
            agent = orch.agents[aid]
            op = rng.random()
            cash = agents.cash(con, agent)
            if op < 0.35:  # buy
                lots = min(cash // (2 * price), rng.randint(1, 50))
                if lots > 0:
                    agents.buy(con, tick, 0, agent, lots, price, cfg["fee_bps"], "ARENA:petri")
            elif op < 0.70:  # sell
                if agent.lots > 0:
                    agents.sell(con, tick, 0, agent, rng.randint(1, agent.lots), price,
                                cfg["fee_bps"], "ARENA:petri")
            elif op < 0.90:  # rent
                rent = max(cfg["rent_min_u"], cash * 2 // 10_000)
                if cash >= rent:
                    ledger.transfer(con, tick, f"AGENT:{aid}", "TREASURY", rent, "rent")
            elif op < 0.97:  # death
                orch._die(tick, 0, agent, "bankrupt", price)
            else:  # house birth (immigrant-style spawn)
                if ledger.balance(con, "TREASURY") >= cfg["gen0_seed_u"]:
                    orch._birth(
                        tick, agent.genome, 0, (None, None),
                        [("TREASURY", cfg["gen0_seed_u"], "immigrant_seed")],
                        debt=15_000,
                    )
    ledger.verify_invariants(con, cfg["initial_treasury_u"])
    total = con.execute("SELECT SUM(balance_u) FROM balances").fetchone()[0]
    assert total == cfg["initial_treasury_u"]
