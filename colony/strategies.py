"""The three archetypes as pure functions: (genome, history, state) -> Decision.

TODO(v2): cognition layer — a v2 archetype whose decide() calls an external
LLM provider (configured by environment variable). Same inputs, same Decision
dataclass out, plus a cost_u the orchestrator debits via SINK:METABOLISM.
"""

import math
from dataclasses import dataclass


@dataclass
class Decision:
    side: str  # 'BUY' | 'SELL'
    lots: int


def zstats(history, lookback):
    """(z, mean, stdev) of the current price vs the trailing lookback window.

    Returns zeros until lookback+1 prices exist or when the window is flat.
    Prices are ints, so the sums are exact; n^2 * var = n*sum(x^2) - sum(x)^2
    stays in integers and one sqrt at the end keeps this deterministic and
    fast (stdlib statistics.pstdev is exact-fraction arithmetic and was the
    measured hot spot at population 100, spec v2 section 4).
    """
    if len(history) < lookback + 1:
        return 0.0, 0.0, 0.0
    window = history[-lookback:]
    n = lookback
    s1 = sum(window)
    s2 = sum(x * x for x in window)
    var_num = n * s2 - s1 * s1  # n^2 * variance, exact
    mean = s1 / n
    if var_num <= 0:
        return 0.0, mean, 0.0
    root = math.sqrt(var_num)
    return (n * history[-1] - s1) / root, mean, root / n


def _fee_blocked(genome, z, mean, stdev, fee_bps):
    """Optional fee_aware gene: skip entries whose expected edge is below 2x fees.

    Edge heuristic: (|z| - exit_z) z-units, converted to bps via the window's
    coefficient of variation (stdev / mean x 10^4 bps per z-unit).
    """
    if "fee_aware" not in genome.get("genes", ()):
        return False
    if mean <= 0:
        return False
    edge_bps = (abs(z) - genome["params"]["exit_z"]) * (stdev / mean) * 10_000
    return edge_bps < 2 * fee_bps


def decide(genome, history, lots, hold, equity, fee_bps):
    """One decision per tick. Returns a Decision or None (do nothing)."""
    archetype = genome["archetype"]
    if archetype == "sitter":  # the deliberate control: never trades
        return None
    params = genome["params"]
    z, mean, stdev = zstats(history, params["lookback"])
    price = history[-1]

    if archetype == "momentum":
        if lots == 0 and z >= params["entry_z"]:
            if _fee_blocked(genome, z, mean, stdev, fee_bps):
                return None
            return Decision("BUY", int(params["risk_fraction"] * equity) // price)
        if lots > 0 and (z <= params["exit_z"] or hold >= params["hold_max"]):
            return Decision("SELL", lots)
    elif archetype == "mean_revert":
        if lots == 0 and z <= -params["entry_z"]:
            if _fee_blocked(genome, z, mean, stdev, fee_bps):
                return None
            return Decision("BUY", int(params["risk_fraction"] * equity) // price)
        if lots > 0 and (z >= params["exit_z"] or hold >= params["hold_max"]):
            return Decision("SELL", lots)
    return None
