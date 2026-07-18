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
