# LEARNINGS

What three versions of darwin-wallet taught us, what should change, and a
concrete path from day-scale to minute- and second-scale execution. Numbers
below are measured, not guessed — sources are the committed records under
`records/` and DECISIONS.md.

---

## 1. What is now proven

- The machinery works end to end: evolution + double-entry integer-cent
  accounting, deterministic to the byte, on scripted, historical, and live
  prices.
- On 33 years of real SPY dailies the colony is profitable at $200k
  (+4.5–6.3% audited cash across seeds) and strongly profitable at $100
  (+28.5% to +1,853%).
- A live session is exactly as auditable as a backtest: the journal is the
  tape, and the replay twin reproduces the live ledger hash-for-hash.

## 2. Measured learnings (the ones that will bite at speed)

**2.1 The treasury identity.** By conservation, `treasury > initial` can
only hold while *realized* extraction exceeds the colony's retained
mark-to-market wealth. On SPY, survivors held ~$31k of appreciated lots
against ~$4.9k realized (seed 42). Any "did we make money?" claim on finite
data must liquidate first — hence the terminal audit. At faster scales with
shorter sessions this matters even more: never read mid-run treasury as
profit.

**2.2 Fees ate 63% of gross.** Full-stakes SPY: ~$13k gross trading profit,
$8.2k paid in fees (20 bps + 1-cent floor). Daily z-score churn barely
clears a 20 bps toll. Per-bar edges shrink roughly with √(bar length), so
at minute scale a 20 bps fee is enormous and at second scale it is
disqualifying. Fee realism per venue is not a tuning detail — it decides
whether evolution can find anything at all.

**2.3 The 1-cent floor dominates below ~$100.** At $10 total, the minimum
fee is ~100 bps on a $1 trade and rent rounds to 0; profit became
seed-dependent (+26.5% / −67.3% / −4.1%). $100 was the smallest
capitalization with reliable economics. The floor is a *unit* problem, not
a strategy problem (see 4.2).

**2.4 Data resolution must match tick cadence.** The live demo polled
Yahoo every 5s, but `regularMarketPrice` updates in coarser steps — long
flat stretches, stdev 0, zero signals, zero trades in 100 ticks. Polling
faster than the source refreshes just replays the same number. Second-scale
ticks need a source that actually emits second-scale data (trade streams,
not quote snapshots).

**2.5 Lifecycle constants are denominated in ticks, silently.**
`max_age_ticks = 3000` means 12 years at day-bars and **50 minutes** at 1s
ticks. Rent of 2 bps/tick is ~5%/year daily but ~5,600%/hour at 1s (the
live config had to zero it). Nothing in the config knows how long a tick
is. This is the single biggest change needed for re-scaling (see 4.1).

**2.6 Population sat at the floor on real data.** SPY runs hovered at
`population_floor` (40) with immigration recycling ~$126k of treasury seeds
against ~$136k of returned residues — roughly a wash, but it means the
colony was on life support between profitable stretches, not compounding.
Breeding thresholds tuned for the Petri drift (12 bps/tick!) are far too
demanding for real markets' ~3.5 bps/day.

