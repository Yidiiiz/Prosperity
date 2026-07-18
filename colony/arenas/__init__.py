"""Arena construction: dispatch on arena.kind ("petri" default, "replay")."""

from .petri import Petri
from .replay import Replay


def make_arena(arena_cfg):
    kind = arena_cfg.get("kind", "petri")
    if kind == "petri":
        return Petri(arena_cfg)
    if kind == "replay":
        return Replay(arena_cfg)
    raise ValueError(f"unknown arena kind {kind!r}")
