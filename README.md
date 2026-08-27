# Prosperity

An evolutionary colony of autonomous trading agents, and a research bench for
testing whether any of them actually has an edge.

Each agent owns a strictly isolated wallet and trades in a simulated market
arena. Profitable agents reproduce by mitosis, funding their children out of
their own profits; unprofitable agents go bankrupt and die. Selection,
crossover, mutation and immigration drive the colony toward profit with no
human tuning.

Every micro-dollar is integer money in a double-entry SQLite ledger, and the
system is deterministic: the same configuration and RNG seed produce
byte-identical ledgers.

**Simulation only.** The project uses virtual money and contains no
order-placement code. See [Scope and safety](#scope-and-safety).

---

## Contents

- [Key properties](#key-properties)
- [Installation](#installation)
- [Quick start](#quick-start)
- [The always-on daemon](#the-always-on-daemon)
- [The economy](#the-economy)
- [The genome bank](#the-genome-bank)
- [The Observatory](#the-observatory)
- [Market data](#market-data)
- [Research benches](#research-benches)
- [Results](#results)
- [Methodology](#methodology)
- [Money conservation](#money-conservation)
- [Testing and CI](#testing-and-ci)
- [Scope and safety](#scope-and-safety)
- [Repository layout](#repository-layout)

## Key properties

| Property | Detail |
|---|---|
| **Deterministic** | The same config and seed produce byte-identical ledgers. Resume is guarded by digests of the consumed price series; a changed tape refuses to resume. |
| **Zero dependencies** | The core is pure Python 3.11+ standard library with SQLite. `pytest` is the only development dependency. |
| **Airtight accounting** | Double-entry ledger in integer micro-dollars. Conservation is verified continuously, and any violation halts the run. |
| **Crash-safe** | Live configurations pin `flush_every 1`, so the database always sits at a committed tick boundary. A hard kill at any instant is recoverable. |
| **Offline by default** | Only `tools/` touches the network, and only to read public market data. Committed fixtures keep the entire test suite offline. |

## Installation

```bash
pip install -e .[dev]
```

Requires Python 3.11 or newer.

## Quick start

```bash
python -m colony init                 # create colony.db and seed generation 0
python -m colony run --ticks 10000    # run the simulation
python -m colony serve                # live dashboard (separate terminal)
python -m colony report               # plain-text summary
python -m colony inspect 000001       # one agent's genome, P&L and trades
python -m colony verify               # audit the ledger invariants
python -m colony test                 # test suite, teed into records/tests/
```

`init` refuses to touch an existing database. `run` resumes exactly where the
previous run stopped, with RNG, arena and per-agent state all checkpointed.

## The always-on daemon

```bash
python -m colony --db live.db daemon      # uses config.live.json by default
python -m colony daemon status            # health probe; exits 0, 1 or 2
python -m colony --db live.db audit       # replay-twin audit, on demand
python -m colony daemon clear-audit       # clear a latched CRITICAL incident
```

A single process supervises the feed subprocess (`tools/live_feed.py`, a
Binance `@kline_1s` websocket carrying public market data only), consumes the
journal one appended row per tick, verifies conservation every tick, and writes
a health sidecar served at `/api/health`.

After each UTC-midnight segment rotation it replays the closed day offline
through the replay arena and compares ledger hashes. A mismatch raises a
CRITICAL incident that latches until an operator clears it, but the daemon
keeps running: an audit failure is an alarm about the past, not a reason to
lose the present.

The journal is a directory of daily segments (`data/journal/YYYY-MM-DD.csv`),
each sealed with a `.sha256` on rotation. A stale feed pauses the colony. Feed
gaps are counted and reported rather than raised as errors.

## The economy

**Treasury** is the primary metric: the house account that funded generation
zero. Agents pay rent as an annual rate (`rent_apr_bps`) charged per tick,
house-funded agents repay a seed quota, and every estate returns to the
treasury on death. A treasury above its initial capitalization means every
deployed micro-dollar was recovered and profit was banked on top.

**The venue is modelled honestly.** Taker fees plus a bid/ask spread are
charged at the fill and rounded against the agent. Orders decided at bar *N*
fill at bar *N+1*. Same-bar fills exist only in the scripted test arena.

**Four archetypes** — `momentum`, `mean_revert`, `breakout`, and `sitter` (a
deliberate do-nothing control) — share three universal gate genes: a volatility
gate, a trades-per-day throttle on a rolling 24-hour window, and a 24-bit UTC
active-hours mask. Gates block opens only; closing is always permitted, so
evolution decides when *not* to trade.

**Immigration is budget-capped.** The treasury reseeds the population from a
token bucket accruing at `immigration_budget_apr_bps`. When the budget is
exhausted the population sits below its floor visibly, rather than the treasury
churning itself into life support.

## The genome bank

```bash
python -m colony bank list
python -m colony bank show a1b2c3d4e5f6
python -m colony bank certify --tape data/spy_d.csv --from 2019-01-01
```

When a replay colony winds down, its terminal audit admits the top realized
profitmakers to an append-only JSONL bank as *candidates* — in-sample
performance proves nothing on its own. A candidate is **certified** by a frozen
solo probe on a window that must postdate its admission window; overlapping
windows are refused rather than warned about. Lapsed genomes stay visible
permanently, so the bank records its failures alongside its successes.

A colony configured with `bank_path` copies the certified set into an immutable
snapshot at init and draws half its immigrants from it. A running colony never
reads the live bank; refreshing champions requires starting a new colony. The
bank stores parameter dictionaries, never code.

## The Observatory

`python -m colony serve` publishes a single-file, read-only dashboard at
`http://127.0.0.1:8477/`:

- **Money strip** — extracted cash, treasury and colony cash, marked position
  value (labelled unrealized, never summed with cash), and the delta versus
  buy-and-hold on the same tape at the same costs.
- **Liveness chips** — feed status, ticks behind, invariant badge, last audit
  result, immigration-budget gauge.
- **Strata chart** — stacked archetype shares over wall-clock time, with regime
  bands and UTC day rules.
- **Trade tape** — the last 50 fills, streamed live.
- **Leaderboard and inspector** — per-agent origin and an inline ancestry
  chain; bank-descended agents are badged.

Data arrives by Server-Sent Events with automatic fallback to polling. Series
are bucketed server-side, so a full 86,400-tick day renders in under 100 KB.
The web layer opens SQLite read-only and answers GET only; control stays in the
CLI. Below 720 px the grid collapses to a single column.

## Market data

```bash
python tools/fetch_binance_klines.py BTCUSDT 1m --days 365 -o data/btcusdt_1m.csv
python tools/fetch_market_data.py SPY -o data/spy_d.csv
python tools/live_feed.py BTCUSDT --journal data/journal
```

All network code lives in `tools/` and reads public market data only. The core
replays CSV tapes offline and remains fully deterministic. Large fetched tapes
are gitignored; their digests are pinned in each experiment record.

## Research benches

The colony answers whether agents can evolve toward profit. The benches answer
the harder question: does any of this beat simply buying and holding the index?

```bash
python -m experiments.profit_matrix          # environment pre-check; run first
python -m experiments.walk_forward           # evolve on window k, certify on k+1
python -m experiments.allocation15 --mode all
```

Each bench is specified in a `BUILD_SPEC_V*.md` document written **before** the
run, so its families, parameter grids and success criteria are fixed in
advance. Findings are recorded in [DECISIONS.md](DECISIONS.md).

| Bench | Question | Outcome |
|---|---|---|
| v4 Frequency frontier | Does trading faster help? | No edge at any frequency; higher frequencies certified fewer champions |
| v5 Allocation | Can asset rotation beat the index? | Dual momentum, +18.0 pp/yr on its holdout — the first validated edge |
| v6 Universe | What does rotation need to work? | The edge is **dispersion**; it fails on correlated ETF sets |
| v7 Dispersion gate | Can dispersion be gated explicitly? | Frontier in-grid; forward holdout armed |
| v8 Regime | Do inverse ETFs help in a bear market? | No — daily-reset decay; moving to safety beats shorting |
| v9–v10 Market timing | Does a market-timing brake help? | No; a careful implementation halved the drag but never reversed it |
| v11 Regime-gated rotation | Does the brake help momentum? | Momentum rescues the brake, but the brake drags momentum |
| v12 Risk budget | Does volatility-targeted sizing help? | No — sizing removed ~78% of the return to shave ~15% of the drawdown |
| v13 Cross-section | Does momentum work across stocks? | Yes: beats a survivorship-neutral control in 9 of 9 windows |
| v14 Survivorship stress | How much of that is survivor bias? | The bias **inflates**; the residual edge is bull-regime and robust to simulated delistings |
| v15 Breadth indicators | Was the weak market-timing proxy the problem? | No — a faithful breadth indicator differs measurably from the proxy yet reaches the same verdict |

Results are summarized above; each bench's full record, including the outcomes
that contradicted the original hypothesis, is in `DECISIONS.md` and the
per-version specifications.

## Results

Every figure below is measured rather than projected, and is net of the venue's
fees and spread. Read the evidence column before the returns column: only one
result in this repository has ever survived a true out-of-sample holdout, and
everything else should be discounted accordingly.

| Evidence tier | What it means |
|---|---|
| **Holdout** | Fired once, on data never used for selection, guarded by a committed `.SHOT` file that makes reruns refuse. The strongest claim available here. |
| **Walk-forward** | Parameters chosen on window *k* and judged on window *k+1*. Out-of-sample per window, but the family and the grid were still chosen by someone who had already seen the data. |
| **In-sample** | A full-span sweep. Useful for locating a mechanism, worthless as a performance claim. |

### Ranked: the stock cross-section, 1999–2026

One universe, one span (6,732 trading days), one benchmark, so these rank
against each other honestly. **SPY buy-and-hold returned +6.61 %/yr with a
56.5 % maximum drawdown** over the same window.

| # | Approach | Walk-forward vs SPY | CAGR | maxDD |
|---|---|---|---|---|
| 1 | **Cross-sectional momentum** — own the strongest few names, rebalanced monthly | **+24.96 pp/yr**, 9/9 windows | +20.58 % | 58.0 % |
| 2 | **Green-line breakouts, widened stops** — breakouts ranked by strength, stop ~30 % or ~2.5× volatility | **+10.12 pp/yr**, 8/9 | +21.58 % | 58.7 % |
| 3 | **Own the entire universe, equal weight** — zero selection skill | +4.60 pp/yr, 9/9 | +12.46 % | 52.0 % |
| 4 | **Breadth-gated momentum** — the same momentum book, gated on a market-health indicator | +1.29 pp/yr, 5/9 | +9.61 % | **30.9 %** |
| 5 | **Quality-selected breakouts** — breakouts filtered by strength, 10 % stop | +0.28 pp/yr, 5/9 | +8.86 % | 52.6 % |
| 6 | **Green-line breakouts, 5 % stop** — the textbook rule as published | −1.66 pp/yr, 5/9 | +3.67 % | 59.9 % |
| 7 | **Moving-average fan ranking** — the "good-looking chart" screen | −2.71 pp/yr, 3/9 | −0.60 % | 89.8 % |

The walk-forward column re-selects parameters every window; the CAGR and
drawdown columns are a single fixed parameterization held across the whole
span, so the two describe related but different things.

**Three corrections belong on top of that table**, and they matter more than the
ordering:

1. **Roughly a quarter of the leader's margin is survivorship, not skill.** Row 3
   owns every surviving name with no selection skill at all and still beats the
   index by 4.60 pp/yr, purely because the universe is a list of companies that
   are still alive. Measured against that control rather than against SPY, the
   leader's honest margin is about **+20 pp/yr**, not +25.
2. **The leader earns all of it in rising markets.** Split by regime, its edge
   over the zero-skill control is +3.54 in bull markets and −1.06 in bear ones.
   It offers no downside protection; the 58 % drawdown is worse than the index's.
3. **None of it is out-of-sample at the family level.** The forward holdout for
   row 1 is registered but not yet ripe.

Row 4 is the one honest case for a market-timing gate: it costs about 11 pp/yr
against holding the momentum book, but it nearly halves the worst drawdown
(30.9 % against 58.0 %). Whether that trade is worth making is a preference
about risk, not a claim about return.

### Other universes

Not comparable with the table above — different assets, different spans — but
they are where the strongest and weakest results in the project both live.

**High-dispersion universe** (8 assets including crypto, 2017–2026), mean
walk-forward delta vs SPY:

| Approach | Result |
|---|---|
| Momentum rotation, full size | +105.46 pp/yr, 6/9 windows |
| Momentum rotation, regime-gated | +61.03 pp/yr, 7/9 |
| Momentum rotation, risk-parity sized | +25.06 pp/yr, 4/9 |
| Momentum rotation, volatility-targeted | +23.45 pp/yr, 5/9 |
| Market timing alone, no rotation | −3.35 pp/yr |
| Buy-and-hold the best single asset | −11.34 pp/yr |

These are the project's largest numbers and its least transferable: the edge is
**dispersion**, and it is powered by crypto's volatility over a span that
contains one of the largest bull runs on record.

**Index-timing universe** (ETFs, 2010–2022): nothing beat buy-and-hold. The
least-bad family lost 1.57 pp/yr; timing into inverse ETFs lost 7.56 pp/yr.

### Holdouts fired

| Strategy | Result | Verdict |
|---|---|---|
| Dual momentum (v5) | **+18.00 pp/yr** | The one clean out-of-sample win |
| Buy-and-hold QQQ (v6) | +5.22 pp/yr | Beat SPY, but it is passive beta |
| Regime-switch to safety (v8) | +10.62 pp/yr | Disclosed as contaminated — the span was chosen knowing it held a bear market |
| Market-timing switch (v10) | +0.30 pp/yr | Nominally positive, substantively no edge: it loses at 2× costs and its drawdown is worse than SPY's |
| Sector momentum (v9) | −11.82 pp/yr | No edge |
| Bitcoin daily (v4) | No edge | 0 of 3 seeds |

### What did not work

Ordered by how thoroughly each was tested before being abandoned:

1. **Downside timing**, in five separate forms across v8–v11 — inverse ETFs,
   percentage stops, a market-health gate, and that gate fused onto momentum.
   Every one either lost to buy-and-hold or clipped the edge it was protecting.
2. **Risk budgeting by position size** (v12) — removed about 78 % of the return
   to shave about 15 % of the drawdown, and lost on the risk-adjusted metric it
   was specifically built to win.
3. **Trading faster** (v4) — no edge at any frequency; the higher frequencies
   certified fewer champions, not more.
4. **Leveraged inverse ETFs** (v9) — a −3× fund tracked its daily target
   faithfully at −2.96 beta, and still decayed to near zero over twelve years
   through daily reset. A short-horizon instrument, never a holding.
5. **The 5 % trailing stop** (v15) — the worst rule in a 156-cell sweep; not one
   of its twelve cells beat buy-and-hold. The optimum sits roughly six times
   wider.
6. **Chart-pattern screening** (v15) — ranking breakout candidates by moving-average
   fan alignment scored below ranking them by plain momentum, and below
   taking every breakout indiscriminately.
7. **Relative strength** (v15) — not merely weak but mathematically empty as a
   same-day cross-sectional ranker: dividing every candidate by the same index
   return cannot reorder them, and it reproduced plain momentum to the decimal.

## Methodology

The benches are built so that a negative result is as publishable as a positive
one, and so that an accidental positive is hard to manufacture.

- **Pre-registration.** Families, grids and the frontier metric are declared in
  the specification before the run. Grid order breaks ties, so selection cannot
  drift toward a favoured parameter.
- **Walk-forward validation.** A training window selects parameters by audited
  final cash; the frozen selection is then tested on the *next* window and
  judged against buy-and-hold over that same window.
- **Holdout discipline.** One-shot historical holdouts are guarded by a
  committed `.SHOT` file whose presence makes reruns refuse. Where every
  historical span is already spent, a forward holdout is registered instead: a
  `.FORWARD` declaration names the family, parameters, cutoff date and minimum
  row count *before the data exists*, and fires only on rows postdating it.
- **Bias controls.** A survivorship-shaped universe is measured against a
  zero-skill control sharing the identical universe, so the bias cancels and
  only the residual skill is claimed.
- **Cost ladders.** Every headline result is re-run at 2× and 5× costs as a
  diagnostic. Cost ladders never change a verdict; they qualify it.
- **Terminal audits.** Replay experiments liquidate every estate at the last
  real price, so verdicts are audited cash rather than mark-to-market.

## Money conservation

Every movement of money is a single ledger row with a debit and a credit
account. There is no other path. At all times:

```
SUM(all account balances) == initial_treasury_u
```

Each cached balance must also equal the ledger-derived sum for its account. A
fast O(accounts) check runs on cadence; the full O(ledger) audit runs at run
boundaries and on `colony verify`. Any violation raises and halts the run.

The nightly replay twin extends the guarantee across days: the live ledger must
be byte-identical to an offline replay of its own journal.

## Testing and CI

```bash
python -m pytest -q
```

391 tests. GitHub Actions runs the full suite on **Windows and Linux** —
including the daemon's pid-liveness, subprocess supervision and hard-kill
resume tests — plus a 500-tick smoke simulation with a full ledger audit. A
throughput benchmark enforces at least 250 ticks per second in CI.

## Scope and safety

- **Simulation only.** Virtual money. There are no payment rails and no
  order-placement code anywhere in the repository. The core makes no network
  calls; the only network code is in `tools/`, and it only reads public market
  data.
- **No self-modification.** Genomes change only between generations, through
  the orchestrator's genetic operators. Agents cannot rewrite themselves or the
  rules, and the bank stores parameter dictionaries, never code.
- **No leverage or shorting** in the allocation benches: exposure never exceeds
  1.0, and positions are long-only.
- Remaining seams — treasury withdrawal and order execution — are documented
  interfaces only. None of that code exists in this repository.

## Repository layout

| Path | Contents |
|---|---|
| `colony/` | Core package: agents, evolution, ledger, bank, daemon, web dashboard |
| `colony/arenas/` | Market arenas, including the offline replay arena |
| `experiments/` | Research benches, one module per version |
| `tools/` | The only network code: data fetchers and the live feed |
| `tests/` | Test suite |
| `records/` | Append-only run records; `INDEX.txt` is the master index |
| `data/` | Price tapes, fixtures, journal segments, holdout declarations |
| `BUILD_SPEC_V*.md` | Per-version specifications, written before each run |
| `DECISIONS.md` | Numbered record of every design decision and finding |
