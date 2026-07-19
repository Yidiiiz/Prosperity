"""v2 acceptance: the colony on REAL market data, down a capitalization ladder.

Replays 33 years of actual SPY daily closes (fetch first:
`python tools/fetch_market_data.py SPY -o data/spy_d.csv`). Three rungs,
run in order, with the pass bar lowering as integer-cent friction rises:

Every rung ends with a TERMINAL AUDIT: when the data runs out, every living
agent is liquidated at the last real price and its whole estate returns to
the treasury ("the end of history is the end of the world"). The audited
number is therefore hard cash in the house account — no mark-to-market
hand-waving. Requiring `treasury > initial` DURING a finite replay would
penalize holding the winning asset (realized extraction must beat the
colony's retained wealth); after the audit the claim is exact.

  full stakes -- $200,000 virtual, the standard colony (100 agents seeded
    $1,000), one lot = 1/100 SPY share (~44c in 1993). Pass per seed:
    colony survives to the end of history, and the post-audit treasury
    exceeds $200,000 — every deployed cent recovered plus real-market
    profit, in cash.

  micro stakes -- $100.00 TOTAL: 10 agents seeded $10, one lot = 1/1000
    SPY share (~4c in 1993). Granularity is still fine (a $10 seed buys
    ~200 lots) and the 1-cent minimum fee is near the nominal 20 bps, so
    the bar stays: survival + post-audit treasury above $100.00.

  tiny stakes -- $10.00 TOTAL: 4 agents seeded $2.50, 'small_stakes'
    waiver. Here the integer floor DOMINATES (min fee 1c is ~100 bps on a
    $1 trade; late in the series one lot is 74c against ~$2 equities), so
    the claim tested is the machinery, not the economics: pass per seed is
    survival to the end of history with invariants intact; the post-audit
    treasury is reported per seed and expected to be seed-dependent.

Usage: python -m experiments.real_market [--rung full|micro|tiny|all]
       [--seeds 42,7] [--workdir DIR]
(the default runs the whole ladder; per-rung/per-seed invocations exist so
long runs can be parallelized, each writing its own record; a persistent
--workdir lets interrupted runs resume exactly)
"""

import argparse
import json
import sys
import tempfile
from pathlib import Path

from colony import db, ledger, orchestrator
from colony.config import validate
from colony.evolution import archetype_shares
from colony.records import Record

ROOT = Path(__file__).resolve().parent.parent
CSV = ROOT / "data" / "spy_d.csv"
SEEDS = [42, 7, 2026]


def base_config(seed):
    with open(ROOT / "config.default.json", encoding="utf-8") as f:
        cfg = json.load(f)
    cfg["rng_seed"] = seed
    return cfg


def full_stakes_config(seed):
    cfg = base_config(seed)
    cfg["arena"] = {"kind": "replay", "name": "spy", "csv": str(CSV),
                    "lot_denominator": 100}
    validate(cfg)
    return cfg


def micro_stakes_config(seed):
    cfg = base_config(seed)
    cfg.update({
        "initial_treasury_u": 10_000,  # $100.00, total
        "gen0_population": 10,
        "gen0_seed_u": 1_000,          # $10.00 per agent, ~200 lots
        "max_population": 40,
        "population_floor": 8,
        "death_floor_u": 100,
        "reserve_floor_u": 150,
        "rent_min_u": 0,               # 1c/tick rent would be ~0.1%/day
        "elitism_top_k": 2,
    })
    cfg["arena"] = {"kind": "replay", "name": "spy", "csv": str(CSV),
                    "lot_denominator": 1000}
    validate(cfg)
    return cfg


def tiny_stakes_config(seed):
    cfg = base_config(seed)
    cfg.update({
        "initial_treasury_u": 1_000,   # $10.00, total
        "gen0_population": 4,
        "gen0_seed_u": 250,            # $2.50 per agent
        "max_population": 20,
        "population_floor": 4,
        "death_floor_u": 50,
        "reserve_floor_u": 50,
        "rent_min_u": 0,               # 1c/tick rent would be ~0.4%/day
        "elitism_top_k": 2,
        "small_stakes": True,
    })
    cfg["arena"] = {"kind": "replay", "name": "spy", "csv": str(CSV),
                    "lot_denominator": 1000}
    validate(cfg)
    return cfg


