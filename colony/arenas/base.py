"""The arena interface. The 'pluggable arena' is just a class with 4 methods.

v2 adds Replay (real historical data from a local CSV). Still open: a
paper-trading adapter implementing this same protocol against a live feed.
"""

from typing import Protocol


class Arena(Protocol):
    def step(self, rng) -> None:
        """Advance one tick."""

    def price(self) -> int:
        """Current price, cents per lot."""

    def history(self, n) -> list[int]:
        """Last n prices (oldest first)."""

    def exhausted(self) -> bool:
        """True when no further ticks exist (finite data); loops return False."""
