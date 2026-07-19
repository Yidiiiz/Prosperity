"""The arena interface. The 'pluggable arena' is just a class with 4 methods.

v2 adds Replay (real historical data from a local CSV). Still open: a
paper-trading adapter implementing this same protocol against a live feed.
"""

from typing import Protocol


class Arena(Protocol):
    def step(self, rng) -> None:
        """Advance one tick."""

    def price(self) -> int:
        """Current price, micro-dollars per lot."""

    def utc(self) -> int:
        """UTC unix seconds of the current bar (wall-clock axes, spec v2 3.4)."""

    def history(self, n) -> list[int]:
        """Last n prices (oldest first)."""

    def exhausted(self) -> bool:
        """True when no further ticks exist (finite data); loops return False."""
