"""Reference prototype for BUILD_SPEC.md - in-memory, integer cents.
Purpose: empirically verify (1) gen-0 selection rates, (2) sitter extinction,
(3) regime-flip adaptation, (4) money conservation, before finalizing spec defaults.
"""
import random, math, statistics, json
from dataclasses import dataclass, field

CFG = {
    "rng_seed": 42,
    "initial_treasury_cents": 20_000_000,
    "gen0_population": 100,
    "gen0_seed_cents": 100_000,
    "max_population": 400,
    "death_floor_cents": 15_000,
    "rent_min_cents": 20,
    "rent_bps_of_equity": 3,          # proportional rent (spec change under test)
    "fee_bps": 20,
    "repro_cash_threshold_cents": 160_000,
    "reserve_floor_cents": 40_000,
    "breed_cooldown_ticks": 50,
    "solo_breed_patience": 10,
    "max_age_ticks": 5000,
    "stagnation_ticks": 200,
    "max_action_fraction": 0.25,
    "sigma_fraction": 0.10,
    "gene_flip_prob": 0.05,
    "archetype_hop_prob": 0.01,
    "snapshot_every": 25,
}

BOUNDS = {
    "lookback": (5, 200, int),
    "entry_z": (0.2, 3.0, float),
    "exit_z": (-2.0, 2.0, float),
    "risk_fraction": (0.01, 0.25, float),
    "hold_max": (20, 1500, int),
    "child_seed_fraction": (0.15, 0.60, float),
}
ARCHETYPES = ["momentum", "mean_revert", "sitter"]

def _fix_exit(g):
    # exit_z is the SIGNED z-level to exit at.
    # momentum enters at z>=entry_z, exits when z<=exit_z  -> need exit_z < entry_z
    # mean_revert enters at z<=-entry_z, exits when z>=exit_z -> need exit_z > -entry_z
    if g["archetype"] == "momentum" and g["exit_z"] >= g["entry_z"]:
        g["exit_z"] = g["entry_z"] - 1.0
    if g["archetype"] == "mean_revert" and g["exit_z"] <= -g["entry_z"]:
        g["exit_z"] = -g["entry_z"] + 1.0

@dataclass
class Agent:
    id: int
    genome: dict
    cash: int
    born: int
    lots: int = 0
    hold: int = 0
    last_trade: int = -1
    queue_since: int = -1
    last_birth: int = -10**9
    snaps: list = field(default_factory=list)  # (tick, equity)
    peak: int = 0

def rand_genome(rng, archetype=None):
    a = archetype or rng.choice(ARCHETYPES)
    g = {"archetype": a, "child_seed_fraction": rng.uniform(0.15, 0.60)}
    for k in ("lookback", "entry_z", "exit_z", "risk_fraction", "hold_max"):
        lo, hi, t = BOUNDS[k]
        v = rng.uniform(lo, hi)
        g[k] = int(round(v)) if t is int else v
    _fix_exit(g)
    return g

def mutate(g, rng, sigma):
    g = dict(g)
    for k, (lo, hi, t) in BOUNDS.items():
        v = g[k] + rng.gauss(0, sigma * (hi - lo))
        v = max(lo, min(hi, v))
        g[k] = int(round(v)) if t is int else v
    _fix_exit(g)
    if rng.random() < CFG["archetype_hop_prob"]:
        g["archetype"] = rng.choice(ARCHETYPES)
    return g

def crossover(gA, gB, fitA, fitB, rng):
    base, other = (gA, gB) if fitA >= fitB else (gB, gA)
    child = dict(base)
    child["child_seed_fraction"] = other["child_seed_fraction"]
    if gA["archetype"] == gB["archetype"]:
        for k in BOUNDS:
            if rng.random() < 0.5:
                child[k] = other.get(k, child[k])
    return mutate(child, rng, CFG["sigma_fraction"])

