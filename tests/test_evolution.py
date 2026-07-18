import random

from colony.evolution import (
    ARCHETYPES, ECON_BOUNDS, PARAM_BOUNDS, adaptive_sigma, archetype_shares, crossover,
    diversity, fitness, mutate, random_genome,
)

MUT_CFG = {"gene_flip_prob": 0.05, "archetype_hop_prob": 0.01}
NO_MUT_CFG = {"gene_flip_prob": 0.0, "archetype_hop_prob": 0.0}
ADAPTIVE = {"window_generations": 3, "stagnant_multiplier": 1.5, "improving_multiplier": 0.8,
            "sigma_min": 0.02, "sigma_max": 0.30}


def in_bounds(genome):
    for key, (lo, hi, typ) in PARAM_BOUNDS.items():
        value = genome["params"][key]
        if not (lo <= value <= hi) or (typ is int and not isinstance(value, int)):
            return False
    for key, (lo, hi, _) in ECON_BOUNDS.items():
        if not (lo <= genome["econ"][key] <= hi):
            return False
    return True


def repaired(genome):
    p = genome["params"]
    if genome["archetype"] == "momentum":
        return p["exit_z"] < p["entry_z"]
    if genome["archetype"] == "mean_revert":
        return p["exit_z"] > -p["entry_z"]
    return True


def test_random_genome_in_bounds_and_repaired():
    rng = random.Random(1)
    for _ in range(200):
        g = random_genome(rng)
        assert g["archetype"] in ARCHETYPES
        assert in_bounds(g) and repaired(g)


def test_mutation_clamps_to_bounds():
    rng = random.Random(2)
    g = random_genome(rng, "momentum")
    for _ in range(500):
        g = mutate(g, 0.5, MUT_CFG, rng)  # huge sigma: clamping must hold
        assert in_bounds(g) and repaired(g)


def test_mutation_does_not_touch_parent():
    rng = random.Random(3)
    g = random_genome(rng, "momentum")
    snapshot = (dict(g["params"]), dict(g["econ"]), list(g["genes"]))
    mutate(g, 0.3, MUT_CFG, rng)
    assert (g["params"], g["econ"], g["genes"]) == (snapshot[0], snapshot[1], snapshot[2])


def test_crossover_same_archetype_picks_from_parents():
    rng = random.Random(4)
    a = random_genome(rng, "momentum")
    b = random_genome(rng, "momentum")
    child = crossover(a, b, 1.0, 0.5, 0.0, NO_MUT_CFG, rng)  # sigma 0: pure blend
    assert child["archetype"] == "momentum"
    for key in PARAM_BOUNDS:
        if key == "exit_z":
            continue  # may have been constraint-repaired
        assert child["params"][key] in (a["params"][key], b["params"][key])


def test_crossover_different_archetypes_takes_fitter_body():
    rng = random.Random(5)
    a = random_genome(rng, "momentum")
    b = random_genome(rng, "mean_revert")
    child = crossover(a, b, 2.0, 1.0, 0.0, NO_MUT_CFG, rng)
    assert child["archetype"] == "momentum"
    assert child["econ"] == b["econ"]  # the other parent contributes econ + genes
    child = crossover(a, b, 1.0, 2.0, 0.0, NO_MUT_CFG, rng)
    assert child["archetype"] == "mean_revert"
    assert child["econ"] == a["econ"]


def test_archetype_hop_redraws_params():
    rng = random.Random(6)
    g = random_genome(rng, "momentum")
    hopped = mutate(g, 0.0, {"gene_flip_prob": 0.0, "archetype_hop_prob": 1.0}, rng)
    assert in_bounds(hopped) and repaired(hopped)


def test_gene_flip_toggles_pool_membership():
    rng = random.Random(7)
    g = random_genome(rng, "momentum")
    g["genes"] = []
    flipped = mutate(g, 0.0, {"gene_flip_prob": 1.0, "archetype_hop_prob": 0.0}, rng)
    assert flipped["genes"] == ["fee_aware"]
    flipped = mutate(flipped, 0.0, {"gene_flip_prob": 1.0, "archetype_hop_prob": 0.0}, rng)
    assert flipped["genes"] == []


def test_fitness_zero_before_min_age():
    assert fitness(200_000, 100_000, 74, 200_000, 75) == 0.0
    assert fitness(200_000, 100_000, 75, 200_000, 75) > 0.0


def test_fitness_rewards_growth_penalizes_drawdown():
    grower = fitness(200_000, 100_000, 100, 200_000, 75)
    drawn_down = fitness(200_000, 100_000, 100, 400_000, 75)
    assert grower > drawn_down > 0
    loser = fitness(50_000, 100_000, 100, 100_000, 75)
    assert loser < 0


def test_fitness_none_first_snapshot_is_zero():
    assert fitness(200_000, None, 500, 200_000, 75) == 0.0


def test_adaptive_sigma_widens_on_stagnation_narrows_on_improvement():
    assert abs(adaptive_sigma(0.10, [1.2, 1.1, 1.0], ADAPTIVE) - 0.15) < 1e-12
    assert abs(adaptive_sigma(0.10, [1.0, 1.1, 1.2], ADAPTIVE) - 0.08) < 1e-12
    # too few cohorts: unchanged (but still clamped)
    assert adaptive_sigma(0.10, [1.0], ADAPTIVE) == 0.10


def test_adaptive_sigma_clamped():
    assert adaptive_sigma(0.29, [3, 2, 1], ADAPTIVE) == 0.30
    assert adaptive_sigma(0.021, [1, 2, 3], ADAPTIVE) == 0.02


def test_diversity_and_shares():
    rng = random.Random(8)
    same = [random_genome(rng, "momentum")] * 10
    assert diversity(same) == 0.0
    varied = [random_genome(rng) for _ in range(50)]
    assert diversity(varied) > 1.0
    shares = archetype_shares(varied)
    assert abs(sum(shares.values()) - 1.0) < 1e-9
    assert diversity([]) == 0.0
