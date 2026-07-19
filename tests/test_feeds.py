"""Feeds & journal segmentation (spec v2 section 5): daily-segment journals
with rotation digests, the Live arena chaining segments transparently, the
segmented resume digest, and the websocket frame codec — all offline; tests
play the role of the feed."""

import datetime
import importlib.util
import sys
from pathlib import Path

import pytest

from colony.arenas.live import Live
from colony.config import ConfigError
from tests.conftest import make_cfg

TOOLS = Path(__file__).resolve().parent.parent / "tools"


def load_tool(name):
    spec = importlib.util.spec_from_file_location(name, TOOLS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


live_feed = load_tool("live_feed")
UTC = datetime.timezone.utc


def write_segment(directory, day, rows, torn=False):
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{day}.csv"
    lines = ["Date,Close"]
    for hh_mm_ss, close in rows:
        lines.append(f"{day}T{hh_mm_ss},{close}")
    text = "\n".join(lines) + ("" if torn else "\n")
    path.write_text(text, encoding="utf-8")
    return path


# ------------------------------------------------------------ Journal writer

def test_journal_rotates_at_utc_midnight_and_seals_digest(tmp_path):
    journal = live_feed.Journal(directory=str(tmp_path / "j"))
    day1 = datetime.datetime(2026, 7, 18, 23, 59, 59, tzinfo=UTC)
    day2 = datetime.datetime(2026, 7, 19, 0, 0, 0, tzinfo=UTC)
    journal.append(day1, 100.0)
    journal.append(day2, 101.0)  # crosses midnight: rotates
    journal.append(day2 + datetime.timedelta(seconds=1), 102.0)
    journal.close()
    root = tmp_path / "j"
    assert (root / "2026-07-18.csv").exists() and (root / "2026-07-19.csv").exists()
    # the closed segment got its sha256; the open one did not
    sealed = (root / "2026-07-18.sha256").read_text().strip()
    import hashlib
    assert sealed == hashlib.sha256((root / "2026-07-18.csv").read_bytes()).hexdigest()
    assert not (root / "2026-07-19.sha256").exists()
    assert (root / "2026-07-19.csv").read_text() == (
        "Date,Close\n2026-07-19T00:00:00,101.0\n2026-07-19T00:00:01,102.0\n"
    )


def test_journal_appends_to_todays_segment_on_restart(tmp_path):
    stamp = datetime.datetime(2026, 7, 18, 12, 0, 0, tzinfo=UTC)
    j1 = live_feed.Journal(directory=str(tmp_path / "j"))
    j1.append(stamp, 100.0)
    j1.close()
    j2 = live_feed.Journal(directory=str(tmp_path / "j"))
    j2.append(stamp + datetime.timedelta(seconds=1), 101.0)
    j2.close()
    text = (tmp_path / "j" / "2026-07-18.csv").read_text()
    assert text.count("Date,Close") == 1 and text.count("\n") == 3


# --------------------------------------------------------- websocket codec

def test_ws_frame_roundtrip():
    frame = live_feed.encode_frame(0x1, b"hello")
    # our encoder masks (client side); the decoder tolerates masked frames
    fin, opcode, payload, rest = live_feed.decode_frame(frame)
    assert (fin, opcode, payload, rest) == (True, 0x1, b"hello", b"")
    # server-style unmasked frame, split delivery
    server = bytes([0x81, 0x03]) + b"abc"
    assert live_feed.decode_frame(server[:3]) is None  # incomplete
    assert live_feed.decode_frame(server) == (True, 0x1, b"abc", b"")
    # 16-bit length
    big = bytes([0x81, 126]) + (300).to_bytes(2, "big") + b"x" * 300
    assert live_feed.decode_frame(big)[2] == b"x" * 300


# ------------------------------------------------------ Live arena chaining

def seg_rows(n, start=0, base=100.0):
    return [(f"00:00:{s:02d}" if s < 60 else f"00:{s // 60:02d}:{s % 60:02d}",
             round(base + s, 2)) for s in range(start, start + n)]


def test_live_chains_segments_in_date_order(tmp_path):
    root = tmp_path / "j"
    write_segment(root, "2026-07-17", seg_rows(3, base=100))
    write_segment(root, "2026-07-18", seg_rows(2, base=200))
    arena = Live({"name": "x", "journal": str(root)})
    assert arena.price() == 100_000_000  # first row of the first segment
    seen = [arena.price()]
    for _ in range(4):
        arena.step(None)
        seen.append(arena.price())
    assert seen == [100_000_000, 101_000_000, 102_000_000, 200_000_000, 201_000_000]
    expected = int(datetime.datetime(2026, 7, 18, 0, 0, 1, tzinfo=UTC).timestamp())
    assert arena.utc() == expected  # from the Date column, not synthesized


def test_live_picks_up_a_new_segment_mid_session(tmp_path):
    root = tmp_path / "j"
    write_segment(root, "2026-07-17", seg_rows(2, base=100))
    arena = Live({"name": "x", "journal": str(root), "poll_timeout_seconds": 0.5})
    arena.step(None)
    assert not arena.wait_for_data()  # nothing more yet: stale, not exhausted
    write_segment(root, "2026-07-18", seg_rows(1, base=200))  # feed rotates
    assert arena.wait_for_data()
    arena.step(None)
    assert arena.price() == 200_000_000


def test_live_torn_tail_in_growing_segment_ignored(tmp_path):
    root = tmp_path / "j"
    write_segment(root, "2026-07-17", seg_rows(1), torn=False)
    with open(root / "2026-07-17.csv", "a", encoding="utf-8") as f:
        f.write("2026-07-17T00:00:01,999")  # no newline: feed mid-write
    arena = Live({"name": "x", "journal": str(root), "poll_timeout_seconds": 0.3})
    assert not arena.wait_for_data()
    with open(root / "2026-07-17.csv", "a", encoding="utf-8") as f:
        f.write("\n")
    assert arena.wait_for_data()


def test_segmented_resume_digest_accepts_growth_rejects_tampering(tmp_path):
    root = tmp_path / "j"
    write_segment(root, "2026-07-17", seg_rows(3, base=100))
    write_segment(root, "2026-07-18", seg_rows(3, base=200))
    arena = Live({"name": "x", "journal": str(root)})
    for _ in range(4):
        arena.step(None)  # cursor is 2 rows into the second segment
    state = arena.get_state()
    assert [s[0] for s in state["segments"]] == ["2026-07-17.csv"]
    assert state["tail"][0] == "2026-07-18.csv" and state["tail"][2] == 2

    write_segment(root, "2026-07-19", seg_rows(2, base=300))  # append-only growth
    resumed = Live({"name": "x", "journal": str(root)})
    resumed.set_state(state)
    assert resumed.price() == arena.price()

    # tampering with a consumed segment refuses
    write_segment(root, "2026-07-17", seg_rows(3, base=999))
    tampered = Live({"name": "x", "journal": str(root)})
    with pytest.raises(RuntimeError):
        tampered.set_state(state)


def test_live_config_requires_exactly_one_journal_form(tmp_path):
    with pytest.raises(ConfigError):
        make_cfg(arena={"kind": "live", "name": "x", "tick_seconds": 1},
                 venue={"fill_delay_ticks": 1})
    with pytest.raises(ConfigError):
        make_cfg(arena={"kind": "live", "name": "x", "csv": "a.csv", "journal": "j",
                        "tick_seconds": 1},
                 venue={"fill_delay_ticks": 1})
    make_cfg(arena={"kind": "live", "name": "x", "journal": str(tmp_path), "csv": None,
                    "tick_seconds": 1, "regimes": None},
             venue={"fill_delay_ticks": 1})


def test_binance_fixture_parses_and_replays(tmp_path):
    """The committed CI fixture: a few days of real BTCUSDT 1m (spec v2 5.1)."""
    from colony.arenas.replay import Replay
    fixture = Path(__file__).resolve().parent.parent / "data" / "btcusdt_1m_fixture.csv"
    arena = Replay({"name": "btc", "csv": str(fixture), "lot_denominator": 100_000})
    assert arena.ticks_total() > 4_000
    arena.step(None)
    assert arena.utc() % 60 == 0  # minute bars on minute boundaries
    assert arena.price() > 0
