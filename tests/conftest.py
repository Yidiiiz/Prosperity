import copy
import json

import pytest

from colony import db, orchestrator
from colony.config import validate

BASE_CFG = {
    "rng_seed": 42,
    "debug": True,
    "initial_treasury_cents": 2_000_000,
    "gen0_population": 12,
    "gen0_seed_cents": 100_000,
    "max_population": 40,
    "population_floor": 4,
    "death_floor_cents": 10_000,
    "rent_min_cents": 10,
    "rent_bps_of_equity": 2,
    "fee_bps": 20,
    "repro_multiple": 1.25,
    "repay_multiple": 0.15,
    "reserve_floor_cents": 15_000,
    "breed_cooldown_ticks": 50,
    "solo_breed_patience": 10,
    "max_age_ticks": 3000,
    "stagnation_ticks": 120,
    "max_action_fraction": 0.80,
    "min_ticks_for_fitness": 75,
    "snapshot_every": 25,
    "hall_size": 100,
    "hall_immigrant_prob": 0.4,
    "mutation": {
        "sigma_fraction": 0.10,
        "gene_flip_prob": 0.05,
        "archetype_hop_prob": 0.01,
        "adaptive": {
            "window_generations": 5,
            "stagnant_multiplier": 1.5,
            "improving_multiplier": 0.8,
            "sigma_min": 0.02,
            "sigma_max": 0.30,
        },
    },
    "elitism_top_k": 3,
    "arena": {
        "name": "petri",
        "start_price_cents": 200,
        "price_floor_cents": 20,
        "regimes": [{"kind": "trend_up", "ticks": 3000, "drift_bps": 12, "vol_bps": 60}],
    },
}


def make_cfg(**overrides):
    cfg = copy.deepcopy(BASE_CFG)
    arena = overrides.pop("arena", None)
    if arena:
        cfg["arena"].update(arena)
    cfg.update(overrides)
    validate(cfg)
    return cfg


def make_colony(tmp_path, cfg, name="colony.db"):
    con = db.connect(tmp_path / name)
    orch = orchestrator.init_colony(con, cfg)
    return con, orch


@pytest.fixture
def base_cfg():
    return make_cfg()
