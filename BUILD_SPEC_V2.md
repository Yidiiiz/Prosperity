# DARWIN-WALLET — Build Specification v2.0
### The Always-On Colony

**You are upgrading an existing, working repository** (built to BUILD_SPEC.md v1; 96 tests green; DECISIONS.md has 32 entries; LEARNINGS.md is the build report this spec answers). Do not start from scratch. v1 and DECISIONS.md remain in force except where this document amends them — where they conflict, **this document wins**.

**Target, in one sentence:** an always-on colony, ticking every second against a live venue's real prices, doing micro-trades in sub-cent units, with a UI that shows money being extracted in real time — and an audited cash number backing every claim. Money remains virtual (§12): this measures whether edges exist; it never places orders.

**Engineering principles carry over unchanged from v1 §2**: stdlib-only core, pytest the only dev dependency, integer money, one RNG, determinism, small boring functions, crash on invariant violation. The ~1,770-line v1 core earned its size (DECISIONS #22); v2 adds systems, so the new ceiling is ~2,600 non-blank lines for `colony/` — same rule: if a module fights the ceiling, simplify it.

---

## 0. How to Use This Document

1. Read LEARNINGS.md and DECISIONS.md first, then this spec end to end.
2. Build in the §10 order; each step's acceptance tests green before the next.
3. Every judgment call goes in DECISIONS.md, numbering continued from #32.
4. All v1 acceptance criteria (§13 of v1) must STILL pass after v2 lands, re-based onto the v2 unit and time systems — regression is failure.

---

## 1. Ratifications and Settled Ambiguities

The following are promoted from judgment calls to spec law. Cite the DECISIONS number in code comments where the behavior lives.

1. **Treasury genesis is money creation, not movement** (DECISIONS #2). `init` sets the treasury balance directly with no ledger row; `verify_invariants` checks `TREASURY == initial + credits − debits` and every other account against plain ledger sums.
2. **Runtime state is durable** (DECISIONS #4). The `agent_state` table and `runs.state_json` are mandatory schema, flushed inside the tick transaction. Exact resume after any interruption is a core guarantee, not a feature. New in v2: flush cadence is configurable (§5) — live keeps 1, backtests may batch.
3. **Ratified verbatim**: rent floor-division (#5); BUY-only action cap (#6); the `fee_aware` formula (#7); newborn cooldown (#8); gen-0 round-robin archetypes (#9); `fee_aware` gene probability (#10); elitism mechanics (#11); gate-failed breeders stay queued (#12); adaptive-sigma cadence (#13); diversity buckets (#14); `births_cum` semantics (#15); in-memory profit-matrix harness (#16); `--config` override semantics (#17); lifetime P&L definition (#18); dashboard degradation split (#19); `/api/summary` sourcing (#20); replay determinism + digest guard (#24); network-out-of-core (#25); granularity check at init with `small_stakes` waiver (#26); zero-amount transfers skipped by the orchestrator, never sent to the ledger (#27); terminal audits on finite replays (#29); journal-tail live mode with torn-line tolerance and stale≠exhausted (#30); replay-twin verification with consumed-prefix digest (#31); live-is-paper (#32).
4. **Lifecycle constants must not be commensurate with regime lengths** (fixes LEARNINGS #23): the config validator WARNS whenever `max_age` equals, or is an integer multiple or divisor of, any Petri regime length. Shipped Petri defaults change `max_age` to the equivalent of 3,100 v1-ticks.
5. **Record filenames must be collision-proof**: UTC timestamp with milliseconds, plus `_seed<N>` when a seed applies, plus `_p<pid>`. Creating an existing path still raises.
6. **Portability is a requirement, not luck** (LEARNINGS §1.3): nothing may depend on POSIX-only behavior; temp dirs are cleaned with `ignore_cleanup_errors=True`; "hard kill" in tests means SIGKILL on POSIX and `taskkill /F` on Windows behind one helper. CI must run the suite on Windows and Linux.
7. **Schema is versioned**: `PRAGMA user_version = 2` at v2 init. Opening a database with any other version refuses with a clear message. There is NO migration path from v1 databases — v2 changes the money unit (§2); old colonies are archives, new colonies are fresh `init`s.

---

## 2. Money v2 — Micro-Dollars and Honest Execution Costs

Measured facts driving this (LEARNINGS §2.1): fees consumed 63% of gross at full stakes; the 1¢ integer floor alone breaks $10 colonies (minimum fee ≈ 100 bps on a $1 trade, rent rounds to 0, 2–3 affordable lots). These are unit problems. Fix the units.

**2.1 The ledger unit is the micro-dollar (µ$): 1 dollar = 1,000,000 units, int64 everywhere.** Headroom is $9.2 trillion — no overflow concern. Every config key, column, and variable that carried `_cents` is renamed `_u` and re-based (×10,000 from cents). All v1 invariants carry over mechanically; the conservation constant is now stated in µ$. Rendering: the UI and reports display dollars with appropriate precision (2 decimals above $1, up to 6 below); raw µ$ appears only in the ledger and debug output.

**2.2 Per-venue fee model.** The single `fee_bps` becomes a `venue` config block, one per arena:
```json
"venue": { "taker_bps": 10, "maker_bps": 0, "spread_bps": 2, "min_fee_u": 0 }
```
- All v2 orders are market orders → charged `taker_bps` (maker_bps is schema for future limit-order work; unused paths are not built).
- **The spread is charged even on paper** — it is the dominant real cost at speed: BUY fills at `price × (1 + spread_bps/2/10⁴)`, SELL at `price × (1 − spread_bps/2/10⁴)`, rounded against the agent.
- `min_fee_u` defaults to 0 — with µ$ units there is no reason to reintroduce the integer-floor predator; a venue config may still set one to model a real venue honestly.

**2.3 Fill delay.** `fill_delay_ticks: 1` (config; 0 allowed only for the Petri arena). An agent's decision at row N executes at row N+1's price. Mechanics: the decision is stored in `agent_state` as a pending order; at the start of the next tick's agent phase, pending orders execute FIRST (at the new price, spread and fees applied, risk-engine re-checked against current equity — a fill that now violates caps is shrunk, and cancelled if unaffordable), THEN the agent decides again. One pending order per agent; a new decision replaces an unfilled one. This single rule removes the most common source of fake intraday alpha (deciding and filling on the same bar).

**2.4 Rent at small magnitudes** stays per DECISIONS #27: computed by floor division, zero results are skipped, and the report prints the near-rent-free caveat below the threshold equity.

---

## 3. Time v2 — Wall Time Is the Config Language

Measured why (LEARNINGS §4.1): `max_age_ticks 3000` is 12 years at day-bars but 50 minutes at 1-second ticks; per-tick rent bps calibrated for the Petri is ~5,600%/hour at seconds. Tick counts are meaningless across cadences.

**3.1 `tick_seconds` is a first-class config field**, set per arena config: Petri default 86,400 (its economics were tuned as "one bar = one day"), replay = the bar interval of its data, live = the feed cadence (1 for second streams).

**3.2 All lifecycle constants are configured in wall time and converted at load**, stored on the config object as ticks (rounded, minimum 1):
```json
"lifecycle": {
  "max_age_days": 3100, "stagnation_days": 400, "breed_cooldown_days": 50,
  "solo_breed_patience_days": 10, "snapshot_every_days": 25, "checkpoint_every_days": 2000
}
```
(Field names use the unit; a live 1s config would express the same concepts in `_seconds` fields — the loader accepts `_seconds`, `_hours`, or `_days` suffixes on any lifecycle key, converts via `tick_seconds`, and rejects configs that mix a key given twice.)

**3.3 Rates are annualized.** `rent_apr_bps` replaces `rent_bps_of_equity`: per-tick rent = `equity × rent_apr_bps × tick_seconds ÷ (10⁴ × 31,536,000)`, floor division, zero-skip. Shipped Petri equivalent: the v1 2 bps/tick ≡ 7,300 bps APR at day-ticks — set `rent_apr_bps: 7300` so Petri economics are unchanged. A live 1s config inherits a sane rent automatically instead of needing a hand-zeroed override (retiring the v1 live workaround, DECISIONS #32).

**3.4 The dashboard and all reports use wall-clock axes** (UTC), derived from journal/bar timestamps; tick numbers appear only in debug output and the ledger.

---

## 4. Performance — the ≥500 ticks/s Requirement

Measured baseline: 15–20 ticks/s at population 40–100; bottlenecks measured and named (LEARNINGS §1.3). Live needs ~1 tick/s, but auditing one day of seconds is 86,400 ticks — the nightly replay-twin audit (§6.5) is only viable at **≥500 ticks/s (population 100, commodity hardware)**. That number is an acceptance criterion (§11.3), not an aspiration. The three fixes, mandated:

1. **In-memory balance mirror.** `transfer()` already maintains the `balances` cache; mirror it in a dict owned by the ledger module, updated in the same call. `agents.cash()` reads the dict — zero SELECTs on the hot path. The dict is rebuilt from the db at open and verified against it on the invariant cadence (any divergence is an `AccountingError`).
2. **Batched equity marks.** Positions also live in a per-agent in-memory mirror (rebuilt at open, updated on fill/liquidation); the per-tick equity pass touches no SQL. Snapshots and `colony_metrics` write from the mirrors.
3. **Configurable flush cadence.** `flush_every: N` — runtime state (`agent_state`, `runs.state_json`) flushes every N ticks instead of every tick. Live and daemon configs pin 1 (exact resume, non-negotiable). Backtests default 100: a crash loses at most N ticks of progress, and because replay is deterministic (#24), resuming from the last flush reproduces the identical ledger — add a test proving flush_every 1 and 100 yield byte-identical final ledgers on the same replay.

Everything else stays boring: no threads in the core, no caching frameworks, no query planners. If 500/s is not reached with these three, profile and write the finding in DECISIONS before optimizing further.

---

## 5. Data & Feeds — Minute History, Second Streams

`tools/` remains the only network code (#25). Two additions:

**5.1 Historical minutes:** `tools/fetch_binance_klines.py` — public `/api/v3/klines` (no key), paginated, any symbol/interval, writing the standard `Date,Close` CSV (UTC ISO timestamps). Yahoo daily fetcher stays for equities dailies with its known pins (#25). Committed test fixtures: a few days of BTCUSDT 1m for CI.

**5.2 Second stream:** `tools/live_feed.py` gains a websocket mode — Binance public `@kline_1s` (close of each 1-second candle → exactly one row per second while the stream is alive), REST polling retained as fallback mode. The feed process stays a dumb appender: connect, write `Date,Close` rows to the journal, flush per row, reconnect forever with exponential backoff (1s doubling to 60s cap). It never reads the colony, never decides anything. Torn-line tolerance and stale≠exhausted semantics carry over unchanged (#30).

**5.3 Journal rotation:** the journal is now a directory of daily segments — `journal/YYYY-MM-DD.csv` (UTC boundaries). The feed opens the segment for "today" and rolls at midnight. On closing a segment it writes `journal/YYYY-MM-DD.sha256`. The Live arena chains segments in date order, consuming across boundaries transparently; the consumed-prefix resume digest (#31) becomes (list of complete segment digests) + (prefix digest of the current segment).

---

## 6. The Daemon — the Core New Deliverable

`colony daemon --config config.live.json` replaces session-style runs for live operation. One process, standard library only, structured as a supervisor loop around the existing orchestrator:

**6.1 Supervision.** The daemon spawns the feed as a subprocess and restarts it on exit with the same 1s→60s backoff; feed stdout/stderr tee into `records/feed/`. The colony loop consumes the journal exactly as v1 live mode did — the wall clock paces, the journal decides (#30). A `daemon.pid` file guards against double-start; stale pid files (dead process) are reclaimed with a logged notice.

**6.2 Lifecycle.** SIGTERM/SIGINT (or Windows CTRL events) → finish the current tick's transaction, flush, stop feed, exit 0. Hard kill at any instant is safe by construction (flush_every=1 + transactional ticks): on restart the daemon resumes exactly (proven byte-identical in v1, #4 — keep the test, now against the daemon).

**6.3 Stale vs exhausted.** No new row within `poll_timeout_seconds`: the colony PAUSES (state: `stale`), keeps serving the web layer, resumes on the next row. It never exits for staleness; only operator signals stop the daemon.

**6.4 Health surface** (extends the read-only web layer, §8): `/api/health` → `{state: running|stale|auditing, uptime_s, tick, ticks_behind_feed, feed: {connected, last_row_utc, reconnects, gap_count}, last_invariant_check_utc, last_audit: {segment, ok, utc}, flush_every}`. `colony daemon status` hits it and exits non-zero if state is unhealthy or the last audit failed — cron/Task Scheduler friendly.

**6.5 Continuous verification.** After each segment rotation (and at startup for any unaudited closed segment), the daemon runs the **replay-twin audit** on the closed segment: rebuild the twin offline through the replay arena, compare ledger hashes (#31). Pass → an audit record + health update. **Mismatch → CRITICAL**: a CRITICAL record is written, `/api/health` reports `last_audit.ok: false` permanently until an operator clears it, `daemon status` exits non-zero, and the daemon KEEPS RUNNING (an audit failure is an alarm about the past, not a reason to lose the present). This nightly audit is why §4's throughput target exists.

**6.6 Gap accounting.** Missing seconds in the journal (feed outage) are counted (`gap_count`, gap ranges in the audit record) and are NOT errors — the colony simply didn't tick. Unexplained divergence between ticks consumed and rows present IS an error.

---

## 7. Genome & Economy Recalibration for Real Data

Measured why (LEARNINGS §2.1, §4.4): breeding thresholds tuned to Petri's 12 bps/tick drift are far too demanding at real ~3.5 bps/day; population sat at the immigration floor with ~$126k of seeds recycled against ~$136k residues — life support, not compounding. And second-bars are mostly noise plus microstructure: the flat-tape problem and fee predation must become GENES, so evolution can discover when not to play.

**7.1 Three new universal genes** (apply to every trading archetype; sitter ignores them):
| Gene | Bounds | Effect |
|---|---|---|
| `vol_gate_bps` | 0 – 100 | trade only when trailing `lookback` stdev ≥ gate (in bps of price). 0 = always on. The flat-tape lesson as a heritable trait. |
| `max_trades_per_day` | 1 – 500 | wall-time trade-rate throttle (converted via `tick_seconds`); counts fills, rolling 24h window from agent_state |
| `active_hours_mask` | 24-bit int, ≥1 bit set | UTC hours the agent will open positions (closing is always allowed). Mutation flips 1 random bit; repair: if 0, set all bits |

Bounds join `PARAM_BOUNDS`; mutation/crossover treat them like every other gene; the three touch points remain `ARCHETYPES`, `PARAM_BOUNDS`, `decide` (LEARNINGS §4.4). No new archetypes ship in v2.0 — expansion happens through these gates first, and the sitter control group STAYS: if do-nothing wins at some cadence, that is a finding about the venue's costs and it must surface in records, never be tuned away.

**7.2 Breeding economy per arena class.** Config gains `repro_multiple` per arena config; shipped values: Petri keeps 1.25 (validated); replay/live real-data configs ship **1.08**. The mitosis mechanics (§3.4 v1) are unchanged — only the bar moves.

**7.3 Immigration budget.** New config `immigration_budget_apr_bps` (default 2,000): treasury spend on immigrant seeds is capped per rolling wall-time window (converted like rent) at `initial_treasury × budget`. When the budget is exhausted, population may sit below the floor — that is the honest signal the venue cannot support the floor, and it surfaces on the dashboard (§8) instead of the treasury churning itself into permanent life support.

**7.4 Expected-fail honesty.** The first 1-second ladders are EXPECTED to fail their audits with the current genome (LEARNINGS §4.4). The experiments must run, measure, and record the failure with per-seed economics — a documented negative result at a rung is an acceptance PASS for the machinery (§11.1). The spec demands the measurement, not the sign.

---

## 8. Observatory v2 — Watch Money Move

Keep from v1: read-only always (property-tested), localhost bind, single self-contained `dashboard.html`, Chart.js from CDN as the only external asset, full numeric/table degradation without it (#19). Changes, in priority order:

**8.1 Push, not poll.** New endpoint `/api/events` (Server-Sent Events, `text/event-stream`, stdlib-served): emits a `summary` event on every tick, throttled to at most 1 event/second (coalesce; latest wins), plus `health` events on state changes and a `fill` event per trade. The dashboard consumes SSE with automatic reconnect and falls back to the v1 2s polling if the stream errors twice. No websockets — SSE is one-directional, which is exactly the read-only guarantee expressed as a protocol.

**8.2 The hero is the Money Strip.** Replaces the treasury tile row as the signature element (the Strata Chart moves to slot 2, unchanged). One full-width band, three groups of figures, all from the ledger and reconciling exactly with `colony verify` output:
- **EXTRACTED** (audited cash pulled from the market: `−Δ ARENA` over window): today · this hour · per-second rate, each with sign and color (amber positive, red negative — the number is allowed to be red; that is the product working, not failing).
- **CASH**: treasury (µ$-accurate, rendered in dollars) · colony cash.
- **MARKED**: colony position value at last price, visually distinct (outlined, not filled) with the label "unrealized" — realized and mark-to-market are never summed into one figure on screen. (Fixes the measured confusion: a fully deployed colony showing treasury $0.00 is correct and previously looked broken.)

**8.3 Wall-clock everywhere.** All x-axes are UTC time; session/day boundaries drawn as faint vertical rules; the regime bands (Petri) and feed-gap ranges (live) shade the background.

**8.4 Downsampled series.** `/api/timeseries` gains `max_points` (default 2,000): server-side bucketing — for each of N equal time buckets return first/min/max/last of each series (simple, stdlib, good enough; LTTB explicitly NOT required). A full 86,400-tick day must render from <100 KB transferred (§11.4).

**8.5 Trade tape.** New panel: the last 50 fills (SSE-appended live): UTC time, agent id (links to inspector), side, size, fill price, fee+spread cost. At micro-trade cadence the tape is the product demo.

**8.6 Liveness affordances.** Header gains: feed status chip (LIVE green / STALE + seconds amber / RECONNECTING red), ticks-behind counter, invariant badge with last-checked wall time, audit badge (last segment ✓/✗), immigration-budget gauge (§7.3) so "population below floor" is visibly a budget fact.

**8.7 Lineage inline.** Retire the DOT export's prominence (kept as a CLI command, barely used per LEARNINGS): the agent inspector drawer gains a collapsible ancestor chain (id · generation · archetype · peak equity · fate), rendered as nested `<details>` — no graph library.

**8.8 Mobile-usable.** The grid collapses to a single column under 720px: Money Strip → health chips → Strata → tape → the rest. Tap targets ≥44px. An always-on colony gets checked from a phone; this is layout discipline, not a separate app.

---

## 9. Experiments & Records v2

**9.1 Every experiment is incremental, per-seed, and resumable** (mandated by LEARNINGS §1.3): each takes `--workdir` (per-seed DBs resume exactly), `--seed`, and a per-rung selector; prints each seed's result the moment it finishes; a thin driver fans seeds out as parallel processes (`subprocess`, not threads). A 30-minute monolith that prints only at the end is a spec violation.

**9.2 The v2 flagship experiment: the minute-bar ladder.** `experiments/minute_ladder.py` — Binance 1m history (≥1 year, fetched by §5.1, digest-pinned), rungs at $200,000 / $1,000 / $10 total capitalization, seeds {42, 7, 2026}, terminal audits (#29), venue model with spread ON, fill delay 1. Each rung records audited cash vs initial per seed and a PASS/FAIL/EXPECTED-FAIL verdict per §7.4.

**9.3 The soak experiment.** `experiments/live_soak.py` orchestrates §11.2: starts the daemon against the 1s feed, schedules one hard kill at a random point, verifies resume, and collects the 24h evidence into a single record.

**9.4 Records** gain the collision-proof names (§1.5), a `records/feed/` folder, `records/audits/` for replay-twin results, and CRITICAL-level entries render in the INDEX with a `!! ` prefix so `grep '^!!' records/INDEX.txt` is the incident query.

---

## 10. Build Order (tests green before advancing)

1. **Unit migration** — µ$ ledger, `_u` renames, schema `user_version=2`, v1-db refusal; full v1 suite re-based and green.
2. **Time system** — `tick_seconds`, lifecycle suffix loader, annualized rent, commensurability warning; Petri defaults re-expressed; v1 flagship re-validated unchanged in behavior.
3. **Venue model + fill delay** — spread, taker fees, pending-order mechanics; profit-matrix re-run with spread on (record the new baseline).
4. **Performance** — mirrors, batched marks, `flush_every`; the 500/s bench test; flush-equivalence test.
5. **Feeds** — Binance klines fetcher, websocket feed mode, journal segmentation + digests; Live arena chaining.
6. **Daemon** — supervision, lifecycle, health endpoint, gap accounting; hard-kill resume test.
7. **Continuous audit** — segment replay-twin in the daemon, CRITICAL path, `daemon status`.
8. **Genome & economy** — three genes, per-arena `repro_multiple`, immigration budget; Petri regression (genes at neutral defaults must not change validated Petri results beyond seed noise).
9. **Observatory v2** — SSE, Money Strip, downsampling, tape, liveness, lineage, mobile.
10. **Experiments** — minute ladder + soak driver; records upgrades.
11. **Polish** — README v2 (daemon quickstart, phone screenshot-free walkthrough), CI on Windows + Linux, DECISIONS entries for every call made.

---

## 11. Acceptance Criteria (v2 is DONE when all hold, plus all v1 criteria re-based)

**11.1 Minute-bar ladder** (§9.2): every rung completes with exact conservation and terminal audits; each seed's audited result recorded; rungs may be EXPECTED-FAIL only with the per-seed economics written into the record (§7.4). The machinery never fails; the strategy is allowed to.

**11.2 24-hour live soak at 1s ticks**: zero invariant violations; zero unexplained missed rows (gaps must match feed-outage records); survives one induced hard kill with exact byte-identical resume; nightly replay-twin audit passes on the closed segment; health endpoint accurate throughout.

**11.3 Throughput**: ≥500 ticks/s, population 100, replay arena, `flush_every 100`, commodity hardware — enforced as a benchmark test with a generous CI margin (≥250/s in CI, ≥500/s documented on the reference laptop in a record).

**11.4 Observatory**: a full 86,400-tick day renders from <100 KB transferred and stays responsive; the Money Strip reconciles exactly with `colony verify`; SSE delivers a fill event for every trade in a test session; realized and unrealized are never summed in any displayed figure; layout usable at 390px width.

**11.5 Time & units**: the same colony config expressed at `tick_seconds` 86,400 and 60 yields identical *annualized* rent and identical lifecycle wall-times (property test); no `_cents` identifier remains; no tick-number axis remains in the UI.

**11.6 Honesty checks**: the sitter archetype still exists and its survival at any cadence is reported, never suppressed; immigration-budget exhaustion is visible in `/api/health` and the dashboard; every EXPECTED-FAIL verdict cites its record path.

---

## 12. Red Lines (unchanged, restated because the target moved)

Always-on and second-cadence change nothing here: **virtual money only; no order-placement code anywhere in the repository; no self-modification; conservation checked continuously with crash-on-violation; every session leaves a journal that replays byte-identically.** Pointing the colony at real capital is a separate future project with its own spec, safeguards, and legal review — not a config change to this one.

---

*End of specification. The colony already survived 33 years of history and a regime flip; now teach it to live on the clock.*
