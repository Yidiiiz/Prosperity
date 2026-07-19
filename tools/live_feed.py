"""Live feed: the network side of live mode, and nothing else. A dumb
appender — it connects, writes Date,Close rows to the journal, flushes per
row, and reconnects forever with exponential backoff (1s doubling to 60s).
It never reads the colony and never decides anything (spec v2 5.2).

Modes:
  --mode ws    Binance public @kline_1s websocket: the close of each
               1-second candle -> exactly one row per second while the
               stream is alive. Stdlib-only RFC 6455 client, no libraries.
  --mode poll  REST polling fallback (Yahoo Finance quote, v1 behavior).

Output (one required):
  -o FILE        single-file journal (v1 compatible)
  --journal DIR  daily segments DIR/YYYY-MM-DD.csv on UTC boundaries; a
                 closed segment gets DIR/YYYY-MM-DD.sha256 (spec v2 5.3)

The journal is append-only and doubles as the permanent tape of the
session — replaying it through the replay arena reproduces the live run
byte-identically (#31).
"""

import argparse
import base64
import datetime
import hashlib
import json
import os
import socket
import ssl
import sys
import time
import urllib.request

BINANCE_WS_HOST = "data-stream.binance.vision"  # public market data, no key
YAHOO_QUOTE_URL = ("https://query1.finance.yahoo.com/v8/finance/chart/"
                   "{symbol}?range=1d&interval=1m")
WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
BACKOFF_START, BACKOFF_CAP = 1.0, 60.0
UTC = datetime.timezone.utc


# ------------------------------------------------------------------ journal

class Journal:
    """Append-only Date,Close writer: single file, or daily UTC segments
    with a sha256 digest written when a segment closes."""

    def __init__(self, single_file=None, directory=None):
        if bool(single_file) == bool(directory):
            raise ValueError("exactly one of -o FILE / --journal DIR")
        self.single_file = single_file
        self.directory = directory
        self._fh = None
        self._date = None
        self._path = None
        if single_file:
            os.makedirs(os.path.dirname(single_file) or ".", exist_ok=True)
        else:
            os.makedirs(directory, exist_ok=True)

    def _open(self, path):
        new = not os.path.exists(path) or os.path.getsize(path) == 0
        self._fh = open(path, "a", newline="", encoding="utf-8")
        self._path = path
        if new:
            self._fh.write("Date,Close\n")
            self._fh.flush()

    def _seal(self):
        """Close the current segment and write its .sha256 (spec v2 5.3)."""
        if self._fh is None:
            return
        self._fh.close()
        self._fh = None
        if self.directory and self._path:
            digest = hashlib.sha256(
                open(self._path, "rb").read()
            ).hexdigest()
            with open(self._path[: -len(".csv")] + ".sha256", "w", encoding="utf-8") as f:
                f.write(digest + "\n")

    def append(self, dt, close):
        if self.single_file:
            if self._fh is None:
                self._open(self.single_file)
        else:
            day = dt.date()
            if day != self._date:
                self._seal()
                self._date = day
                self._open(os.path.join(self.directory, f"{day.isoformat()}.csv"))
        stamp = dt.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "")
        self._fh.write(f"{stamp},{close}\n")
        self._fh.flush()
        return self._path

    def close(self):
        # a mid-day stop does NOT seal: the segment is still today's tape and
        # will be appended to on restart; sealing happens on rotation only
        if self._fh is not None:
            self._fh.close()
            self._fh = None


# ------------------------------------------------------- websocket (stdlib)

def encode_frame(opcode, payload=b""):
    """One client->server frame (always masked, per RFC 6455)."""
    mask = os.urandom(4)
    masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    n = len(payload)
    header = bytes([0x80 | opcode])
    if n < 126:
        header += bytes([0x80 | n])
    elif n < 65_536:
        header += bytes([0x80 | 126]) + n.to_bytes(2, "big")
    else:
        header += bytes([0x80 | 127]) + n.to_bytes(8, "big")
    return header + mask + masked


def decode_frame(buf):
    """Parse one server frame from buf. Returns (fin, opcode, payload, rest)
    or None while incomplete. Server frames are unmasked."""
    if len(buf) < 2:
        return None
    fin = bool(buf[0] & 0x80)
    opcode = buf[0] & 0x0F
    length = buf[1] & 0x7F
    offset = 2
    if length == 126:
        if len(buf) < 4:
            return None
        length = int.from_bytes(buf[2:4], "big")
        offset = 4
    elif length == 127:
        if len(buf) < 10:
            return None
        length = int.from_bytes(buf[2:10], "big")
        offset = 10
    if buf[1] & 0x80:  # masked server frame: tolerate, though non-standard
        if len(buf) < offset + 4 + length:
            return None
        mask = buf[offset: offset + 4]
        raw = buf[offset + 4: offset + 4 + length]
        payload = bytes(b ^ mask[i % 4] for i, b in enumerate(raw))
        rest = buf[offset + 4 + length:]
    else:
        if len(buf) < offset + length:
            return None
        payload = buf[offset: offset + length]
        rest = buf[offset + length:]
    return fin, opcode, payload, rest


