# BUILD SPEC v7 — the Dispersion Gate

Operator directive (2026-07-19): "go ahead, please make improvements and
changes as needed" — following the v6 report, which proposed exactly this
round. The v6 finding being built on: **momentum rotation earns its keep
only where the universe has real cross-asset dispersion** (6/9 and +31.7
pp/yr with crypto; 3/9 and −3.2 without), while on the correlated ETF
universe the winner was riding decade-scale regimes (the control's GLD
2005–2011, QQQ 2014–2020 runs). v7 turns that diagnosis into strategies
and tests them under the same protocol.

## 1. Universes and spans

Same tapes and universes as v6 (`U_FULL` 8 assets, `U_ETF` 6 ETFs). Every
historical holdout span is now spent (btc_1d, alloc, alloc6 — three .SHOTs),
so **no historical carve exists in v7**: both benches walk the full spans
(U_FULL 2017-08-17 →, U_ETF 2004-11-18 →), and windows overlapping spent
holdout regions are regression evidence, not validation. Fresh validation
comes only from the forward holdout (§4).

## 2. Families (deterministic, grids frozen here)

| family | idea | grid |
|---|---|---|
| `dm_gated` | every 21 days measure dispersion = (best − worst) trailing Lf-day return across the universe. If dispersion ≥ G, act as momentum (hold top-1 by Lf if its return > 0, else cash); if below, ride the regime (hold top-1 by trailing 378 days, no positivity filter) | Lf ∈ {63,126} × G ∈ {0.15,0.30} |
| `slow_bh` | the v6 control made explicit: always hold the trailing-L winner regardless of sign, rebalance every R days | L ∈ {252,378} × R ∈ {63,126} |
| `dm_cadence` | the cadence arm: dm_topk K=1 at rebalance period R instead of 21 | L ∈ {126,252} × R ∈ {5,21,63} |
| `dm_topk` | the v5/v6 incumbent, unchanged (reference) | K ∈ {1,2,3} × L ∈ {63,126,252} |
| `best_bh` (control) | buy-and-hold the train-window winner | one per asset |

`dm_cadence` [L,R=21] must reproduce `dm_topk` [K=1,L] exactly (bridge
test). No leverage, no seeds, 1-day signal lag, base venue tolls, integer
micro-dollars — all unchanged from v5/v6.

## 3. Benches

- **Bench A** (`U_FULL`, decisive for §4): 10 windows → 9 tests over the
  full 2017→now span.
- **Bench B** (`U_ETF`): 12 windows → 11 tests over the full 2004→now span
  (~1.8y windows) — the regime-robustness check for the same families.
- Train/test/judging identical to v5 §3 / v6 §3 (train-window selection by
  audited cash, frozen next-window OOS, win iff beats same-window SPY
  buy-and-hold, BEATS-SPX iff strict majority).

## 4. The forward holdout (pre-registered, fires on data that does not exist)

The one v7 shot is **forward**: it may only run on joint rows strictly
postdating 2026-07-19, and only once at least **126 new rows** (≈ 6 months
of SPY days) exist. The target is declared in
`data/holdout/alloc7.FORWARD` (committed) after bench A reports:
**the family with the highest mean OOS delta vs SPY across bench A's 9
test windows**, universe `U_FULL`. At fire time, parameters are re-selected
once on all rows ≤ 2026-07-19, frozen, then run on the post-cutoff rows;
the run writes `data/holdout/alloc7.SHOT` and reruns refuse. The runner
refuses any family other than the declared one and refuses while fewer
than 126 post-cutoff rows exist. This is the first fully uncontaminated
test available to the repo since the v5 shot: nobody, including the
operator, has seen the data it will run on, because it hasn't happened.
The declaration must not be edited after commit; the shot inherits the
2×/5× cost ladder from v6 §4.

## 5. Acceptance

- Records with the mandatory footer + spx line per judged window.
- Tests: leakage (future-corruption) across all v7 families; the
  `dm_cadence` R=21 ≡ `dm_topk` K=1 bridge; `slow_bh` stays fully invested
  once history exists; `dm_gated` provably switches modes on synthetic
  dispersion; flat-tape conservation; forward-holdout refusal both for a
  spent SHOT and for insufficient post-cutoff data; the FORWARD declaration
  parses and names a family from the declared grid.
- DECISIONS entries for every ratified rule; README gains a v7 section.

## 6. Red lines (unchanged from v4 §11)

Virtual money only. No order-placement code anywhere in the repository. No
self-modification. The core makes no network calls; `tools/` only reads
public market data.
