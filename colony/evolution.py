"""Genetic machinery: genomes, mutation, crossover, fitness, adaptive sigma.

Pure functions taking the orchestrator's RNG explicitly. Nothing here touches
the ledger; fitness is analysis-only and floats are fine.
"""

import math
from collections import Counter

ARCHETYPES = ["momentum", "mean_revert", "sitter", "breakout"]  # v3 section 6

MASK24 = "mask24"  # 24-bit UTC-hour bitmask gene: bit-flip mutation, not gauss

# gene: (min, max, type). Mutation output is clamped to these.
# v2 (spec v2 7.1): three universal gates so evolution can discover when NOT
# to play — the flat-tape and fee-predation lessons as heritable traits.
PARAM_BOUNDS = {
    "lookback": (5, 100, int),
    "entry_z": (0.2, 3.0, float),
    "exit_z": (-2.0, 2.0, float),
    "risk_fraction": (0.05, 0.80, float),
    "hold_max": (20, 1500, int),
    "vol_gate_bps": (0, 100, int),
    "max_trades_per_day": (1, 500, int),
    "active_hours_mask": (1, (1 << 24) - 1, MASK24),
    # v3 section 6, breakout only: enter on a new lookback-bar high by
    # >= confirm_bps; exit trail_bps below the post-entry high
    "confirm_bps": (0, 100, int),
    "trail_bps": (50, 2000, int),
}
ECON_BOUNDS = {
    "child_seed_fraction": (0.30, 0.55, float),
}
GENE_POOL = ["fee_aware"]


def _clamp(value, lo, hi, typ):
    value = max(lo, min(hi, value))
    return int(round(value)) if typ is int else value


def repair(genome):
    """Constraint repair after mutation/crossover (spec 6.1, exit_z semantics).

    exit_z is the SIGNED z-level the agent exits at: momentum exits DOWN
    through it, mean_revert exits UP through it. An all-zero hours mask
    (an agent that would never open) repairs to all hours (spec v2 7.1).
    """
    params = genome["params"]
    if params.get("active_hours_mask") == 0:
        params["active_hours_mask"] = (1 << 24) - 1
    if genome["archetype"] == "momentum" and params["exit_z"] >= params["entry_z"]:
        params["exit_z"] = params["entry_z"] - 1.0
    if genome["archetype"] == "mean_revert" and params["exit_z"] <= -params["entry_z"]:
        params["exit_z"] = -params["entry_z"] + 1.0
    lo, hi, typ = PARAM_BOUNDS["exit_z"]
    params["exit_z"] = _clamp(params["exit_z"], lo, hi, typ)
    return genome


def _draw_one(lo, hi, typ, rng):
    if typ is MASK24:
        return rng.getrandbits(24)  # 0 is repaired to all-hours
    return _clamp(rng.uniform(lo, hi), lo, hi, typ)


def _draw(bounds, rng):
    return {key: _draw_one(lo, hi, typ, rng) for key, (lo, hi, typ) in bounds.items()}


def random_genome(rng, archetype=None):
    genome = {
        "archetype": archetype if archetype is not None else rng.choice(ARCHETYPES),
        "params": _draw(PARAM_BOUNDS, rng),
        "econ": _draw(ECON_BOUNDS, rng),
        "genes": ["fee_aware"] if rng.random() < 0.5 else [],
    }
    return repair(genome)


