"""The flagship acceptance experiment: does evolution actually adapt? (spec 13.3b)

Fresh db per seed, two-regime arena: 3000 ticks of trend_up (drift 12, vol 60),
then 5000 ticks of mean_revert (kappa 0.15, vol 200). Records the living
archetype distribution at the flip and at the end.

Pass (every seed in {42, 7, 2026}):
  - living-population share of mean_revert rises >= +20 percentage points
  - total system wealth (treasury + agent wealth) ends above its start
  - ending treasury > initial_treasury_cents (spec 13.3c: full capital
    recovery + banked profit, via rent + death residues + debt_repay alone)

Usage: python -m experiments.regime_flip
"""

import json
import sys
import tempfile
from pathlib import Path

from colony import db, ledger, orchestrator
from colony.config import validate
from colony.evolution import archetype_shares
from colony.records import Record

SEEDS = [42, 7, 2026]
TREND_TICKS = 3000
MR_TICKS = 5000


def build_config(seed):
    with open(Path(__file__).resolve().parent.parent / "config.default.json",
              encoding="utf-8") as f:
        cfg = json.load(f)
    cfg["rng_seed"] = seed
    cfg["arena"]["regimes"] = [
        {"kind": "trend_up", "ticks": TREND_TICKS, "drift_bps": 12, "vol_bps": 60},
        {"kind": "mean_revert", "ticks": MR_TICKS, "kappa": 0.15, "vol_bps": 200},
    ]
    validate(cfg)
    return cfg


def shares_of_living(orch):
    shares = archetype_shares([a.genome for a in orch.agents.values()])
    return shares, len(orch.agents)


def system_wealth(con, orch):
    treasury = ledger.balance(con, "TREASURY")
    price = orch.arena.price()
    colony = sum(
        ledger.balance(con, f"AGENT:{aid}") + agent.lots * price
        for aid, agent in orch.agents.items()
    )
    return treasury, colony


def run_seed(seed, workdir):
    cfg = build_config(seed)
    con = db.connect(Path(workdir) / f"regime_flip_{seed}.db")
    orch = orchestrator.init_colony(con, cfg)
    start_treasury, start_colony = system_wealth(con, orch)
    orch.run(TREND_TICKS)
    after_trend, pop_trend = shares_of_living(orch)
    orch.run(MR_TICKS)
    after_mr, pop_mr = shares_of_living(orch)
    treasury, colony = system_wealth(con, orch)
    ledger.verify_invariants(con, cfg["initial_treasury_cents"])
    con.close()
    return {
        "seed": seed,
        "after_trend": after_trend, "pop_trend": pop_trend,
        "after_mr": after_mr, "pop_mr": pop_mr,
        "shift_pts": (after_mr["mean_revert"] - after_trend["mean_revert"]) * 100,
        "start_wealth": start_treasury + start_colony,
        "end_wealth": treasury + colony,
        "treasury": treasury,
        "initial_treasury": cfg["initial_treasury_cents"],
    }


def main():
    record = Record("records", "experiments", "regime_flip",
                    config={"seeds": SEEDS, "trend_ticks": TREND_TICKS, "mr_ticks": MR_TICKS},
                    seed=SEEDS)
    lines = [f"regime flip: {TREND_TICKS}t trend_up -> {MR_TICKS}t mean_revert,"
             f" seeds {SEEDS}", ""]
    all_pass = True
    for seed in SEEDS:
        with tempfile.TemporaryDirectory() as workdir:
            r = run_seed(seed, workdir)
        wealth_gain = (r["end_wealth"] / r["start_wealth"] - 1) * 100
        treasury_gain = (r["treasury"] / r["initial_treasury"] - 1) * 100
        shift_ok = r["shift_pts"] >= 20
        wealth_ok = r["end_wealth"] > r["start_wealth"]
        treasury_ok = r["treasury"] > r["initial_treasury"]
        ok = shift_ok and wealth_ok and treasury_ok
        all_pass = all_pass and ok
        at, am = r["after_trend"], r["after_mr"]
        lines.append(f"seed {r['seed']}  [{'PASS' if ok else 'FAIL'}]")
        lines.append(f"  after trend  (pop {r['pop_trend']:>3}):"
                     f"  mom {at['momentum']:>5.0%}  mr {at['mean_revert']:>5.0%}"
                     f"  sit {at['sitter']:>4.0%}")
        lines.append(f"  after m-rev  (pop {r['pop_mr']:>3}):"
                     f"  mom {am['momentum']:>5.0%}  mr {am['mean_revert']:>5.0%}"
                     f"  sit {am['sitter']:>4.0%}")
        lines.append(f"  mean_revert share shift {r['shift_pts']:+.0f} pts"
                     f"  (want >= +20: {'ok' if shift_ok else 'FAIL'})")
        lines.append(f"  system wealth {wealth_gain:+.1f}%"
                     f"  (want > 0: {'ok' if wealth_ok else 'FAIL'})")
        lines.append(f"  treasury      {treasury_gain:+.1f}% vs initial"
                     f"  (want > 0: {'ok' if treasury_ok else 'FAIL'})")
        lines.append("")
    lines.append("FLAGSHIP PASS" if all_pass else "FLAGSHIP FAIL")
    output = "\n".join(lines)
    print(output)
    record.section("results", output)
    record.finish("PASS" if all_pass else "FAIL")
    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