def ws_messages(host, path, timeout=30):
    """Generator of text messages from one websocket connection. Answers
    pings; returns on close frame; raises OSError family on trouble."""
    raw = socket.create_connection((host, 443), timeout=timeout)
    sock = ssl.create_default_context().wrap_socket(raw, server_hostname=host)
    key = base64.b64encode(os.urandom(16)).decode()
    sock.sendall(
        (f"GET {path} HTTP/1.1\r\nHost: {host}\r\nUpgrade: websocket\r\n"
         f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\n"
         f"Sec-WebSocket-Version: 13\r\n\r\n").encode()
    )
    buf = b""
    while b"\r\n\r\n" not in buf:
        chunk = sock.recv(4096)
        if not chunk:
            raise ConnectionError("EOF during websocket handshake")
        buf = buf + chunk
    head, buf = buf.split(b"\r\n\r\n", 1)
    if b" 101 " not in head.split(b"\r\n")[0]:
        raise ConnectionError(f"handshake refused: {head.split(b'\r\n')[0]!r}")
    accept = base64.b64encode(
        hashlib.sha1((key + WS_GUID).encode()).digest()
    ).decode()
    if accept.encode() not in head:
        raise ConnectionError("bad Sec-WebSocket-Accept")
    try:
        fragments = []
        while True:
            frame = decode_frame(buf)
            if frame is None:
                chunk = sock.recv(65_536)
                if not chunk:
                    raise ConnectionError("websocket EOF")
                buf = buf + chunk
                continue
            fin, opcode, payload, buf = frame
            if opcode == 0x9:  # ping
                sock.sendall(encode_frame(0xA, payload))
            elif opcode == 0x8:  # close
                return
            elif opcode in (0x1, 0x0):  # text / continuation
                fragments.append(payload)
                if fin:
                    yield b"".join(fragments).decode("utf-8")
                    fragments = []
    finally:
        sock.close()


# ----------------------------------------------------------------- sources

def run_ws(symbol, journal, max_rows, host):
    """Binance @kline_1s: one closed candle per second -> one row per second."""
    path = f"/ws/{symbol.lower()}@kline_1s"
    rows = 0
    backoff = BACKOFF_START
    while max_rows is None or rows < max_rows:
        try:
            for msg in ws_messages(host, path):
                k = json.loads(msg).get("k")
                if not k or not k.get("x"):  # only the CLOSE of each candle
                    continue
                dt = datetime.datetime.fromtimestamp(k["t"] / 1000, tz=UTC)
                journal.append(dt, float(k["c"]))
                rows += 1
                backoff = BACKOFF_START
                if rows == 1 or rows % 60 == 0:
                    print(f"{dt.isoformat()}  {symbol} {float(k['c']):,.2f}  ({rows} rows)",
                          flush=True)
                if max_rows is not None and rows >= max_rows:
                    break
        except (OSError, ConnectionError, ValueError) as exc:
            print(f"ws error: {exc}; reconnecting in {backoff:.0f}s", file=sys.stderr,
                  flush=True)
            time.sleep(backoff)
            backoff = min(BACKOFF_CAP, backoff * 2)
    return rows


def quote(symbol):
    url = YAHOO_QUOTE_URL.format(symbol=symbol.upper())
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.load(resp)
    return float(data["chart"]["result"][0]["meta"]["regularMarketPrice"])


def run_poll(symbol, journal, max_rows, interval):
    rows = 0
    backoff = BACKOFF_START
    while max_rows is None or rows < max_rows:
        try:
            price = quote(symbol)
        except Exception as exc:  # transient network hiccups: back off, retry
            print(f"poll failed: {exc}; retrying in {backoff:.0f}s", file=sys.stderr,
                  flush=True)
            time.sleep(backoff)
            backoff = min(BACKOFF_CAP, backoff * 2)
            continue
        backoff = BACKOFF_START
        dt = datetime.datetime.now(UTC)
        journal.append(dt, round(price, 4))
        rows += 1
        if rows == 1 or rows % 12 == 0:
            print(f"{dt.isoformat()}  {symbol} {price:,.2f}  ({rows} rows)", flush=True)
        time.sleep(interval)
    return rows


def main(argv=None):
    parser = argparse.ArgumentParser(description="append live prices to a journal")
    parser.add_argument("symbol", help="ws mode: BTCUSDT; poll mode: BTC-USD, SPY")
    parser.add_argument("-o", "--out", default=None, help="single-file journal CSV")
    parser.add_argument("--journal", default=None, help="journal DIRECTORY (daily segments)")
    parser.add_argument("--mode", choices=("ws", "poll"), default="ws")
    parser.add_argument("--interval", type=float, default=5.0, help="poll seconds")
    parser.add_argument("--max-rows", type=int, default=None, help="stop after N rows")
    parser.add_argument("--ws-host", default=BINANCE_WS_HOST)
    args = parser.parse_args(argv)

    journal = Journal(single_file=args.out, directory=args.journal)
    try:
        if args.mode == "ws":
            rows = run_ws(args.symbol, journal, args.max_rows, args.ws_host)
        else:
            rows = run_poll(args.symbol, journal, args.max_rows, args.interval)
    except KeyboardInterrupt:
        rows = -1
    finally:
        journal.close()
    print("feed stopped", flush=True)
    return 0 if rows != 0 else 1


if __name__ == "__main__":
    sys.exit(main())
