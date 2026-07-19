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

63. **Realized P&L is the agent's net ledger flow against ARENA accounts**
    (spec v3 3.1: "sells minus buys minus fees, from the ledger"). One CASE
    aggregation over `ledger` joined on ARENA-kind counterparties gives
    sells − buys − fees exactly; identity checked in tests:
    realized == delivered-to-system + liquidation-holdings − funding.
    `active_days` floors at 1 day when the agent has any fill (a
    same-day scalper's rate should not divide by epsilon); zero fills means
    zero rate, keeping never-traded sitters off the profitmaker board.

64. **`bank_path` defaults to None — the bank is opt-in.** A colony with no
    configured bank runs exactly as v2 did (no admission at wind-down, no
    snapshot at init, no immigration draws), which is what keeps every v2
    archive resumable and replayable. `records_root` (default "records")
    was added alongside it so tests and experiments can redirect the §4.3
    admission record away from the repo's records/ tree.

65. **Extinction on a fully-consumed tape is economics, not machinery**
    (re-affirming #59 under v3 tiers). The real-market tiny rung wipes out;
    the machinery FAIL stamp is reserved for exceptions and invariant
    breaks. The rung reports EXPECTED-FAIL and the ladder headline stays
    honest about which bar each rung met.

66. **The solo probe is deliberately spartan: $1,000, no rent, no breeding,
    no immigration** (spec v3 4.4 "frozen"). Certification asks one
    question — does this genome alone extract cash from a postdating
    window — so the probe replays the pending-order/fill-window/gate
    semantics of the live arena (fill delay 1, 24h window, gates, terminal
    liquidation) and nothing else. Rent would measure treasury policy, not
    the genome.

67. **Schema evolution is additive; `user_version` stays 2.** v3 adds
    `bank_snapshot` and `agents.origin` via CREATE-IF-MISSING / ALTER at
    Orchestrator start. A version bump would have forced a migration
    story for v2 archives that a guard clause covers in four lines; the
    replay-twin byte-identity tests are the proof the guards are inert.

68. **Champion seeds that outrun the token bucket fall back to the base
    seed.** `champion_seed_multiple × gen0_seed` is drawn from the same
    immigration bucket as everyone else (spec v3 5.3 "no new money"); if
    the bucket cannot afford the multiple it funds a plain gen-0 seed
    instead of waiting, so a lean bucket degrades the *privilege*, never
    the *flow*, of immigration.

69. **Accrual tops up to base capacity; the ratchet alone exceeds it.**
    The APR accrual stops adding tokens at base capacity (as in v2), while
    a compounding redeploy may push the bucket to 4× base (spec v3 5.4).
    Without the clamp ordering, accrual after a redeploy would double-count
    headroom. The high-water mark is one-way and persisted in run state,
    so resume cannot re-trigger a redeploy already taken.

70. **Footer entries carry optional per-entry spans.** Walk-forward
    annualizes each out-of-sample window over *its own* dates, not the
    whole tape (a +30% window over 8 years is not +30%/8yr). `footer()`
    accepts 4-tuples (whole-span) and 6-tuples (own-span); every other
    caller keeps the 4-tuple form.

71. **The A/B experiment disables admission arithmetically, not with a
    flag.** Both arms run `bank_min_fills = 10^9` (and top-k 1), so
    neither arm can write to the bank mid-experiment — arm B *reads* a
    snapshot, nothing writes. One mechanism, zero new config surface,
    and the reuse measurement can't contaminate its own input.

72. **Held-out enforcement is by date interval, not file identity.**
    `bank_reuse` refuses (SystemExit) if the held-out window overlaps any
    banked genome's admission *or* certification window — comparing dates
    catches a re-sliced CSV of the same bars, which a filename or digest
    check would wave through.

73. **Breakout trails from an approximated post-entry high.** The strategy
    keeps no per-agent state between decisions (archetypes are pure
    functions of history + position), so the trail reference is
    `max(history[-(hold+1):])` — the high since the entry could last have
    happened. It can only be ≥ the true post-entry high, so the trail
    triggers no later than a stateful version; entries pay gates and
    fee_aware, exits are always allowed (#50).

74. **Profit-matrix bars for breakout: ≥ +2.0 bps/t on trend_up, ≤ 0.0 on
    mean_revert.** Same shape as momentum's criteria: the archetype must
    make real money in its home regime and must not thrive where its
    premise is false (measured: +5.30 and −38.24).

75. **Walk-forward EDGE is a strict majority of test windows** (wins × 2 >
    tests), pooled certified-champion cash vs pooled B&H per window. A
    windowless tie (0/0) is NO-EDGE. Measured on SPY 1993–2026, 4 windows
    × 3 seeds: NO-EDGE across the board — champions beat B&H only in the
    2001–2009 test window (the one containing two crashes). The
    measurement is the deliverable; the spec forbids demanding EDGE.

76. **`colony run`'s footer marks, it does not audit.** The run command
    prints "system total (marked)" against the benchmark because open
    positions exist mid-run; only wind_down/experiment records use
    audited-cash language. No bare "PASS" appears in any replay output
    (spec v3 2.4).

77. **One v2 test re-based, none deleted.** The 4-way archetype rotation
    changed which gen-0 agents are sitters; the lifecycle stagnation test
    now filters to sitters born early enough to exhaust their grace
    window instead of assuming rotation order. Every other v1/v2 test
    passes unchanged.

78. **v3 line budget: 3,826 non-blank lines against the ~3,600 ceiling,
    accepted with cause** (continuing #57/#61). The +657 over v2's 3,169
    is the spec's own new machinery: benchmark.py (72), bank.py (193),
    and the reuse/compounding/Champions surface spread across
    orchestrator, server, report and the CLI (~390). As at v2 close,
    nothing is dead weight — shrinking further means deleting mandated
    behavior, so the honest move is recording the number.

79. **v3.0 acceptance status at build completion.** Machine-verifiable
    criteria hold: 198 tests green (Windows, Python 3.14); every replay
    verdict is tiered against buy-and-hold with the mandatory
    span/wall/annualized/benchmark footer, no bare "PASS" in replay
    output; realized-P&L profitmaker boards in report/inspect; bank
    admission + out-of-sample certification with the postdating refusal
    tested; replay-twin audit byte-identical with bank immigrants
    present; compounding ratchet tested including resume. Evidence runs
    (records/): SPY real-market ladder full/micro/tiny all CASH; profit
    matrix PASS with breakout (+5.30 bps/t trend_up, −38.24
    mean_revert); regime-flip flagship PASS all seeds; SPY walk-forward
    1993–2026, 4 windows × 3 seeds: **NO-EDGE** (champions beat B&H
    only in the 2001–2009 crash window), zero machinery failures;
    held-out reuse A/B (train ≤2018-02, test from 2018-03): **B ≥ A on
    all three seeds** (+$1,203.59, +$512.13, ±$0 — the third drew no
    bank immigrants), direction reported, not demanded. Remaining
    operator-time items (10.7), commands ready: (a) year-long minute
    ladder re-run under v3 tiers (`python -m experiments.minute_ladder
    --parallel` with ≥1y of BTCUSDT 1m), (b) 24h live soak
    (`python -m experiments.live_soak --hours 24`), (c) green CI matrix
    on GitHub's Windows and Linux runners.

80. **BUILD_SPEC_V4.md ("The Edge Hunt") authored on operator directive.**
    The operator asked to sweep trading frequency (second → daily) and
    assets (crypto, index ETFs) for "a way to earn money faster than the
    S&P". The spec turns that into a falsifiable measurement: a frequency
    × asset grid where every cell is walk-forward certified out-of-sample
    and judged against SPY buy-and-hold over the *same calendar window*.
    As with v3's EDGE (#75), BEATS-SPX is reported, never demanded — the
    acceptance criterion is the measurement, not the direction.

81. **Cadence profiles are a registry; daily and minute are byte-identical
    to v3.** `experiments/profiles.py` holds four factories. `daily`
    replicates walk_forward's config (walk_forward now imports it back,
    so v3 experiments re-run unchanged); `minute` wraps
    minute_ladder.base_config. Only `hourly` (max_age 365d, stagnation
    30d, min_ticks 200) and `second` (config.live.json lifecycle with
    hourly snapshots, min_ticks 300) are new. The fairness rule (v4
    §3.3): capital, archetypes, gates, and venue costs are identical
    across cadences — only tick cadence, lot size, and wall-time
    lifecycle vary, so a frequency comparison compares frequency and
    nothing else. A test asserts the invariant field-by-field.

82. **The SPY yardstick compares same-window CAGR only, at base venue
    costs.** `yardstick.spx_over` runs buy-and-hold on SPY over exactly
    the cell's test-window dates; comparing a cell against SPY's long-run
    CAGR is forbidden (a 2026 crypto window vs 1993–2026 SPY is not a
    comparison). SPY coverage below 0.9 of the window is labeled
    "(partial SPY coverage)"; a window with zero SPY rows is *skipped* —
    it does not count as an SPX test, because a comparison with no
    yardstick data is not a comparison either way. The yardstick always
    charges base venue costs even in cost-arm runs: the counterfactual
    applies to the strategy, not to the benchmark.

83. **BEATS-SPX requires a strict majority of test windows (wins × 2 >
    tests), same rule as EDGE (#75).** Consequence, accepted: btc_1d
    shows mean OOS +17.5–33.3 %/yr vs SPY's +9.65 across seeds yet
    verdicts NO-EDGE, because the wins concentrate in a minority of
    windows. A mean can be rescued by one lucky window; a majority
    cannot. The rule stands even where it costs us the headline.

84. **The holdout is 20% of every tape, carved before windowing, spent
    once.** The grid driver slices the final 20% of rows to
    data/holdout/<cell>.csv before split_windows ever sees the tape, and
    asserts the last window ends before the holdout begins. The one-shot
    `--holdout` run writes a `.SHOT` guard file and refuses to run again
    — a second look at a holdout is data snooping, and the machinery
    enforces the discipline rather than trusting the operator's memory.

85. **Holdout carving is atomic; Windows contention is verified, not
    retried.** Parallel seeds of one cell carve concurrently, so the
    carve writes to a pid-suffixed tmp file and os.replace()s it into
    place. On Windows, replace fails with PermissionError when another
    seed holds the target open for reading; the loser deletes its tmp
    and *verifies* the existing file's rows equal what it would have
    written (SystemExit on drift). Both races were observed live
    (eth_1h seed 7, btc_1m seed 7) before the fix.

86. **Big tapes stay out of git; digests are pinned in records.** The 1s
    tape alone is 604,802 rows; btcusdt_1s/1m, all *_1h tapes, and
    data/holdout/ are gitignored. Every grid record pins the tape's
    digest and row count in its config line, so a re-run on different
    bytes is detectable even though the bytes aren't versioned.

87. **The cost arm ran on btc_1s and separated friction from signal.**
    At base costs (10/2 bps) and cheap (2/1), zero lineages certified on
    second bars across all seeds — friction kills every candidate before
    out-of-sample. At free (0/0, labeled COUNTERFACTUAL — no retail
    venue offers these costs), champions certify at last: seed 7
    "BEATS-SPX" on its single test window by losing 25.7 %/yr while SPY
    annualized −90.7 over the same 1.9 down days; seeds 42/2026 NO-EDGE.
    Conclusion, recorded not assumed: second-scale trading fails on
    friction *first* (any real venue) and signal *second* (even free,
    the best result is losing slower than a falling yardstick). The
    operator's 1 %/hour hypothesis is dead at both layers.

88. **The holdout went to btc_1d by the pre-registered rule, not to
    eth_1d by its verdicts — and said NO-EDGE.** Spec §6.2 fixes the
    holdout cell as best OOS annualized delta vs SPY; that is btc_1d
    (deltas +7.9/+17.8/+23.7 pp/yr, sum ≈ 49 vs eth_1d's ≈ 30) even
    though only eth_1d earned BEATS-SPX verdicts in-grid. Overriding
    the rule after seeing verdicts is the post-hoc selection the rule
    exists to prevent, so the rule fired as written. Result, one shot,
    2024-10-06 → 2026-07-19 (1.78y, 652 rows): 0/3 seeds beat SPY
    (champions −2.23/+1.82/−1.11 %/yr vs SPY +16.16; BTC B&H itself
    only +1.54). The grid's star cell was riding an asset whose beta
    then went flat — which is precisely what a holdout is for. The
    .SHOT guard now forbids reruns; a second look requires data that
    postdates 2026-07-19.

89. **v4.0 acceptance status at build completion.** All five §10
    criteria hold. (1) 214 tests green (198 v3 + 16 v4). (2) Every §4.1
    cell recorded with digest, span, per-seed verdicts, three-way
    footer; frontier table in the summary record. Grid verdicts, base
    costs, mean OOS %/yr vs SPY same-window: spy_1d −0.4..−0.6 vs +4.6;
    qqq_1d +1.9..+2.5 vs +8.0; btc_1d +17.5..+33.3 vs +9.7 yet NO-EDGE
    by window majority (#83); eth_1d BEATS-SPX ×2 seeds (+16.2, +27.2
    vs +14.9), NO-EDGE ×1; btc_1h +5.8..+9.4 vs +22.2; eth_1h
    −6.4..−0.8 vs +22.2; btc_1m one certified line at −89.7, two seeds
    zero champions; btc_1s zero champions certified at any seed. The
    frequency gradient is monotone the *wrong* way for the fast-trading
    thesis: daily > hourly ≫ minute > second. (3) Cost arm recorded
    with counterfactual labels (#87). (4) The holdout ran exactly once:
    **HOLDOUT btc_1d: NO-EDGE, 0/3 seeds** (#88) — the recorded v4
    headline. (5) BEATS-SPX was not required, and indeed did not
    survive the holdout: at retail costs on these tapes, nothing here
    earns faster than the S&P out-of-sample. That sentence is the
    deliverable. Line budget: experiments/ is 1,618 non-blank against
    the ~1,600 ceiling (+18, accepted — the overage is the holdout
    guard and the Windows carve-race handling, both mandated);
    colony/ untouched at v3's surface. The .SHOT guard is committed
    (gitignore exception) so the rerun refusal survives clones.

90. **BUILD_SPEC_V5.md ("the Allocation Bench") authored on operator
    directive.** The operator asked to "brainstorm ideas to beat s&p
    and keep testing." v4 settled the frequency axis (daily won and
    still lost the holdout), so v5 tests the axis v4 could not: which
    assets to hold and when, at daily cadence only. Four hand-crafted
    parameterized families (dual_momentum, trend, equal_weight,
    vol_target) plus a best_bh beta control over SPY/QQQ/BTC/ETH, with
    every parameter grid pre-declared in the spec before any code ran.
    No leverage anywhere: a financing-cost model would be invented
    rather than measured. No seeds: the families are deterministic
    (deviation from the three-seed convention, accepted because there
    is no RNG to vary — robustness is 7 test windows plus the holdout).

91. **The joint calendar samples crypto backward, never forward, and
    signals lag fills by one day.** Master clock = SPY trading days
    inside the span all four tapes cover (2017-08-17 → 2026-07-17,
    2,240 rows). Crypto trades weekends; each SPY day takes the latest
    crypto close ≤ that day. Signals on day i use closes through i−1
    and fill at day i's close, mirroring the colony's
    fill_delay_ticks = 1. A machinery test corrupts all data ≥ i and
    asserts targets are unchanged — future-leakage is a test failure,
    not a code-review hope. All fills pay base venue costs (10 bps
    taker, 2 bps spread) through the same risk helpers agents use.

92. **Bench results (7 test windows, train-window selection, frozen
    OOS): three families certified BEATS-SPX; the beta control did
    not.** dual_momentum 5/7 windows beat SPY (mean OOS delta +122.2
    pp/yr — dominated by the w4 melt-up window where L=252 held
    BTC/ETH through Apr 2020→Feb 2021, $10,000 → $72,583); trend 4/7
    (+0.5); equal_weight 4/7 (+53.3); vol_target 2/7 NO-EDGE (−3.7);
    best_bh 3/7 NO-EDGE (−5.1). The control's failure is the point:
    picking last window's best asset loses, so momentum's majority is
    not explained by asset selection alone. Means are outlier-heavy;
    the win-count majority (v4 §5 strict rule) is the verdict that
    matters, and dual_momentum leads on both.

93. **The v5 holdout fired once at dual_momentum [L=252] and said
    BEATS-SPX — the first positive holdout in this repository.** The
    pre-registered rule (v5 §4: highest mean OOS delta) chose the same
    family the win counts chose. Params re-selected on the full 1,792-
    row grid span, frozen, run on the carved final 448 rows
    (2024-10-02 → 2026-07-17, 1.79y — the same flat-crypto period that
    killed v4's btc_1d): $10,000 → $16,871.76 (+33.99%/yr) vs SPY
    buy-and-hold $13,035.91 (+15.99%/yr), delta +18.00 pp/yr. It also
    beat every single-asset buy-and-hold on the holdout (btc $10,518,
    eth $7,774, qqq $14,394, spy $13,036): the rotation added value
    over even the best asset it could have sat in. Caveats recorded
    with the result: one window, one shot, 1.79 years; cross-asset
    momentum is the best-documented anomaly in the literature *and*
    is known for decade-scale crashes; this is evidence, not proof.
    data/holdout/alloc.SHOT forbids reruns; a second look requires
    data postdating 2026-07-19.

94. **v5 acceptance status at build completion.** Spec §5 holds:
    records carry the mandatory footer and per-window spx lines;
    tests cover the calendar join, the cost round-trip (22 bps), the
    future-corruption leakage check, deterministic selection from the
    declared grid, the holdout guard refusal, and the no-leverage cap;
    experiments/allocation.py is one module (~300 lines) reusing v3/v4
    risk, benchmark, yardstick, and Record machinery unchanged. The
    holdout CSV stays gitignored (digest-derivable from the four
    committed tapes); the .SHOT is committed via the existing
    gitignore exception. Red lines untouched: virtual money only, no
    order placement, no self-modification, no network calls in core.
