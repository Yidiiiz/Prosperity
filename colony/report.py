"""Fossil Record queries and plain-text reports. No plotting dependencies."""

import json

from . import evolution


def money(cents):
    sign = "-" if cents < 0 else ""
    return f"{sign}${abs(cents) / 100:,.2f}"


def latest_metrics(con):
    return con.execute("SELECT * FROM colony_metrics ORDER BY tick DESC LIMIT 1").fetchone()


def treasury_flows(con):
    """Treasury inflow/outflow breakdown by ledger memo (spec 9): the quota
    channels are rent + death residues (+ scoped debt_repay); outflows are
    gen-0 seeds and immigrant seeds. Nothing else may touch the treasury."""
    inflows = {}
    for memo, total in con.execute(
        "SELECT memo, SUM(amount_cents) FROM ledger WHERE credit_account = 'TREASURY' GROUP BY memo"
    ):
        key = "death_residue" if memo.startswith("death_residue:") else memo
        inflows[key] = inflows.get(key, 0) + total
    outflows = dict(
        con.execute(
            "SELECT memo, SUM(amount_cents) FROM ledger WHERE debit_account = 'TREASURY' GROUP BY memo"
        ).fetchall()
    )
    return inflows, outflows


def cause_of_death_histogram(con):
    return dict(
        con.execute(
            "SELECT death_cause, COUNT(*) FROM agents WHERE died_tick IS NOT NULL"
            " GROUP BY death_cause ORDER BY COUNT(*) DESC"
        ).fetchall()
    )


def living_genomes(con):
    return [
        json.loads(row[0])
        for row in con.execute("SELECT genome_json FROM agents WHERE died_tick IS NULL")
    ]


def lineage(con, agent_id):
    """Ancestor chain following parent_a (the primary lineage)."""
    chain = []
    current = agent_id
    while current is not None:
        row = con.execute(
            "SELECT id, parent_a, generation FROM agents WHERE id = ?", (current,)
        ).fetchone()
        if row is None:
            break
        chain.append(row["id"])
        current = row["parent_a"]
    return chain


def agent_fitness(con, row, state, current_tick, cfg):
    """Lifetime fitness from snapshots + state (works for dead or alive)."""
    first = state["first_snap_equity_cents"]
    if row["died_tick"] is not None:
        equity_now = state["final_equity_cents"]
        age = row["died_tick"] - row["born_tick"]
    else:
        last = con.execute(
            "SELECT equity_cents FROM snapshots WHERE agent_id = ? ORDER BY tick DESC LIMIT 1",
            (row["id"],),
        ).fetchone()
        equity_now = last[0] if last else first
        age = current_tick - row["born_tick"]
    if equity_now is None:
        return 0.0
    min_age = max(cfg["min_ticks_for_fitness"], 3 * cfg["snapshot_every"])
    return evolution.fitness(equity_now, first, age, state["peak_equity_cents"], min_age)


def hall_of_fame(con, k, cfg):
    """Top-k agents, dead or alive, by lifetime fitness (with enough history)."""
    current_tick = con.execute("SELECT MAX(last_tick) FROM runs").fetchone()[0] or 0
    scored = []
    for row in con.execute("SELECT * FROM agents"):
        state = con.execute(
            "SELECT * FROM agent_state WHERE agent_id = ?", (row["id"],)
        ).fetchone()
        if state is None:
            continue
        score = agent_fitness(con, row, state, current_tick, cfg)
        if score != 0.0:
            scored.append((score, row["id"], row["generation"],
                           json.loads(row["genome_json"])["archetype"]))
    scored.sort(key=lambda s: (-s[0], s[1]))
    return scored[:k]


