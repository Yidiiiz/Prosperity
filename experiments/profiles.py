"""Cadence profiles (spec v4 3): one colony definition per timescale.

A profile is a config factory `(seed, csv_path, lot_denominator=None,
name=None) -> config dict`. The fairness rule (v4 3.3): across cadences the
capitalization, archetype set, gate genes, and venue costs are identical —
only tick cadence, lot size, and wall-time lifecycle constants vary. `daily`
and `minute` reproduce the v3 walk-forward/minute-ladder configs exactly
(regression-tested byte-identical); `hourly` and `second` are new.
"""

import json
from pathlib import Path

from experiments.minute_ladder import base_config as _minute_base

ROOT = Path(__file__).resolve().parent.parent

HOURLY_LIFECYCLE = {
    "max_age_days": 365,
    "stagnation_days": 30,           # > max lookback (100 hours), by far
    "breed_cooldown_days": 7,
    "solo_breed_patience_days": 2,
    "snapshot_every_days": 7,
    "checkpoint_every_days": 30,
}

SECOND_LIFECYCLE = {                 # mirrors config.live.json (v4 3.1)
    "max_age_days": 30,
    "stagnation_hours": 6,
    "breed_cooldown_hours": 1,
    "solo_breed_patience_seconds": 600,
    "snapshot_every_hours": 1,       # replay: 1/s would drown the timeseries
    "checkpoint_every_hours": 6,
}


def _spy_base(seed, csv_path):
    with open(ROOT / "config.spy.json", encoding="utf-8") as f:
        cfg = json.load(f)
    cfg["rng_seed"] = seed
    cfg["arena"]["csv"] = str(csv_path)
    return cfg


def _override(cfg, lot_denominator, name):
    if lot_denominator is not None:
        cfg["arena"]["lot_denominator"] = lot_denominator
    if name is not None:
        cfg["arena"]["name"] = name
    return cfg


def daily_config(seed, csv_path, lot_denominator=None, name=None):
    """Exactly walk_forward's v3 daily profile when called with defaults."""
    cfg = _spy_base(seed, csv_path)
    cfg["arena"]["name"] = "walk"
    return _override(cfg, lot_denominator, name)


def minute_config(seed, csv_path, lot_denominator=None, name=None):
    """Exactly the minute ladder's v3 base profile when called with defaults."""
    cfg = _minute_base(seed, csv_path)
    return _override(cfg, lot_denominator, name)


def hourly_config(seed, csv_path, lot_denominator=None, name=None):
    cfg = _spy_base(seed, csv_path)
    cfg["arena"].update({"name": "hourly", "tick_seconds": 3600})
    cfg["lifecycle"] = dict(HOURLY_LIFECYCLE)
    cfg["min_ticks_for_fitness"] = 200
    return _override(cfg, lot_denominator, name)


def second_config(seed, csv_path, lot_denominator=None, name=None):
    cfg = _spy_base(seed, csv_path)
    cfg["arena"].update({"name": "second", "tick_seconds": 1,
                         "lot_denominator": 100_000})
    cfg["lifecycle"] = dict(SECOND_LIFECYCLE)
    cfg["min_ticks_for_fitness"] = 300
    return _override(cfg, lot_denominator, name)


PROFILES = {
    "second": second_config,
    "minute": minute_config,
    "hourly": hourly_config,
    "daily": daily_config,
}
