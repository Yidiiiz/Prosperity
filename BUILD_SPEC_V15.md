# BUILD SPEC v15 — the Wish Bench, done properly

## 0. Premise: the excuse is now testable

Three versions implemented Dr. Eric Wish's system (wishingwealthblog.com) and
three versions printed the same apology:

- v9: *"The full 6-component GMI (breadth, new-highs, IBD lists) needs
  market-internals data this repo does not carry."*
- v10: *"The repo has no wide-universe breadth tape, so v10's GMI-lite
  approximates: components 1–5 are index-vs-MA … component 6 is a narrow
  breadth proxy over {spy, qqq, ita, itb}."*
- v11: *"the real GMI needs ~4,000-stock new-high breadth this repo lacks."*

Every one of those benches then lost, and the loss was booked as a verdict on
*timing* (decision 119: "the GMI brake is a drag"; v12 §0: "downside timing is a
drag … four times over"). But the thing that lost was never the GMI. It was a
tally of index moving averages wearing the GMI's name. **The real GMI is a
breadth instrument** — four of its six components count what the broad market's
individual stocks are doing, and none of them were computed.

v13 changed the facts on the ground: it fetched 66 large-cap tapes into
`data/stocks/` for the cross-sectional momentum bench. The repo now owns a stock
cross-section. The breadth components can be computed for the first time, so the
excuse can be retired one way or the other.

v15 also fixes a second misapplication. Wish's Green Line Breakout is a
**stock-selection** rule — "a strong stock breaking out to an all-time high after
at least a 3-month consolidation." v9 and v10 ran it on QQQ and SPY, which are
the two instruments a GLB is least likely to fire usefully on. Its native habitat
is the cross-section, and the cross-section now exists.

Standing red lines (unchanged): virtual money only; no order-placement code; no
shorting; no leverage (exposure ≤ 1.0, long-only); core code makes no network
calls — only `tools/fetch_market_data.py` may.

## 1. The source rules (wishingwealthblog.com, as published)

**GMI — six components, 1 point each, 0..6.** Green signal at GMI ≥ 4, red at
GMI ≤ 2, and the count must hold two consecutive days to flip the signal.

1. Successful 10-Day New High Index > 100 — of ~4,000 actively traded stocks,
   those that hit a 52-week high 10 days ago and closed higher today.
2. Daily new 52-week highs ≥ 100.
3. QQQ daily trend up — above its 10-day EMA and above its close 5 days ago.
4. SPY daily trend up — same rule.
5. QQQ weekly trend up — above its 10-week (50-day) average, and the 10-week
   above the 30-week (150-day). Wish calls this the component that matters most
   for staying invested.
6. T2108 > 50% — percentage of NYSE stocks above their 40-day simple average.
   (Wish's 2005 formulation used the IBD Mutual Fund Index vs its 50-day MA;
   the modern daily table reports T2108. See §2 for which one v15 can compute.)

**GLB — Green Line Breakout.** On a monthly chart, a green line marks an
all-time high that has stood unpenetrated for three consecutive months; the buy
is the breakout above it. The operator's own v10 refinement of the exit stands:
ride it and sell when the close falls ~5% below the running all-time high.

**RWB / BWR / RLC.** Six short exponential averages (3, 5, 8, 10, 12, 15) fanned
*above* six long ones (30, 35, 40, 45, 50, 60), with white space between, is the
RWB uptrend; the inverse fan is BWR. Periods are weeks or days — the glossary
allows both. RLC counts how many of the six short averages the close sits above:
6 is the preferred entry, 0 the exit.

## 2. What the tapes can and cannot support (state it before running anything)

`data/stocks/*_d.csv` and the ETF tapes are `Date,Close`. No highs, no lows, no
volume. Consequences, each disclosed in the record:

- **New highs are CLOSING highs.** Wish's counts use intraday 52-week highs;
  v15's use closing highs. Systematically fewer, and a small level shift, not a
  direction shift.
- **The green line is a closing all-time high.** Reuse v9's deterministic form
  verbatim so results stay comparable: green line at day *j* = the highest close
  at least 63 trading days (~3 months) old; a breakout is a close above it.
- **Volume is unavailable**, so Wish's volume confirmation and the thirteen
  EasyScans are out of scope. Not approximated, not faked.
- **T2108 is a 66-name proxy**, not NYSE-wide: the fraction of *listed* universe
  names above their own 40-day SMA. Component 6 uses this; the IBD Mutual Fund
  Index has no fetchable tape and is **not** substituted with something
  unrelated — the substitution is named in the record.
- **The breadth universe is 66 names, not ~4,000.** The absolute thresholds
  ("> 100", "≥ 100") are meaningless at N=66 and are re-expressed as fractions
  of the listed universe (§3, `B`).

**The bias that matters, stated in advance.** The 66 names are survivors, and
breadth measured on survivors reads **optimistically**: survivors print more new
highs and fewer new lows than the market they are drawn from. So v15's GMI-real
will sit GREEN more often than the true GMI did, and the gate will be *late* to
turn red — biased toward looking like the ungated control, and biased against
finding a brake that works. If the gate nevertheless helps, the bias worked
against that result. If it hurts, survivorship is a live alternative explanation
and must be reported as one, not buried.

> **Resolved this run — this prediction FAILED, and the test was confounded.**
> The 66 names print a new closing high on **5.6%** of name-days; SPY prints one
> on **11.0%** of days — fewer, not more. But the comparison cannot settle the
> question either way: SPY is one *diversified index* that grinds along its own
> high, while the 66 are individual volatile names each off its own high most of
> the time. The gap measures diversification, not survivorship. The claim is
> retracted rather than reinterpreted; `ew_all` (v13) remains the survivorship
> control, v14 settled the direction on returns (it **inflates**), and no
> breadth-based read of it exists while the delisted tapes are unobtainable
> (v14 §1).

## 3. GMI-real (the new instrument)

Computed on history ≤ `j`, `None` during a 252 + 40 day warmup. Breadth spans
the listed subset of the v13 universe; components 3–5 use the `qqq`/`spy` tapes.

| # | component | v15 form |
|---|---|---|
| 1 | Successful 10-day new-high index | fraction of listed names that made a 252-day closing high at `j−10` and close higher at `j` ≥ `B` |
| 2 | New 52-week highs | fraction of listed names at a 252-day closing high ≥ `B` |
| 3 | QQQ daily trend | `qqq[j] > EMA10(qqq, j)` and `qqq[j] > qqq[j−5]` |
| 4 | SPY daily trend | same rule on `spy` |
| 5 | QQQ weekly trend | `qqq[j] > SMA50(qqq, j)` and `SMA50(qqq, j) > SMA150(qqq, j)` |
| 6 | T2108 proxy | fraction of listed names above their own 40-day SMA > 0.50 |

**Calendar.** GMI-real needs the QQQ tape, which starts 1999-03-10, so the bench
window opens at the first day the count is computable (master row 1691,
1999-10-11) and **every family is judged on that identical span** — no family
gets a warmup fudge. The master arrays are *not* truncated, only the window
bounds move, so momentum lookbacks still reach the pre-1999 rows they saw in
v13. An explicit warmup guard (`j ≥ 252 + 10`) is required because
`rolling_new_high` reports False, not None, before a name owns 252 closes;
without it the count would go live with both new-high components silently
pinned at zero and read red for a year of tape.

`B` is the fraction-scaled stand-in for Wish's "100 of ~4,000" (= 2.5%). At N=66
that is ≥ 2 names, and on large-cap survivors components 1–2 may then be pinned
at 1 and carry no information — a real risk of building a 6-count that is
secretly a 4-count. Rather than tune `B` to taste, it is a **pre-declared grid
axis** `B ∈ {0.025, 0.05, 0.10}` selected in the train window like every other
parameter, and the per-component positive rate is reported (§5 fidelity) so a
pinned component is visible rather than assumed away.

Signal: two-consecutive-day confirmation, green at ≥ `GREEN`, red at ≤ 2 — Wish's
published band, replacing the v10/v11 hysteresis constants.

## 4. Families and grids (pre-declared; grid order breaks train-window ties)

Universe, masked calendar, 21-day cadence, 1-day signal lag, `BASE_VENUE`
(10 bps taker + 2 bps spread), integer micro-dollars, money-conserving
sells-before-buys: all inherited unchanged from `experiments.allocation13`.

- **`xs_topk`** — v13's cross-sectional momentum, the incumbent and the thing to
  beat. Grid: K ∈ {5, 10, 20}, L ∈ {63, 126, 252}.
- **`ew_all`** — own every listed name equal weight; the survivorship-beta
  control. No params. **The decisive comparison remains vs `ew_all`**, not SPY.
- **`wish_gmi`** — `xs_topk` at the frozen v13 forward pick [K=5, L=63], gated by
  GMI-real: green → hold the momentum book, red → cash, re-entering on the
  green flip rather than waiting for the next 21-day boundary. Grid:
  `GREEN ∈ {4, 5}`, `B ∈ {0.025, 0.05, 0.10}`. K/L are **frozen on purpose** —
  the family exists to isolate the gate, not to re-optimize momentum.
- **`glb_xs`** — Wish's GLB in its native habitat. Each day, the eligible set is
  the listed names whose close exceeds their green line (highest close ≥ 63
  trading days old); own up to K of them, equal weight, most recent breakout
  first; short-fall is cash. Evaluated daily, trades only when the held set
  changes. Grid: K ∈ {5, 10}, `STOP ∈ {0.05, 0.10}`, `ANCHOR ∈ {green, entry}`.

  **The exit is the operator's rule, stated precisely: "put a stop loss 5% under
  every time you do the green line breakout and raise it as it increases."** The
  stop is therefore set ONCE at entry and then *ratchets* — it is not recomputed
  each day from the running all-time high, which is what v10 (and this spec's
  first draft) did. The distinction is the whole rule: a stock that has just
  broken out is trading at its own ATH, so an ATH-anchored stop sits *above* the
  breakout level and ejects on the first retest, while an entry-anchored stop
  leaves the trade room to retest and hold. Two consequences fall out:
  - `ANCHOR` settles the one genuine ambiguity in "5% under the breakout"
    head-to-head rather than by assumption (the v11 precedent for the safe
    destination `D`): 5% under the green **line** (the breakout pivot) or 5%
    under the breakout **close**. They differ by the size of the breakout gap.
  - A stopped-out name is still sitting above its old, lower green line, so a
    naive eligibility test re-buys it the next day and the stop means nothing.
    Re-entry therefore requires a **fresh** breakout — a green-line cross that
    postdates the exit.
- **`glb_sel`** — the operator's refinement: *don't take every breakout, only
  the good-looking ones.* Identical to `glb_xs` in every respect (green line,
  entry-anchored ratcheting stop, fresh-breakout re-entry, daily evaluation) but
  the candidate pool is filtered and ranked by quality instead of by breakout
  recency, which was arbitrary. Wish does not buy every breakout; he buys
  breakouts in strong growth names. Grid: K ∈ {5, 10}, `STOP ∈ {0.05, 0.10}`,
  `QUAL ∈ {rwb, mom, both}` — `rwb` demands the chart look right (RLC = 6,
  ranked by fan alignment; the RWB pattern *is* the good-looking chart), `mom`
  ranks by trailing `QUAL_L`=126 return, `both` filters on the fan and ranks by
  momentum. `ANCHOR` is frozen at `green` (measured non-load-bearing above), so
  the selective grid is the same size as the unselective one it is compared
  against. **`glb_sel` vs `glb_xs` is a clean A/B of selectivity alone.**

  One structural fact this design must not paper over: a GLB-eligible name
  closes above *every* close older than `GLB_HOLD`=63 days, and `QUAL_L` > 63,
  so its trailing return is positive **by construction**. `QUAL='mom'` can
  therefore only *rank* candidates — it can never veto one. Only the RWB fan
  actually rejects.

- **`glb_wide`** (§4a) — the widened search: 6 selection statistics ×
  13 stop rules × K ∈ {5, 10} = **156 cells**. Statistics: `mom` (trailing
  126-day return), `rs` (the same relative to SPY), `vol` (calmest breakout
  first), `base` (longest consolidation — how many days the green line's high
  has stood), `prox` (least extended above the line first), `rwb` (the fan).
  Stop rules: `pct` at 5/8/10/15/20/30/50%, `atr` at 0.5/1.0/1.5/2.5 × the
  name's own realized vol × √21, and `ma` under the 10-week (50d) and 30-week
  (150d) averages — Wish's own trail lines. Every mode is set at entry and only
  ever raised, so they differ in how the distance is *measured*, not in whether
  the stop ratchets.

  **`glb_wide` is kept separate from `glb_sel` on purpose**: widening a search
  usually buys in-sample performance and pays for it out of sample, and the
  narrow pre-declared grid is the control that measures whether that happened
  here. `--mode sweep` reports the search as a **distribution** — median cell,
  share of cells beating SPY, and per-row breakdowns — because with 156 cells
  the best cell exists whether or not anything works, while a *monotone shift
  across a whole row* is evidence a single tall cell can never be.

- **`rwb_xs`** — the RWB fan as a ranker. Score = number of the 66 adjacent
  ordered pairs across the twelve EMAs (3, 5, 8, 10, 12, 15, 30, 35, 40, 45, 50,
  60, daily) that are correctly stacked short-above-long; own the top K scorers
  that also have RLC = 6, drop any held name at RLC = 0, equal weight,
  short-fall is cash. Grid: K ∈ {5, 10}.

## 5. Protocol

**Bench.** Walk-forward over the masked calendar, `--windows` (default 10): the
train window selects params by audited final cash (grid-order tiebreak); the
frozen selection is tested on the next window; a window is a win iff final cash
> SPY buy-and-hold over the SAME window at base costs; BEATS-SPX iff
wins × 2 > tests; frontier = highest mean OOS delta vs SPY. Survivorship-neutral
read = frontier vs `ew_all`.

**Gate A/B (`--mode gate`).** `wish_gmi` against `xs_topk` at the identical
frozen [K=5, L=63] over the full masked history — same book, gate on and gate
off, one difference. Report final cash, max drawdown, and (following v14) the
edge decomposed by SPY 200-day regime. This is the clean answer to the question
v9–v12 only thought they answered.

**Fidelity (`--mode fidelity`).** Diagnostic, not a strategy:
per-component positive rate for GMI-real (exposing any pinned component);
day-by-day agreement between GMI-real and v11's `gmi_count` GMI-lite; and the
survivorship read — the universe's new-closing-high rate against SPY's own, the
gap being the optimism §2 predicts. If GMI-real and GMI-lite agree ~always, then
v9–v12's verdict stands unchanged and v15's contribution is that the excuse was
never load-bearing. That is a publishable negative and is pre-declared as one.

## 6. Prior, pre-registered

Four benches say the brake loses, and v14 established that this universe's
momentum edge is earned in **bull** regimes (+3.54 log-edge bull, −1.06 bear) —
a gate that cuts bull exposure is cutting exactly where the edge lives. So the
expectation is that `wish_gmi` loses to ungated `xs_topk`. Recording that here
means a loss is a confirmation rather than a story, and it means the interesting
result — the one that would overturn four versions — is the other one.

## 7. Holdout discipline (forward-only)

v15 carves **no historical one-shot**: survivor-shaped universe, and a calendar
that overlaps every span that shaped these priors. A forward is armed by hand
only if a *new* family (`wish_gmi`, `glb_xs`, `rwb_xs`) is the frontier **and**
beats `ew_all` **and** beats the incumbent `xs_topk` out of sample.
`data/holdout/alloc15.FORWARD` names it; fired only on virgin post-cutoff rows
via `--holdout FAMILY --forward`; `--holdout` without `--forward` refuses
(rc 2) before touching disk. If no new family clears all three bars, **nothing
is armed** — the v14 precedent, where `xs_skip` lost and no forward was carved.

## 8. Tests (offline; synthetic + tmp CSVs — the real tapes are gitignored)

Each GMI-real component fires on a hand-built tape and stays dark on its
negation; the count is `None` through warmup; the two-day confirmation does not
flip on a one-day dip; `B` scales the breadth thresholds with the listed count,
and an unlisted name is invisible to every breadth component; `wish_gmi` holds
the momentum book while green, is flat while red, and re-enters off-cadence on
the green flip; the gate never changes the book while green (A/B integrity);
`glb_xs` requires the 63-day-old green line (a 30-day-old high does not qualify),
fires on the breakout close, exits at exactly `STOP` below the running ATH, and
its ATH ratchets; `rwb_xs` scores a perfect fan above a tangled one, enters only
at RLC = 6 and drops at RLC = 0; daily evaluation trades only on set changes;
no-leverage and money-conservation-with-a-`None`-leg invariants; the 1-day lag
boundary; the no-forward refusal (rc 2) and `read_forward` parsing.
