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
