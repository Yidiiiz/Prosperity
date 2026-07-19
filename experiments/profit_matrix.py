"""Environment pre-check: the profitability matrix (spec 13.3a).

Runs a SINGLE agent (no death, no breeding, rent on) with a known-good genome
per archetype in each pure regime, 3000 ticks x 5 seeds, and reports mean
realized bps/tick of equity growth. If this matrix has the wrong signs,
adaptation is impossible no matter how good the GA is — fix the regime
parameters, not the GA.

Pass criteria:
  momentum     >= +3 bps/t in trend_up   AND <= -2 bps/t in mean_revert
  mean_revert  >= +2 bps/t in mean_revert AND <= 0 bps/t in trend_up

Usage: python -m experiments.profit_matrix
"""

import math
import random
import sys

from colony import strategies, risk
from colony.arenas.petri import Petri
from colony.records import Record

SEEDS = [42, 7, 2026, 11, 99]
TICKS = 3000
START_CASH = 100_000
FEE_BPS = 20
RENT_MIN = 10
RENT_BPS = 2
MAX_ACTION_FRACTION = 0.80

REGIMES = {
    "trend_up": {"kind": "trend_up", "ticks": TICKS, "drift_bps": 12, "vol_bps": 60},
    "mean_revert": {"kind": "mean_revert", "ticks": TICKS, "kappa": 0.15, "vol_bps": 200},
}

# hold_max set to its upper bound so the probes express pure signal behavior
PROBES = {
    "momentum": {
        "archetype": "momentum",
        "params": {"lookback": 30, "entry_z": 1.5, "exit_z": -0.8, "risk_fraction": 0.6,
                   "hold_max": 1500},
        "econ": {"child_seed_fraction": 0.40},
        "genes": [],
    },
    "mean_revert": {
        "archetype": "mean_revert",
        "params": {"lookback": 60, "entry_z": 1.5, "exit_z": 0.2, "risk_fraction": 0.6,
                   "hold_max": 1500},
        "econ": {"child_seed_fraction": 0.40},
        "genes": [],
    },
}

CRITERIA = {  # (regime, archetype) -> (comparator, threshold_bps)
    ("trend_up", "momentum"): (">=", 3.0),
    ("mean_revert", "momentum"): ("<=", -2.0),
    ("mean_revert", "mean_revert"): (">=", 2.0),
    ("trend_up", "mean_revert"): ("<=", 0.0),
}


def run_single(genome, regime, seed):
    """One agent against one pure regime, in-memory cash, real strategy/risk/fee code."""
    rng = random.Random(seed)
    arena = Petri({"name": "petri", "start_price_u": 200, "price_floor_u": 20,
                   "regimes": [regime]})
    cash, lots, hold = START_CASH, 0, 0
    for _ in range(TICKS):
        arena.step(rng)
        price = arena.price()
        equity = cash + lots * price
        rent = max(RENT_MIN, equity * RENT_BPS // 10_000)
        if cash < rent and lots > 0:  # force-liquidate, as in the real loop
            proceeds = lots * price
            cash += proceeds - risk.fee_u(proceeds, FEE_BPS)
            lots = 0
        cash = max(0, cash - rent)
        equity = cash + lots * price
        history = arena.history(101)
        decision = strategies.decide(genome, history, lots, hold, equity, FEE_BPS)
        decision = risk.check(decision, cash, equity, lots, price, MAX_ACTION_FRACTION, FEE_BPS)
        if decision is not None:
            if decision.side == "BUY":
                cost = decision.lots * price
                cash -= cost + risk.fee_u(cost, FEE_BPS)
                lots += decision.lots
                hold = 0
            else:
                proceeds = decision.lots * price
                cash += proceeds - risk.fee_u(proceeds, FEE_BPS)
                lots -= decision.lots
        if lots > 0:
            hold += 1
    equity = cash + lots * arena.price()
    return 10_000 * math.log(max(equity, 1) / START_CASH) / TICKS  # realized bps/tick


def main():
    record = Record("records", "experiments", "profit_matrix",
                    config={"seeds": SEEDS, "ticks": TICKS, "probes": PROBES,
                            "regimes": REGIMES},
                    seed=SEEDS)
    lines = [f"profitability matrix: {TICKS} ticks x seeds {SEEDS}", ""]
    lines.append(f"{'regime':<14}{'archetype':<14}{'mean bps/t':>12}   per-seed")
    all_pass = True
    for regime_name, regime in REGIMES.items():
        for arch, genome in PROBES.items():
            results = [run_single(genome, regime, s) for s in SEEDS]
            mean = sum(results) / len(results)
            op, threshold = CRITERIA[(regime_name, arch)]
            ok = mean >= threshold if op == ">=" else mean <= threshold
            all_pass = all_pass and ok
            verdict = "PASS" if ok else "FAIL"
            lines.append(
                f"{regime_name:<14}{arch:<14}{mean:>+12.2f}   "
                + " ".join(f"{r:+.1f}" for r in results)
                + f"   [{verdict}: want {op} {threshold:+.0f}]"
            )
    lines.append("")
    lines.append("MATRIX PASS" if all_pass else
                 "MATRIX FAIL — fix arena/regime parameters, not the GA")
    output = "\n".join(lines)
    print(output)
    record.section("matrix", output)
    record.finish("PASS" if all_pass else "FAIL")
    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
