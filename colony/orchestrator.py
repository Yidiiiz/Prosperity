"""The tick loop (spec section 7). One RNG, sorted-id iteration, one
transaction per tick, invariants verified on cadence."""

import datetime
import json
import random
import statistics

from . import agents, db, evolution, ledger, risk, strategies
from .arenas.petri import Petri

TREASURY = "TREASURY"
EPSILON = 1e-6  # matchmaker weight floor so zero-fitness agents can still pair


def _encode_rng(state):
    return [state[0], list(state[1]), state[2]]


def _decode_rng(state):
    return (state[0], tuple(state[1]), state[2])


def init_colony(con, cfg):
    """Create schema, accounts, the run row, and gen-0 agents (seeds from
    treasury). Gen-0 archetypes are assigned round-robin for even coverage."""
    db.init_schema(con)
    if con.execute("SELECT COUNT(*) FROM runs").fetchone()[0]:
        raise RuntimeError("database already initialized; init refuses to run twice")
    rng = random.Random(cfg["rng_seed"])
    arena = Petri(cfg["arena"])
    with db.tx(con):
        ledger.create_account(con, TREASURY, "TREASURY")
        con.execute(
            "UPDATE balances SET balance_cents = ? WHERE account_id = ?",
            (cfg["initial_treasury_cents"], TREASURY),
        )
        ledger.create_account(con, f"ARENA:{arena.name}", "ARENA")
        debt = int(cfg["repay_multiple"] * cfg["gen0_seed_cents"])
        for i in range(1, cfg["gen0_population"] + 1):
            genome = evolution.random_genome(rng, evolution.ARCHETYPES[(i - 1) % 3])
            agents.spawn(
                con, 0, f"{i:06d}", genome, 0, (None, None),
                [(TREASURY, cfg["gen0_seed_cents"], "seed")], debt,
            )
        state = {
            "rng": _encode_rng(rng.getstate()),
            "sigma": cfg["mutation"]["sigma_fraction"],
            "max_gen_seen": 0,
            "arena": arena.get_state(),
        }
        con.execute(
            "INSERT INTO runs (started_at, config_json, last_tick, state_json) VALUES (?, ?, 0, ?)",
            (
                datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
                json.dumps(cfg),
                json.dumps(state),
            ),
        )
    orch = Orchestrator(con)
    with db.tx(con):
        orch._snapshot(0, arena.price())
    return orch