**2.7 Yahoo gotchas** (documented in DECISIONS #25): `range=max` silently
degrades to monthly bars — pin `period1/period2&interval=1d`; Stooq is
behind a JS challenge. Intraday limits: Yahoo serves ~7 days of 1m bars,
~60 days of 5m. For deep minute/second history use exchange APIs (see 5.1).

## 3. Design decisions that paid off — keep them

- **The journal-tail architecture.** Network code writes a file; the core
  reads the file. This one boundary gave us offline determinism, live
  reproducibility, crash tolerance (torn-line handling), and free
  regression data. It scales unchanged to any cadence.
- **The arena protocol is 5 small methods** — petri, replay, and live took
  a factory and ~90 lines each. A websocket-backed arena is the same shape.
- **SAVEPOINT births inside one-transaction ticks**; per-tick state flush
  bought exact SIGINT-safe resume (which the interrupted full-stakes runs
  then relied on, repeatedly, by accident — it paid for itself).
- **Records + DECISIONS discipline.** Every failed criterion (treasury
  identity, $10 seed-dependence) became a documented finding instead of a
  silent re-tune.

## 4. Things to change (independent of speed)

1. **Make tick duration explicit.** Add `tick_seconds` to the config and
   express lifecycle in durations: `max_age`, `stagnation`, cooldowns,
   patience as wall-time (or bars-of-X); rent and fitness as per-day rates
   converted at load. One number re-bases the whole economy to any cadence.
2. **Sub-cent money units.** Promote the ledger unit from cents to
   micro-dollars (int64 headroom is ample: 9.2 × 10¹² dollars). Kills the
   1-cent fee floor that wrecks $10 colonies and second-scale fees; keep a
   `fee_min_units` knob (default 0) and render dollars in reports. Purely
   mechanical change — every invariant carries over.
3. **Cache agent cash in memory.** `agents.cash()` is a SELECT per call and
   is called several times per agent per tick; the balance is already
   maintained by `ledger.transfer`. Mirroring it on `AgentState` (source of
   truth stays in SQLite, verified on cadence) is the single biggest sim
   speedup available (~3–5× fewer queries in the hot loop).
4. **Configurable flush cadence.** Exact-resume-every-tick costs a full
   state write per tick. At 8,400 ticks that's irrelevant; at 500k it's the
   bottleneck. `flush_every: N` trades resume granularity for throughput
   (replay/backtest can afford N=100; live keeps N=1).
5. **Recalibrate breeding for real drift.** `repro_multiple 1.25` above a
   moving baseline was tuned to Petri's 12 bps/tick trend. On real data,
   consider 1.05–1.10, or fund children from realized-profit-above-seed.
   Immigration deserves a budget cap per window so the treasury can't churn
   itself into recycling mode (2.6).
6. **Fee model per venue.** `fee_bps` → `{taker_bps, maker_bps, min_units,
   spread_bps}`. Even paper trading should charge the spread — it is the
   real cost at fast scales and ignoring it manufactures fake alpha.
7. **Small fixes:** validate replay/live CSVs at `init` with a clear error
   (done for granularity; extend to malformed files); document the
   `horizon` death cause in report legends; `records` filenames gained
   seed-suffixes to avoid same-second collisions — keep that pattern.

## 5. The road to minute- and second-scale

The good news: **nothing in the core assumes days.** A tick is "one row
appended." The work is data, calibration, and throughput — in that order.

### Phase A — minute bars, backtest first (small effort)

Prove economics at 1m before going live at 1m, exactly like v2 preceded v3.

- **Data:** Binance public klines (`/api/v3/klines`, `interval=1m`, no API
  key) give years of BTC/ETH minute bars; extend `fetch_market_data.py`
  with a `--source binance --interval 1m` mode writing the same
  `Date,Close` CSV. Yahoo 1m (7 days) is fine for smoke tests only.
- **Calibration:** `tick_seconds: 60`; lifecycle in wall time (agent life
  ~days of minutes, not 50 minutes); fees per venue (Binance taker 10 bps,
  maker ~0); stagnation measured in bars still works.
- **Acceptance:** rerun the capitalization ladder on minute data with
  terminal audits. Expect much thinner margins than daily SPY — minute
  z-scores on crypto after 10 bps fees may only clear on volatile regimes.
  If no seed extracts cash, that is the experiment working: the current
  archetypes may simply have no minute-scale edge (see 5.4).

### Phase B — live at minutes (near-zero effort)

Today's stack already does this: `live_feed.py --interval 60` against
Yahoo or Binance ticker endpoints emits real minute rows (unlike the 5s
demo, quotes genuinely refresh at 1m). Same journal, same verifier. This
should be the first long-running live deployment: run it for days, wind
down, audit, replay-verify.

### Phase C — seconds (the real build)

1. **Feed:** polling dies here. Subscribe to an exchange websocket
   (Binance `<sym>@trade` or `@kline_1s`) and append one row per second
   (last trade price). The daemon stays a separate process writing the
   journal; the core still just tails a file. Journals grow to ~86k
   rows/day — rotate daily and teach `Live._load` to chain
   `journal.YYYY-MM-DD.csv` segments (digest per segment).
2. **Throughput:** at 1s ticks the sim needs ~1 tick/s live (trivial) but
   backtesting a day of seconds means 86k ticks — with 4.3 and 4.4 the
   current ~15–20 ticks/s at pop 100 should reach several hundred/s,
   i.e. a simulated day in minutes. Batch equity marks per tick (one
   query for all balances) rather than per agent.
3. **Money:** sub-cent units (4.2) become mandatory — at 1s scale,
   meaningful per-trade fees are fractions of a cent.
4. **Latency honesty:** at seconds, fill assumptions start to matter. The
   arena fills at the journal row price; a real venue would fill at the
   *next* trade ± spread. Add `fill_delay_ticks: 1` (decide on row N,
   fill at row N+1) — one line in the execution path, and it removes the
   most common source of fake intraday profits.

### 5.4 The uncomfortable truth about faster alpha

Evolution can only select edges that exist in the data. Daily SPY has a
33-year drift the colony surfed. Minute/second data is closer to noise
plus microstructure, and the current genome (z-score momentum /
mean-reversion on closes) is a blunt instrument there. Expect the first
minute-scale ladders to fail their audits — then let that drive the genome,
not the other way around:

- new genes: volatility regime filter (trade only when stdev/mean clears a
  floor — the flat-tape problem from 2.4 becomes a *gene*), time-of-day
  masks, trade-rate throttles (fees are the predator at speed; `fee_aware`
  should evolve into the dominant allele or the fees are set wrong);
- new archetypes are cheap to add (`ARCHETYPES`, `PARAM_BOUNDS`,
  `strategies.decide` — three touch points);
- keep the sitter. If do-nothing outcompetes trading at some cadence, that
  is a *result* — it means the venue's costs exceed the signal, and the
  honest move is to report it, not to delete the control group.

### Suggested order of work

| Step | Scope | Effort |
|---|---|---|
| 1 | `tick_seconds` + duration-based config (4.1) | small |
| 2 | Binance kline fetcher + 1m ladder experiment (A) | small |
| 3 | Cash cache + flush cadence + batched marks (4.3/4.4) | medium |
| 4 | Sub-cent units + venue fee model (4.2/4.6) | medium |
| 5 | Long-running 1m live deployment + audit (B) | small |
| 6 | Websocket feed + journal rotation + fill delay (C) | medium |
| 7 | Genome expansion driven by failed 1s audits (5.4) | open-ended |

## 6. Unchanged red lines

Faster ticks change none of these: virtual money only, no order-placement
code, no self-modification, conservation checked continuously, and every
live session leaves a journal that replays byte-identically. If the system
is ever pointed at real capital, that is a new project with new safeguards
— not a config change to this one.