class Petri:
    def __init__(self, regimes, start_price):
        self.regimes = regimes
        self.p = start_price
        self.hist = [start_price]
        self.ri = 0
        self.rt = 0
        self.anchor = start_price
    def step(self, rng):
        kind, params = self.regimes[self.ri]
        if kind in ("trend_up", "crash"):
            drift = params["drift_bps"] / 10_000
            vol = params["vol_bps"] / 10_000
            self.p = self.p * (1 + drift + rng.gauss(0, vol))
        else:  # mean_revert
            kappa, vol = params["kappa"], params["vol_bps"] / 10_000
            self.p = self.p + kappa * (self.anchor - self.p) + self.p * rng.gauss(0, vol)
        self.p = max(20, int(round(self.p)))
        self.hist.append(self.p)
        self.rt += 1
        if self.rt >= params["ticks"]:
            self.ri = (self.ri + 1) % len(self.regimes)
            self.rt = 0
            self.anchor = self.p

def zscore(hist, lookback):
    if len(hist) < lookback + 1:
        return 0.0
    w = hist[-lookback:]
    m = statistics.fmean(w)
    s = statistics.pstdev(w)
    return 0.0 if s == 0 else (hist[-1] - m) / s

class Colony:
    def __init__(self, cfg, regimes, seed):
        self.cfg = cfg
        self.rng = random.Random(seed)
        self.arena = Petri(regimes, 200)
        self.treasury = cfg["initial_treasury_cents"]
        self.arena_acct = 0
        self.agents = {}
        self.dead = []
        self.next_id = 0
        self.tick = 0
        self.queue = []
        self.hall = []
        n0 = min(cfg["gen0_population"], self.treasury // cfg["gen0_seed_cents"])
        # gen-0: force even archetype coverage
        for i in range(n0):
            g = rand_genome(self.rng, ARCHETYPES[i % 3])
            self.spawn(g, cfg["gen0_seed_cents"], from_treasury=True)

    def spawn(self, genome, seed_cents, from_treasury=False, funders=None):
        if from_treasury:
            self.treasury -= seed_cents
        a = Agent(self.next_id, genome, seed_cents, self.tick)
        a.debt = int(self.cfg.get("repay_multiple", 0.15) * seed_cents) if from_treasury else 0
        a.birth_seed = seed_cents
        a.baseline = seed_cents
        a.peak = seed_cents
        self.next_id += 1
        self.agents[a.id] = a
        return a

    def equity(self, a):
        return a.cash + a.lots * self.arena.p

    def fitness(self, a):
        # growth-rate fitness (spec change under test): log-growth/tick × (1 − maxDD)
        eq = self.equity(a)
        age = max(1, self.tick - a.born)
        seed0 = a.snaps[0][1] if a.snaps else eq
        seed0 = max(1, seed0)
        growth = math.log(max(1, eq) / seed0) / age
        dd = 0.0 if a.peak <= 0 else max(0.0, min(0.99, 1 - eq / a.peak))
        return growth * (1 - dd)

    def sell_all(self, a):
        if a.lots > 0:
            proceeds = a.lots * self.arena.p
            fee = max(1, round(proceeds * self.cfg["fee_bps"] / 10_000))
            a.cash += proceeds - fee
            self.arena_acct -= proceeds - fee
            a.lots = 0

    def die(self, a, cause):
        if a.peak >= 2 * a.birth_seed:
            self.hall.append(dict(a.genome))
            if len(self.hall) > 100:
                self.hall.pop(0)          # rolling: the hall reflects RECENT success
        self.sell_all(a)
        self.treasury += a.cash
        a.cash = 0
        self.dead.append((a, cause, self.tick))
        del self.agents[a.id]

    def step(self):
        cfg, rng = self.cfg, self.rng
        # seed-repayment quota (validated, replicated): house-funded agents sweep
        # min(debt, surplus-above-baseline) to treasury; must be debt-free to breed
        for a in self.agents.values():
            if a.debt > 0:
                surplus = a.cash - a.baseline
                if surplus > 0:
                    pay = min(a.debt, surplus)
                    a.cash -= pay; a.debt -= pay; self.treasury += pay
        self.arena.step(rng)
        p = self.arena.p
        for aid in sorted(self.agents):
            a = self.agents.get(aid)
            if a is None: continue
            # rent: proportional with a floor (constant selection pressure at all scales)
            eq = self.equity(a)
            rent = max(cfg["rent_min_cents"], round(eq * cfg["rent_bps_of_equity"] / 10_000))
            if a.cash < rent:
                self.sell_all(a)
            if a.cash < rent:
                self.die(a, "liquidity_death"); continue
            a.cash -= rent
            self.treasury += rent
            # decide
            g = a.genome
            z = zscore(self.arena.hist, g["lookback"])
            act = None
            if g["archetype"] == "momentum":
                if a.lots == 0 and z >= g["entry_z"]:
                    act = "BUY"
                elif a.lots > 0 and (z <= g["exit_z"] or a.hold >= g["hold_max"]):
                    act = "SELL"
            elif g["archetype"] == "mean_revert":
                if a.lots == 0 and z <= -g["entry_z"]:
                    act = "BUY"
                elif a.lots > 0 and (z >= g["exit_z"] or a.hold >= g["hold_max"]):
                    act = "SELL"
            if act == "BUY":
                eq = self.equity(a)
                budget = int(min(g["risk_fraction"], cfg["max_action_fraction"]) * eq)
                lots = budget // p
                cost = lots * p
                fee = max(1, round(cost * cfg["fee_bps"] / 10_000))
                if lots > 0 and a.cash >= cost + fee:
                    a.cash -= cost + fee
                    self.arena_acct += cost + fee
                    a.lots += lots
                    a.hold = 0
                    a.last_trade = self.tick
            elif act == "SELL":
                self.sell_all(a)
                a.last_trade = self.tick
            if a.lots > 0:
                a.hold += 1
                a.last_trade = self.tick  # holding a position IS economic activity
            a.peak = max(a.peak, self.equity(a))
        # deaths
        for aid in sorted(list(self.agents)):
            a = self.agents[aid]
            eq = self.equity(a)
            if eq <= cfg["death_floor_cents"]:
                self.die(a, "bankrupt")
            elif self.tick - a.born >= cfg["max_age_ticks"]:
                self.die(a, "old_age")
            elif a.last_trade < 0 and self.tick - a.born >= cfg["stagnation_ticks"]:
                self.die(a, "stagnation")  # never traded by grace age (sitters + broken genomes)
        # breeding
        self.queue = [q for q in self.queue if q in self.agents]
        for aid in sorted(self.agents):
            a = self.agents[aid]
            if (a.cash >= int(cfg["repro_multiple"] * a.baseline)
                    and self.tick - a.last_birth >= cfg["breed_cooldown_ticks"]
                    and len(self.agents) < cfg["max_population"]
                    and a.debt == 0 and aid not in self.queue):
                self.queue.append(aid)
                a.queue_since = self.tick
        births = []
        while len(self.queue) >= 2:
            wts = [max(self.fitness(self.agents[q]), 0) + 1e-6 for q in self.queue]
            i = rng.choices(range(len(self.queue)), weights=wts)[0]
            aid = self.queue.pop(i)
            wts = [max(self.fitness(self.agents[q]), 0) + 1e-6 for q in self.queue]
            j = rng.choices(range(len(self.queue)), weights=wts)[0]
            bid = self.queue.pop(j)
            A, B = self.agents[aid], self.agents[bid]
            sA = int(A.genome["child_seed_fraction"] / 2 * A.cash)
            sB = int(B.genome["child_seed_fraction"] / 2 * B.cash)
            seed = sA + sB
            if (A.cash - sA >= cfg["reserve_floor_cents"] and B.cash - sB >= cfg["reserve_floor_cents"]
                    and seed > 2 * cfg["death_floor_cents"]):
                A.cash -= sA; B.cash -= sB
                A.baseline = A.cash; B.baseline = B.cash
                A.last_birth = B.last_birth = self.tick
                child = crossover(A.genome, B.genome, self.fitness(A), self.fitness(B), rng)
                births.append((child, seed))
        for aid in list(self.queue):
            a = self.agents.get(aid)
            if a and self.tick - a.queue_since >= cfg["solo_breed_patience"]:
                seed = int(a.genome["child_seed_fraction"] * a.cash)
                if a.cash - seed >= cfg["reserve_floor_cents"] and seed > 2 * cfg["death_floor_cents"]:
                    a.cash -= seed
                    a.baseline = a.cash
                    a.last_birth = self.tick
                    births.append((mutate(a.genome, rng, cfg["sigma_fraction"]), seed))
                self.queue.remove(aid)
        for g, s in births:
            if len(self.agents) < cfg["max_population"]:
                self.spawn(g, s)
        # IMMIGRATION: recycle treasury into fresh agents when population is thin.
        # Half are mutated copies of the best genomes ever (hall of fame), half random restarts.
        floor_pop = cfg.get("population_floor", 0)
        seed_c = cfg["gen0_seed_cents"]
        while len(self.agents) < floor_pop and self.treasury >= seed_c:
            if self.hall and rng.random() < 0.4:
                g = mutate(rng.choice(self.hall), rng, cfg["sigma_fraction"] * 2)
            else:
                g = rand_genome(rng)
            self.spawn(g, seed_c, from_treasury=True)
        # snapshots
        if self.tick % cfg["snapshot_every"] == 0:
            for a in self.agents.values():
                a.snaps.append((self.tick, self.equity(a)))
        self.tick += 1

    def conservation_ok(self):
        total = self.treasury + sum(a.cash for a in self.agents.values()) + self.arena_acct
        return total == self.cfg["initial_treasury_cents"], total

    def arch_dist(self):
        d = {a: 0 for a in ARCHETYPES}
        for ag in self.agents.values():
            d[ag.genome["archetype"]] += 1
        n = max(1, len(self.agents))
        return {k: v / n for k, v in d.items()}, len(self.agents)

def run_experiment():
    regimes_flip = [("trend_up", {"ticks": 3000, "drift_bps": 8, "vol_bps": 60}),
                    ("mean_revert", {"ticks": 3000, "kappa": 0.05, "vol_bps": 80})]
    results = []
    for seed in (42, 7, 2026):
        c = Colony(CFG, regimes_flip, seed)
        marks = {}
        for t in range(6000):
            c.step()
            if t == 2999:
                marks["after_trend"] = c.arch_dist()
        marks["after_mr"] = c.arch_dist()
        ok, total = c.conservation_ok()
        gen0_dead = sum(1 for (a, cause, tk) in c.dead if a.born == 0)
        sitters_alive = sum(1 for a in c.agents.values() if a.genome["archetype"] == "sitter")
        colony_pnl = -c.arena_acct  # money extracted FROM the market
        results.append(dict(seed=seed, conservation=ok, total=total,
                            gen0_dead_frac=gen0_dead / CFG["gen0_population"],
                            after_trend=marks["after_trend"], after_mr=marks["after_mr"],
                            sitters_alive=sitters_alive, births=c.next_id - CFG["gen0_population"],
                            deaths=len(c.dead), colony_pnl_cents=colony_pnl,
                            max_gen_pop=len(c.agents)))
    return results

if __name__ == "__main__":
    for r in run_experiment():
        at, na = r["after_trend"]; am, nm = r["after_mr"]
        print(f"\nseed={r['seed']}  conservation={'OK' if r['conservation'] else 'FAIL '+str(r['total'])}")
        print(f"  gen0 death frac: {r['gen0_dead_frac']:.0%}   births: {r['births']}  deaths: {r['deaths']}  pop now: {r['max_gen_pop']}")
        print(f"  after TREND  (pop {na}): mom {at['momentum']:.0%}  mr {at['mean_revert']:.0%}  sit {at['sitter']:.0%}")
        print(f"  after MEANREV(pop {nm}): mom {am['momentum']:.0%}  mr {am['mean_revert']:.0%}  sit {am['sitter']:.0%}")
        print(f"  mean_revert share shift: {(am['mean_revert']-at['mean_revert'])*100:+.0f} pts   sitters alive: {r['sitters_alive']}")
        print(f"  colony P&L vs market: {r['colony_pnl_cents']/100:+,.2f} (currency units)")
