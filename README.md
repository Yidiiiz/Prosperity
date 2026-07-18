# darwin-wallet

An evolutionary colony of autonomous agents, each with a strictly isolated
wallet, acting in a simulated market arena. Profitable agents reproduce by
**mitosis** — funding their children out of their own profits — and
unprofitable agents go bankrupt and die. Selection, crossover, mutation and
immigration drive the colony toward profit with no human tuning. Every cent is
integer money in a double-entry SQLite ledger, and the whole system is
deterministic: same config + same RNG seed ⇒ byte-identical ledgers.

## Quickstart

```
pip install -e .[dev]            # stdlib core; pytest is the only dev dep
python -m colony init                            # create colony.db, seed gen-0
python -m colony run --ticks 10000               # run the simulation
python -m colony serve                           # (second terminal) live dashboard
python -m colony report                          # plain-text summary
python -m colony tree > tree.dot                 # family tree (Graphviz DOT)
python -m colony inspect 000001                  # one agent's genome/P&L/trades
python -m colony verify                          # audit the ledger invariants
python -m colony test                            # pytest, teed into records/tests/
```

`init` refuses to touch an existing database; `run` resumes exactly where the
last run stopped (RNG, arena and per-agent state are checkpointed every tick).
Interrupting a run with Ctrl-C is safe.

## What you are looking at

- **Treasury** is the north-star KPI: the house account that funded gen-0.
  Agents pay proportional **rent** every tick, house-funded agents repay a
  small seed quota (0.15×), and every agent's entire estate returns to the
  treasury at death (**senescence is the quota** — there is no mid-life tax).
  Treasury above its initial capitalization means every deployed cent has been
  recovered *and* profit is banked.
- **The arena** (`Petri Dish`) is a scripted price path through market regimes
  (trend, mean-reversion, crash). Agents trade against it at the scripted
  price plus fees; they cannot move the price.
- **Three archetypes**: `momentum`, `mean_revert`, and `sitter` — the
  deliberate do-nothing control that the never-trader stagnation rule must
  drive extinct.
- **Immigration** makes extinction impossible: whenever population falls below
  the floor and the treasury can afford a seed, the treasury spawns an
  immigrant (a mutated hall-of-fame genome or a fresh random one), recycling
  money the colony already returned.

## The Observatory

`python -m colony serve` (default port 8477) serves a single-file, read-only
dashboard at `http://127.0.0.1:8477/`:

1. **Strata chart** — stacked archetype shares over time with regime bands
   behind them; the colony's evolutionary history reads like sediment layers,
   and a regime flip is visible as a stratum changing color.
2. KPI row — treasury (with an "all capital recovered" badge), system total,
   colony wealth, money extracted from the market, population, and live
   invariant status.
3. Wealth, price/population charts; death-cause bars; diversity sparkline;
   outstanding-debt gauge; a leaderboard whose rows open an agent inspector.

The web layer opens SQLite read-only and answers GET only — control stays in
the CLI. The single external asset is Chart.js from a CDN; with the CDN
unreachable every number and table still renders (charts are absent).

Plain-text **records** land in `records/` (`runs/`, `experiments/`, `tests/`),
each with a reproducibility header (UTC time, git commit, full config, seed);
`records/INDEX.txt` accumulates one summary line per record. Records are
append-only and never overwritten.

## Experiments

```
python -m experiments.profit_matrix    # environment pre-check — run FIRST
python -m experiments.regime_flip      # the flagship adaptation experiment
```

The profit matrix verifies each archetype earns/loses where it should in each
pure regime (momentum makes money in trends and bleeds in mean-reversion, and
vice versa). If its signs are wrong, adaptation is impossible — fix the arena,
not the GA.

The regime flip runs 3,000 ticks of `trend_up` then 5,000 ticks of
`mean_revert` on seeds {42, 7, 2026}. A passing result shows, on **every**
seed: the living mean_revert share rising by ≥ 20 percentage points, total
system wealth above its start, and the treasury above its initial
capitalization. Both experiments print verdict tables and write records.

## Money conservation, stated plainly

Every movement of money is one ledger row with a debit and a credit account.
There is no other way money moves. At all times,

```
SUM(all account balances) == initial_treasury_cents
```

and every cached balance equals the ledger-derived sum for its account. The
invariant is verified every 100 ticks (every tick in debug mode); any
violation raises and halts the run. `colony verify` audits it on demand.

## Safety by construction

- **Simulation only.** Virtual money; no exchange APIs, no payment rails,
  no network calls in the core (the dashboard is a localhost read-only view).
- **No self-modification.** Genomes change only between generations, via the
  orchestrator's genetic operators; agents cannot rewrite themselves or the
  rules.
- **Airtight accounting.** Double-entry ledger, integer cents, conservation
  checked continuously, crash-on-violation.
- v2 seams (an LLM cognition layer, paper-trading arenas, live treasury
  withdrawal) are documented interfaces only — none of that code exists here.
