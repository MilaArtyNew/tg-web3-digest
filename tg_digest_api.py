"""
Minimal HTTP server serving /data/sources/ markdown files.
Protected by API_SECRET env var.
"""
import asyncio
import json
import logging
import os
import re
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

log = logging.getLogger(__name__)

SOURCES_DIR = os.environ.get("SOURCES_DIR", "/data/sources")
DB_PATH = os.environ.get("DB_PATH", "/data/tg_digest.sqlite3")
API_SECRET = os.environ.get("API_SECRET", "")
PORT = int(os.environ.get("PORT", "8080"))

NOISE_PATTERNS = [
    r"\bjoin\s+now\b",
    r"\bgiveaway\b",
    r"\breferral\b",
    r"\binvite\b",
    r"\bclaim\s+now\b",
]


def is_noise(text: str) -> bool:
    low = (text or "").strip().lower()
    if len(low) < 20:
        return True
    return any(re.search(pattern, low) for pattern in NOISE_PATTERNS)


def iso_or_default(raw: str | None, default: datetime) -> datetime:
    if not raw:
        return default
    value = raw.replace("Z", "+00:00")
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def json_response(handler: BaseHTTPRequestHandler, payload, status=200):
    body = json.dumps(payload, ensure_ascii=False).encode()
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", len(body))
    handler.end_headers()
    handler.wfile.write(body)


def build_messages_debug(query):
    now = datetime.now(timezone.utc)
    end = iso_or_default(query.get("end", [None])[0], now)
    start = iso_or_default(query.get("start", [None])[0], end - timedelta(hours=3))

    info = {
        "db_path": DB_PATH,
        "db_exists": os.path.exists(DB_PATH),
        "start_utc": start.isoformat(),
        "end_utc": end.isoformat(),
    }
    if not info["db_exists"]:
        return {**info, "error": "db_missing"}

    con = sqlite3.connect(DB_PATH, timeout=30)
    cur = con.cursor()
    cur.execute("SELECT COUNT(*), MIN(date_utc), MAX(date_utc) FROM messages")
    total_count, min_date, max_date = cur.fetchone()
    cur.execute("SELECT key, value FROM state WHERE key LIKE 'collector:%' OR key LIKE 'digest:%' ORDER BY key")
    state = dict(cur.fetchall())
    cur.execute(
        """
        SELECT channel, msg_id, date_utc, text
        FROM messages
        WHERE date_utc > ? AND date_utc <= ?
        ORDER BY date_utc ASC
        """,
        (start.isoformat(), end.isoformat()),
    )
    rows = cur.fetchall()
    con.close()

    after_noise = [row for row in rows if not is_noise(row[3])]
    by_channel = {}
    for channel, _msg_id, _date_utc, text in rows:
        stats = by_channel.setdefault(channel, {"raw": 0, "after_noise": 0})
        stats["raw"] += 1
        if not is_noise(text):
            stats["after_noise"] += 1

    return {
        **info,
        "total_messages": total_count,
        "first_message_utc": min_date,
        "last_message_utc": max_date,
        "state": state,
        "window_raw_rows": len(rows),
        "window_after_noise_filter": len(after_noise),
        "window_channels": len(by_channel),
        "top_channels": sorted(
            [{"channel": ch, **stats} for ch, stats in by_channel.items()],
            key=lambda x: (x["raw"], x["after_noise"]),
            reverse=True,
        )[:20],
    }

_main_loop = None
_send_callback = None
_collect_callback = None


def register_send_callback(loop, callback):
    global _main_loop, _send_callback
    _main_loop = loop
    _send_callback = callback


def register_collect_callback(loop, callback):
    global _main_loop, _collect_callback
    _main_loop = loop
    _collect_callback = callback


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass  # suppress default access log

    def _check_auth(self) -> bool:
        if not API_SECRET:
            return True
        return self.headers.get("X-API-Key") == API_SECRET

    def do_GET(self):
        if not self._check_auth():
            self.send_response(401)
            self.end_headers()
            return

        parsed = urlparse(self.path)
        path = parsed.path.lstrip("/")
        query = parse_qs(parsed.query)

        # GET /debug/messages?start=<iso>&end=<iso> → SQLite aggregate counters (no message text)
        if path == "debug/messages":
            try:
                json_response(self, build_messages_debug(query))
            except Exception as e:
                log.exception("Debug messages error")
                json_response(self, {"error": repr(e)}, status=500)
            return

        # GET /list → JSON array of available .md filenames
        if path == "list":
            src = Path(SOURCES_DIR)
            files = sorted(f.name for f in src.glob("*.md")) if src.exists() else []
            body = json.dumps(files).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", len(body))
            self.end_headers()
            self.wfile.write(body)
            return

        # GET /sources/<filename.md> → file content
        if path.startswith("sources/") and path.endswith(".md"):
            filename = os.path.basename(path)
            filepath = os.path.join(SOURCES_DIR, filename)
            if os.path.exists(filepath):
                with open(filepath, "rb") as f:
                    body = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "text/markdown; charset=utf-8")
                self.send_header("Content-Length", len(body))
                self.end_headers()
                self.wfile.write(body)
                return

        self.send_response(404)
        self.end_headers()

    def do_POST(self):
        if not self._check_auth():
            self.send_response(401)
            self.end_headers()
            return

        path = self.path.lstrip("/")

        # POST /send → trigger digest sender immediately
        if path == "send" and _main_loop and _send_callback:
            asyncio.run_coroutine_threadsafe(_send_callback(), _main_loop)
            body = b'{"ok": true}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", len(body))
            self.end_headers()
            self.wfile.write(body)
            return

        # POST /collect → trigger collector immediately in its own thread
        if path == "collect" and _collect_callback:
            threading.Thread(target=lambda: asyncio.run(_collect_callback()), daemon=True).start()
            body = b'{"ok": true}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", len(body))
            self.end_headers()
            self.wfile.write(body)
            return

        self.send_response(404)
        self.end_headers()


def start_server():
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    log.info("API server started on port %d", PORT)
    server.serve_forever()
