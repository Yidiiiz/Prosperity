# BUILD SPEC v10 — the Careful Wish Bench

## 0. Premise

v9 implemented Dr. Eric Wish's strategies crudely and every family lost to
buy-and-hold on the 2010→2022 bull grid; `gmi_inv` (a single-MA gate that
flipped straight into a decaying leveraged inverse) was the *worst* family.
The operator's refinement, verbatim:

> "when the gmi signal is red, switch from buy and hold to an inverse or smth
> else, and when it is back to green switch back to the etfs. to avoid the
> bearish market. please use more careful approaches to each strategy. green
> line breakouts focus on when stocks hit all time highs, and if they go back
> down less than 5% of that all time high you sell. otherwise you stay in
> until that 5% drop."

Two corrections, implemented carefully here:

1. **GMI is a *count*, not one moving average.** The real GMI (Wish, 2005) is
   a 0–6 tally of six short/long-term indicators, mostly QQQ-centric. Green ≥3;
   Wish goes defensive below 4 and to cash below 3. v10 builds a 6-component
   **GMI-lite** from the index tapes the repo holds and switches on a
   *hysteresis* band (red below 3, green at/above 4) to suppress the whipsaw
   that sank v9's `gmi_inv`. When red, the destination — inverse **or** cash /
   bonds / gold — is a parameter, so "an inverse or smth else" is settled by
   the bench, head-to-head, instead of assumed.

2. **GLB exits on a percent stop from the all-time high, not a moving
   average.** v9 used a 150/210-day MA stop. v10 uses the operator's rule:
   ride the breakout and sell only when the close falls ~5% below the running
   all-time high (the green line, which keeps rising as new highs print).
   Re-entry requires a fresh green-line breakout.

### GMI-lite — honest disclosure

The real GMI's six components include two market-breadth counts over a
~4,000-stock universe (daily new highs; the 10-day Successful New High Index)
and a proprietary QQQ/SPY trend method. The repo has no wide-universe breadth
tape, so v10's GMI-lite approximates: components 1–5 are index-vs-MA and
short-trend signals on SPY/QQQ; component 6 is a *narrow* breadth proxy over
{spy, qqq, ita, itb}. This is a faithful trend-count in spirit, not the true
GMI. Disclosed in the record and the README.

## 1. Universe

`U_WISH2` = the v9 set on real tapes, bound by SQQQ inception (2010-02-11):

    spy qqq gld tlt ita itb sh psq sds spxu sqqq

Inverse legs pair to the index they hedge: qqq → psq (−1×) / sqqq (−3×);
spy → sh (−1×) / spxu (−3×). No margin, no synthetic shorts; leveraged inverse
exposure lives inside the fund and is held as an ordinary long lot ≤ 1.0 of
equity (red lines intact).

## 2. Families and grids (pre-registered; grid order breaks train ties)

- **`glb_pct`** — Green Line Breakout, percent trailing stop.
  Green line = highest close at least `GL_CONFIRM=63` trading days old (a
  confirmed all-time high ≥ ~3 months). Enter when close > green line. Exit
  when close ≤ (1 − p) × running all-time high. Grid: R ∈ {qqq, spy},
  p ∈ {0.03, 0.05, 0.08}.
- **`gmi_switch`** — GMI-lite hysteresis regime switch (the operator's core
  idea, careful). Green → hold index R; Red → hold destination D. Grid:
  R ∈ {qqq, spy}, D ∈ {cash, gld, tlt, inv1, inv3}, where inv1/inv3 resolve to
  R's −1×/−3× inverse. Hysteresis: flip to red when GMI < 3, back to green when
  GMI ≥ 4; hold through the 3-band to avoid churn.
- **`gmi_glb`** — GLB entries gated by GMI-lite (Wish only buys breakouts while
  GMI is green). Long only when GMI green AND a live green-line breakout; exit
  to cash on either the p-stop or GMI turning red. Grid: R ∈ {qqq, spy},
  p ∈ {0.05, 0.08}.
- **`best_bh`** — buy-and-hold each single asset; the control the whole
  exercise must clear.

`GMI-lite` count (history ≤ j; None before 200 days of warmup):
1. qqq > SMA(qqq, 50)   2. qqq > SMA(qqq, 150)   3. spy > SMA(spy, 50)
4. spy > SMA(spy, 200)  5. qqq[j] > qqq[j−10]    6. ≥2 of {spy,qqq,ita,itb} > own SMA(50)

## 3. Bench protocol (unchanged from v6–v9)

Joint daily calendar (SPY clock; other tapes sample latest close ≤ each day).
1-day signal lag (day i uses history ≤ i−1, fills at day i close). Integer
micro-dollars, CAPITAL_U = $10,000. Base venue 10 bps taker + 2 bps spread.
Walk-forward: train window k selects params by audited final cash (grid order
breaks ties); the frozen pick is tested on window k+1. Win iff final cash >
SPY buy-and-hold same window. Family verdict BEATS-SPX iff wins×2 > tests, else
NO-EDGE. Frontier = family with the highest mean OOS delta (pp/yr). Post-shot
COST_LADDER at 2× and 5× friction is diagnostic only.

**New diagnostic (does not change any verdict):** the historical shot also
reports the frontier's max drawdown vs SPY's over the reserved span, so the
drawdown-avoidance that market timing is actually designed for is visible even
though the score remains raw return.

## 4. Dual holdout

- **Historical carve** — the final 20% of the joint span is reserved
  (`data/holdout/alloc10.csv`), the frontier family re-selected on the first
  80%, and one shot fired on the carve. Contamination disclosed: the author
  knew this span held the 2018/2020/2022/2025 drawdowns. Writes
  `data/holdout/alloc10.SHOT`; reruns refuse.
- **Forward registration** — `data/holdout/alloc10.FORWARD` names the frontier
  family, universe `wish2`, cutoff = build date, `min_new_rows = 126`. It fires
  only once ≥126 joint rows postdate the cutoff (ripe ~2027 after refetching
  tapes): `python -m experiments.allocation10 --holdout <frontier> --forward`.
  Params are re-selected on rows ≤ cutoff at fire time, frozen, run once on the
  virgin rows. The clean test.

## 5. Tests (`tests/test_allocation_v10.py`)

Leakage (future closes cannot change any target); GLB enters on a breakout and
exits exactly on the p% stop from the peak (not before); GMI-lite hysteresis
holds through the 3-band and reaches both −1× and −3× destinations; gmi_glb
exits to cash when GMI goes red even without a price stop; no daily churn;
weights never exceed 1.0; flat tape never mints money; three forward refusals
(spent / unripe / undeclared-family); FORWARD declaration integrity.
