# BUILD SPEC v6 — the Universe Bench

Operator directive (2026-07-19): "take your learnings onto v6. expand on the
working idea and try new ones too." The v5 learnings being carried:

- **Cross-asset momentum rotation is the one survivor.** dual_momentum won
  5/7 test windows and the one-shot holdout (+18.00 pp/yr vs SPY), while the
  `best_bh` beta control failed — the rotation itself added the value.
- **Monthly cadence makes costs nearly irrelevant** (the v5 holdout win
  survived 5× venue friction in an informal probe). v6 formalizes that probe.
- **Faster is worse** (v4). Nothing in v6 trades more often than daily
  signals with ~monthly turnover; no mean-reversion scalping families —
  that axis is settled and closed.

v6 expands the working idea on two axes — **more assets** and **more
history** — and adds genuinely new families alongside the momentum variants.

## 1. Universes

| universe | assets | span | role |
|---|---|---|---|
| `U_FULL` (8) | spy, qqq, iwm, efa, gld, tlt, btc, eth | 2017-08-17 → last common day (crypto binds) | exploratory bench A |
| `U_ETF` (6) | spy, qqq, iwm, efa, gld, tlt | 2004-11-18 → last day (GLD binds) | decisive bench B + the v6 holdout |

New daily tapes fetched via the existing `tools/fetch_market_data.py` (the
only network code): `gld_d.csv`, `tlt_d.csv`, `iwm_d.csv`, `efa_d.csv`. Lot
denominators 100 (ETFs), 100 000 (crypto). Joint calendar, backward crypto
sampling, 1-day signal lag, base venue costs (10 bps taker + 2 bps spread),
integer micro-dollar accounting, $10,000 capital — all unchanged from v5 §2.

## 2. Families (deterministic, grids frozen here)

| family | idea | grid |
|---|---|---|
| `dm_topk` | every 21 days rank by trailing L-day return; hold top K equal-weight, each only if its return > 0 (empty slots stay cash). K=1 on the v5 universe recovers the v5 winner exactly. | K ∈ {1,2,3} × L ∈ {63,126,252} |
| `dm_1201` | classical 12-1 momentum: rank by return from day −252 to day −21 (skip the latest month), monthly, top-K with the same positive filter | K ∈ {1,2,3} |
| `dm_defensive` | top-1 momentum, but negative-momentum slots hold a defensive asset D instead of cash | L ∈ {126,252} × D ∈ {tlt,gld} |
| `sma_ew` | Faber-style tactical: every 21 days each asset is on (weight 1/N) iff close > SMA(L), else its slot stays cash | L ∈ {150,200} |
| `inv_vol` | inverse-volatility weights across the whole universe (63-day realized vol), rebalanced every R days; flat-vol fallback = equal weight | R ∈ {21,63} |
| `best_bh` (control) | buy-and-hold the train-window winner — the beta control, again | one combo per asset |

The first three expand the working idea (wider top-K, the classic skip-month
signal, defensive fallback); `sma_ew` and `inv_vol` are new ideas from the
tactical-allocation literature. No leverage anywhere; exposure ≤ 1.0; no
seeds (no RNG — robustness is windows + holdout, per v5 precedent).

## 3. Protocol

- **Bench A** (`U_FULL`): walk-forward over the *entire* 2017→now span,
  10 windows → 9 tests. No holdout is carved from this span — its final 20%
  was spent by the v5 shot and the momentum result there is known. Bench A
  windows overlapping that region are regression evidence, not validation;
  bench A never decides the holdout.
- **Bench B** (`U_ETF`): carve the final 20% of the equity-era calendar to
  `data/holdout/alloc6.csv` before anything else sees it (≈ 2022 → 2026,
  containing the 2022 bear market). Walk-forward the first 80% with
  10 windows → 9 tests spanning 2008, 2011, 2015, 2018, 2020.
- Train/test/judging identical to v5 §3: train window k selects by audited
  final cash (grid-order tiebreak), frozen on window k+1; win iff cash beats
  same-window SPY buy-and-hold at base costs; BEATS-SPX iff strict majority.

## 4. The holdout (one-shot, pre-registered)

The family with the **highest mean OOS delta vs SPY (pp/yr) across bench B's
9 test windows** fires one shot on `alloc6.csv`: parameters re-selected once
on the full bench-B grid span, frozen, run once. Writes
`data/holdout/alloc6.SHOT`; reruns refuse. This rule must not be overridden
post hoc (v5 §4 precedent).

**Contamination disclosure, written before any v6 code runs:** the final
months of this holdout overlap the spent v5 holdout span, and the operator
of this bench knows 4-asset momentum beat SPY there. The universe differs
(no crypto — the v5 holdout context showed BTC *lost* money over that span,
so the overlap does not pre-announce the equity-only outcome) and 2022→2024
is fresh, but a momentum-family win here is weaker evidence than the v5
shot was. Recorded here and in DECISIONS so the caveat cannot be quietly
dropped later. The fully clean test remains data postdating 2026-07-19.

After the shot, the frozen holdout run replays at 2× and 5× venue costs —
a diagnostic **cost ladder** in the same record (formalizing the v5 probe).
It never changes the verdict; it measures friction sensitivity.

## 5. Acceptance

- Records with the mandatory footer + spx line per judged window.
- Tests: equity-era calendar join; leakage (future-corruption) across all v6
  families; **bridge test** — `dm_topk` K=1 reproduces v5 `dual_momentum`
  targets on the v5 universe; weight invariants (sums ≤ 1, no leverage);
  flat-tape no-money-creation; alloc6 holdout guard refusal.
- DECISIONS entries for every ratified rule; README gains a v6 section.

## 6. Red lines (unchanged from v4 §11)

Virtual money only. No order-placement code anywhere in the repository. No
self-modification. The core makes no network calls; `tools/` only reads
public market data.