def summary_text(con, last_n=None):
    cfg = json.loads(
        con.execute("SELECT config_json FROM runs ORDER BY id DESC LIMIT 1").fetchone()[0]
    )
    metrics = latest_metrics(con)
    if metrics is None:
        return "no metrics yet - run the simulation first"
    tick = metrics["tick"]
    since = max(0, tick - last_n) if last_n else 0

    lines = []
    lines.append(f"=== colony report @ tick {tick}" + (f" (last {last_n} ticks)" if last_n else ""))
    initial = cfg["initial_treasury_cents"]
    treasury = metrics["treasury_cents"]
    delta = treasury - initial
    lines.append("")
    lines.append("TREASURY (north star)")
    lines.append(f"  balance          {money(treasury)}  ({'+' if delta >= 0 else ''}{delta / initial * 100:.1f}% vs initial {money(initial)})")
    if delta > 0:
        lines.append("  all deployed capital recovered; profit banked and withdrawal-ready")
    inflows, outflows = treasury_flows(con)
    lines.append(f"  inflows          rent {money(inflows.get('rent', 0))}"
                 f" | death residues {money(inflows.get('death_residue', 0))}"
                 f" | debt repaid {money(inflows.get('debt_repay', 0))}")
    lines.append(f"  outflows         gen-0 seeds {money(outflows.get('seed', 0))}"
                 f" | immigrant seeds {money(outflows.get('immigrant_seed', 0))}")

    lines.append("")
    lines.append("COLONY")
    lines.append(f"  population       {metrics['population']}")
    lines.append(f"  colony wealth    {money(metrics['colony_wealth_cents'])}")
    lines.append(f"  system total     {money(treasury + metrics['colony_wealth_cents'])}")
    lines.append(f"  extracted from market  {money(-metrics['arena_cents'])}")
    lines.append(f"  price / regime   {money(metrics['price_cents'])} / {metrics['regime_kind']}")

    births = con.execute(
        "SELECT COUNT(*) FROM agents WHERE born_tick > ? AND born_tick <= ?", (since, tick)
    ).fetchone()[0]
    deaths_rows = con.execute(
        "SELECT death_cause, COUNT(*) FROM agents WHERE died_tick > ? AND died_tick <= ?"
        " GROUP BY death_cause ORDER BY COUNT(*) DESC",
        (since, tick),
    ).fetchall()
    lines.append("")
    lines.append("FLUX" + (f" (ticks {since + 1}-{tick})" if last_n else " (all time)"))
    lines.append(f"  births           {births}")
    lines.append(f"  deaths           {sum(n for _, n in deaths_rows)}"
                 + (":  " + ", ".join(f"{c} {n}" for c, n in deaths_rows) if deaths_rows else ""))

    genomes = living_genomes(con)
    shares = evolution.archetype_shares(genomes)
    lines.append("")
    lines.append("LIVING POPULATION")
    lines.append("  archetypes       "
                 + "  ".join(f"{a} {shares[a] * 100:.0f}%" for a in evolution.ARCHETYPES))
    lines.append(f"  diversity        {evolution.diversity(genomes):.3f} (Shannon entropy, nats)")
    outstanding = con.execute(
        "SELECT COALESCE(SUM(debt_cents), 0) FROM agents WHERE died_tick IS NULL"
    ).fetchone()[0]
    lines.append(f"  outstanding debt {money(outstanding)}")
    # lot-granularity watchdog (spec 3.11)
    median_eq = con.execute(
        "SELECT equity_cents FROM snapshots WHERE tick = ? ORDER BY equity_cents"
        " LIMIT 1 OFFSET (SELECT COUNT(*) FROM snapshots WHERE tick = ?) / 2",
        (tick, tick),
    ).fetchone()
    if median_eq and metrics["price_cents"] * 200 > median_eq[0]:
        lines.append("  WARNING: price per lot is large vs median equity"
                     " (lot granularity risk, spec 3.11)")
    return "\n".join(lines)


