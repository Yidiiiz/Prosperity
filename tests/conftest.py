import copy
import json

import pytest

from colony import db, orchestrator
from colony.config import validate

BASE_CFG = {
    "rng_seed": 42,
    "debug": True,
    "initial_treasury_u": 20_000_000_000,
    "gen0_population": 12,
    "gen0_seed_u": 1_000_000_000,
    "max_population": 40,
    "population_floor": 4,
    "death_floor_u": 100_000_000,
    "rent_min_u": 100_000,
    "rent_apr_bps": 730,
    "venue": {"taker_bps": 20, "maker_bps": 0, "spread_bps": 0, "min_fee_u": 0,
              "fill_delay_ticks": 0},
    "repro_multiple": 1.25,
    "repay_multiple": 0.15,
    "reserve_floor_u": 150_000_000,
    "lifecycle": {
        "max_age_days": 3100,
        "stagnation_days": 120,
        "breed_cooldown_days": 50,
        "solo_breed_patience_days": 10,
        "snapshot_every_days": 25,
        "checkpoint_every_days": 2000,
    },
    "max_action_fraction": 0.80,
    "min_ticks_for_fitness": 75,
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
        "start_price_u": 2_000_000,
        "price_floor_u": 200_000,
        "regimes": [{"kind": "trend_up", "ticks": 3000, "drift_bps": 12, "vol_bps": 60}],
    },
}


def make_cfg(**overrides):
    cfg = copy.deepcopy(BASE_CFG)
    arena = overrides.pop("arena", None)
    if arena:
        cfg["arena"].update(arena)
    venue = overrides.pop("venue", None)
    if venue:
        cfg["venue"].update(venue)
    lifecycle = overrides.pop("lifecycle", None)
    if lifecycle:
        # an override replaces the base key whatever unit suffix it used
        for key in lifecycle:
            base = key.rsplit("_", 1)[0]
            for existing in [k for k in cfg["lifecycle"] if k.rsplit("_", 1)[0] == base]:
                del cfg["lifecycle"][existing]
        cfg["lifecycle"].update(lifecycle)
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