class Orchestrator:
    def __init__(self, con, cfg=None):
        self.con = con
        row = con.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 1").fetchone()
        if row is None:
            raise RuntimeError("database not initialized; run `colony init` first")
        self.run_id = row["id"]
        self.cfg = cfg if cfg is not None else json.loads(row["config_json"])
        self.tick = row["last_tick"]
        state = json.loads(row["state_json"])
        self.rng = random.Random()
        self.rng.setstate(_decode_rng(state["rng"]))
        self.sigma = state["sigma"]
        self.max_gen_seen = state["max_gen_seen"]
        self.arena = Petri(self.cfg["arena"])
        self.arena.set_state(state["arena"])
        self.arena_account = f"ARENA:{self.arena.name}"
        self.agents = agents.load_living(con)
        self.queue = sorted(
            (a.id for a in self.agents.values() if a.queue_since is not None),
            key=lambda aid: (self.agents[aid].queue_since, aid),
        )
        self.hall = self._load_hall()
        self.births_cum = con.execute(
            "SELECT COUNT(*) FROM agents WHERE born_tick > 0"
        ).fetchone()[0]
        self.deaths_cum = con.execute(
            "SELECT COUNT(*) FROM agents WHERE died_tick IS NOT NULL"
        ).fetchone()[0]
        self.next_id = 1 + (con.execute(
            "SELECT COALESCE(MAX(CAST(id AS INTEGER)), 0) FROM agents"
        ).fetchone()[0])
        self.dead_growth = {}  # generation -> [final_equity / birth_seed, ...]
        for row in con.execute(
            "SELECT a.generation, s.birth_seed_cents, s.final_equity_cents FROM agents a"
            " JOIN agent_state s ON s.agent_id = a.id WHERE a.died_tick IS NOT NULL"
        ):
            self.dead_growth.setdefault(row[0], []).append(row[2] / max(1, row[1]))

    def _load_hall(self):
        """The hall of fame is a ROLLING window of recently dead high performers
        (peak equity >= 2x birth seed), reconstructed from the fossil record."""
        rows = self.con.execute(
            "SELECT a.genome_json FROM agents a JOIN agent_state s ON s.agent_id = a.id"
            " WHERE a.died_tick IS NOT NULL AND s.peak_equity_cents >= 2 * s.birth_seed_cents"
            " ORDER BY a.died_tick, a.id"
        ).fetchall()
        return [json.loads(r[0]) for r in rows][-self.cfg["hall_size"]:]

    # ------------------------------------------------------------------ loop

    def run(self, n_ticks, checkpoint_cb=None):
        end = self.tick + n_ticks
        while self.tick < end:
            self.step()
            if checkpoint_cb and self.tick % 2000 == 0:
                checkpoint_cb(self.tick)
        ledger.verify_invariants(self.con, self.cfg["initial_treasury_cents"])

    def step(self):
        cfg = self.cfg
        t = self.tick + 1
        with db.tx(self.con):
            self._quota_sweep(t)
            self.arena.step(self.rng)
            price = self.arena.price()
            self._live_phase(t, price)
            self._death_phase(t, price)
            self._breeding_phase(t, price)
            if t % cfg["snapshot_every"] == 0:
                self._snapshot(t, price)
            self._flush(t)
        self.tick = t
        if cfg["debug"] or t % 100 == 0:
            ledger.verify_invariants(self.con, cfg["initial_treasury_cents"])

    # ---------------------------------------------------------------- phases

    def _quota_sweep(self, t):
        """Spec 3.14: house-funded agents sweep min(debt, cash - baseline) to
        the treasury each tick; capital at or below baseline is never touched."""
        for aid in sorted(self.agents):
            agent = self.agents[aid]
            if agent.debt <= 0:
                continue
            pay = min(agent.debt, agents.cash(self.con, agent) - agent.baseline)
            if pay <= 0:
                continue
            ledger.transfer(self.con, t, agents.account_id(aid), TREASURY, pay, "debt_repay")
            agent.debt -= pay
            self.con.execute("UPDATE agents SET debt_cents = ? WHERE id = ?", (agent.debt, aid))

    def _live_phase(self, t, price):
        cfg = self.cfg
        history = self.arena.history(evolution.PARAM_BOUNDS["lookback"][1] + 1)
        for aid in sorted(self.agents):
            agent = self.agents[aid]
            c = agents.cash(self.con, agent)
            equity = c + agent.lots * price
            rent = max(cfg["rent_min_cents"], equity * cfg["rent_bps_of_equity"] // 10_000)
            if c < rent and agent.lots > 0:
                # force-liquidate the ENTIRE position in one sale (fees apply)
                agents.sell_all(self.con, t, agent, price, cfg["fee_bps"], self.arena_account)
                c = agents.cash(self.con, agent)
            if c < rent:
                self._die(t, agent, "liquidity_death", price)
                continue
            ledger.transfer(self.con, t, agents.account_id(aid), TREASURY, rent, "rent")
            c -= rent
            equity = c + agent.lots * price
            decision = strategies.decide(
                agent.genome, history, agent.lots, agent.hold, equity, cfg["fee_bps"]
            )
            decision = risk.check(
                decision, c, equity, agent.lots, price, cfg["max_action_fraction"], cfg["fee_bps"]
            )
            if decision is not None:
                if decision.side == "BUY":
                    agents.buy(self.con, t, agent, decision.lots, price, cfg["fee_bps"],
                               self.arena_account)
                else:
                    agents.sell(self.con, t, agent, decision.lots, price, cfg["fee_bps"],
                                self.arena_account)
                c = agents.cash(self.con, agent)
            if agent.lots > 0:
                agent.hold += 1
                agent.dirty = True
            equity = c + agent.lots * price
            if equity > agent.peak_equity:
                agent.peak_equity = equity
                agent.dirty = True

    def _death_phase(self, t, price):
        cfg = self.cfg
        for aid in sorted(list(self.agents)):
            agent = self.agents[aid]
            equity = agents.equity(self.con, agent, price)
            age = t - agent.born_tick
            if equity <= cfg["death_floor_cents"]:
                cause = "bankrupt"
            elif age >= cfg["max_age_ticks"]:
                cause = "old_age"
            elif not agent.ever_traded and age >= cfg["stagnation_ticks"]:
                cause = "stagnation"  # never-traders only (spec 3.6)
            else:
                continue
            self._die(t, agent, cause, price)

    def _die(self, t, agent, cause, price):
        if agent.peak_equity >= 2 * agent.birth_seed:
            self.hall.append(agent.genome)
            if len(self.hall) > self.cfg["hall_size"]:
                self.hall.pop(0)
        final = agents.die(self.con, t, agent, cause, price, self.cfg["fee_bps"],
                           self.arena_account)
        self.dead_growth.setdefault(agent.generation, []).append(
            final / max(1, agent.birth_seed)
        )
        if agent.id in self.queue:
            self.queue.remove(agent.id)
        del self.agents[agent.id]
        self.deaths_cum += 1

    def _fitness(self, agent, price):
        cfg = self.cfg
        min_age = max(cfg["min_ticks_for_fitness"], 3 * cfg["snapshot_every"])
        return evolution.fitness(
            agents.equity(self.con, agent, price),
            agent.first_snap_equity,
            self.tick + 1 - agent.born_tick,
            agent.peak_equity,
            min_age,
        )

    def _breeding_phase(self, t, price):
        cfg = self.cfg
        fit = {aid: self._fitness(agent, price) for aid, agent in self.agents.items()}
        ids = sorted(self.agents)
        elite = set(sorted(ids, key=lambda i: (-fit[i], i))[: cfg["elitism_top_k"]])
        # a. enqueue: mitosis trigger is RELATIVE to the moving baseline (spec 3.4)
        queued = set(self.queue)
        for aid in ids:
            agent = self.agents[aid]
            if aid in queued or agent.debt > 0:
                continue
            if (agent.last_birth_tick is not None
                    and t - agent.last_birth_tick < cfg["breed_cooldown_ticks"]):
                continue
            if len(self.agents) >= cfg["max_population"] and aid not in elite:
                continue
            if agents.cash(self.con, agent) >= int(cfg["repro_multiple"] * agent.baseline):
                self.queue.append(aid)
                agent.queue_since = t
                agent.dirty = True
        # b. matchmaker: fitness-weighted pairing; failed gates stay in queue
        candidates = list(self.queue)
        while len(candidates) >= 2:
            weights = [max(fit[a], 0.0) + EPSILON for a in candidates]
            a_id = candidates.pop(evolution.pick_weighted(self.rng, range(len(candidates)), weights))
            weights = [max(fit[a], 0.0) + EPSILON for a in candidates]
            b_id = candidates.pop(evolution.pick_weighted(self.rng, range(len(candidates)), weights))
            self._try_crossover(t, a_id, b_id, fit, elite)
        # c. patient loners reproduce asexually
        for aid in list(self.queue):
            agent = self.agents[aid]
            if t - agent.queue_since >= cfg["solo_breed_patience"]:
                self._try_solo(t, aid, elite)
        # e. immigration (spec 3.12): extinction is impossible while the
        # treasury can afford a seed; funded ONLY by recycled colony money
        while (len(self.agents) < cfg["population_floor"]
               and ledger.balance(self.con, TREASURY) >= cfg["gen0_seed_cents"]):
            if self.hall and self.rng.random() < cfg["hall_immigrant_prob"]:
                genome = evolution.mutate(
                    self.rng.choice(self.hall), self.sigma * 2, cfg["mutation"], self.rng
                )
            else:
                genome = evolution.random_genome(self.rng)
            self._birth(
                t, genome, 0, (None, None),
                [(TREASURY, cfg["gen0_seed_cents"], "immigrant_seed")],
                debt=int(cfg["repay_multiple"] * cfg["gen0_seed_cents"]),
            )

    def _pop_cap(self, elite_involved):
        cfg = self.cfg
        return cfg["max_population"] + (cfg["elitism_top_k"] if elite_involved else 0)

    def _try_crossover(self, t, a_id, b_id, fit, elite):
        cfg = self.cfg
        agent_a, agent_b = self.agents[a_id], self.agents[b_id]
        cash_a = agents.cash(self.con, agent_a)
        cash_b = agents.cash(self.con, agent_b)
        seed_a = int(agent_a.genome["econ"]["child_seed_fraction"] / 2 * cash_a)
        seed_b = int(agent_b.genome["econ"]["child_seed_fraction"] / 2 * cash_b)
        seed = seed_a + seed_b
        if (cash_a - seed_a < cfg["reserve_floor_cents"]
                or cash_b - seed_b < cfg["reserve_floor_cents"]
                or seed <= 2 * cfg["death_floor_cents"]
                or len(self.agents) >= self._pop_cap(a_id in elite or b_id in elite)):
            return  # hard gate: skip the attempt, both stay in queue
        genome = evolution.crossover(
            agent_a.genome, agent_b.genome, fit[a_id], fit[b_id],
            self.sigma, cfg["mutation"], self.rng,
        )
        self._birth(
            t, genome, max(agent_a.generation, agent_b.generation) + 1, (a_id, b_id),
            [(agents.account_id(a_id), seed_a, "child_seed"),
             (agents.account_id(b_id), seed_b, "child_seed")],
            debt=0, funder_agents=(agent_a, agent_b),
        )
        self.queue.remove(a_id)
        self.queue.remove(b_id)

    def _try_solo(self, t, aid, elite):
        cfg = self.cfg
        agent = self.agents[aid]
        c = agents.cash(self.con, agent)
        seed = int(agent.genome["econ"]["child_seed_fraction"] * c)
        if (c - seed < cfg["reserve_floor_cents"]
                or seed <= 2 * cfg["death_floor_cents"]
                or len(self.agents) >= self._pop_cap(aid in elite)):
            return  # stays in queue, retried next tick
        genome = evolution.mutate(agent.genome, self.sigma, cfg["mutation"], self.rng)
        self._birth(
            t, genome, agent.generation + 1, (aid, None),
            [(agents.account_id(aid), seed, "child_seed")],
            debt=0, funder_agents=(agent,),
        )
        self.queue.remove(aid)

    def _birth(self, t, genome, generation, parents, funders, debt, funder_agents=()):
        """One atomic birth: seed transfers + baseline resets + agent rows.
        Either everything lands or nothing does (spec 3.4)."""
        aid = f"{self.next_id:06d}"
        with db.savepoint(self.con, "birth"):
            child = agents.spawn(self.con, t, aid, genome, generation, parents, funders, debt)
            for funder in funder_agents:
                funder.baseline = agents.cash(self.con, funder)
                funder.last_birth_tick = t
                funder.queue_since = None
                agents.save_state(self.con, funder)
        self.next_id += 1
        self.agents[aid] = child
        self.births_cum += 1
        if generation > self.max_gen_seen:
            self._adapt_sigma(generation)
            self.max_gen_seen = generation

    def _adapt_sigma(self, new_generation):
        """When a birth opens a new generation, judge the trailing cohorts'
        median growth (final-or-current equity / birth seed) and adapt sigma."""
        window = self.cfg["mutation"]["adaptive"]["window_generations"]
        first = new_generation - window
        if first < 0:
            return
        medians = []
        for gen in range(first, new_generation):
            growths = list(self.dead_growth.get(gen, []))
            price = self.arena.price()
            for agent in self.agents.values():
                if agent.generation == gen:
                    growths.append(
                        agents.equity(self.con, agent, price) / max(1, agent.birth_seed)
                    )
            if not growths:
                return  # cannot judge an empty cohort
            medians.append(statistics.median(growths))
        self.sigma = evolution.adaptive_sigma(
            self.sigma, medians, self.cfg["mutation"]["adaptive"]
        )

    # ------------------------------------------------------------- persistence

    def _snapshot(self, t, price):
        rows = []
        genomes = []
        wealth = 0
        for aid in sorted(self.agents):
            agent = self.agents[aid]
            c = agents.cash(self.con, agent)
            equity = c + agent.lots * price
            if agent.first_snap_equity is None:
                agent.first_snap_equity = equity
                agent.dirty = True
            rows.append((t, aid, c, equity))
            genomes.append(agent.genome)
            wealth += equity
        self.con.executemany(
            "INSERT OR REPLACE INTO snapshots (tick, agent_id, cash_cents, equity_cents)"
            " VALUES (?, ?, ?, ?)",
            rows,
        )
        shares = evolution.archetype_shares(genomes)
        self.con.execute(
            "INSERT OR REPLACE INTO colony_metrics VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                t,
                ledger.balance(self.con, TREASURY),
                wealth,
                ledger.balance(self.con, self.arena_account),
                len(self.agents),
                self.births_cum,
                self.deaths_cum,
                price,
                self.arena.regime_kind(),
                shares["momentum"],
                shares["mean_revert"],
                shares["sitter"],
                evolution.diversity(genomes),
            ),
        )

    def _flush(self, t):
        for agent in self.agents.values():
            if agent.dirty:
                agents.save_state(self.con, agent)
        state = {
            "rng": _encode_rng(self.rng.getstate()),
            "sigma": self.sigma,
            "max_gen_seen": self.max_gen_seen,
            "arena": self.arena.get_state(),
        }
        self.con.execute(
            "UPDATE runs SET last_tick = ?, state_json = ? WHERE id = ?",
            (t, json.dumps(state), self.run_id),
        )
