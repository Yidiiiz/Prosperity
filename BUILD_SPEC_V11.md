# BUILD_SPEC_V11 — the Regime-Gated Rotation Bench

## 0. Premise: fuse the two idea-lines

The operator's standing brief this session: *"please work on former promising
ideas, and this current one too. please make improvements and changes as
needed."*

Two lines of work exist, and each has the flaw the other fixes:

- **The former promising idea — cross-asset momentum rotation.** The only edge
  this repo ever validated out of sample (v5 dual_momentum, +18 pp/yr on its
  holdout; v7 dm_gated armed forward). Its diagnosed source is *dispersion*:
  rotation earns where return streams genuinely diverge, and the widest
  dispersion set the repo owns is the crypto-era universe (crypto + equities +
  international + gold + bonds). **Its flaw:** momentum is slow. A monthly
  rotation rides a crash weeks into it before the trailing return finally turns
  the pick defensive. Momentum has upside capture and no brake.

- **This current idea — the GMI regime timing (v10).** A 6-count breadth/trend
  regime read with a hysteresis band. **Its flaw (v8/v9/v10, three times over):**
  timing a *single index* in a correlated equity bull just sacrifices upside —
  best it ever did was lose by less. Timing has a brake and no upside capture.

v11 puts them together: **gate the momentum rotation on the GMI regime.** When
the regime is healthy, rotate into the strongest risk asset (momentum's upside,
including crypto); when it turns red, step to the safe sleeve (timing's brake).
Momentum decides *what to own when we're on*; GMI decides *whether we're on*.

This is the honest synthesis, and it isolates two real questions the earlier
benches could not:

1. **Does the brake help momentum?** `gated_mom` vs `pure_mom` — same rotation,
   with and without the GMI gate. If gating wins, the brake earned its keep.
2. **Does momentum help the brake?** `gated_mom` vs `gmi_bh` — same gate, but
   momentum's risk-on rotation versus timing a single index. If gating wins,
   the upside capture earned its keep.

## 1. Universe

`U_GATE` = `sorted(btc, eth, spy, qqq, iwm, efa, gld, tlt)` — the 8-asset
crypto-era set from v6's `U_FULL`. It is bound by the Binance tapes'
2017-08-17 inception, giving ~2,240 joint days through 2026-07-17 that contain
**real drawdowns for the brake to matter**: the 2018 crypto winter, the 2020
COVID crash, and the 2022 bear. No inverse or leveraged ETFs — v8/v9/v10 all
showed they decay from daily reset and lose to flee-to-safety; the safe sleeve
here is cash / gold / bonds, ordinary long positions only (exposure ≤ 1.0).

- **Risk sleeve** `RISK` = `(btc, eth, spy, qqq, iwm, efa)` — what momentum
  rotates among when the regime is green.
- **Safe sleeve** = `cash` / `gld` / `tlt` — where the GMI-red brake steps.

## 2. Families and grids (pre-declared; grid order breaks train ties)

- **`gated_mom`** `{L in (63,126,252), D in (cash,gld,tlt)}` — the synthesis.
  Green: top-1 trailing-`L` momentum among `RISK`, held if its return is
  positive else cash; rebalanced monthly (21d) and immediately on the red→green
  re-entry. Red: the safe destination `D`. No daily churn: hold between monthly
  rebalances while green, hold in `D` while red.
- **`pure_mom`** `{L in (63,126,252)}` — the momentum rotation alone (v5/v6
  incumbent over `RISK`), monthly, no gate. The upside-without-brake reference.
- **`gmi_bh`** `{R in (spy,qqq), D in (cash,gld,tlt)}` — the GMI timing alone
  (v10 `gmi_switch` minus the inverse legs, since this universe has none). Green:
  hold index `R`. Red: the safe destination `D`. The brake-without-upside
  reference.
- **`best_bh`** `{asset in U_GATE}` — the passive control.

### GMI-lite (unchanged 0..6 count, breadth improved for this universe)

Six trend components on history ≤ `j`, `None` during the 200-day warmup
(spec v10 0's disclosed proxy — the real GMI needs ~4,000-stock new-high
breadth this repo lacks):

1. QQQ > its 50-day SMA
2. QQQ > its 150-day SMA
3. SPY > its 50-day SMA
4. SPY > its 200-day SMA
5. QQQ > QQQ ten days ago
6. breadth: ≥ 2 of `{spy, qqq, iwm, efa}` above their own 50-day SMA

Improvement over v10: the breadth proxy now spans large-cap, tech, **small-cap
(IWM), and international (EFA)** — a broader read of market health than v10's
aerospace/homebuilder sectors, and every name is already in `U_GATE`.

Hysteresis band (unchanged): leave green only below `GMI_RED=3`, re-enter green
only at `GMI_GREEN=4`.

## 3. Bench protocol

Same machinery as v6/v7/v10: joint SPY calendar, 1-day signal lag (day `i` uses
history ≤ `i−1`, fills at `i`'s close), integer micro-dollars, `CAPITAL_U =
$10,000`, base venue 10 bps taker + 2 bps spread, deterministic (no seeds).
Walk-forward over `--windows` (default 10): train window `k` selects params by
audited final cash (grid-order tiebreak), the frozen selection is tested on
window `k+1`, each test judged same-window against SPY buy-and-hold. Family
verdict BEATS-SPX iff wins×2 > tests. Frontier = highest mean OOS delta (pp/yr).
The shot carries the v10 diagnostics: a **drawdown** line (the brake's whole
purpose) and the 2×/5× **cost ladder** (never change a verdict).

## 4. Holdout discipline — forward only

Every historical span is spent: the v5 shot consumed the final 20% of this same
2017→2026 crypto calendar, so no fresh carve exists here. v11 therefore fires
**no historical shot** and registers exactly one clean **forward** holdout:
`data/holdout/alloc11.FORWARD` pre-declares the bench frontier family on
`U_GATE`, cutoff 2026-07-23, `min_new_rows = 126`, firing once that many joint
rows postdate the cutoff (~2027 after refetching tapes), writing `alloc11.SHOT`;
reruns and other families refuse.

## 5. Tests (`tests/test_allocation_v11.py`)

Leakage (corrupted future leaves every family's target unchanged); the gate
mechanics (green→top-momentum risk asset, red→the safe destination, warmup→cash);
`gated_mom` no-daily-churn while green; `gated_mom` brakes to `D` on the
green→red flip even while the momentum pick is still rising (the synthesis's
reason to exist); `pure_mom` never brakes (holds the risk pick through a GMI-red
crash — the flaw gating fixes); GMI hysteresis band; weight ≤ 1 and flat-tape
conservation invariants; GMI-lite `None` during warmup; the three forward
refusals (spent / unripe / undeclared) and FORWARD-declaration integrity.
