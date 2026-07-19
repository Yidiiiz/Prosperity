# darwin-wallet

An evolutionary colony of autonomous agents, each with a strictly isolated
wallet, acting in a market arena. Profitable agents reproduce by **mitosis**
— funding their children out of their own profits — and unprofitable agents
go bankrupt and die. Selection, crossover, mutation and immigration drive the
colony toward profit with no human tuning. Every micro-dollar is integer
money in a double-entry SQLite ledger, and the whole system is deterministic:
same config + same RNG seed ⇒ byte-identical ledgers.

v2.0 makes the colony **always-on**: a supervised daemon trades the live
1-second BTC tape (paper only), audits its own history nightly against an
offline replay twin, and serves a phone-usable observatory. Money is
micro-dollars (1 $ = 1,000,000 u) so second-scale economics don't round to
zero; config speaks wall time (`_seconds`/`_hours`/`_days`, annualized rent)
so the same colony definition runs at day, minute, or second bars.

v3.0 is the **profitmaker economy**: every replay verdict is measured against
buy-and-hold on the same tape at the same costs (tiers: **ALPHA** beats
buy-and-hold, **CASH** beats initial, **EXPECTED-FAIL** made no money with
sound machinery; **FAIL** means the machinery broke — the only failure);
agents are ranked by **realized** P&L from the ledger, not marks; proven
genomes are admitted to a **bank**, certified out-of-sample by a frozen solo
probe on a postdating window, and reused as immigrants in later colonies;
and treasury profit **compounds** — a one-way high-water ratchet redeploys
half of each new high into the immigration budget.

## Quickstart (simulation)

```
pip install -e .[dev]            # stdlib core; pytest is the only dev dep
python -m colony init                            # create colony.db, seed gen-0
python -m colony run --ticks 10000               # run the simulation
python -m colony serve                           # (second terminal) live dashboard
python -m colony report                          # plain-text summary
python -m colony inspect 000001                  # one agent's genome/P&L/trades
python -m colony verify                          # audit the ledger invariants
python -m colony test                            # pytest, teed into records/tests/
```

`init` refuses to touch an existing database; `run` resumes exactly where the
last run stopped (RNG, arena and per-agent state are checkpointed). A hard
kill at any instant is safe: live configs pin `flush_every 1`, so the database
is always at a committed tick boundary.

## Quickstart (the always-on daemon)

```
python -m colony --db live.db daemon                 # config.live.json by default
python -m colony daemon status                       # health probe: exit 0/1/2
python -m colony --db live.db audit                  # replay-twin audit, on demand
python -m colony daemon clear-audit                  # operator clears a CRITICAL latch
```

One process does everything: it supervises the feed subprocess
(`tools/live_feed.py`, Binance `@kline_1s` websocket — public market data, no
key, no orders), consumes the journal one appended row per tick, verifies
conservation **every tick**, writes a health sidecar served at
`/api/health`, and after each UTC-midnight segment rotation replays the
closed day offline through the replay arena and compares ledger hashes. A
mismatch is a CRITICAL incident (`grep '^!!' records/INDEX.txt`) that latches
until an operator clears it — but the daemon keeps running: an audit failure
is an alarm about the past, not a reason to lose the present.

The journal is a directory of daily segments (`data/journal/YYYY-MM-DD.csv`,
sealed with a `.sha256` on rotation). A stale feed pauses the colony — the
wall clock paces, the journal decides; only operators stop it. Feed gaps are
counted and reported, never errors: the colony simply didn't tick.

## The economy, briefly

- **Treasury** is the north-star KPI: the house account that funded gen-0.
  Agents pay **rent** (an annual rate, `rent_apr_bps`, charged per tick),
  house-funded agents repay a seed quota (0.15×), and every estate returns to
  the treasury at death. Treasury above initial capitalization means every
  deployed micro-dollar was recovered *and* profit is banked.
- **The venue is honest**: taker fees plus a bid/ask spread charged at the
  fill (rounded against the agent), and orders decided at bar N fill at bar
  N+1's price. Same-bar fills exist only in the scripted Petri arena.