def mutate(genome, sigma, mut_cfg, rng):
    """Gaussian-perturb numeric genes, maybe flip a gene, maybe hop archetype."""
    child = {
        "archetype": genome["archetype"],
        "params": dict(genome["params"]),
        "econ": dict(genome["econ"]),
        "genes": list(genome.get("genes", [])),
    }
    for key, (lo, hi, typ) in PARAM_BOUNDS.items():
        if key not in child["params"]:
            # a pre-v2 genome (old hall of fame): the new gene joins by draw
            child["params"][key] = _draw_one(lo, hi, typ, rng)
        elif typ is MASK24:
            if sigma > 0:  # sigma 0 must be a pure blend, like the gauss genes
                child["params"][key] ^= 1 << rng.randrange(24)  # flip one hour
        else:
            child["params"][key] = _clamp(child["params"][key] + rng.gauss(0, sigma * (hi - lo)), lo, hi, typ)
    for key, (lo, hi, typ) in ECON_BOUNDS.items():
        child["econ"][key] = _clamp(child["econ"][key] + rng.gauss(0, sigma * (hi - lo)), lo, hi, typ)
    if rng.random() < mut_cfg["gene_flip_prob"]:
        gene = rng.choice(GENE_POOL)
        if gene in child["genes"]:
            child["genes"].remove(gene)
        else:
            child["genes"].append(gene)
    if rng.random() < mut_cfg["archetype_hop_prob"]:
        # macro-mutation: new archetype, params re-drawn uniformly in bounds
        child["archetype"] = rng.choice(ARCHETYPES)
        child["params"] = _draw(PARAM_BOUNDS, rng)
    return repair(child)


def crossover(genome_a, genome_b, fit_a, fit_b, sigma, mut_cfg, rng):
    if genome_a["archetype"] == genome_b["archetype"]:
        child = {
            "archetype": genome_a["archetype"],
            "params": {
                key: (genome_a if rng.random() < 0.5 else genome_b)["params"][key]
                for key in PARAM_BOUNDS
            },
            "econ": {
                key: (genome_a if rng.random() < 0.5 else genome_b)["econ"][key]
                for key in ECON_BOUNDS
            },
            "genes": list((genome_a if rng.random() < 0.5 else genome_b).get("genes", [])),
        }
    else:
        fitter, other = (genome_a, genome_b) if fit_a >= fit_b else (genome_b, genome_a)
        child = {
            "archetype": fitter["archetype"],
            "params": dict(fitter["params"]),
            "econ": dict(other["econ"]),
            "genes": list(other.get("genes", [])),
        }
    return mutate(child, sigma, mut_cfg, rng)


def fitness(equity_now, first_snap_equity, age_ticks, peak_equity, min_age_ticks):
    """Risk-adjusted log-growth per tick (spec 3.5). Zero before min_age_ticks."""
    if age_ticks < min_age_ticks or first_snap_equity is None:
        return 0.0
    growth = math.log(max(equity_now, 1) / max(first_snap_equity, 1)) / age_ticks
    max_dd = 0.0
    if peak_equity > 0:
        max_dd = max(0.0, min(0.99, 1 - equity_now / peak_equity))
    return growth * (1 - max_dd)


def adaptive_sigma(sigma, cohort_medians, adaptive_cfg):
    """Widen the search when generations stop improving, narrow it when they do."""
    window = adaptive_cfg["window_generations"]
    if len(cohort_medians) >= window:
        recent = cohort_medians[-window:]
        if all(b <= a for a, b in zip(recent, recent[1:])):
            sigma *= adaptive_cfg["stagnant_multiplier"]
        else:
            sigma *= adaptive_cfg["improving_multiplier"]
    return max(adaptive_cfg["sigma_min"], min(adaptive_cfg["sigma_max"], sigma))


def pick_weighted(rng, indices, weights):
    """One fitness-weighted draw for the matchmaker (spec 7.3)."""
    return rng.choices(indices, weights=weights, k=1)[0]


def genome_bucket(genome):
    params = genome["params"]
    return (
        genome["archetype"],
        (params["lookback"] - 5) // 24,
        int(params["entry_z"] * 2),
        int(params["risk_fraction"] * 5),
    )


def diversity(genomes):
    """Shannon entropy (nats) over archetype + binned params."""
    if not genomes:
        return 0.0
    counts = Counter(genome_bucket(g) for g in genomes)
    n = len(genomes)
    return -sum((c / n) * math.log(c / n) for c in counts.values())


def archetype_shares(genomes):
    shares = {arch: 0.0 for arch in ARCHETYPES}
    if genomes:
        for genome in genomes:
            shares[genome["archetype"]] += 1
        for arch in shares:
            shares[arch] /= len(genomes)
    return shares
