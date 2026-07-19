"""The tick loop (spec section 7). One RNG, sorted-id iteration, one
transaction per tick, invariants verified on cadence."""

import datetime
import json
import random
import statistics

from . import agents, db, evolution, ledger, risk, strategies
from .arenas import make_arena
from .config import ConfigError, immigration_accrual, immigration_capacity, rent_due

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
    ledger.attach_mirror(con)
    rng = random.Random(cfg["rng_seed"])
    arena = make_arena(cfg["arena"])
    # Lot granularity (spec 3.11): replay start prices come from the CSV, so
    # this check can only happen here. small_stakes acknowledges the risk.
    if cfg["gen0_seed_u"] < 200 * arena.price() and not cfg.get("small_stakes"):
        raise ConfigError(
            f"gen0_seed_u must be at least 200 x the starting lot price"
            f" ({arena.price()} cents); set 'small_stakes': true to accept the"
            " lot-granularity risk"
        )
    with db.tx(con):
        ledger.create_account(con, TREASURY, "TREASURY")
        ledger.genesis(con, TREASURY, cfg["initial_treasury_u"])
        ledger.create_account(con, f"ARENA:{arena.name}", "ARENA")
        if cfg.get("bank_path"):
            # copy the certified set into bank_snapshot (spec v3 5.1): the
            # run never reads the live bank file again — refreshing champions
            # into a running colony means starting a new colony
            from . import bank as bank_mod

            for h, entry in sorted(bank_mod.fold(cfg["bank_path"]).items()):
                if entry["status"] != "certified":
                    continue
                src = entry["admit"]["source"]
                oos = entry["certify"]["audited"]["realized_bps_per_day"]
                prov = (f"{src['arena']} {src['window'][0][:10]}..{src['window'][1][:10]}"
                        f" seed {src['config_seed']} agent {src['agent_id']}"
                        f" | oos {oos:+.2f} bps/day")
                con.execute("INSERT INTO bank_snapshot VALUES (?, ?, ?)",
                            (h, json.dumps(entry["genome"]), prov))
        debt = int(cfg["repay_multiple"] * cfg["gen0_seed_u"])
        for i in range(1, cfg["gen0_population"] + 1):
            # round-robin over all four archetypes (v3 section 6); the sitter
            # control group stays in the rotation
            genome = evolution.random_genome(
                rng, evolution.ARCHETYPES[(i - 1) % len(evolution.ARCHETYPES)]
            )
            agents.spawn(
                con, 0, f"{i:06d}", genome, 0, (None, None),
                [(TREASURY, cfg["gen0_seed_u"], "seed")], debt,
            )
        state = {
            "rng": _encode_rng(rng.getstate()),
            "sigma": cfg["mutation"]["sigma_fraction"],
            "max_gen_seen": 0,
            "arena": arena.get_state(),
            # the bucket starts FULL (one year's budget, spec v2 7.3): early
            # deaths can be replaced; only sustained churn exhausts it
            "immigration_tokens_u": immigration_capacity(cfg),
            # v3 5.4: the compounding ratchet starts at initial capitalization
            "treasury_high_water_u": cfg["initial_treasury_u"],
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
        ledger.attach_mirror(con)  # zero-SELECT cash reads (spec v2 section 4)
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
        self.imm_capacity = immigration_capacity(self.cfg)
        self.imm_accrual = immigration_accrual(self.cfg)
        self.imm_tokens = state.get("immigration_tokens_u", self.imm_capacity)
        # v3 5.4: the one-way compounding ratchet, persisted in run state so a
        # hard-kill resume is byte-identical (spec v3 10.4)
        self.high_water = state.get("treasury_high_water_u",
                                    self.cfg["initial_treasury_u"])
        self.arena = make_arena(self.cfg["arena"])
        self.arena.set_state(state["arena"])
        self.arena_account = f"ARENA:{self.arena.name}"
        self.venue = self.cfg["venue"]
        # v3 additive schema: pre-v3 databases resume with no snapshot and
        # gain the origin column in place (ledger bytes are untouched)
        if not con.execute("SELECT COUNT(*) FROM sqlite_master"
                           " WHERE name = 'bank_snapshot'").fetchone()[0]:
            con.execute("CREATE TABLE bank_snapshot (genome_hash TEXT PRIMARY KEY,"
                        " genome_json TEXT NOT NULL, provenance TEXT NOT NULL)")
        if "origin" not in [r[1] for r in con.execute("PRAGMA table_info(agents)")]:
            con.execute("ALTER TABLE agents ADD COLUMN origin TEXT")
        self.bank_pool = [
            (row[0], json.loads(row[1])) for row in con.execute(
                "SELECT genome_hash, genome_json FROM bank_snapshot"
                " ORDER BY genome_hash")
        ]
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
            "SELECT a.generation, s.birth_seed_u, s.final_equity_u FROM agents a"
            " JOIN agent_state s ON s.agent_id = a.id WHERE a.died_tick IS NOT NULL"
        ):
            self.dead_growth.setdefault(row[0], []).append(row[2] / max(1, row[1]))

    def _load_hall(self):
        """The hall of fame is a ROLLING window of recently dead high performers
        (peak equity >= 2x birth seed), reconstructed from the fossil record."""
        rows = self.con.execute(
            "SELECT a.genome_json FROM agents a JOIN agent_state s ON s.agent_id = a.id"
            " WHERE a.died_tick IS NOT NULL AND s.peak_equity_u >= 2 * s.birth_seed_u"
            " ORDER BY a.died_tick, a.id"
        ).fetchall()
        return [json.loads(r[0]) for r in rows][-self.cfg["hall_size"]:]

    # ------------------------------------------------------------------ loop

    def run(self, n_ticks, checkpoint_cb=None):
        """Run up to n_ticks (fewer if a finite arena runs out of data, or a
        live feed goes stale). Returns the number of ticks actually executed.

        flush_every N (spec v2 section 4): one transaction spans up to N
        ticks, committed with the flushed runtime state — a crash loses at
        most N ticks and the database is always at a flushed boundary.
        Live configs pin 1 (the validator enforces it), so the blocking
        wait for feed data never happens inside an open transaction.
        """
        start = self.tick
        end = start + n_ticks
        flush_every = self.cfg.get("flush_every", 1)
        checkpoint_every = self.cfg["checkpoint_every"]
        wait = getattr(self.arena, "wait_for_data", None)
        while self.tick < end and not self.arena.exhausted():
            if wait is not None and not wait():
                break  # live feed went stale; state is saved, rerun to resume
            window_start = self.tick
            with db.tx(self.con):
                while (self.tick - window_start < flush_every
                       and self.tick < end and not self.arena.exhausted()):
                    self._step_inner()
                self._flush(self.tick)
            if checkpoint_cb and self.tick // checkpoint_every > window_start // checkpoint_every:
                checkpoint_cb(self.tick)
        ledger.verify_invariants(self.con, self.cfg["initial_treasury_u"])
        return self.tick - start

    def step(self):
        """One tick in its own transaction, state flushed (the flush_every 1
        path; tests and the daemon drive this directly)."""
        with db.tx(self.con):
            self._step_inner()
            self._flush(self.tick)

    def _step_inner(self):
        """Advance one tick inside an already-open transaction."""
        cfg = self.cfg
        t = self.tick + 1
        # accrual fills only up to base capacity; tokens reinvested above it
        # (v3 5.4) are spent down, never clamped away
        if self.imm_tokens < self.imm_capacity:
            self.imm_tokens = min(self.imm_capacity, self.imm_tokens + self.imm_accrual)
        self._quota_sweep(t)
        prev_day = self.arena.utc() // 86_400
        self.arena.step(self.rng)
        price = self.arena.price()
        utc = self.arena.utc()
        if utc // 86_400 > prev_day:
            self._reinvest()
        self._live_phase(t, utc, price)
        self._death_phase(t, utc, price)
        self._breeding_phase(t, price)
        if t % cfg["snapshot_every"] == 0:
            self._snapshot(t, price)
        self.tick = t
        if cfg["debug"] or t % 100 == 0:
            # O(accounts) conservation check on cadence; the full O(ledger)
            # audit runs at run boundaries (spec v2 section 4 keeps the hot
            # path free of table scans)
            ledger.verify_fast(self.con, cfg["initial_treasury_u"])

    def wind_down(self, cause="horizon"):
        """Terminal audit for finite arenas: liquidate every living agent at
        the current price and return all estates to the treasury. After this,
        colony wealth is 0 and the treasury holds the system's entire cash."""
        price = self.arena.price()
        utc = self.arena.utc()
        with db.tx(self.con):
            for aid in sorted(list(self.agents)):
                self._die(self.tick, utc, self.agents[aid], cause, price)
            self._flush(self.tick)
        ledger.verify_invariants(self.con, self.cfg["initial_treasury_u"])
        if self.cfg.get("bank_path"):
            # bank admission is automatic at every terminal audit (spec v3
            # 4.3) — in-sample by definition, candidate status only
            from . import bank

            bank.admit_from_db(self.con, self.cfg["bank_path"],
                               records_root=self.cfg.get("records_root", "records"))

    # ---------------------------------------------------------------- phases

    def _reinvest(self):
        """The compounding ratchet (spec v3 5.4), at each UTC-day boundary:
        redeploy reinvest_fraction of treasury headroom above the high-water
        mark into the immigration token bucket (capped at 4x base capacity),
        then ratchet the mark up. Tokens are budget, not money — no ledger
        rows; the money moves only when an immigrant is seeded. Drawdowns
        below high-water redeploy nothing: a losing colony cannot
        chain-refill itself."""
        treasury = ledger.balance(self.con, TREASURY)
        if treasury <= self.high_water:
            return
        headroom = treasury - self.high_water
        add = headroom * self.cfg.get("reinvest_fraction_bps", 5_000) // 10_000
        self.imm_tokens = min(self.imm_tokens + add, 4 * self.imm_capacity)
        self.high_water = treasury

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
            self.con.execute("UPDATE agents SET debt_u = ? WHERE id = ?", (agent.debt, aid))

    def _execute(self, t, utc, agent, decision, price):
        if decision.side == "BUY":
            agents.buy(self.con, t, utc, agent, decision.lots, price, self.venue,
                       self.arena_account)
        else:
            agents.sell(self.con, t, utc, agent, decision.lots, price, self.venue,
                        self.arena_account)

    def _live_phase(self, t, utc, price):
        cfg = self.cfg
        venue = self.venue
        delay = venue.get("fill_delay_ticks", 1)
        cost_bps = risk.per_side_cost_bps(venue)
        history = self.arena.history(evolution.PARAM_BOUNDS["lookback"][1] + 1)
        utc_hour = (utc // 3_600) % 24
        fill_cutoff = utc - 86_400  # rolling 24h fill window (spec v2 7.1)
        for aid in sorted(self.agents):
            agent = self.agents[aid]
            # Pending order from the last bar fills FIRST at THIS bar's price
            # (spec v2 2.3): risk re-checked against current equity — shrunk
            # if it now violates caps, cancelled if unaffordable.
            if agent.pending_side is not None:
                c = agents.cash(self.con, agent)
                fill = risk.check(
                    strategies.Decision(agent.pending_side, agent.pending_lots),
                    c, c + agent.lots * price, agent.lots, price,
                    cfg["max_action_fraction"], venue,
                )
                agent.pending_side = None
                agent.pending_lots = 0
                agent.dirty = True
                if fill is not None:
                    self._execute(t, utc, agent, fill, price)
            c = agents.cash(self.con, agent)
            equity = c + agent.lots * price
            rent = rent_due(equity, cfg)
            if c < rent and agent.lots > 0:
                # force-liquidate the ENTIRE position in one sale (fees apply)
                agents.sell_all(self.con, t, utc, agent, price, venue, self.arena_account)
                c = agents.cash(self.con, agent)
            if c < rent:
                self._die(t, utc, agent, "liquidity_death", price)
                continue
            if rent > 0:  # can round to 0 at small stakes; a 0-amount row is no row
                ledger.transfer(self.con, t, agents.account_id(aid), TREASURY, rent, "rent")
                c -= rent
            equity = c + agent.lots * price
            fills = agent.fills
            while fills and fills[0] <= fill_cutoff:  # prune is deterministic,
                fills.pop(0)  # so no dirty flag: stale entries re-prune on load
            decision = strategies.decide(
                agent.genome, history, agent.lots, agent.hold, equity, cost_bps,
                utc_hour, len(fills),
            )
            decision = risk.check(
                decision, c, equity, agent.lots, price, cfg["max_action_fraction"], venue
            )
            if decision is not None:
                if delay == 0:
                    self._execute(t, utc, agent, decision, price)
                    c = agents.cash(self.con, agent)
                else:
                    # decided at row N, executes at row N+1 (one pending order
                    # per agent; a new decision replaces an unfilled one)
                    agent.pending_side = decision.side
                    agent.pending_lots = decision.lots
                    agent.dirty = True
            if agent.lots > 0:
                agent.hold += 1
                agent.dirty = True
            equity = c + agent.lots * price
            if equity > agent.peak_equity:
                agent.peak_equity = equity
                agent.dirty = True

    def _death_phase(self, t, utc, price):
        cfg = self.cfg
        for aid in sorted(list(self.agents)):
            agent = self.agents[aid]
            equity = agents.equity(self.con, agent, price)
            age = t - agent.born_tick
            if equity <= cfg["death_floor_u"]:
                cause = "bankrupt"
            elif age >= cfg["max_age_ticks"]:
                cause = "old_age"
            elif not agent.ever_traded and age >= cfg["stagnation_ticks"]:
                cause = "stagnation"  # never-traders only (spec 3.6)
            else:
                continue
            self._die(t, utc, agent, cause, price)

    def _die(self, t, utc, agent, cause, price):
        if agent.peak_equity >= 2 * agent.birth_seed:
            self.hall.append(agent.genome)
            if len(self.hall) > self.cfg["hall_size"]:
                self.hall.pop(0)
        final = agents.die(self.con, t, utc, agent, cause, price, self.venue,
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
        # e. immigration (spec 3.12, budget-capped in v2 7.3): funded ONLY by
        # recycled colony money AND the rolling budget. An exhausted budget
        # leaves the population below the floor — the honest signal that the
        # venue cannot support it, not treasury life support.
        while (len(self.agents) < cfg["population_floor"]
               and ledger.balance(self.con, TREASURY) >= cfg["gen0_seed_u"]
               and self.imm_tokens >= cfg["gen0_seed_u"]):
            if self._bank_immigrant(t):
                continue
            self.imm_tokens -= cfg["gen0_seed_u"]
            if self.hall and self.rng.random() < cfg["hall_immigrant_prob"]:
                genome = evolution.mutate(
                    self.rng.choice(self.hall), self.sigma * 2, cfg["mutation"], self.rng
                )
            else:
                genome = evolution.random_genome(self.rng)
            self._birth(
                t, genome, 0, (None, None),
                [(TREASURY, cfg["gen0_seed_u"], "immigrant_seed")],
                debt=int(cfg["repay_multiple"] * cfg["gen0_seed_u"]),
            )

    def _bank_immigrant(self, t):
        """Spec v3 5.2/5.3: with probability bank_immigrant_share, clone a
        uniformly-drawn snapshot genome UNMUTATED (pure reuse; diversity is
        the other half's job), funded at gen0_seed x champion_seed_multiple
        from the same token bucket. Empty snapshot -> always random (and no
        RNG is consumed, so pre-v3 streams replay identically)."""
        cfg = self.cfg
        if not self.bank_pool:
            return False
        if self.rng.random() >= cfg.get("bank_immigrant_share_bps", 5_000) / 10_000:
            return False
        seed = cfg["gen0_seed_u"] * cfg.get("champion_seed_multiple", 2)
        if self.imm_tokens < seed or ledger.balance(self.con, TREASURY) < seed:
            return False  # champion unaffordable right now: random gen-0 instead
        h, genome = self.bank_pool[self.rng.randrange(len(self.bank_pool))]
        self.imm_tokens -= seed
        self._birth(
            t, json.loads(json.dumps(genome)), 0, (None, None),
            [(TREASURY, seed, "immigrant_seed")],
            debt=int(cfg["repay_multiple"] * seed), origin=f"bank:{h[:12]}",
        )
        return True

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
        if (cash_a - seed_a < cfg["reserve_floor_u"]
                or cash_b - seed_b < cfg["reserve_floor_u"]
                or seed <= 2 * cfg["death_floor_u"]
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
        if (c - seed < cfg["reserve_floor_u"]
                or seed <= 2 * cfg["death_floor_u"]
                or len(self.agents) >= self._pop_cap(aid in elite)):
            return  # stays in queue, retried next tick
        genome = evolution.mutate(agent.genome, self.sigma, cfg["mutation"], self.rng)
        self._birth(
            t, genome, agent.generation + 1, (aid, None),
            [(agents.account_id(aid), seed, "child_seed")],
            debt=0, funder_agents=(agent,),
        )
        self.queue.remove(aid)

    def _birth(self, t, genome, generation, parents, funders, debt, funder_agents=(),
               origin=None):
        """One atomic birth: seed transfers + baseline resets + agent rows.
        Either everything lands or nothing does (spec 3.4)."""
        aid = f"{self.next_id:06d}"
        with db.savepoint(self.con, "birth"):
            child = agents.spawn(self.con, t, aid, genome, generation, parents, funders,
                                 debt, origin)
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
            "INSERT OR REPLACE INTO snapshots (tick, agent_id, cash_u, equity_u)"
            " VALUES (?, ?, ?, ?)",
            rows,
        )
        shares = evolution.archetype_shares(genomes)
        self.con.execute(
            "INSERT OR REPLACE INTO colony_metrics VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                t,
                self.arena.utc(),
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
            "immigration_tokens_u": self.imm_tokens,
            "treasury_high_water_u": self.high_water,
        }
        self.con.execute(
            "UPDATE runs SET last_tick = ?, state_json = ? WHERE id = ?",
            (t, json.dumps(state), self.run_id),
        )