def run_seed(cfg, workdir, label):
    path = Path(workdir) / f"real_{label}_{cfg['rng_seed']}.db"
    resuming = path.exists()
    con = db.connect(path)
    if resuming:  # a persistent workdir resumes an interrupted run exactly
        orch = orchestrator.Orchestrator(con)
    else:
        orch = orchestrator.init_colony(con, cfg)
    orch.run(10 ** 9)  # to the end of history
    ticks = orch.tick
    price = orch.arena.price()
    pop = len(orch.agents)
    births, deaths = orch.births_cum, orch.deaths_cum
    shares = archetype_shares([a.genome for a in orch.agents.values()])
    colony = sum(
        ledger.balance(con, f"AGENT:{aid}") + agent.lots * price
        for aid, agent in orch.agents.items()
    )
    audited = con.execute(
        "SELECT COUNT(*) FROM agents WHERE death_cause = 'horizon'"
    ).fetchone()[0] > 0
    if pop == 0 and audited:
        # re-running an already-audited db: recover pre-audit stats from the
        # last populated snapshot instead of reporting an empty world
        m = con.execute(
            "SELECT population, colony_wealth_u, births_cum, deaths_cum,"
            " share_momentum, share_mean_revert, share_sitter FROM colony_metrics"
            " WHERE population > 0 ORDER BY tick DESC LIMIT 1"
        ).fetchone()
        if m:
            pop, colony, births, deaths = m[0], m[1], m[2], m[3]
            shares = {"momentum": m[4], "mean_revert": m[5], "sitter": m[6]}
    wealth = ledger.balance(con, "TREASURY") + colony
    survived = len(orch.agents) > 0 or audited
    orch.wind_down()  # terminal audit: verifies invariants after liquidation
    result = {
        "ticks": ticks, "pop": pop, "births": births, "deaths": deaths,
        "colony": colony, "wealth": wealth, "survived": survived,
        "treasury_liq": ledger.balance(con, "TREASURY"),
        "initial": cfg["initial_treasury_u"],
        "shares": shares, "price": price,
    }
    con.close()
    return result


def money(cents):
    return f"${cents / 100:,.2f}"


def report_rung(lines, label, cfg_fn, require_profit, seeds, workdir=None):
    rung_pass = True
    for seed in seeds:
        cfg = cfg_fn(seed)
        if workdir:
            r = run_seed(cfg, workdir, label)
        else:
            with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
                r = run_seed(cfg, tmp, label)
        profit_ok = r["treasury_liq"] > r["initial"]
        ok = r["survived"] and (profit_ok or not require_profit)
        rung_pass = rung_pass and ok
        s = r["shares"]
        gain = (r["treasury_liq"] / r["initial"] - 1) * 100
        profit_note = "ok" if profit_ok else ("FAIL" if require_profit else "reported only")
        lines.append(f"seed {seed}  [{'PASS' if ok else 'FAIL'}]")
        lines.append(f"  {r['ticks']} ticks (trading days) | pop {r['pop']}"
                     f" | births {r['births']} deaths {r['deaths']}"
                     f" | survived: {'ok' if r['survived'] else 'FAIL'}")
        lines.append(f"  at data end: colony wealth {money(r['colony'])}"
                     f" | lot price {money(r['price'])}"
                     f" | mom {s['momentum']:.0%} mr {s['mean_revert']:.0%}"
                     f" sit {s['sitter']:.0%}")
        lines.append(f"  terminal audit: all estates liquidated at the last real price ->")
        lines.append(f"    treasury {money(r['treasury_liq'])} cash"
                     f" vs {money(r['initial'])} initial  ({gain:+.1f}%: {profit_note})")
        lines.append("")
    return rung_pass


def main(argv=None):
    parser = argparse.ArgumentParser(description="real-market capitalization ladder")
    parser.add_argument("--rung", choices=["full", "micro", "tiny", "all"], default="all")
    parser.add_argument("--seeds", default=None,
                        help=f"comma-separated RNG seeds (default {SEEDS})")
    parser.add_argument("--workdir", default=None,
                        help="persistent scratch dir; lets an interrupted run resume")
    args = parser.parse_args(argv)
    seeds = [int(s) for s in args.seeds.split(",")] if args.seeds else SEEDS
    if not CSV.exists():
        print(f"missing {CSV}; run: python tools/fetch_market_data.py SPY -o data/spy_d.csv",
              file=sys.stderr)
        return 2
    name = "real_market" if args.rung == "all" else f"real_market_{args.rung}"
    if args.seeds:
        name += "_" + args.seeds.replace(",", "_")
    record = Record("records", "experiments", name,
                    config={"csv": str(CSV), "seeds": seeds, "rung": args.rung}, seed=seeds)
    lines = ["real market replay: SPY daily closes, capitalization ladder", ""]
    verdicts = []

    if args.rung in ("full", "all"):
        lines.append("== rung 1: full stakes ($200,000, lot = 1/100 share) ==")
        lines.append("")
        ok = report_rung(lines, "full", full_stakes_config, True, seeds, args.workdir)
        verdicts.append(("full stakes", ok))
    if args.rung in ("micro", "all"):
        lines.append("== rung 2: micro stakes ($100.00 TOTAL, lot = 1/1000 share) ==")
        lines.append("")
        ok = report_rung(lines, "micro", micro_stakes_config, True, seeds, args.workdir)
        verdicts.append(("micro stakes", ok))
    if args.rung in ("tiny", "all"):
        lines.append("== rung 3: tiny stakes ($10.00 TOTAL, lot = 1/1000 share) ==")
        lines.append("")
        ok = report_rung(lines, "tiny", tiny_stakes_config, False, seeds, args.workdir)
        verdicts.append(("tiny stakes", ok))

    all_pass = all(ok for _, ok in verdicts)
    detail = ", ".join(f"{label}: {'pass' if ok else 'FAIL'}" for label, ok in verdicts)
    lines.append(f"REAL MARKET {'PASS' if all_pass else 'FAIL'}  ({detail})")
    output = "\n".join(lines)
    print(output)
    record.section("results", output)
    record.finish("PASS" if all_pass else "FAIL")
    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
