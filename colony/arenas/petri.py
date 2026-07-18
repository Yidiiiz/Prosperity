"""The Petri Dish: a scripted price path through the config's regime list.

Exogenous by design — agents cannot move the price in v1. When the regime
list is exhausted, it loops.
"""

MAX_HISTORY = 512  # far beyond the max strategy lookback (100)


class Petri:
    def __init__(self, arena_cfg):
        self.name = arena_cfg["name"]
        self.regimes = arena_cfg["regimes"]
        self.floor = arena_cfg["price_floor_cents"]
        self._price = arena_cfg["start_price_cents"]
        self._hist = [self._price]
        self._regime_idx = 0
        self._regime_tick = 0
        self._anchor = self._price

    def step(self, rng):
        regime = self.regimes[self._regime_idx]
        p = float(self._price)
        if regime["kind"] in ("trend_up", "crash"):
            p = p * (1 + regime["drift_bps"] / 10_000 + rng.gauss(0, regime["vol_bps"] / 10_000))
        else:  # mean_revert, OU-style around the price at regime start
            p = p + regime["kappa"] * (self._anchor - p) + p * rng.gauss(0, regime["vol_bps"] / 10_000)
        self._price = max(self.floor, int(round(p)))
        self._hist.append(self._price)
        if len(self._hist) > MAX_HISTORY:
            del self._hist[0]
        self._regime_tick += 1
        if self._regime_tick >= regime["ticks"]:
            self._regime_idx = (self._regime_idx + 1) % len(self.regimes)
            self._regime_tick = 0
            self._anchor = self._price

    def price(self):
        return self._price

    def history(self, n):
        return self._hist[-n:]

    def regime_kind(self):
        return self.regimes[self._regime_idx]["kind"]

    def get_state(self):
        return {
            "price": self._price,
            "hist": self._hist,
            "regime_idx": self._regime_idx,
            "regime_tick": self._regime_tick,
            "anchor": self._anchor,
        }

    def set_state(self, state):
        self._price = state["price"]
        self._hist = list(state["hist"])
        self._regime_idx = state["regime_idx"]
        self._regime_tick = state["regime_tick"]
        self._anchor = state["anchor"]
