"""Time v2 (spec v2 section 3): wall time is the config language.

The 11.5 acceptance properties: the same config expressed at tick_seconds
86,400 and 60 yields identical annualized rent and identical lifecycle
wall-times; lifecycle suffixes convert correctly; commensurate max_age
warns; and no _cents identifier survives in the core.
"""

from pathlib import Path

import pytest

from colony.config import SECONDS_PER_YEAR, ConfigError, rent_due, validate
from tests.conftest import make_cfg


def cfg_at(tick_seconds, **overrides):
    return make_cfg(arena={"tick_seconds": tick_seconds}, **overrides)


def test_lifecycle_suffixes_are_equivalent():
    day = cfg_at(86_400, lifecycle={"max_age_days": 2})
    hour = cfg_at(86_400, lifecycle={"max_age_hours": 48})
    sec = cfg_at(86_400, lifecycle={"max_age_seconds": 172_800})
    assert day["max_age_ticks"] == hour["max_age_ticks"] == sec["max_age_ticks"] == 2


def test_lifecycle_key_given_twice_rejected():
    with pytest.raises(ConfigError):
        make_cfg(lifecycle={"max_age_days": 1, "max_age_hours": 24})


def test_lifecycle_missing_and_unknown_keys_rejected():
    cfg = make_cfg()
    del cfg["lifecycle"]["max_age_days"]
    with pytest.raises(ConfigError):
        validate(cfg)
    with pytest.raises(ConfigError):
        make_cfg(lifecycle={"warp_speed_days": 1})
    with pytest.raises(ConfigError):
        make_cfg(lifecycle={"max_age": 3100})  # no suffix


def test_lifecycle_ticks_round_with_minimum_one():
    cfg = cfg_at(86_400, lifecycle={"solo_breed_patience_seconds": 1})
    assert cfg["solo_breed_patience"] == 1  # far below one tick still rounds to 1


def test_tick_seconds_required_for_replay(tmp_path):
    from tests.test_replay import trend_closes, write_csv
    path = write_csv(tmp_path / "p.csv", trend_closes(10))
    with pytest.raises(ConfigError):
        make_cfg(arena={"kind": "replay", "name": "x", "csv": path})


def test_same_config_same_annualized_rent_across_cadences():
    """11.5: per-tick rent scales exactly with tick_seconds, so a year of
    rent is identical at day-ticks and minute-ticks."""
    day = cfg_at(86_400, rent_min_u=0)
    minute = cfg_at(60, rent_min_u=0)
    for equity in (10_000 * SECONDS_PER_YEAR, 3 * 10_000 * SECONDS_PER_YEAR):
        annual_day = rent_due(equity, day) * (SECONDS_PER_YEAR // 86_400)
        annual_minute = rent_due(equity, minute) * (SECONDS_PER_YEAR // 60)
        assert annual_day == annual_minute > 0


def test_rent_matches_v1_two_bps_at_day_ticks():
    """730 bps APR at day ticks is exactly the validated v1 2 bps/tick."""
    cfg = cfg_at(86_400, rent_min_u=0)
    assert cfg["rent_apr_bps"] == 730
    for equity in (0, 1, 4_999, 5_000, 123_456_789, 10**12):
        assert rent_due(equity, cfg) == equity * 2 // 10_000


def test_same_config_same_lifecycle_wall_times_across_cadences():
    day = cfg_at(86_400)
    minute = cfg_at(60)
    for key in ("max_age_ticks", "stagnation_ticks", "breed_cooldown_ticks",
                "solo_breed_patience", "snapshot_every", "checkpoint_every"):
        assert day[key] * 86_400 == minute[key] * 60


def test_commensurate_max_age_warns():
    regimes = [{"kind": "trend_up", "ticks": 3000, "drift_bps": 12, "vol_bps": 60}]
    with pytest.warns(UserWarning, match="commensurate"):
        make_cfg(lifecycle={"max_age_days": 3000}, arena={"regimes": regimes})  # equal
    with pytest.warns(UserWarning, match="commensurate"):
        make_cfg(lifecycle={"max_age_days": 6000}, arena={"regimes": regimes})  # multiple
    with pytest.warns(UserWarning, match="commensurate"):
        make_cfg(lifecycle={"max_age_days": 1500}, arena={"regimes": regimes})  # divisor


def test_coprime_max_age_does_not_warn(recwarn):
    regimes = [{"kind": "trend_up", "ticks": 3000, "drift_bps": 12, "vol_bps": 60}]
    make_cfg(lifecycle={"max_age_days": 3100}, arena={"regimes": regimes})
    assert not [w for w in recwarn if "commensurate" in str(w.message)]


def test_no_cents_identifier_remains_in_core():
    """11.5: the migration is total — no _cents name survives in colony/."""
    root = Path(__file__).resolve().parent.parent / "colony"
    offenders = [
        str(path)
        for path in root.rglob("*.py")
        if "_cents" in path.read_text(encoding="utf-8")
    ]
    assert offenders == []


def test_petri_utc_advances_by_tick_seconds():
    import random
    from colony.arenas.petri import Petri
    arena_cfg = {
        "name": "petri", "start_price_u": 2_000_000, "price_floor_u": 200_000,
        "tick_seconds": 60,
        "regimes": [{"kind": "trend_up", "ticks": 50, "drift_bps": 12, "vol_bps": 60}],
    }
    arena = Petri(arena_cfg)
    t0 = arena.utc()
    rng = random.Random(1)
    arena.step(rng)
    arena.step(rng)
    assert arena.utc() == t0 + 120
    # utc survives a state round-trip
    twin = Petri(arena_cfg)
    twin.set_state(arena.get_state())
    assert twin.utc() == arena.utc()


def test_replay_utc_comes_from_the_date_column(tmp_path):
    from colony.arenas.replay import Replay
    path = tmp_path / "p.csv"
    path.write_text(
        "Date,Close\n2021-06-01T00:00:00+00:00,2.0\n2021-06-01T00:01:00+00:00,2.5\n",
        encoding="utf-8",
    )
    arena = Replay({"name": "x", "csv": str(path)})
    assert arena.utc() == 1_622_505_600
    arena.step(None)
    assert arena.utc() == 1_622_505_660
