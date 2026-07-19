# DECISIONS

Judgment calls made where the spec is silent, with reasons. Spec references in
parentheses.

1. **Repo root = project root.** The spec's `darwin-wallet/` layout maps onto
   this repository's root directly.

2. **Treasury genesis is money creation, not movement.** `init` sets the
   treasury's starting balance directly in `balances` with no ledger row.
   `verify_invariants` therefore checks
   `TREASURY balance == initial_treasury_cents + credits − debits` and every
   other account against plain ledger sums; both spec invariants (§4.4) hold
   exactly under this reading. The alternative (a genesis ledger row from a
   world account) would make `SUM(balances) == 0` instead of the spec'd
   constant.

3. **Overdraft rule keyed on `accounts.kind`.** ARENA-kind accounts may go
   negative; the kind is read in the same statement as the debit balance. This
   keeps the v2 `SINK:*` accounts (ARENA-kind) working with no ledger change.

4. **Two additions to the spec schema: `agent_state` table and
   `runs.state_json`.** Runtime state the spec keeps in memory (baseline, peak
   equity, hold counter, ever-traded flag, queue membership, first-snapshot
   equity, final equity) and run-level state (RNG state, adaptive sigma, arena
   state, max generation seen) are flushed **every tick inside the tick's
   transaction**, so `colony run` resumes exactly and SIGINT anywhere is safe
   (tested: interrupted-and-resumed runs produce byte-identical ledgers).
   The hall of fame is *not* stored: it is reconstructed from the fossil
   record (dead agents with peak ≥ 2× seed, ordered by death, last
   `hall_size`), which matches the rolling-window semantics of §3.13 by
   construction.

5. **Rent uses floor division**: `max(rent_min_cents, equity * rent_bps // 10000)`
   — all-integer math per §2.2.

6. **`max_action_fraction` caps BUY orders only.** The cap exists to stop
   bet-it-all *entries* (§3.6); capping sells would leave agents unable to
   fully exit, contradicting the archetypes' sell-all behavior and forced
   liquidation.

7. **`fee_aware` formula** (§6.2 leaves it open):
   `edge_bps = (|z| − exit_z) × (stdev/mean) × 10⁴`; a BUY is skipped when
   `edge_bps < 2 × fee_bps`.

8. **Newborns start with no breed cooldown** (`last_birth_tick = NULL`),
   matching the reference prototype. The 25% cash climb to the mitosis
   threshold dominates timing anyway.

9. **Gen-0 archetypes are assigned round-robin** (momentum, mean_revert,
   sitter) with random params, as in the reference prototype, guaranteeing
   the sitter control group exists (§6.2).

10. **`random_genome` includes the `fee_aware` gene with probability 0.5.**

11. **Elitism (§7.3):** the current top-`elitism_top_k` living agents by
    fitness may *enqueue* past `max_population`; a birth may exceed the cap
    only when a funder is elite, and the population can never exceed
    `max_population + elitism_top_k`.