def tree_dot(con):
    """Graphviz DOT of the family tree: node label id / gen / peak equity."""
    lines = ["digraph colony {", '  node [shape=box, fontname="monospace"];']
    rows = con.execute(
        "SELECT a.id, a.generation, a.parent_a, a.parent_b, a.died_tick,"
        " s.peak_equity_cents FROM agents a"
        " LEFT JOIN agent_state s ON s.agent_id = a.id ORDER BY a.id"
    ).fetchall()
    for row in rows:
        peak = money(row["peak_equity_cents"] or 0)
        style = "" if row["died_tick"] is None else ", style=dashed"
        lines.append(
            f'  "{row["id"]}" [label="{row["id"]}\\ngen {row["generation"]}\\npeak {peak}"{style}];'
        )
    for row in rows:
        for parent in (row["parent_a"], row["parent_b"]):
            if parent:
                lines.append(f'  "{parent}" -> "{row["id"]}";')
    lines.append("}")
    return "\n".join(lines)


def inspect_text(con, agent_id):
    row = con.execute("SELECT * FROM agents WHERE id = ?", (agent_id,)).fetchone()
    if row is None:
        return f"no agent {agent_id}"
    state = con.execute("SELECT * FROM agent_state WHERE agent_id = ?", (agent_id,)).fetchone()
    cfg = json.loads(
        con.execute("SELECT config_json FROM runs ORDER BY id DESC LIMIT 1").fetchone()[0]
    )
    current_tick = con.execute("SELECT MAX(last_tick) FROM runs").fetchone()[0] or 0
    genome = json.loads(row["genome_json"])

    lines = [f"=== agent {agent_id}"]
    lines.append(f"generation {row['generation']}"
                 f" | parents {row['parent_a'] or '-'} {row['parent_b'] or '-'}"
                 f" | born tick {row['born_tick']}")
    if row["died_tick"] is not None:
        lines.append(f"DIED tick {row['died_tick']} cause {row['death_cause']}")
    lines.append(f"genome: {json.dumps(genome)}")
    if state:
        lines.append(f"birth seed {money(state['birth_seed_cents'])}"
                     f" | baseline {money(state['baseline_cents'])}"
                     f" | peak equity {money(state['peak_equity_cents'])}"
                     f" | debt {money(row['debt_cents'])}")
        lines.append(f"fitness {agent_fitness(con, row, state, current_tick, cfg):+.6f}")
        # lifetime P&L: wealth generated = what it holds/held + what it gave
        # away (rent, debt, child seeds, residue) minus what it was given
        account = f"AGENT:{agent_id}"
        given = con.execute(
            "SELECT COALESCE(SUM(amount_cents), 0) FROM ledger WHERE debit_account = ?"
            " AND (memo IN ('rent', 'debt_repay', 'child_seed') OR memo LIKE 'death_residue:%')",
            (account,),
        ).fetchone()[0]
        if row["died_tick"] is not None:
            holding = 0
        else:
            last = con.execute(
                "SELECT equity_cents FROM snapshots WHERE agent_id = ? ORDER BY tick DESC LIMIT 1",
                (agent_id,),
            ).fetchone()
            holding = last[0] if last else 0
        pnl = holding + given - state["birth_seed_cents"]
        lines.append(f"lifetime P&L {money(pnl)} (equity {money(holding)}"
                     f" + paid out {money(given)} - seed {money(state['birth_seed_cents'])})")
    lines.append(f"lineage: {' <- '.join(lineage(con, agent_id))}")
    trades = con.execute(
        "SELECT tick, side, lots, price_cents, fee_cents FROM trades WHERE agent_id = ?"
        " ORDER BY seq DESC LIMIT 50",
        (agent_id,),
    ).fetchall()
    lines.append(f"last {len(trades)} trades (newest first):")
    for t in trades:
        lines.append(f"  t={t['tick']:>7}  {t['side']:<4} {t['lots']:>6} lots"
                     f" @ {money(t['price_cents'])}  fee {money(t['fee_cents'])}")
    return "\n".join(lines)