- **Four archetypes** (`momentum`, `mean_revert`, `breakout`, and `sitter` —
  the deliberate do-nothing control) share three universal **gate genes**: a
  volatility gate
  (don't play flat tapes), a trades-per-day throttle (rolling 24h fill
  window), and a 24-bit UTC active-hours mask. Gates block opens only;
  closing is always allowed. Evolution decides when *not* to trade.
- **Immigration is budget-capped**: the treasury reseeds the population from
  a token bucket accruing at `immigration_budget_apr_bps` (default 20%/yr of
  initial treasury). When the budget is exhausted the population honestly
  sits below the floor — visible on the dashboard — instead of the treasury
  churning itself into life support.

## The bank — proven genomes, reused

```
python -m colony bank list                       # every genome ever admitted
python -m colony bank show a1b2c3d4e5f6          # one genome, full history
python -m colony bank certify --tape data/spy_d.csv --from 2019-01-01
```

When a replay colony winds down, the terminal audit admits the top realized
profitmakers to an **append-only JSONL bank** as *candidates* — in-sample
performance proves nothing. A candidate is **certified** by a frozen solo
probe ($1,000, no rent, no evolution) on a window that must postdate its
admission window; overlap is refused, not warned about. Lapsed genomes stay
visible forever — the bank remembers its failures.

A new colony configured with `bank_path` copies the certified set into an
immutable `bank_snapshot` at init and draws half its immigrants from it
(unmutated clones, seeded at `champion_seed_multiple ×` the gen-0 stake, paid
from the same immigration budget — reputation buys size, not new money). A
running colony **never** reads the live bank: to refresh champions, start a
new colony. The bank stores parameter dictionaries, never code.

## The Observatory

