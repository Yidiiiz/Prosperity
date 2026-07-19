"""The honest bar (spec v3 section 2): buy-and-hold at the same venue costs.

Every replay verdict is measured against holding the asset on the same tape
through the same cost model — the benchmark pays the same tolls (taker fee +
spread, rounded against the buyer, via the risk helpers agents use). Verdict
tiers (spec v3 2.4): ALPHA beats buy-and-hold, CASH beats initial only,
EXPECTED-FAIL made no money with sound machinery; FAIL (machinery broke) is
assigned by the caller and remains the only failure.
"""

import datetime

from . import risk
from .arenas.replay import to_price_u
from .report import money

SECONDS_PER_YEAR = 365.25 * 86_400  # 365.25-day years (spec v3 2.2)


def buy_and_hold(prices, capital_u, venue, lot_denominator=1):
    """Audited cash from buying max affordable lots at the FIRST bar and
    selling everything at the LAST bar, both at the venue's fill prices.
    Leftover cash rides along uninvested, exactly as an agent's would."""
    fill = risk.buy_price_u(to_price_u(prices[0], lot_denominator), venue)
    lots = capital_u // fill
    while lots > 0 and lots * fill + risk.fee_u(lots * fill, venue) > capital_u:
        lots -= 1
    cash_u = capital_u
    if lots > 0:
        cash_u -= lots * fill + risk.fee_u(lots * fill, venue)
        proceeds = lots * risk.sell_price_u(to_price_u(prices[-1], lot_denominator), venue)
        cash_u += proceeds - risk.fee_u(proceeds, venue)
    return cash_u


def cash(capital_u):
    """The do-nothing benchmark, named so reports can print both."""
    return capital_u


def tier(initial_u, audited_u, bench_u):
    """Spec v3 2.4 verdict tier for a completed replay (machinery sound)."""
    if audited_u > bench_u:
        return "ALPHA"
    if audited_u > initial_u:
        return "CASH"
    return "EXPECTED-FAIL"


def span_years(first_utc, last_utc):
    return (last_utc - first_utc) / SECONDS_PER_YEAR


def cagr(initial_u, final_u, years):
    """Compound annual growth rate as a fraction; -1.0 when wiped out."""
    if final_u <= 0:
        return -1.0
    return (final_u / initial_u) ** (1 / max(years, 1e-9)) - 1


def _pct_yr(initial_u, final_u, years):
    suffix = " (projected)" if years < 1 else ""  # spec v3 2.3: never suppress
    return f"{cagr(initial_u, final_u, years) * 100:+.2f}%/yr{suffix}"


def _iso(utc):
    return datetime.datetime.fromtimestamp(utc, datetime.timezone.utc).isoformat(
        timespec="seconds"
    )


def footer(first_utc, last_utc, entries, wall_seconds):
    """The mandatory replay-record footer (spec v3 2.2): span, wall,
    annualized, benchmark. entries: (label, initial_u, audited_u, bench_u)
    — one annualized/benchmark line pair per labelled result. A 6-tuple
    (…, first_utc, last_utc) annualizes over that entry's own span (a
    walk-forward window rather than the whole tape)."""
    years = span_years(first_utc, last_utc)
    lines = [
        f"span: {_iso(first_utc)} -> {_iso(last_utc)} ({years:.2f} years)",
        f"wall: {wall_seconds:.1f} s",
    ]
    for entry in entries:
        label, initial_u, audited_u, bench_u = entry[:4]
        yrs = span_years(entry[4], entry[5]) if len(entry) == 6 else years
        delta = (cagr(initial_u, audited_u, yrs) - cagr(initial_u, bench_u, yrs)) * 100
        lines.append(f"annualized: {label} audited {money(audited_u)}"
                     f" vs initial {money(initial_u)} -> {_pct_yr(initial_u, audited_u, yrs)}")
        lines.append(f"benchmark: {label} buy-and-hold {money(bench_u)}"
                     f" ({_pct_yr(initial_u, bench_u, yrs)})"
                     f" | delta {delta:+.2f} pp/yr")
    return "\n".join(lines)
