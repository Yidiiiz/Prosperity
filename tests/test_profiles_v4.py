"""spec v4 section 3: the cadence-profile registry."""

import json
from pathlib import Path

from colony import db, orchestrator
from colony.config import validate
from experiments.minute_ladder import base_config as minute_base
from experiments.profiles import (PROFILES, daily_config, hourly_config,
                                  minute_config, second_config)

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "data" / "btcusdt_1m_fixture.csv"


def test_registry_names_and_cadences():
    assert set(PROFILES) == {"second", "minute", "hourly", "daily"}
    ticks = {name: PROFILES[name](1, FIXTURE)["arena"]["tick_seconds"]
             for name in PROFILES}
    assert ticks == {"second": 1, "minute": 60, "hourly": 3600, "daily": 86400}


def test_daily_profile_is_byte_identical_to_v3_walk_forward():
    with open(ROOT / "config.spy.json", encoding="utf-8") as f:
        v3 = json.load(f)  # exactly what walk_forward.daily_config did in v3
    v3["rng_seed"] = 42
    v3["arena"]["csv"] = str(FIXTURE)
    v3["arena"]["name"] = "walk"
    assert daily_config(42, FIXTURE) == v3


def test_minute_profile_is_byte_identical_to_v3_ladder():
    assert minute_config(7, FIXTURE) == minute_base(7, FIXTURE)


def test_fairness_capital_and_venue_identical_across_cadences():
    cfgs = [f(42, FIXTURE) for f in PROFILES.values()]
    for key in ("initial_treasury_u", "gen0_seed_u", "venue", "mutation"):
        assert len({json.dumps(c[key], sort_keys=True) for c in cfgs}) == 1


def test_new_profiles_validate_and_accept_overrides():
    for factory in (hourly_config, second_config):
        cfg = factory(42, FIXTURE, lot_denominator=1_000_000, name="cell_x")
        validate(cfg)
        assert cfg["arena"]["lot_denominator"] == 1_000_000
        assert cfg["arena"]["name"] == "cell_x"


def test_second_profile_runs_a_fixture_window(tmp_path):
    cfg = second_config(42, FIXTURE)
    cfg.update({"initial_treasury_u": 20_000_000_000, "gen0_population": 8,
                "max_population": 20, "population_floor": 4})
    validate(cfg)
    con = db.connect(tmp_path / "sec.db")
    orch = orchestrator.init_colony(con, cfg)
    orch.run(300)
    assert orch.tick == 300
    con.close()
