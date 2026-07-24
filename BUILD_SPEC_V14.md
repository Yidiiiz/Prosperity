# BUILD SPEC v14 — the Survivorship Stress Bench

## 0. Premise

v13 measured cross-sectional momentum on a fixed 66-name large-cap universe and
disclosed the catch: that universe is a basket of *survivors*, so the vs-SPY
number is survivor-inflated. v13 controlled the **level** of that inflation with
`ew_all` (own every survivor, zero skill) and reported the residual —
xs_topk beats ew_all ~+17 pp/yr, 9/9 — as the honest edge.

But `ew_all` only cancels the inflation of the shared universe's *return level*.
It does not answer the sharper question: **would putting the dead companies back
into the ranking pool blow up the concentrated momentum book?** Momentum chases
recent winners; some of history's biggest winners (Enron, WorldCom, the
dot-coms) rose for years and then went to zero. A top-5 book that can step on
those landmines is more fragile than a 66-name equal-weight control that merely
dilutes them. v14 attacks that question.

Standing red lines (unchanged): virtual money only; no order-placement code; no
shorting; no leverage (exposure ≤ 1.0, long-only); core code makes no network
calls — only `tools/fetch_market_data.py` may.

## 1. The data finding (why the literal test is impossible)

The intended v14 was "fetch the graveyard and re-rank." It cannot be done
honestly. The only permitted network tool is Yahoo's chart API, and it does not
serve delisted price history:

- **404 (symbol gone):** LEH, ENE, WCOM, BSC, CFC, NT, EK, PALM, SUNW,
  GMGMQ, NRTLQ.
- **Empty shell (symbol resolves, zero price rows):** LEHMQ, ENRNQ, WAMUQ,
  WCOEQ, CPQ (the post-bankruptcy "Q" tickers).
- **Recycled symbol (resolves to a *different*, newer company):** WB (Weibo,
  2014→), CC (Chemours, 2015→), SHLD (2023→), GM (post-bankruptcy GM, 2010→) —
  none are the dead originals.

So a real delisted tape is unobtainable with the tools this repo allows, and
fabricating one and calling it history would violate the repo's integrity. v14
therefore answers the survivorship question three honest ways instead.

## 2. Families and grids (pre-declared; grid order breaks train-window ties)

- **`xs_topk`** — v13's raw cross-sectional momentum: top-K by trailing-L
  return, equal weight `1/K`, only positive-momentum names, short-fall = cash.
  Grid: K ∈ {5, 10, 20}, L ∈ {63, 126, 252}. The v13 baseline, carried forward.
- **`xs_skip`** — 12–1 momentum (Jegadeesh–Titman canonical): identical, but the
  trailing window **skips the most recent 21 days** (`skip = REBAL`), dodging
  short-term reversal. Grid: K ∈ {5, 10, 20}, L ∈ {126, 252} (L > skip always).
- **`ew_all`** — own every listed name equal-weight; the survivorship-beta
  control, no params. Carried forward from v13 as the decisive comparison.

Cadence, lag, tolls, integer micro-dollars, money-conserving rebalance, masked
loader: all inherited unchanged from v13 (`experiments.allocation13`).

## 3. Protocol

**Bench (real data).** Walk-forward over the masked calendar, `--windows`
(default 10): train window selects K/L by audited final cash (grid-order
tiebreak); frozen selection tested on the next window; win iff final cash > SPY
buy-and-hold over the SAME window at base costs; BEATS-SPX iff wins×2 > tests;
frontier = highest mean OOS delta vs SPY. The decisive comparison remains the
frontier momentum family **vs `ew_all`**, not vs SPY.

**Survivorship-direction test (real data).** Run xs_topk [K=5, L=63] (the v13
forward pick) and ew_all as two continuous daily-equity curves over the full
masked history. Classify each day by SPY regime (close ≥ / < its 200-day MA).
Attribute momentum's cumulative log-outperformance over ew_all to bull-regime
vs bear-regime days. Interpretation: an edge earned in **bear** regimes comes
from the absolute-momentum cash-exit sidestepping weak names — a real graveyard
would be sidestepped too, so survivorship *understates* the relative edge; an
edge earned in **bull** regimes is hindsight-winner chasing that survivorship
*inflates*.

**Synthetic graveyard stress (labeled, seeded — NOT history).** Inject `M`
phantom "landmine" names into the ranking pool. Each phantom lists at a random
date, follows calibrated positive-drift GBM (so it earns positive momentum and
can be chased), then at a random death date collapses ~-95% over ~40 days and
delists (becomes `None`; a holder is stuck at the last tradeable price).
Phantoms hit xs_topk and ew_all identically. Sweep `M` (delisting intensity)
across a fixed set of seeds; report the break-even intensity at which the
xs_topk − ew_all edge crosses zero. Every phantom price is synthetic, seeded for
reproducibility, and never written to a tape or a record as real history.

## 4. Holdout discipline (forward-only)

Same as v13: v14 carves **no historical one-shot** (survivor-shaped universe,
overlapping calendar). A clean forward is armed **by hand** only if a *new*
family (`xs_skip`) is the frontier AND beats `ew_all` out of sample —
`data/holdout/alloc14.FORWARD` names it, fired only on virgin post-cutoff rows
via `--holdout FAMILY --forward`; `--holdout` without `--forward` refuses
(rc 2). If xs_skip does not clearly improve on v13's already-armed xs_topk,
**no new forward is armed** — re-arming a near-duplicate would be a cherry-pick.

## 5. Tests (offline; synthetic + tmp CSVs — the real tapes are gitignored)

`xs_skip` skips the most recent 21 days (a spike inside the skip window does NOT
select a name that a raw-momentum spike would); `xs_skip` short-fall→cash and
all-negative→cash; the regime classifier labels bull/bear correctly against a
known 200-MA cross; the graveyard generator is deterministic under a fixed seed,
produces a rise-then-collapse-then-`None` path, and a held phantom inflicts its
collapse loss; ew_all eats a phantom that xs_topk's cash-exit escapes; the
no-forward refusal (rc 2) and `read_forward` parsing.
