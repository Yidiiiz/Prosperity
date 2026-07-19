"""Order caps, venue costs, and validation. Enforced by the engine, never
trusted to the genome.

v2 (spec v2 2.2): execution goes through a per-venue cost model. All orders
are market orders and pay taker_bps on notional; the spread is charged even
on paper — it is the dominant real cost at speed. BUY fills above the mark,
SELL below, both rounded against the agent.
"""

import logging

from .strategies import Decision

log = logging.getLogger(__name__)


def fee_u(notional_u, venue):
    """Taker fee on notional, floored at min_fee_u (default 0: micro-dollars
    need no integer-floor predator; set it to model a real venue honestly)."""
    return max(venue["min_fee_u"], round(notional_u * venue["taker_bps"] / 10_000))


def buy_price_u(price_u, venue):
    """BUY fill: mark x (1 + spread_bps/2/10^4), rounded UP (against the agent)."""
    return -(-price_u * (20_000 + venue["spread_bps"]) // 20_000)


def sell_price_u(price_u, venue):
    """SELL fill: mark x (1 - spread_bps/2/10^4), rounded DOWN (against the agent)."""
    return price_u * (20_000 - venue["spread_bps"]) // 20_000


def per_side_cost_bps(venue):
    """Effective per-side execution cost the fee_aware gene compares edges
    against (#7 ratified formula, cost now includes half the spread)."""
    return venue["taker_bps"] + venue["spread_bps"] / 2


def check(decision, cash, equity, lots_held, price, max_action_fraction, venue):
    """Cap or reject an order at the price it would ACTUALLY fill (spread
    applied). Returns an adjusted Decision or None.

    BUY: capped by cash sufficiency (cost + fee) and by max_action_fraction
    of equity — the anti-gambling cap on committing new capital. SELL:
    capped at the held position (no shorting); never capped by
    max_action_fraction, so an agent can always fully exit.
    """
    if decision is None:
        return None
    if decision.side == "SELL":
        lots = min(decision.lots, lots_held)
        if lots <= 0:
            log.debug("reject SELL: no position")
            return None
        return Decision("SELL", lots)
    if decision.side == "BUY":
        fill = buy_price_u(price, venue)
        lots = min(
            decision.lots,
            int(max_action_fraction * equity) // fill,
            cash // fill,
        )
        while lots > 0 and lots * fill + fee_u(lots * fill, venue) > cash:
            lots -= 1
        if lots <= 0:
            log.debug("reject BUY: cannot afford one lot within caps")
            return None
        return Decision("BUY", lots)
    log.debug("reject: unknown side %r", decision.side)
    return None
