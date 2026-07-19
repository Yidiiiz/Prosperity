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
- **Three archetypes** (`momentum`, `mean_revert`, `sitter` — the deliberate
  do-nothing control) share three universal **gate genes**: a volatility gate
  (don't play flat tapes), a trades-per-day throttle (rolling 24h fill
  window), and a 24-bit UTC active-hours mask. Gates block opens only;
  closing is always allowed. Evolution decides when *not* to trade.
- **Immigration is budget-capped**: the treasury reseeds the population from
  a token bucket accruing at `immigration_budget_apr_bps` (default 20%/yr of
  initial treasury). When the budget is exhausted the population honestly
  sits below the floor — visible on the dashboard — instead of the treasury
  churning itself into life support.

## The Observatory

`python -m colony serve` (or the daemon's `--port`) serves a single-file,
read-only dashboard at `http://127.0.0.1:8477/`:

- **The Money Strip**: EXTRACTED (audited cash pulled from the market —
  today, this hour, per second; the number is allowed to be red), CASH
  (treasury, colony cash), MARKED (position value, outlined, labeled
  *unrealized* — never summed with cash).
- **Liveness chips**: feed LIVE/STALE/RECONNECTING, ticks-behind, invariant
  badge, last audit ✓/✗, immigration-budget gauge.
- **Strata chart** — stacked archetype shares over wall-clock time with
  regime bands and UTC day rules; the colony's history reads like sediment.
- **Trade tape** — the last 50 fills, streamed live.
- Wealth/price charts, death causes, diversity, a leaderboard opening an
  agent inspector with an inline collapsible ancestry chain.

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
  rules.
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
