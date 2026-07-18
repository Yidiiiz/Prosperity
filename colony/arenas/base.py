"""The arena interface. The 'pluggable arena' is just a class with 3 methods.

TODO(v2): real arenas — a paper-trading adapter implementing this same
protocol against live exchange testnet data, then per-agent sub-accounts.
"""

from typing import Protocol


class Arena(Protocol):
    def step(self, rng) -> None:
        """Advance one tick."""

    def price(self) -> int:
        """Current price, cents per lot."""

    def history(self, n) -> list[int]:
        """Last n prices (oldest first)."""
