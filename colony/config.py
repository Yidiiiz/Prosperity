"""Config loading and validation. Refuses to run on nonsense (spec section 5)."""

import json

from .evolution import PARAM_BOUNDS

REQUIRED_KEYS = [
    "rng_seed", "debug", "initial_treasury_cents", "gen0_population", "gen0_seed_cents",
    "max_population", "population_floor", "death_floor_cents", "rent_min_cents",
    "rent_bps_of_equity", "fee_bps", "repro_multiple", "repay_multiple",
    "reserve_floor_cents", "breed_cooldown_ticks", "solo_breed_patience", "max_age_ticks",
    "stagnation_ticks", "max_action_fraction", "min_ticks_for_fitness", "snapshot_every",
    "hall_size", "hall_immigrant_prob", "mutation", "elitism_top_k", "arena",
]

REGIME_KINDS = ("trend_up", "mean_revert", "crash")


class ConfigError(Exception):
    pass


def load_config(path):
    with open(path, encoding="utf-8") as f:
        cfg = json.load(f)
    validate(cfg)
    return cfg


def validate(cfg):
    for key in REQUIRED_KEYS:
        if key not in cfg:
            raise ConfigError(f"missing config key {key!r}")
    for key, value in cfg.items():
        if key.endswith("_cents") and not isinstance(value, int):
            raise ConfigError(f"{key} must be an integer number of cents, got {value!r}")

    arena = cfg["arena"]
    kind = arena.get("kind", "petri")
    if kind == "petri":
        for key in ("name", "start_price_cents", "price_floor_cents", "regimes"):
            if key not in arena:
                raise ConfigError(f"missing arena key {key!r}")
        if not arena["regimes"]:
            raise ConfigError("arena.regimes must not be empty")
        for regime in arena["regimes"]:
            if regime.get("kind") not in REGIME_KINDS:
                raise ConfigError(f"unknown regime kind {regime.get('kind')!r}")
            if regime.get("ticks", 0) <= 0:
                raise ConfigError("regime ticks must be positive")
    elif kind == "replay":
        for key in ("name", "csv"):
            if key not in arena:
                raise ConfigError(f"missing arena key {key!r}")
        denom = arena.get("lot_denominator", 1)
        if not isinstance(denom, int) or denom < 1:
            raise ConfigError("arena.lot_denominator must be a positive integer")
    else:
        raise ConfigError(f"unknown arena kind {kind!r}")

    if not cfg["death_floor_cents"] < cfg["gen0_seed_cents"]:
        raise ConfigError("death_floor_cents must be below gen0_seed_cents")
    if not cfg["reserve_floor_cents"] >= cfg["death_floor_cents"]:
        raise ConfigError("reserve_floor_cents must be at least death_floor_cents")
    # Lot granularity can silently kill the colony (spec 3.11). Replay start
    # prices come from the CSV, so init_colony re-checks against real data.
    if (kind == "petri" and not cfg.get("small_stakes")
            and cfg["gen0_seed_cents"] < 200 * arena["start_price_cents"]):
        raise ConfigError("gen0_seed_cents must be at least 200 x arena.start_price_cents"
                          " (or set 'small_stakes': true to accept the risk)")
    max_lookback = PARAM_BOUNDS["lookback"][1]
    if not cfg["stagnation_ticks"] > max_lookback:
        raise ConfigError(f"stagnation_ticks must exceed the max lookback bound ({max_lookback})")
    # Rent must stay far below achievable earn rates (spec 3.6).
    if cfg["rent_bps_of_equity"] > 2:
        raise ConfigError("rent_bps_of_equity must be <= 2")
    if cfg["gen0_population"] * cfg["gen0_seed_cents"] > cfg["initial_treasury_cents"]:
        raise ConfigError("treasury cannot fund gen0_population x gen0_seed_cents")
    if cfg["population_floor"] > cfg["max_population"]:
        raise ConfigError("population_floor must not exceed max_population")
    # The seed-repayment quota has a SILENT failure cliff above 0.15 (spec 3.14).
    if cfg["repay_multiple"] > 0.25:
        raise ConfigError("repay_multiple > 0.25 is hard-rejected: it silently breaks adaptation")
    if cfg["repay_multiple"] > 0.15 and cfg.get("revalidated") is not True:
        raise ConfigError(
            "repay_multiple > 0.15 requires 'revalidated': true (re-run the validation suite first)"
        )
