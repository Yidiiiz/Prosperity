"""Paper-trade the pure_mom strategy against the latest LOCAL tapes.

pure_mom is the frontier of every bench in this repo (v11/v12): once a month,
own the single strongest-momentum asset in the RISK sleeve (btc, eth, spy, qqq,
iwm, efa); if none has positive trailing momentum, sit in cash. This runner
does not predict or place orders — it keeps a virtual $10,000 book, and each
time you run it it (a) reads the newest closes already on disk, (b) decides
what pure_mom would hold this month, (c) marks the book to the latest close and
appends a dated row to a ledger. It is the honest way to build a clean,
forward-only track record while the armed forward holdout (~2027) ripens.

This file makes NO network calls (the repo red line). Refresh the tapes first
with the only tool that may:
    python tools/fetch_market_data.py SPY  -o data/spy_d.csv     # + QQQ/IWM/EFA/GLD/TLT
    python tools/fetch_binance_klines.py BTCUSDT -o data/btcusdt_1d.csv   # + ETHUSDT
then run this to log the month:
    python -m experiments.papertrade                 # decide + record this month
    python -m experiments.papertrade --dry-run       # show the decision, write nothing
    python -m experiments.papertrade --force         # rebalance even mid-month
    python -m experiments.papertrade --reset         # start a fresh $10,000 book

Cadence: the backtest rotates every 21 trading days; here we rebalance on the
first run of each new calendar month (the practical human analog) and hold
otherwise. Faithful to the backtest's 1-day lag, the momentum signal uses
history through the prior close and the fill is booked at the latest close,
through the same venue tolls (10 bps taker + 2 bps spread) the benches use.
"""

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

from colony.report import money
from experiments.allocation import BASE_VENUE, CAPITAL_U, fmt
from experiments.allocation6 import momentum_ranked, rebalance
from experiments.allocation12 import RISK, U_RISK, load_joint, select

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / "data" / "papertrade" / "pure_mom_state.json"
LEDGER = ROOT / "data" / "papertrade" / "pure_mom_ledger.csv"


def _dstr(ts):
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).date().isoformat()


def decide(closes, j, L):
    """pure_mom's target as of signal day j (history <= j): (targets, holding,
    ranked). holding is the asset name or 'cash'."""
    ranked = momentum_ranked(closes, j, L, RISK)
    if not ranked:
        return {}, "cash", ranked
    best_r, best = ranked[0]
    if best_r > 0:
        return {best: 1.0}, best, ranked
    return {}, "cash", ranked


def load_state():
    if not STATE.exists():
        return None
    return json.loads(STATE.read_text(encoding="utf-8"))


def save_state(state):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def append_ledger(row):
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    new = not LEDGER.exists()
    with open(LEDGER, "a", newline="", encoding="utf-8") as f:
        if new:
            f.write("date,action,holding,equity_usd,cash_usd,"
                    "since_inception_pct,top_momentum\n")
        f.write(",".join(str(c) for c in row) + "\n")


def run(argv=None, *, state_path=None, ledger_path=None):
    global STATE, LEDGER
    if state_path is not None:
        STATE = Path(state_path)
    if ledger_path is not None:
        LEDGER = Path(ledger_path)
    parser = argparse.ArgumentParser(description="paper-trade pure_mom")
    parser.add_argument("--dry-run", action="store_true",
                        help="show the decision, write nothing")
    parser.add_argument("--force", action="store_true",
                        help="rebalance even if the month has not changed")
    parser.add_argument("--reset", action="store_true",
                        help="discard the book and start fresh at $10,000")
    args = parser.parse_args(argv)

    if args.reset and STATE.exists():
        STATE.unlink()

    times, closes, prices = load_joint(U_RISK)
    last = len(times) - 1
    today = _dstr(times[last])
    month = today[:7]

    state = load_state()
    if state is None:
        L = select("pure_mom", closes, prices, 0, len(times), U_RISK)["L"]
        state = {"strategy": "pure_mom", "L": L, "inception": today,
                 "cash_u": CAPITAL_U, "lots": {x: 0 for x in U_RISK},
                 "last_rebal_month": None, "runs": 0}
        print(f"new paper book opened {today}: $10,000, pure_mom [L={L}]"
              " (L frozen on all history to date)")

    L = state["L"]
    lots = {x: int(state["lots"].get(x, 0)) for x in U_RISK}
    cash = int(state["cash_u"])

    j = last - 1                                  # 1-day lag: signal <= yesterday
    target, want, ranked = decide(closes, j, L)
    price_now = {x: prices[x][last] for x in U_RISK}
    held = next((x for x in U_RISK if lots[x] > 0), "cash")
    top = " ".join(f"{x}:{r*100:+.0f}%" for r, x in ranked[:3]) or "n/a"

    do_rebal = args.force or state["last_rebal_month"] != month
    action = "hold"
    if do_rebal and not args.dry_run:
        cash = rebalance(cash, lots, target, price_now, BASE_VENUE, U_RISK)
        state["cash_u"], state["lots"] = cash, lots
        state["last_rebal_month"] = month
        state["runs"] = state.get("runs", 0) + 1
        held = next((x for x in U_RISK if lots[x] > 0), "cash")
        action = "rebalance"

    equity = cash + sum(lots[x] * price_now[x] for x in U_RISK)
    since = (equity / CAPITAL_U - 1) * 100

    print(f"\n  as of {today}  (strategy pure_mom [L={L}], since"
          f" {state['inception']})")
    print(f"  momentum ranking (through {_dstr(times[j])}):  {top}")
    print(f"  target this month: {want}   |   currently holding: {held}")
    if do_rebal and args.dry_run:
        print(f"  [dry-run] would {'rotate to ' + want if want != held else 'stay in ' + held}"
              " — nothing written")
    elif do_rebal:
        print(f"  action: rebalanced -> {held}")
    else:
        print(f"  action: hold (already rebalanced this month, {month})")
    print(f"  book: equity {money(equity)}  (cash {money(cash)}),"
          f" {since:+.1f}% since inception\n")

    if not args.dry_run:
        append_ledger([today, action, held, f"{equity/1e6:.2f}",
                       f"{cash/1e6:.2f}", f"{since:+.2f}", top.replace(",", "")])
        save_state(state)
    return 0


if __name__ == "__main__":
    sys.exit(run())