`python -m colony serve` (or the daemon's `--port`) serves a single-file,
read-only dashboard at `http://127.0.0.1:8477/`:

- **The Money Strip**: EXTRACTED (audited cash pulled from the market —
  today, this hour, per second; the number is allowed to be red), CASH
  (treasury, colony cash), MARKED (position value, outlined, labeled
  *unrealized* — never summed with cash), and **vs B&H** (system total minus
  buy-and-hold on the same tape at the same costs — a red delta on a green
  treasury is the honest picture).
- **Liveness chips**: feed LIVE/STALE/RECONNECTING, ticks-behind, invariant
  badge, last audit ✓/✗, immigration-budget gauge.
- **Strata chart** — stacked archetype shares over wall-clock time with
  regime bands and UTC day rules; the colony's history reads like sediment.
- **Trade tape** — the last 50 fills, streamed live.
- Wealth/price charts, death causes, diversity, a leaderboard (bank-descended
  agents wear a BANK badge) opening an agent inspector with origin and an
  inline collapsible ancestry chain, and a **Champions** panel: the bank
  snapshot this colony started from, with living descendants per champion.

Data arrives by Server-Sent Events (`/api/events`: coalesced summaries ≤1/s,
per-fill events, health changes) with automatic fallback to polling; series
are server-bucketed (`/api/timeseries?max_points=`) so a full 86,400-tick day
renders from under 100 KB. The web layer opens SQLite read-only and answers
GET only — control stays in the CLI. Under 720 px the grid collapses to one
column: an always-on colony gets checked from a phone.

## Feeds and data

```
python tools/fetch_binance_klines.py BTCUSDT 1m --days 365 -o data/btcusdt_1m.csv
python tools/fetch_market_data.py SPY -o data/spy_d.csv
python tools/live_feed.py BTCUSDT --journal data/journal            # 1s websocket
```

The only network code in the project lives in `tools/` (Binance public data
hosts: `data-api.binance.vision`, `data-stream.binance.vision`). The core
replays CSVs offline and stays fully deterministic; resume is guarded by
digests of the consumed price series — a changed tape refuses to resume.
Committed fixtures (`data/btcusdt_1m_fixture.csv`, `data/spy_d.csv`) keep the
entire test suite offline.

## Experiments

```
python -m experiments.profit_matrix     # environment pre-check — run FIRST
python -m experiments.regime_flip       # the flagship adaptation experiment
python -m experiments.real_market       # SPY dailies, $200k → $100 → $10
python -m experiments.minute_ladder     # BTC 1m, $200k → $1k → $10 (--parallel)
python -m experiments.live_soak --hours 24   # the acceptance soak (daemon + kill)
python -m experiments.walk_forward      # evolve on window k, certify on k+1
python -m experiments.bank_reuse --bank BANK.jsonl --csv TAPE --from DATE
```

Every experiment is per-seed, incremental (results print the moment a seed
finishes), and resumable via `--workdir`. Replay experiments end in a
**terminal audit**: every estate liquidated at the last real price, so
verdicts are audited cash vs initial — no mark-to-market hand-waving. The
minute ladder separates machinery from economics: a rung may be
EXPECTED-FAIL (sound machinery, negative economics, numbers recorded) — only
the machinery can truly fail. The soak starts the daemon, hard-kills it at a
random moment, verifies exact resume, and hands the verdict to the
replay-twin audit.

The v3 evidence experiments *measure* rather than demand: `walk_forward`
splits a tape into contiguous windows, evolves on each, certifies champions
on the next, and reports EDGE/NO-EDGE per seed (SPY 1993–2026: NO-EDGE — the
honest result); `bank_reuse` runs a with-bank vs without-bank A/B on a
held-out window that must not overlap any banked genome's history, and
reports the delta whichever way it points. Every replay record ends with the
mandatory footer: span, wall, annualized return (marked "(projected)" under
a year), and the buy-and-hold benchmark with delta.

## The frequency frontier (v4)

```
python -m experiments.frequency_grid --cells all --workdir work_grid --parallel
python -m experiments.frequency_grid --summarize --workdir work_grid
python -m experiments.frequency_grid --holdout <cell> --workdir work_grid   # fires ONCE
```

v4 asks one question: is there a trading cadence (second / minute / hourly /
daily) and asset (BTC, ETH, SPY, QQQ) where certified champions earn faster
than S&P-500 buy-and-hold over the same calendar window? Every cell is the
v3 walk-forward — evolve on window k, certify on k+1 by frozen solo probe —
judged against `spx_over` the same dates (never against SPY's long-run
average), with the final 20 % of every tape carved off as a one-shot holdout
before any window is cut. Intraday equities are absent by honesty, not
oversight: no free intraday equity tape of fetchable quality exists, so
crypto carries the intraday axis.

Measured (mean OOS %/yr vs SPY same-window, seeds 42/7/2026, base retail
costs 10 bps taker / 2 bps spread):

| cell    | verdict        | champions %/yr | SPY %/yr |
|---------|----------------|----------------|----------|
| eth_1d  | BEATS-SPX ×2, NO-EDGE ×1 | +16.2 .. +27.2 | +14.9 |
| btc_1d  | NO-EDGE (window majority) | +17.5 .. +33.3 | +9.7 |
| qqq_1d  | NO-EDGE        | +1.9 .. +2.5   | +8.0     |
| spy_1d  | NO-EDGE        | −0.6 .. −0.4   | +4.6     |
| btc_1h  | NO-EDGE        | +5.8 .. +9.4   | +22.2    |
| eth_1h  | NO-EDGE        | −6.4 .. −0.8   | +22.2    |
| btc_1m  | NO-EDGE        | 1 seed −89.7; 2 seeds zero champions | +4.0 |
| btc_1s  | NO-EDGE        | zero champions certified | — |

The gradient is monotone the wrong way for the trade-faster thesis: daily >
hourly ≫ minute > second. The cost arm (btc_1s at counterfactual 2/1 and
0/0 bps) shows second-scale trading fails on friction first and signal
second — even at free costs the best seed merely lost slower than a falling
yardstick. The one-shot holdout went to the best cell by pre-registered rule
(btc_1d, best OOS delta) and is the v4 headline: **HOLDOUT NO-EDGE — 0/3
seeds beat SPY** on 2024-10-06 → 2026-07-19 (champions −2.2/+1.8/−1.1 %/yr
vs SPY +16.2; BTC buy-and-hold itself +1.5). At retail costs on these
tapes, nothing here earns faster than the S&P out-of-sample — that map is
the deliverable, and it is cheaper than learning it with real money.

## The allocation bench (v5)

```
python -m experiments.allocation                       # all five families
python -m experiments.allocation --holdout <family>    # fires ONCE
```

v4 settled *how fast* (daily, and still not fast enough); v5 tests *what to
hold*: four deterministic daily strategy families over SPY/QQQ/BTC/ETH —
dual momentum (hold the trailing-L winner or cash), trend (asset above its
SMA or cash), equal-weight rebalancing, volatility targeting — plus a
`best_bh` beta control that just buys last window's best asset. Parameter
grids were pre-declared in BUILD_SPEC_V5.md; train window k selects, the
frozen pick runs on window k+1; signals lag fills by one day; every fill
pays base venue costs. No leverage, no seeds (nothing random to seed).

Measured (7 test windows on 2017-08-17 → 2024-10-01, verdict by strict
window majority vs SPY same-window):

| family        | verdict   | windows beat SPY | mean OOS delta |
|---------------|-----------|------------------|----------------|
| dual_momentum | BEATS-SPX | 5/7              | +122.2 pp/yr¹  |
| equal_weight  | BEATS-SPX | 4/7              | +53.3 pp/yr¹   |
| trend         | BEATS-SPX | 4/7              | +0.5 pp/yr     |
| vol_target    | NO-EDGE   | 2/7              | −3.7 pp/yr     |
| best_bh (control) | NO-EDGE | 3/7            | −5.1 pp/yr     |

¹ outlier-heavy: one 2020–21 window where momentum rode BTC/ETH to 7×.

The control failing while momentum certifies means the majority is not
asset selection in a costume. The one-shot holdout (final 448 days,
2024-10-02 → 2026-07-17 — the same flat-crypto stretch that killed v4's
btc_1d) went to dual_momentum by the pre-registered rule and is the first
positive holdout in this repository: **[L=252] $10,000 → $16,871.76
(+33.99 %/yr) vs SPY $13,035.91 (+15.99 %/yr), delta +18.00 pp/yr**, and it
beat every single-asset buy-and-hold it could have hidden in (best was QQQ
at $14,394). One window, one shot, 1.79 years: evidence, not proof —
cross-asset momentum is the literature's most robust anomaly and also its
most famous crasher. `data/holdout/alloc.SHOT` forbids reruns; a second
look requires data that postdates 2026-07-19.

## Money conservation, stated plainly

Every movement of money is one ledger row with a debit and a credit account.
There is no other way money moves. At all times,

```
SUM(all account balances) == initial_treasury_u
```

and every cached balance equals the ledger-derived sum for its account. A
fast O(accounts) check runs on cadence (every tick under the daemon); the
full O(ledger) audit runs at run boundaries and on `colony verify`. Any
violation raises and halts the run. The nightly replay-twin extends the same
guarantee across days: the live ledger must be byte-identical to an offline
replay of its own journal.

## Safety by construction

- **Simulation only.** Virtual money; no payment rails and **no
  order-placement code anywhere in the repository**. The core makes no
  network calls; the only network code is in `tools/`, and it only *reads*
  public market data.
- **No self-modification.** Genomes change only between generations, via the
  orchestrator's genetic operators; agents cannot rewrite themselves or the
  rules. The bank stores parameter dictionaries, never code, and a running
  colony never reads it.
- **Airtight accounting.** Double-entry ledger, integer micro-dollars,
  conservation checked continuously, crash-on-violation.
- Remaining seams (an LLM cognition layer, treasury withdrawal, order
  execution) stay documented interfaces only — none of that code exists here.

## CI

GitHub Actions runs the full suite on **Windows and Linux** (the daemon's
pid-liveness, subprocess supervision, and hard-kill resume tests included),
plus a 500-tick smoke simulation with a full ledger audit. The throughput
benchmark enforces ≥250 ticks/s in CI (≥500 documented on the reference
laptop in `records/`).