12. **Gate-failed breeding attempts stay in the queue** (§3.4 "skip the
    attempt, stay in queue") and are retried on later ticks; queue membership
    ends only at a successful birth or death. Each pair is attempted at most
    once per tick, so no livelock.

13. **Adaptive sigma cadence:** evaluated when a birth opens a new maximum
    generation; cohort growth = (current-or-final equity) / birth seed, median
    per generation over the trailing `window_generations` cohorts. Skipped
    until that many cohorts exist.

14. **Diversity metric:** Shannon entropy (nats) over buckets
    `(archetype, (lookback−5)//24, int(entry_z×2), int(risk_fraction×5))`.

15. **`births_cum` counts every post-init spawn** — children *and* immigrants
    (both are new agents entering the world after tick 0). Immigrants are
    generation 0 (house-funded, like gen-0).

16. **`profit_matrix` runs strategies/risk/arena in an in-memory harness**
    (no ledger): it is an environment pre-check of regime economics, not an
    accounting test. Probe genomes use `hold_max = 1500` (the bound) so pure
    signal behavior is measured; seeds are {42, 7, 2026, 11, 99}.

17. **`colony run --config` overrides the stored config for that invocation**
    (spec CLI shows the flag). Default is the config stored at init; overriding
    mid-run breaks reproducibility and is on the operator.

18. **Lifetime P&L in `inspect`** = (current-or-final equity) + everything
    paid out (rent, debt_repay, child seeds, death residue) − birth seed:
    the wealth the agent generated, net of what it was given.

19. **Dashboard degradation split:** death bars, diversity sparkline and the
    debt gauge are hand-drawn (CSS/canvas) so they render with the CDN
    blocked; only the three big charts need Chart.js. All KPI numbers and
    tables are plain DOM.

20. **`/api/summary` reports the latest `colony_metrics` row** (refreshed
    every `snapshot_every` ticks) — the same series the charts poll, so the
    dashboard is self-consistent; `invariant_ok` is the cheap global check
    (`SUM(balances) == initial`), recomputed per request.

21. **Python 3.11+ with stdlib only** for the core, per §2.1; `pytest` is the
    single dev dependency (`pip install -e .[dev]`).

22. **Size target (§11):** the package lands at ~1,770 non-blank lines vs the
    rough ~1,500 target. The overage is spec-mandated surface — the §4 SQL
    schema verbatim, seven §9.1 endpoints, three CLI report formats — not
    abstraction; no module exceeds ~400 lines and none uses metaclasses,
    decorators, or frameworks. Cutting further would mean deleting docstrings,
    which loses more (§2.6, "boring, readable") than it gains.

23. **§13.2 gen-0 selection reading:** with the shipped defaults,
    `max_age_ticks` (3000) equals the first regime's length, so the gen-0
    senescence wave lands exactly on the regime boundary tick. The 30–90%
    "meaningful selection" criterion is therefore measured on deaths strictly
    before tick 3000 (pre-senescence): 41% on the acceptance run.

## v2 — real market data

24. **Replay arena (v2) is deterministic by construction.** It replays a
    local CSV of real daily closes, one row per tick; `step` ignores the RNG
    because the past is already written. `lot_denominator` scales the asset
    so one lot is an affordable slice (`round(close × 100 / denominator)`,
    floored at 1 cent). Resume is guarded by a digest of the price series:
    resuming against a changed CSV refuses to run rather than silently
    diverging. When the data ends the arena reports `exhausted()` and the
    run loop stops cleanly (`run` returns ticks actually executed).

25. **The network stays out of the core.** `tools/fetch_market_data.py`
    (Yahoo Finance daily closes) is the only network code in the project;
    the simulation replays the fetched file offline. The fetched SPY history
    is committed under `data/` so results stay reproducible without a
    network. Stooq was tried first but sits behind a JavaScript challenge;
    Yahoo's `range=max` silently degrades to monthly bars, so the fetcher
    pins `period1=0&period2=…&interval=1d`.

26. **Lot-granularity check moves to init for replay arenas, with an
    explicit waiver.** The §3.11 rule (`gen0_seed ≥ 200 × start price`)
    needs the CSV's first price, which the pure config validator never sees;
    `init_colony` re-checks it against the constructed arena. Setting
    `'small_stakes': true` waives the rule (both arena kinds) — that is the
    documented, deliberate way to run tiny-capital colonies, and the report
    still prints the granularity warning.

27. **Zero rent is a no-op, not a ledger row.** At small stakes
    `equity × rent_bps // 10⁴` rounds to 0; the ledger correctly refuses
    0-cent rows, so the orchestrator skips the transfer. Small-stakes
    colonies therefore live nearly rent-free until equity reaches
    `10⁴ / rent_bps` cents — reported as a caveat, not hidden.

28. **The v2 acceptance experiment is a capitalization ladder run in order:
    $200,000, then $100, then $10.00 total** (`experiments/real_market.py`,
    33 years of real SPY daily closes, seeds {42, 7, 2026}). The pass bar
    lowers as integer-cent friction rises: full and micro stakes must
    survive AND end with an audited cash profit; the $10 rung must survive
    with invariants intact, its economics reported per seed. Measured
    results (2026-07-18): full +4.7/+4.5/+6.3%; micro +40.9/+28.5/+1853.3%;
    tiny +26.5/−67.3/−4.1% — at $10 the 1-cent floor (min fee ≈ 100 bps on
    a $1 trade, rent rounding to 0, 2–3 affordable lots late in the series)
    makes profit seed-dependent. That finding is the point of the rung, not
    a defect to hide.

29. **Finite replays end with a terminal audit** (`Orchestrator.wind_down`):
    when the data runs out, every living agent is liquidated at the last
    real price and its whole estate returns to the treasury. Requiring
    `treasury > initial` DURING a finite replay would penalize holding the
    winning asset — by conservation, the treasury can only exceed initial
    while realized extraction beats the colony's retained mark-to-market
    wealth (measured on seed 42: agents held $30.9k of appreciated lots
    against $4.9k realized). After the audit the claim is exact and in
    cash: every deployed cent recovered, plus profit, at real prices.

## v3 — live market data (paper)

30. **Live mode is a journal tail, not a network client.** The feed daemon
    (`tools/live_feed.py`, Yahoo quotes) appends `Date,Close` rows to an
    append-only journal CSV; the Live arena only reads that file, blocking
    (outside the tick transaction) until an unconsumed row appears. The
    simulation core stays offline; the wall clock paces ticks but never
    decides anything. A torn tail line (feed caught mid-write) is ignored
    until its newline arrives. If no row arrives within
    `poll_timeout_seconds` the run stops cleanly and can resume later —
    stale is not exhausted.

31. **Live runs are reproducible after the fact.** The journal doubles as
    the session's permanent tape: replaying it through the v2 replay arena
    with the same config and seed produces a byte-identical ledger.
    `tools/verify_live_run.py` rebuilds the twin offline and compares
    ledger hashes; `tests/test_live.py::test_live_run_equals_replay_twin`
    pins the property in CI. Resume of a live run is guarded by a digest of
    the CONSUMED PREFIX only (the journal legitimately grows), unlike
    replay's whole-series digest.

32. **Live mode is still paper.** Prices are real and current; money is
    virtual. No orders are sent anywhere — there is no order-placement code
    in the repository. The live demo config (`config.live.json`) sets rent
    to zero because a per-tick rent calibrated for simulated ticks would be
    absurd at seconds-per-tick pacing; senescence and death residues remain
    the treasury's return path.

## v2.0 — the always-on colony (BUILD_SPEC_V2)

33. **Unit migration is a rename plus a re-base, nothing else.** Every
    `_cents` identifier became `_u` (micro-dollars, ×10,000 from cents) in
    one mechanical pass — schema columns, config keys, variables, tests —
    so v1 semantics carried over exactly. `PRAGMA user_version = 2` stamps
    v2 databases; `db.connect` refuses any initialized file with a
    different version (spec v2 §1.7, no migration path). Until the venue
    model lands (§2.2), the interim fee keeps a 1 µ$ floor so fee ledger
    rows stay valid; the venue model replaces it and skips 0-amount fees
    per #27. `report.money` renders dollars with 2 decimals at or above
    $1 and 6 below; raw µ$ appears only in ledger/debug output.

34. **rent_apr_bps ships at 730, not the spec's 7,300.** Spec v2 §3.3 states
    "v1 2 bps/tick ≡ 7,300 bps APR at day-ticks" — arithmetically 2 bps ×
    365 days = 730 bps. The same clause's controlling requirement is "so
    Petri economics are unchanged", and §10.2 requires the v1 flagship to
    re-validate unchanged, so the correct number wins: at tick_seconds
    86,400, `equity × 730 × 86,400 // (10⁴ × 31,536,000)` equals
    `equity × 2 // 10⁴` exactly (proven by test), byte-identical to v1
    rent. The validator cap moves from 2 bps/tick to 730 bps APR, same
    ceiling in wall-time terms.

35. **Wall-time plumbing.** The lifecycle loader stores derived tick counts
    back onto the config dict under the v1 names (`max_age_ticks`,
    `snapshot_every`, ...), so downstream code never converts and stored
    `config_json` resumes consistently. `min_ticks_for_fitness` stays a
    tick count: it guards a statistical minimum of observations, not a
    wall-time lifecycle. The Petri stamps bars from a fixed epoch
    (2020-01-01T00:00:00Z, `arena.epoch_utc` to override) at tick_seconds
    per bar so UTC axes exist in every arena; replay/live parse the Date
    column (bare dates = UTC midnight). `colony_metrics` and `trades` gain
    a `utc` column. The shipped Petri `max_age_days` 3,100 is commensurate
    with the 100-tick crash regime (31 × 100) and the validator warning
    fires by design — the #23 measurement problem was equality with a LONG
    regime; a warning, not an error, is the spec'd behavior and the crash
    regime length is part of the validated v1 economics we must not touch.

36. **Venue defaults per arena class.** The shipped Petri venue is
    `{taker_bps: 20, spread_bps: 0, min_fee_u: 0, fill_delay_ticks: 0}` —
    exactly the v1 execution model, so the validated Petri economics and
    the v1 acceptance runs re-base unchanged. Real-data configs (replay,
    live) ship the spec's honest block `{taker_bps: 10, spread_bps: 2,
    min_fee_u: 0, fill_delay_ticks: 1}`. The validator rejects
    fill_delay_ticks 0 outside the Petri (spec v2 2.3).

37. **fee_aware sees taker + half-spread.** The #7 formula is ratified
    verbatim (`edge_bps < 2 x fee_bps`); with the venue model, the
    per-side cost passed to `decide` is `taker_bps + spread_bps / 2` —
    the actual cost of one side of a round trip.

38. **Pending orders live one bar, exactly.** A risk-checked decision at
    row N is stored in agent_state (pending_side/pending_lots, flushed in
    the tick transaction, so it survives restarts); at row N+1 it is
    re-checked against current equity at the new price (shrunk to caps,
    cancelled if unaffordable) and consumed either way. Engine actions —
    rent force-liquidation, death liquidation — remain immediate: they are
    not agent decisions. Order of a tick's agent phase: pending fill,
    rent, decide. Sell proceeds of 0 u (1 u prices under spread) post no
    ledger row per #27.

39. **The measured path to 500 ticks/s** (spec v2 section 4 mandated three
    fixes; two more were needed and are recorded here as the profile
    demanded). Baseline at population 100: 165 ticks/s. Profile (2,000
    ticks): (a) stdlib statistics.pstdev — exact-Fraction arithmetic in
    Python 3.14 — cost 8s of 18s; zstats now uses exact integer sums with
    one sqrt (n^2*var = n*sum(x^2) - sum(x)^2), deterministic and
    platform-stable. (b) two UPDATE-balances per transfer cost ~5s; with
    the mirror authoritative inside a transaction, the table is synced
    once per commit (db.flush_balances, executemany over dirty accounts) —
    crash safety unchanged because the sync happens before COMMIT.
    (c) the full O(ledger) invariant audit at 100-tick cadence cost
    ~250ms/call and grows with history; the cadence check is now
    ledger.verify_fast (O(accounts): mirror sum == initial, no negative
    non-ARENA), with the full audit at run boundaries, checkpoints,
    wind_down and `colony verify`. Result: 675 ticks/s on the reference
    laptop (bench in tests/test_perf.py, CI floor 250/s).

40. **flush_every batches transactions, not flushes.** One BEGIN..COMMIT
    spans up to N ticks and the runtime state flushes with it, so the
    database is always at a flushed boundary and a crash loses at most N
    ticks; resume from the boundary replays to a byte-identical ledger
    (tests: flush 1 vs 100 identical; injected crash mid-window resumes
    identical). Live arenas pin flush_every 1 in the validator, which also
    keeps the blocking feed wait outside any open transaction.

41. **Binance via the public data mirror.** api.binance.com geo-blocks this
    build region; data-api.binance.vision (REST) and
    data-stream.binance.vision (websocket) are Binance's official
    public-market-data hosts with the same API shapes and no key. The
    fetcher and feed default to them (--base / --ws-host to override). A
    committed CI fixture (data/btcusdt_1m_fixture.csv, 4,321 real 1m rows,
    close-series digest 34f6b17f238b6079) keeps replay tests offline (#25).

42. **The websocket client is ~90 lines of stdlib.** RFC 6455 over
    socket+ssl: handshake with Sec-WebSocket-Accept verification, masked
    client frames, ping->pong, close handling, fragment reassembly. The
    frame codec is pure functions (encode_frame/decode_frame) so it is
    unit-tested offline; only tools/ touches the network (#25). One row is
    written per @kline_1s candle CLOSE (k.x == true), stamped with the
    candle's open second.

43. **A mid-day feed stop does not seal the segment.** Journal.close()
    leaves today's .csv unsealed; the .sha256 is written only on rotation
    past UTC midnight, because the segment is still the day's growing tape
    and a restart appends to it. The Live arena treats every segment
    except the newest as complete/cacheable and re-reads only the tail;
    the segmented resume digest is (digests of fully-consumed segments) +
    (prefix digest and row count within the cursor's segment), exactly the
    #31 consumed-prefix guarantee lifted to a chain of files.

44. **The daemon is a supervisor around the unchanged orchestrator.** One
    process: it spawns the feed subprocess (restart with 1s->60s backoff,
    output teed into records/feed/), consumes the journal through the
    exact same Orchestrator.step() every mode uses, and pins
    flush_every 1 so a hard kill at any instant resumes byte-identically
    (proven by killing the real CLI subprocess mid-run in
    test_daemon_hard_kill_resumes_byte_identically). Stale (#30) pauses
    forever in production; only bounded runs (--max-ticks, i.e. tests and
    soaks) exit on stale, because a test journal never grows.

45. **Liveness by pid file + OpenProcess, not os.kill.** A second daemon
    on the same db must refuse to start. The pid file next to the db is
    checked with kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION)
    on Windows — os.kill(pid, 0) on Windows calls TerminateProcess and
    would murder the daemon it was checking on. Stale pid files (dead
    process) are reclaimed with a printed notice.

46. **Health is a sidecar file, not shared state.** The daemon atomically
    rewrites {db}.health.json about once a second (tick, feed
    connected/reconnects/gaps, last invariant check, last audit,
    audit_critical). The web layer serves it verbatim as /api/health and
    stays read-only against the db; `colony daemon status` exits 0/1/2
    (healthy / unhealthy-or-critical / unreachable) for cron and
    scripting. "stale" is healthy: a paused colony is a paused market,
    not a failure.

47. **Feed gaps are counted, never raised.** If consecutive consumed bars
    are further apart than tick_seconds, gap_count increments and the
    colony simply didn't tick for the missing seconds — the tape is still
    the tape (#30). The audit records the gap ranges it saw so a PASS
    documents exactly what data it covered.

48. **The audit unit is the fully-consumed closed segment.** The
    replay-twin (#31 continuous) audits only segments that are (a) not
    the growing tail and (b) entirely behind the live cursor, replaying
    from genesis through the last such segment in one twin run
    (flush_every 100 — flush cadence is ledger-neutral, proven at stage
    4) and comparing sha256 over the full ledger prefix. State lives in
    records/audits/state.json; a mismatch is CRITICAL, latches until
    `colony daemon clear-audit`, and prefixes its INDEX.txt line with
    `!! ` — but the daemon keeps running, because an audit failure is an
    alarm about the past, not a reason to stop the present.

49. **The hours mask is a first-class gene type, not a hack.** MASK24 joins
    PARAM_BOUNDS as (1, 2^24-1, mask24): drawn with getrandbits(24),
    mutated by flipping exactly one random bit — but only when sigma > 0,
    because sigma 0 means "pure blend" for every other gene and the mask
    honors the same contract — and repaired 0 -> all-hours (an agent that
    can never open is not a genome, it's a bug). Pre-v2 genomes (an old
    hall of fame) gain missing genes by uniform draw on first mutation.

50. **Gates block opens only, and read as neutral when absent.** decide()
    gains utc_hour and trades_24h; the three gates run before the
    fee_aware check and only on the entry branch — closing is ALWAYS
    allowed (a throttled agent may still exit risk). vol_gate_bps is
    measured as the trailing window's coefficient of variation in bps
    (stdev/mean x 10^4), the same units fee_aware uses. Missing genes
    default to gate 0 / 500 per day / all hours, so a v1 genome behaves
    exactly as it did in v1 (the Petri regression bar, proven in
    test_neutral_genes_are_a_no_op_at_every_hour_and_count).

51. **The fill window is pruned on use, persisted lazily.** Every fill
    (including forced liquidations) appends its bar utc to agent_state's
    fills_json; the orchestrator prunes to (utc - 86400, utc] before each
    decision. Pruning does not dirty the row — the persisted list may be
    a stale superset that re-prunes deterministically on load, so resume
    and replay-twin agree. At Petri day-bars the window degenerates to
    the current bar, and every Petri bar is hour 0 UTC, so random hour
    masks make ~half of random genomes unable to open there — selection
    and stagnation deaths handle them; that is measurement, not a bug.

52. **The immigration budget is a token bucket that starts full.**
    Capacity is one year's budget (initial_treasury x apr / 10^4), accrual
    per tick converts like rent, spend is one gen0 seed per immigrant, and
    the bucket persists in runs.state_json. Starting full means early
    catastrophes can still be repopulated; only sustained churn exhausts
    it, after which the population honestly sits below the floor (visible
    in /api/health's immigration block). The v1 floor test re-bases with
    an explicit 200,000 bps budget because it tests the refill mechanism,
    not the budget.

53. **Breeding bars per arena class (spec v2 7.2).** Petri keeps
    repro_multiple 1.25 (validated); config.spy.json and config.live.json
    ship 1.08 — the mitosis mechanics are untouched, only the bar moves
    to match real-data drift rates.

54. **SSE is a per-connection read loop, not a push pipe.** /api/events
    holds one thread per client: each second it re-opens the db read-only,
    emits `summary` only when the tick advanced (coalesce: latest wins),
    emits one `fill` per new trades.seq past the connection's watermark
    (the watermark starts at MAX(seq), so a fresh stream shows only new
    fills), emits `health` when the sidecar changed, and always emits a
    `ping`. One-directional SSE is the read-only guarantee expressed as a
    protocol (spec v2 8.1); the dashboard falls back to 2s polling after
    two stream errors.

55. **Bucketing is last-of-bucket plus envelopes.** /api/timeseries
    max_points (default 2,000) buckets by equal tick spans: every column
    returns last-of-bucket; price/treasury/colony-wealth/population also
    return _min/_max envelopes. LTTB explicitly skipped per spec. A
    "bucketed" flag tells the client which shape it got; a full 86,400-row
    day is ~60 KB.

56. **The Money Strip is ledger-live, not snapshot-lagged.** colony_cash_u
    (SUM of living agents' balances) and marked_u (open lots x last price)
    come from the balances/positions tables at request time and are never
    summed on screen — realized and mark-to-market stay separate figures
    (spec v2 8.2). Extraction windows (today / hour / per-second) diff the
    arena account against the last colony_metrics row at or before each
    UTC boundary; a missing base row means "since genesis" because ARENA
    starts at 0.

57. **Line budget note.** colony/ stands at ~3,190 non-blank lines against
    the ~2,600 ceiling after stage 9; stage 11 owes a simplification pass
    (report.py and __main__.py are the least-earned lines).

58. **The v1 experiments are re-based, not rewritten.** profit_matrix now
    charges the venue's fill prices (spread ON, taker 20 + spread 2) and
    records the new friction baseline: trend_up/momentum +4.15 bps/t,
    mean_revert/mean_revert +5.77, the cross-cells still correctly
    negative — MATRIX PASS. real_market builds every rung from
    config.spy.json (spread on, fill delay 1, repro 1.08) with stakes in
    micro-dollars. regime_flip needed no code changes and re-validates
    FLAGSHIP PASS on all three seeds with the full v2 economy active
    (gate genes drawn randomly, immigration budget, annualized rent).

59. **The minute ladder separates machinery from economics (spec 7.4).**
    Verdicts per seed are PASS (audited profit), EXPECTED-FAIL (machinery
    sound, economics negative, numbers recorded), or FAIL (crash /
    invariant / incomplete replay) — only machinery can fail the
    experiment. The tape is digest-pinned (--digest refuses a changed
    CSV). --parallel fans (rung, seed) pairs out as subprocesses, each
    writing its own record (9.1). The dust rung ($10 total) demonstrated
    the immigration budget working as designed on the CI fixture: capacity
    $2.00 cannot afford a $2.50 seed, the colony dies out honestly, and
    the terminal audit returns every micro-dollar to the treasury.

60. **The soak is an orchestrator, not a monitor.** live_soak drives the
    acceptance scenario end to end as subprocesses: daemon up, one
    random-point proc.kill(), restart, resume verified by /api/health
    passing the kill tick, clean stop, replay-twin audit last. The audit
    verdict decides the soak verdict — invariants are already enforced
    per tick inside the daemon, so the soak only has to prove continuity
    and collect the evidence into one record.

61. **Final line budget: 3,169 non-blank lines against the ~2,600 ceiling,
    accepted with cause.** The dead-code pass removed everything actually
    dead (two v1 report helpers; the API layer had superseded them). The
    overage is three spec-mandated systems that did not exist when the
    ceiling was set against v1's 1,770: the daemon (292 — supervision, pid
    guard, health, signals), the replay-twin audit (155), and the
    Observatory v2 server surface (+110 for SSE, bucketing, tape,
    lineage). No module is fighting the ceiling with accidental
    complexity; shrinking further means deleting mandated behavior, so the
    honest move is recording the number, not gaming it.

62. **v2.0 acceptance status at build completion.** Machine-verifiable
    criteria hold: 158 tests green on the build machine (Windows, Python
    3.14); throughput 675 ticks/s measured (11.3: >=500 target, >=250 CI
    floor); hard-kill resume byte-identical (test_daemon); replay-twin
    audit PASS + CRITICAL paths tested offline; profit matrix PASS with
    spread on; regime-flip flagship PASS on all seeds under the full v2
    economy; real-market micro rung PASS (+10.8% audited cash, seed 42);
    minute-ladder machinery PASS on the CI fixture (dust rung
    EXPECTED-FAIL with honest budget exhaustion, per 7.4). Remaining
    operator-time items, commands ready: (a) fetch >=1y of BTCUSDT 1m and
    run `python -m experiments.minute_ladder --parallel` (11.1); (b) run
    `python -m experiments.live_soak --hours 24` with the feed up (11.2);
    (c) confirm the CI matrix goes green on GitHub's Windows and Linux
    runners.
