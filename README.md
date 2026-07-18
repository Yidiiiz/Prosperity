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

## v2: real market data

The arena is pluggable, and v2 adds **Replay** — the colony evolves against
real historical prices instead of the scripted Petri Dish:

```
python tools/fetch_market_data.py SPY -o data/spy_d.csv   # once (network)
python -m colony init --config config.spy.json            # 33 years of SPY
python -m colony run --ticks 999999                       # stops at data end
```

The fetch script is the only network code in the project; the simulation
replays the CSV offline, one trading day per tick, and stays fully
deterministic (resume is guarded by a digest of the price series — a changed
CSV refuses to resume). `lot_denominator` scales the asset so one lot is an
affordable slice of a share. The fetched SPY history (1993–2026) is committed
under `data/` so results reproduce without a network.

Small-stakes colonies (down to $10.00 total) are supported via
`'small_stakes': true`, which waives the lot-granularity floor. Expect
different economics at that scale: the 1-cent integer floor makes the minimum
fee ~100 bps on a $1 trade, and rent rounds to 0.

## Experiments

```
python -m experiments.profit_matrix    # environment pre-check — run FIRST
python -m experiments.regime_flip      # the flagship adaptation experiment
python -m experiments.real_market      # v2: real SPY data, $200k then $10
```

The profit matrix verifies each archetype earns/loses where it should in each
pure regime (momentum makes money in trends and bleeds in mean-reversion, and
vice versa). If its signs are wrong, adaptation is impossible — fix the arena,
not the GA.

The regime flip runs 3,000 ticks of `trend_up` then 5,000 ticks of
`mean_revert` on seeds {42, 7, 2026}. A passing result shows, on **every**
seed: the living mean_revert share rising by ≥ 20 percentage points, total
system wealth above its start, and the treasury above its initial
capitalization. All experiments print verdict tables and write records.

The real-market experiment is a **capitalization ladder** on 33 years of
actual SPY closes, run in order: $200,000 virtual, then $100.00 total, then
$10.00 total. Every rung ends with a **terminal audit** — when the data runs
out, every living agent is liquidated at the last real price and its whole
estate returns to the treasury, so the audited number is hard cash, not
mark-to-market. The top two rungs must survive to the end of history and end
with audited cash above initial (every seed passes: +4.5–6.3% at $200k,
+28.5–1,853% at $100); the $10 rung must survive with invariants intact,
and its economics are reported per seed (+26.5%, −67.3%, −4.1% — the 1-cent
integer floor makes profit seed-dependent at that scale).

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
  no network calls in the core (the dashboard is a localhost read-only view;
  `tools/fetch_market_data.py` downloads historical CSVs and is the only
  network code in the repository).
- **No self-modification.** Genomes change only between generations, via the
  orchestrator's genetic operators; agents cannot rewrite themselves or the
  rules.
- **Airtight accounting.** Double-entry ledger, integer cents, conservation
  checked continuously, crash-on-violation.
- v2 delivers the replay arena (real historical data, still offline and
  deterministic). Remaining seams (an LLM cognition layer, live paper-trading
  feeds, treasury withdrawal) stay documented interfaces only — none of that
  code exists here.
