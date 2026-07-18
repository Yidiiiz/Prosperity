import hashlib
import random

from colony import db, orchestrator
from colony.arenas.petri import Petri
from tests.conftest import make_cfg, make_colony


def ledger_hash(con):
    h = hashlib.sha256()
    for row in con.execute(
        "SELECT tick, debit_account, credit_account, amount_cents, memo FROM ledger ORDER BY seq"
    ):
        h.update(repr(tuple(row)).encode())
    return h.hexdigest()


def test_identical_config_and_seed_identical_ledger(tmp_path):
    cfg = make_cfg()
    con_a, orch_a = make_colony(tmp_path, cfg, "a.db")
    con_b, orch_b = make_colony(tmp_path, cfg, "b.db")
    orch_a.run(150)
    orch_b.run(150)
    assert ledger_hash(con_a) == ledger_hash(con_b)


def test_different_seed_different_ledger(tmp_path):
    con_a, orch_a = make_colony(tmp_path, make_cfg(rng_seed=42), "a.db")
    con_b, orch_b = make_colony(tmp_path, make_cfg(rng_seed=43), "b.db")
    orch_a.run(150)
    orch_b.run(150)
    assert ledger_hash(con_a) != ledger_hash(con_b)


def test_resume_matches_uninterrupted_run(tmp_path):
    cfg = make_cfg()
    con_a, orch_a = make_colony(tmp_path, cfg, "a.db")
    orch_a.run(100)
    con_b, orch_b = make_colony(tmp_path, cfg, "b.db")
    orch_b.run(50)
    con_b.close()
    con_b = db.connect(tmp_path / "b.db")
    orch_b2 = orchestrator.Orchestrator(con_b)  # fresh process, state from db
    assert orch_b2.tick == 50
    orch_b2.run(50)
    assert ledger_hash(con_a) == ledger_hash(con_b)


def test_petri_deterministic_given_seed():
    arena_cfg = {
        "name": "petri", "start_price_cents": 200, "price_floor_cents": 20,
        "regimes": [
            {"kind": "trend_up", "ticks": 50, "drift_bps": 12, "vol_bps": 60},
            {"kind": "mean_revert", "ticks": 50, "kappa": 0.15, "vol_bps": 200},
        ],
    }
    paths = []
    for _ in range(2):
        arena = Petri(arena_cfg)
        rng = random.Random(7)
        for _ in range(200):
            arena.step(rng)
        paths.append(arena.history(201))
    assert paths[0] == paths[1]


def test_petri_price_floor():
    arena = Petri({
        "name": "petri", "start_price_cents": 25, "price_floor_cents": 20,
        "regimes": [{"kind": "crash", "ticks": 10_000, "drift_bps": -300, "vol_bps": 200}],
    })
    rng = random.Random(1)
    for _ in range(500):
        arena.step(rng)
        assert arena.price() >= 20


def test_petri_regime_boundaries_and_looping():
    arena = Petri({
        "name": "petri", "start_price_cents": 200, "price_floor_cents": 20,
        "regimes": [
            {"kind": "trend_up", "ticks": 10, "drift_bps": 12, "vol_bps": 0},
            {"kind": "mean_revert", "ticks": 5, "kappa": 0.15, "vol_bps": 0},
        ],
    })
    rng = random.Random(1)
    kinds = []
    for _ in range(30):
        kinds.append(arena.regime_kind())
        arena.step(rng)
    assert kinds == (["trend_up"] * 10 + ["mean_revert"] * 5) * 2
