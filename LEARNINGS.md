# LEARNINGS — build report and requirements brief for the next spec

Audience: the author of BUILD_SPEC.md, drafting a clean v2.0 build. This
document contains only what you do not already have: what happened when the
spec was executed, the measured data, what was built beyond it, and the new
product requirements. Full provenance: `DECISIONS.md` (32 entries),
`records/` (append-only run/experiment records), 16 commits.

Target for the new build, in one sentence: **an always-on colony, ticking
every second against a live venue, doing micro-trades in sub-cent units,
with a UI that shows money being extracted in real time — and an audited
cash number backing every claim.**

---

## 1. Execution report

### 1.1 Outcome

- Spec implemented completely; all §13 acceptance criteria passed on the
  first full acceptance pass. 96 tests, stdlib-only core held, pytest the
  only dev dependency. Core landed ~1,770 non-blank lines vs the ~1,500
  target — overage is spec-mandated surface (schema, 7 endpoints, report
  formats), not abstraction.
- Flagship regime-flip: mean_revert share shift +82/+94/+95 pts across
  seeds {42, 7, 2026}; system wealth +32.7/+52.3/+43.7%; treasury
  +21.6/+37.4/+28.3% vs initial. Long Petri soak reached tick 32,000 with
  treasury +20.8% and zero invariant violations.
- Two extensions were then built and are in the repo (you have no spec for
  these; §2 has their results): a **replay arena** (real historical CSVs)
  and a **live arena** (real-time quotes via an append-only journal file),
  plus fetch/feed/verification tooling.

### 1.2 Spec ambiguities the next spec should settle explicitly

These all cost a judgment call (details in DECISIONS.md by number):

