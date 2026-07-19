"""The genome bank (spec v3 section 4): profitmakers persist.

One append-only JSONL event log (`admit`, `certify`, `lapse`); current state
= fold the log, last status wins per genome. Admission is in-sample by
definition and earns `candidate` status only; certification is an
out-of-sample frozen solo probe (the #16 in-memory harness, replay
semantics) over a window that must postdate the admission window —
overlapping windows refuse. Red line (spec v3 4.6): the bank stores genomes
(parameter dicts), never code. Single-writer by convention: CLI and
experiment drivers write; the daemon never does.
"""

import datetime
import hashlib
import json
from pathlib import Path

from . import report, risk, strategies
from .arenas.replay import parse_utc, read_rows, to_price_u
from .records import Record

PROBE_CAPITAL_U = 1_000_000_000  # $1,000 solo stake for every probe
MAX_ACTION_FRACTION = 0.80       # the standard anti-gambling cap


class BankError(Exception):
    pass


def genome_hash(genome):
    """sha256 of the canonical JSON (sorted keys) — dedup is by hash."""
    return hashlib.sha256(
        json.dumps(genome, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _utc_now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def _iso(utc):
    return datetime.datetime.fromtimestamp(utc, datetime.timezone.utc).isoformat(
        timespec="seconds"
    )


def append_event(path, event):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(event, sort_keys=True) + "\n")
        f.flush()


def fold(path):
    """genome_hash -> {status, genome, admit, certify?, lapse?}. Lapsed
    genomes stay in the log forever (they are data) but are never drawn."""
    state = {}
    path = Path(path)
    if not path.exists():
        return state
    with open(path, encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            ev = json.loads(line)
            entry = state.setdefault(ev["genome_hash"], {"status": "candidate"})
            if ev["event"] == "admit":
                entry.update(genome=ev["genome"], admit=ev)
            else:  # certify | lapse: last status wins
                entry["status"] = {"certify": "certified", "lapse": "lapsed"}[ev["event"]]
                entry[ev["event"]] = ev
    return state


def _record(records_root, name, body, headline):
    rec = Record(records_root, "bank", name)
    rec.section("events", body)
    rec.finish(headline)


def admit_from_db(con, bank_path, records_root="records"):
    """Admission rule (spec v3 4.3): among agents with realized_pnl_u > 0 and
    fills >= bank_min_fills, rank by realized_bps_per_day, admit the top
    bank_admit_top_k, skipping hashes already in the bank. Runs at every
    terminal audit and via `colony bank admit`."""
    row = con.execute(
        "SELECT config_json, state_json FROM runs ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if row is None:
        raise BankError("database has no run to admit from")
    cfg, state = json.loads(row[0]), json.loads(row[1])
    lo, hi = con.execute("SELECT MIN(utc), MAX(utc) FROM colony_metrics").fetchone()
    db_path = con.execute("PRAGMA database_list").fetchone()[2]
    existing = fold(bank_path)
    admitted = []
    for aid, stats in report.realized_leaders(con, cfg.get("bank_min_fills", 20)):
        if len(admitted) >= cfg.get("bank_admit_top_k", 8):
            break
        genome = json.loads(con.execute(
            "SELECT genome_json FROM agents WHERE id = ?", (aid,)
        ).fetchone()[0])
        h = genome_hash(genome)
        if h in existing:
            continue
        event = {
            "event": "admit", "utc": _utc_now(), "genome_hash": h, "genome": genome,
            # full provenance (spec v3 4.2): a banked genome without it is inadmissible
            "source": {"arena": cfg["arena"]["name"],
                       "tape_digest": state.get("arena", {}).get(
                           "digest", cfg["arena"].get("kind", "petri")),
                       "window": [_iso(lo), _iso(hi)],
                       "config_seed": cfg["rng_seed"], "run_db": db_path,
                       "agent_id": aid},
            "audited": {key: stats[key] for key in (
                "realized_pnl_u", "realized_bps_per_day", "fills", "fees_u",
                "active_days")},
        }
        append_event(bank_path, event)
        existing[h] = {"status": "candidate", "genome": genome, "admit": event}
        admitted.append((aid, h, stats["realized_bps_per_day"]))
    if admitted:
        body = "\n".join(f"admit {h[:12]} agent {aid} {bps:+.2f} bps/day"
                         for aid, h, bps in admitted)
        _record(records_root, "admit", body,
                f"{len(admitted)} candidate(s) admitted from {db_path}")
    return admitted


def solo_probe(genome, times, prices_u, venue):
    """Frozen solo probe: the #16 in-memory harness with replay semantics —
    fill delay one bar, gates and fill window active, venue costs only (no
    rent: certification asks about market edge, not colony economics).
    Returns (final cash_u, total fills) after liquidation at the last bar."""
    cost_bps = risk.per_side_cost_bps(venue)
    cash, lots, hold, pending, n_fills = PROBE_CAPITAL_U, 0, 0, None, 0
    window, history = [], []
    for utc, price in zip(times, prices_u):
        history.append(price)
        del history[:-101]  # max lookback bound + 1
        if pending is not None:  # decided last bar, fills at THIS bar (v2 2.3)
            d = risk.check(pending, cash, cash + lots * price, lots, price,
                           MAX_ACTION_FRACTION, venue)
            pending = None
            if d is not None:
                if d.side == "BUY":
                    cost = d.lots * risk.buy_price_u(price, venue)
                    cash -= cost + risk.fee_u(cost, venue)
                    lots += d.lots
                    hold = 0
                else:
                    proceeds = d.lots * risk.sell_price_u(price, venue)
                    cash += proceeds - risk.fee_u(proceeds, venue)
                    lots -= d.lots
                window.append(utc)
                n_fills += 1
        while window and window[0] <= utc - 86_400:
            window.pop(0)
        equity = cash + lots * price
        d = strategies.decide(genome, history, lots, hold, equity, cost_bps,
                              (utc // 3_600) % 24, len(window))
        pending = risk.check(d, cash, equity, lots, price, MAX_ACTION_FRACTION, venue)
        if lots > 0:
            hold += 1
    if lots > 0:  # terminal liquidation, as the colony's audit would
        proceeds = lots * risk.sell_price_u(prices_u[-1], venue)
        cash += proceeds - risk.fee_u(proceeds, venue)
        n_fills += 1
    return cash, n_fills


def certify(bank_path, tape_csv, venue, lot_denominator=1, from_date=None,
            recertify=False, records_root="records"):
    """Certification (spec v3 4.4): probe every candidate (and, with
    recertify, every certified genome) on a tape window that postdates its
    admission window. Profitable after full venue costs -> certify; else
    lapse. Overlap refuses loudly."""
    times, closes = read_rows(tape_csv)
    if from_date is not None:
        start = parse_utc(from_date)
        keep = [i for i, t in enumerate(times) if t >= start]
        times = [times[i] for i in keep]
        closes = [closes[i] for i in keep]
    if len(times) < 2:
        raise BankError(f"{tape_csv}: fewer than 2 bars in the probe window")
    prices = [to_price_u(c, lot_denominator) for c in closes]
    statuses = ("candidate", "certified") if recertify else ("candidate",)
    results = []
    for h, entry in sorted(fold(bank_path).items()):
        if entry["status"] not in statuses or "admit" not in entry:
            continue
        window_end = parse_utc(entry["admit"]["source"]["window"][1])
        if times[0] <= window_end:
            raise BankError(
                f"probe starts {_iso(times[0])} but {h[:12]} was admitted on a"
                f" window ending {_iso(window_end)}: refusing an in-sample"
                " certification (spec v3 4.4)")
        final, n_fills = solo_probe(entry["genome"], times, prices, venue)
        pnl = final - PROBE_CAPITAL_U
        days = max(times[-1] - times[0], 86_400) / 86_400
        event = {
            "event": "certify" if pnl > 0 else "lapse", "utc": _utc_now(),
            "genome_hash": h,
            "probe": {"tape": str(tape_csv), "window": [_iso(times[0]), _iso(times[-1])],
                      "capital_u": PROBE_CAPITAL_U, "venue": venue,
                      "lot_denominator": lot_denominator},
            "audited": {"realized_pnl_u": pnl, "fills": n_fills,
                        "realized_bps_per_day": 10_000 * pnl / PROBE_CAPITAL_U / days},
        }
        append_event(bank_path, event)
        results.append((h, event["event"], pnl))
    if results:
        body = "\n".join(f"{verdict} {h[:12]} pnl {pnl} u" for h, verdict, pnl in results)
        certified = sum(1 for _, v, _ in results if v == "certify")
        _record(records_root, "certify", body,
                f"{certified}/{len(results)} probe(s) certified on {Path(tape_csv).name}")
    return results
