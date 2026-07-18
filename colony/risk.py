"""Order caps and validation. Enforced by the engine, never trusted to the genome."""

import logging

from .strategies import Decision

log = logging.getLogger(__name__)


def fee_cents(amount_cents, fee_bps):
    return max(1, round(amount_cents * fee_bps / 10_000))


def check(decision, cash, equity, lots_held, price, max_action_fraction, fee_bps):
    """Cap or reject an order. Returns an adjusted Decision or None.

    BUY: capped by cash sufficiency (cost + fee) and by max_action_fraction of
    equity — the anti-gambling cap on committing new capital. SELL: capped at
    the held position (no shorting); never capped by max_action_fraction, so
    an agent can always fully exit.
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
        lots = min(
            decision.lots,
            int(max_action_fraction * equity) // price,
            cash // price,
        )
        while lots > 0 and lots * price + fee_cents(lots * price, fee_bps) > cash:
            lots -= 1
        if lots <= 0:
            log.debug("reject BUY: cannot afford one lot within caps")
            return None
        return Decision("BUY", lots)
    log.debug("reject: unknown side %r", decision.side)
    return None