- Treasury genesis: creation vs transfer (#2) — genesis-as-creation makes
  both §4.4 invariants hold exactly; spec v2.0 should just state it.
- Runtime state persistence: the spec kept per-agent runtime state in
  memory. We added an `agent_state` table + `runs.state_json`, flushed
  every tick inside the tick transaction (#4) → exact resume after
  SIGINT/kill, proven byte-identical. **Mandate this**; always-on operation
  (§4) is impossible without it, and it repeatedly saved multi-hour runs.
- `max_age_ticks` equal to the first regime length put the gen-0
  senescence wave exactly on the regime boundary and muddied §13.2
  measurement (#23). Pick co-prime numbers.
- Zero-amount transfers: at small stakes rent rounds to 0; the ledger
  correctly refuses 0 rows, so the orchestrator must skip them (#27).
- Elitism cap mechanics, gate-failed breeders staying queued, newborn
  cooldown, gen-0 round-robin archetypes: #6–#12 record the resolutions
  that passed acceptance; adopt or overrule them explicitly.
- Records filenames are second-stamped; parallel processes collided. Add
  a disambiguator (we appended seeds) or millisecond stamps.

### 1.3 Execution process notes (what made the build go fast)

- The §12 build order (ledger → config → arena → agents → orchestrator →
  CLI → web) worked as written; keep it.
- Long experiments must be **resumable and parallelizable**: we gave the
  experiment runner a persistent `--workdir` (each seed's DB resumes
  exactly) and per-rung/per-seed CLI flags, then fanned seeds out as
  parallel processes. A 30-minute monolithic experiment that prints only
  at the end is an operational hazard; spec experiments as incremental,
  per-seed, resumable from the start.
- Environment: Windows 11, Python 3.14. WAL SQLite was flawless including
  concurrent read-only dashboard + writer. Two quirks: temp-dir cleanup
  needs `ignore_cleanup_errors=True` (open handles), and anything that
  matters must not depend on POSIX-only behavior.
- Perf baseline (single thread, midrange laptop): **~15–20 ticks/s at
  population 40–100**. An 8,422-tick run ≈ 8–10 min. Bottlenecks measured:
  per-tick full state flush, and `agents.cash()` issuing a SELECT per call
  several times per agent per tick. One run wrote 430k rent rows — ledger
  volume itself was never a problem.

## 2. Relevant data

### 2.1 Real-data capitalization ladder (33 years SPY daily, 8,422 ticks)

Every rung ends with a **terminal audit**: at data end, every living agent
is liquidated at the last real price and estates return to the treasury, so
results are cash, not mark-to-market. (Rationale: by conservation,
mid-run `treasury > initial` requires realized extraction to exceed the
colony's *retained* wealth — measured seed 42: $30.9k held vs $4.9k
realized — so the pre-audit criterion punishes holding the winning asset.)

| Capitalization | Audited cash vs initial, seeds {42, 7, 2026} | Verdict |
|---|---|---|
| $200,000 (100 × $1,000, lot = 1/100 share) | +4.7% / +4.5% / +6.3% | pass |
| $100 total (10 × $10, lot = 1/1000) | +40.9% / +28.5% / **+1,853%** | pass |
| $10 total (4 × $2.50) | +26.5% / −67.3% / −4.1% | machinery holds; profit seed-dependent |

Key measured facts behind them:

- **Fees consumed 63% of gross** at full stakes: ~$13k gross trading
  profit, $8.2k fees (20 bps + 1¢ minimum). Per-bar edge shrinks ~√(bar
  length); the fee model decides feasibility at faster cadences.
- **The 1¢ integer floor is what breaks $10 colonies**: minimum fee ≈
  100 bps on a $1 trade; rent rounds to 0; late-series granularity was 2–3
  affordable lots. Unit problem, not strategy problem.
- **Population sat at the floor** (40) on real data; immigration recycled
  ~$126k of seeds vs ~$136k residues returned — life support, not
  compounding. Breeding thresholds tuned to Petri's 12 bps/tick drift are
  far too demanding for real ~3.5 bps/day drift.

### 2.2 Live session (real-time BTC-USD, 5s polling, 100 ticks)

- Conservation exact; run resumable; **replay-twin verified**: replaying
  the session's journal offline through the replay arena reproduced the
  live ledger **byte-identically** (sha256 `cbc6b548c10d93d5` both sides).
  This property — the journal is the tape, the wall clock only decides
  *when* — is the foundation the always-on build should preserve.
- **Zero trades occurred**: Yahoo's quote refreshes coarser than 5s, so
  the tape had long flat stretches → stdev 0 → no signals. Lesson: tick
  cadence must match the *data source's* native cadence (see §4.3).

### 2.3 Data-source intelligence

- Yahoo daily history: must pin `period1=0&period2=…&interval=1d`
  (`range=max` silently degrades to monthly bars). ~8,400 SPY dailies OK.
- Yahoo intraday: ~7 days of 1m, ~60 days of 5m — smoke tests only.
- Stooq: behind a JavaScript challenge; unusable programmatically.
- For minute/second history and streams: exchange APIs (e.g. Binance
  public `/api/v3/klines` for years of 1m candles, websocket `@trade` /
  `@kline_1s` streams for live seconds; no API key for public data).

## 3. What to carry into v2.0 unchanged

- **The journal-tail boundary.** Network code (fetcher/feed daemon) writes
  an append-only CSV; the core only reads files. This one seam delivered
  offline determinism, live reproducibility, crash tolerance (torn-line
  handling), and free regression tapes. It scales to any cadence.
- The 5-method arena protocol + factory (petri/replay/live each ~90
  lines); SAVEPOINT births inside one-transaction ticks; per-tick flush →
  exact resume; the records + DECISIONS discipline; the sitter control
  group; conservation checked on cadence with crash-on-violation.

## 4. New requirements for the v2.0 spec

### 4.1 Time is a first-class config concept

`tick_seconds` in config; every lifecycle constant and rate expressed in
wall-time and converted at load. Measured why: `max_age_ticks 3000` is 12
years at day-bars but **50 minutes** at 1s; rent of 2 bps/tick is ~5%/year
daily but ~5,600%/hour at seconds (the live demo had to zero rent). The
dashboard should likewise show wall-clock axes, not tick numbers.

### 4.2 Sub-cent money for micro-trades

Promote the ledger unit from cents to **micro-dollars** (int64 headroom:
$9.2 trillion). All invariants carry over mechanically. Fee model becomes
per-venue: `{maker_bps, taker_bps, spread_bps, min_units (default 0)}` —
charge the spread even on paper, it is the dominant real cost at speed.
Add `fill_delay_ticks: 1` (decide at row N, fill at row N+1): one line of
execution-path spec that removes the most common source of fake intraday
alpha.

### 4.3 The always-on colony (the core new deliverable)

Replace "run --ticks N" sessions with a **daemon mode**:

- `colony daemon` runs indefinitely: supervises the feed subprocess
  (websocket client appending 1 row/second to the journal), auto-restarts
  it with backoff, resumes the colony exactly after any crash/reboot,
  rotates journals daily (`journal.YYYY-MM-DD.csv`, digest per segment,
  Live arena chains segments).
- Stale-feed handling as measured in v3: stale ≠ exhausted; pause and
  resume, never die.
- Health surface: uptime, ticks behind feed, last invariant check, feed
  gap counter — exposed via the existing read-only web layer.
- Throughput budget: live needs only ~1 tick/s, but replaying/auditing a
  day of seconds is 86,400 ticks — target **≥500 ticks/s** backtest so a
  day audits in ~3 minutes. The measured path from 15–20/s: cache agent
  cash in memory (balances are already maintained by every transfer —
  eliminate per-call SELECTs), batch per-tick equity marks into one query,
  make flush cadence configurable (`flush_every: N`; live keeps 1,
  backtests use 100+).
- Continuous verification: the daemon re-runs the replay-twin audit
  against each closed journal segment nightly; any hash mismatch is a
  page-the-operator event.

### 4.4 "Generating money every second" — spec it as a measured rate

Make the north-star metric an **audited extraction rate**: realized
arena-extraction (cash, not mark-to-market) per hour/day, computed from
the ledger, shown live, and confirmed by terminal audits on rolling
windows. The spec should *demand the measurement*, not assume the sign:
evolution can only select edges that exist in the data, and second-bars
are mostly noise + microstructure. Expect the first 1s ladders to fail
their audits with the current z-score genome — let those failures drive
genome expansion: volatility-regime gate genes (the flat-tape problem
becomes a gene), trade-rate throttles (fees are the predator at speed),
time-of-day masks, new archetypes (three touch points: `ARCHETYPES`,
`PARAM_BOUNDS`, `decide`). Keep the sitter: if do-nothing wins at some
cadence, that is a finding about the venue's costs, and it must surface,
not be tuned away.

Recalibrate the breeding economy for real drift while at it:
`repro_multiple` ~1.05–1.10 over baseline (or fund children from realized
profit above seed), and cap immigration spend per window so the treasury
cannot churn itself into permanent life support (§2.1).

### 4.5 Improved UI (Observatory v2)

Keep: read-only, localhost, single file, graceful no-CDN degradation.
Change, in priority order:

1. **Push, not poll**: server-sent events for tick updates (the 2s polling
   loop is visibly laggy at 1s ticks and wasteful at idle).
2. **A live money strip** as the hero element: audited cash extracted
   (today / this hour / per-second rate), realized vs mark-to-market shown
   as *separate numbers* — the v1 treasury tile confused people the moment
   all capital was deployed ($0.00 is correct and looks broken).
3. Wall-clock time axes everywhere (follows from `tick_seconds`), with
   session/day boundaries marked; downsampled timeseries endpoint (LTTB or
   simple bucketing) so an 86k-tick day renders in <100 KB.
4. A **trade tape** panel (last N fills with agent, side, size, price) —
   at micro-trade cadence the tape *is* the product demo.
5. Colony liveness affordances: feed status (live / stale Ns / reconnect),
   ticks-behind indicator, invariant badge with last-checked time.
6. Lineage view upgrade: the DOT export was barely used; an inline
   collapsible tree (or sediment/strata drill-down by generation) earns
   its place better.
7. Mobile-usable layout — an always-on colony gets checked from a phone.

### 4.6 Proposed v2.0 acceptance criteria

1. Minute-bar capitalization ladder (Binance 1m history) with terminal
   audits; explicit expected-fail documentation if the genome finds no
   edge at a rung.
2. **24-hour continuous live soak** at 1s ticks: zero invariant
   violations, zero missed-row gaps unexplained by feed outages, daemon
   survives at least one induced kill -9 with exact resume, nightly
   replay-twin audit passes.
3. Backtest throughput ≥500 ticks/s at population 100 on commodity
   hardware.
4. UI renders a full 86,400-tick day under 100 KB transferred and stays
   responsive; money strip reconciles to `verify` output exactly.

## 5. Red lines (unchanged from v1, restated because the target moved)

Always-on and second-cadence change nothing here: virtual money only, no
order-placement code, no self-modification, conservation checked
continuously, every session leaves a journal that replays byte-identically.
Pointing the colony at real capital is a separate future project with its
own spec and safeguards — not a config change to this one.
