"""The S&P bar (spec v4 section 2): buy-and-hold SPY over the SAME calendar
window as the run being judged, at the same venue costs.

The only legal comparison is same-window vs same-window (both sides projected
over the same days when sub-year — v3 2.3). Comparing a cell against SPY's
long-run CAGR is forbidden (v4 2.2); this module deliberately exposes no
full-history number.
"""

from pathlib import Path

from colony import benchmark
from colony.arenas.replay import read_rows

ROOT = Path(__file__).resolve().parent.parent
SPY_CSV = ROOT / "data" / "spy_d.csv"
SPY_LOT = 100  # config.spy.json
_cache = {}


def _spy_rows(csv_path):
    key = str(csv_path)
    if key not in _cache:
        _cache[key] = read_rows(csv_path)
    return _cache[key]


def spx_over(t0, t1, capital_u, venue, csv_path=SPY_CSV):
    """Buy-and-hold SPY over [t0, t1] -> (cash_u, cagr_fraction, coverage).

    coverage = how much of [t0, t1] the SPY tape actually spans (0..1); the
    caller must print '(partial SPY coverage)' when it is < 0.9 (v4 2.1).
    CAGR annualizes over the judged window [t0, t1] so both sides of the
    comparison use identical days."""
    if t1 <= t0:
        raise ValueError(f"degenerate window: {t0} -> {t1}")
    times, closes = _spy_rows(csv_path)
    sliced = [(t, c) for t, c in zip(times, closes) if t0 <= t <= t1]
    if len(sliced) < 2:
        return capital_u, 0.0, 0.0
    cash_u = benchmark.buy_and_hold([c for _, c in sliced], capital_u, venue,
                                    SPY_LOT)
    coverage = (sliced[-1][0] - sliced[0][0]) / (t1 - t0)
    years = benchmark.span_years(t0, t1)
    return cash_u, benchmark.cagr(capital_u, cash_u, years), min(coverage, 1.0)


def spx_line(label, t0, t1, initial_u, audited_u, venue, csv_path=SPY_CSV):
    """The one mandatory extra footer line (v4 2.2):
    'spx: <label> buy-and-hold $X (+Y%/yr) | delta ±pp/yr'."""
    from colony.report import money
    cash_u, spx_cagr, coverage = spx_over(t0, t1, initial_u, venue, csv_path)
    years = benchmark.span_years(t0, t1)
    if coverage == 0.0:
        return f"spx: {label} no SPY coverage for this window"
    cell = benchmark.cagr(initial_u, audited_u, years)
    proj = " (projected)" if years < 1 else ""
    partial = " (partial SPY coverage)" if coverage < 0.9 else ""
    return (f"spx: {label} buy-and-hold {money(cash_u)}"
            f" ({spx_cagr * 100:+.2f}%/yr{proj}) | delta"
            f" {(cell - spx_cagr) * 100:+.2f} pp/yr{partial}")
