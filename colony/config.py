"""Config loading and validation. Refuses to run on nonsense (spec section 5).

v2: wall time is the config language (spec v2 section 3). Lifecycle constants
are written with a _seconds/_hours/_days suffix and converted to ticks at
load via the arena's tick_seconds; rates are annualized (rent_apr_bps). The
derived tick values are stored back onto the config dict under the v1 names
(max_age_ticks, snapshot_every, ...) so the rest of the code never converts.
"""

import json
import warnings

from .evolution import PARAM_BOUNDS

SECONDS_PER_YEAR = 31_536_000
SUFFIXES = {"_seconds": 1, "_hours": 3_600, "_days": 86_400}

# lifecycle base key -> the derived config name the rest of the code reads
LIFECYCLE_KEYS = {
    "max_age": "max_age_ticks",
    "stagnation": "stagnation_ticks",
    "breed_cooldown": "breed_cooldown_ticks",
    "solo_breed_patience": "solo_breed_patience",
    "snapshot_every": "snapshot_every",
    "checkpoint_every": "checkpoint_every",
}

REQUIRED_KEYS = [
    "rng_seed", "debug", "initial_treasury_u", "gen0_population", "gen0_seed_u",
    "max_population", "population_floor", "death_floor_u", "rent_min_u",
    "rent_apr_bps", "venue", "repro_multiple", "repay_multiple",
    "reserve_floor_u", "lifecycle", "max_action_fraction", "min_ticks_for_fitness",
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


def tick_seconds(arena_cfg):
    """Seconds of wall time per tick: Petri defaults to one day per bar; replay
    and live must state the cadence of their data (spec v2 3.1)."""
    default = 86_400 if arena_cfg.get("kind", "petri") == "petri" else None
    ts = arena_cfg.get("tick_seconds", default)
    if not isinstance(ts, int) or isinstance(ts, bool) or ts < 1:
        raise ConfigError("arena.tick_seconds must be a positive integer"
                          " (seconds per bar; required for replay/live arenas)")
    return ts


def rent_due(equity_u, cfg):
    """Per-tick rent from the annualized rate (spec v2 3.3): floor division,
    may round to 0 at small stakes (a 0 rent is skipped per DECISIONS #27)."""
    rent = equity_u * cfg["rent_apr_bps"] * cfg["_tick_seconds"] // (10_000 * SECONDS_PER_YEAR)
    return max(cfg["rent_min_u"], rent)


def _derive_lifecycle(cfg):
    """Convert the wall-time lifecycle block to ticks (rounded, minimum 1),
    rejecting unknown keys, missing keys, and a base key given twice."""
    ts = tick_seconds(cfg["arena"])
    cfg["_tick_seconds"] = ts
    derived = {}
    for key, value in cfg["lifecycle"].items():
        for suffix, secs in SUFFIXES.items():
            if key.endswith(suffix):
                base = key[: -len(suffix)]
                break
        else:
            raise ConfigError(f"lifecycle key {key!r} needs a _seconds/_hours/_days suffix")
        if base not in LIFECYCLE_KEYS:
            raise ConfigError(f"unknown lifecycle key {key!r}")
        if base in derived:
            raise ConfigError(f"lifecycle key {base!r} given twice with different units")
        if not isinstance(value, (int, float)) or value <= 0:
            raise ConfigError(f"lifecycle {key} must be a positive number")
        derived[base] = max(1, round(value * secs / ts))
    for base, name in LIFECYCLE_KEYS.items():
        if base not in derived:
            raise ConfigError(f"missing lifecycle key {base!r} (any _seconds/_hours/_days form)")
        cfg[name] = derived[base]


def _warn_commensurate(cfg):
    """Lifecycle constants must not be commensurate with regime lengths
    (spec v2 1.4, fixes LEARNINGS #23): a senescence wave landing exactly on
    a regime boundary muddies selection measurements."""
    if cfg["arena"].get("kind", "petri") != "petri":
        return
    max_age = cfg["max_age_ticks"]
    for regime in cfg["arena"]["regimes"]:
        length = regime["ticks"]
        if max_age == length or max_age % length == 0 or length % max_age == 0:
            warnings.warn(
                f"max_age ({max_age} ticks) is commensurate with a {regime['kind']}"
                f" regime length ({length} ticks): lifecycle waves will align with"
                " regime boundaries and muddy selection measurements",
                stacklevel=2,
            )


def _check_venue(cfg, arena_kind):
    """Per-venue execution costs (spec v2 2.2) and fill delay (2.3). All v2
    orders are market orders: maker_bps is schema for future limit-order
    work and is validated but unused."""
    venue = cfg["venue"]
    for key in ("taker_bps", "maker_bps", "spread_bps", "min_fee_u"):
        value = venue.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ConfigError(f"venue.{key} must be a non-negative integer")
    delay = venue.get("fill_delay_ticks", 1)
    if delay not in (0, 1):
        raise ConfigError("venue.fill_delay_ticks must be 0 or 1")
    if delay == 0 and arena_kind != "petri":
        raise ConfigError("venue.fill_delay_ticks 0 is allowed only for the Petri arena"
                          " (same-bar fills on real data are fake intraday alpha)")


def validate(cfg):
    for key in REQUIRED_KEYS:
        if key not in cfg:
            raise ConfigError(f"missing config key {key!r}")
    for key, value in cfg.items():
        if key.endswith("_u") and not isinstance(value, int):
            raise ConfigError(f"{key} must be an integer number of micro-dollars, got {value!r}")

    arena = cfg["arena"]
    kind = arena.get("kind", "petri")
    if kind == "petri":
        for key in ("name", "start_price_u", "price_floor_u", "regimes"):
            if key not in arena:
                raise ConfigError(f"missing arena key {key!r}")
        if not arena["regimes"]:
            raise ConfigError("arena.regimes must not be empty")
        for regime in arena["regimes"]:
            if regime.get("kind") not in REGIME_KINDS:
                raise ConfigError(f"unknown regime kind {regime.get('kind')!r}")
            if regime.get("ticks", 0) <= 0:
                raise ConfigError("regime ticks must be positive")
    elif kind in ("replay", "live"):
        if "name" not in arena:
            raise ConfigError("missing arena key 'name'")
        if kind == "replay" and "csv" not in arena:
            raise ConfigError("missing arena key 'csv'")
        if kind == "live" and bool(arena.get("csv")) == bool(arena.get("journal")):
            raise ConfigError("live arena needs exactly one of 'csv' (single file)"
                              " or 'journal' (directory of daily segments)")
        denom = arena.get("lot_denominator", 1)
        if not isinstance(denom, int) or denom < 1:
            raise ConfigError("arena.lot_denominator must be a positive integer")
        timeout = arena.get("poll_timeout_seconds", 120)
        if not isinstance(timeout, (int, float)) or timeout <= 0:
            raise ConfigError("arena.poll_timeout_seconds must be positive")
    else:
        raise ConfigError(f"unknown arena kind {kind!r}")

    _derive_lifecycle(cfg)
    _warn_commensurate(cfg)
    _check_venue(cfg, kind)
    flush_every = cfg.setdefault("flush_every", 1)
    if not isinstance(flush_every, int) or isinstance(flush_every, bool) or flush_every < 1:
        raise ConfigError("flush_every must be a positive integer")
    if kind == "live" and flush_every != 1:
        raise ConfigError("live arenas pin flush_every 1 (exact resume is non-negotiable)")

    if not cfg["death_floor_u"] < cfg["gen0_seed_u"]:
        raise ConfigError("death_floor_u must be below gen0_seed_u")
    if not cfg["reserve_floor_u"] >= cfg["death_floor_u"]:
        raise ConfigError("reserve_floor_u must be at least death_floor_u")
    # Lot granularity can silently kill the colony (spec 3.11). Replay start
    # prices come from the CSV, so init_colony re-checks against real data.
    if (kind == "petri" and not cfg.get("small_stakes")
            and cfg["gen0_seed_u"] < 200 * arena["start_price_u"]):
        raise ConfigError("gen0_seed_u must be at least 200 x arena.start_price_u"
                          " (or set 'small_stakes': true to accept the risk)")
    max_lookback = PARAM_BOUNDS["lookback"][1]
    if not cfg["stagnation_ticks"] > max_lookback:
        raise ConfigError(f"stagnation_ticks must exceed the max lookback bound ({max_lookback})")
    # Rent must stay far below achievable earn rates (spec 3.6). 730 bps APR
    # is the v1 2 bps/tick ceiling expressed annually at day-ticks.
    if cfg["rent_apr_bps"] > 730:
        raise ConfigError("rent_apr_bps must be <= 730")
    if cfg["gen0_population"] * cfg["gen0_seed_u"] > cfg["initial_treasury_u"]:
        raise ConfigError("treasury cannot fund gen0_population x gen0_seed_u")
    if cfg["population_floor"] > cfg["max_population"]:
        raise ConfigError("population_floor must not exceed max_population")
    # The seed-repayment quota has a SILENT failure cliff above 0.15 (spec 3.14).
    if cfg["repay_multiple"] > 0.25:
        raise ConfigError("repay_multiple > 0.25 is hard-rejected: it silently breaks adaptation")
    if cfg["repay_multiple"] > 0.15 and cfg.get("revalidated") is not True:
        raise ConfigError(
            "repay_multiple > 0.15 requires 'revalidated': true (re-run the validation suite first)"
        )
