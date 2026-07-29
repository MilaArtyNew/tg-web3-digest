import json
import os
import re
import sqlite3
from pathlib import Path
from datetime import datetime, timedelta, timezone

from telethon import TelegramClient
from telethon.sessions import StringSession

API_ID = int(os.environ["TG_API_ID"])
API_HASH = os.environ["TG_API_HASH"]
_session_str = os.environ.get("TG_SESSION_STRING") or os.environ.get("TG_SESSION_SENDER")
SESSION = StringSession(_session_str) if _session_str and len(_session_str) > 50 else (
    _session_str or os.environ.get("TG_SESSION", "reader_session")
)

DB_PATH = os.environ.get("DB_PATH", "tg_digest.sqlite3")
TG_TARGET = os.environ["TG_TARGET"]

MAX_ITEMS = int(os.environ.get("MAX_ITEMS", "12"))
MAX_CHARS_PER_ITEM = int(os.environ.get("MAX_CHARS_PER_ITEM", "220"))
MAX_ITEMS_PER_BLOCK = int(os.environ.get("MAX_ITEMS_PER_BLOCK", "6"))
SOURCES_CONFIG = os.environ.get(
    "SOURCES_CONFIG",
    str(Path(__file__).with_name("sources.json")),
)

LINK_RE = re.compile(r"https?://\S+", re.IGNORECASE)

# Very light "noise" patterns (optional; keep conservative)
NOISE_PATTERNS = [
    r"\bjoin\s+now\b",
    r"\bgiveaway\b",
    r"\breferral\b",
    r"\binvite\b",
    r"\bclaim\s+now\b",
]


def open_db():
    con = sqlite3.connect(DB_PATH, timeout=30)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA busy_timeout=30000")
    return con


def init_state(con):
    cur = con.cursor()
    cur.execute(
        """
    CREATE TABLE IF NOT EXISTS state (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )
    """
    )
    con.commit()


def get_state(con, key, default=""):
    cur = con.cursor()
    cur.execute("SELECT value FROM state WHERE key = ?", (key,))
    row = cur.fetchone()
    return row[0] if row else default


def set_state(con, key, value):
    cur = con.cursor()
    cur.execute(
        "INSERT INTO state(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )
    con.commit()


def parse_dt(s):
    return datetime.fromisoformat(s)


def now_utc():
    return datetime.now(timezone.utc)


def clean_text(t):
    t = t.strip()
    t = re.sub(r"\s+", " ", t)
    return t


def normalize_channel_name(channel):
    """Normalize Telegram channel labels to compare saved DB labels with sources.json."""
    ch = (channel or "").strip()
    ch = re.sub(r"^https?://t\.me/", "", ch, flags=re.IGNORECASE)
    ch = ch.lstrip("@")
    return ch.lower()


def load_source_groups():
    """Return normalized sets for smart/core source grouping.

    sources.json shape:
    {
      "smart": ["channel1", "@channel2"],
      "core": ["channel3"]
    }
    Missing/invalid config is non-fatal: everything goes to Other.
    """
    groups = {"smart": set(), "core": set()}
    try:
        with open(SOURCES_CONFIG, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except FileNotFoundError:
        return groups
    except Exception:
        return groups

    for group_name in groups:
        for channel in raw.get(group_name, []):
            normalized = normalize_channel_name(str(channel))
            if normalized:
                groups[group_name].add(normalized)
    return groups


def source_group(channel, groups):
    normalized = normalize_channel_name(channel)
    if normalized in groups.get("smart", set()):
        return "smart"
    if normalized in groups.get("core", set()):
        return "core"
    return "other"


def is_noise(t):
    low = t.lower()
    if len(low) < 20:
        return True
    for p in NOISE_PATTERNS:
        if re.search(p, low):
            return True
    return False


def score_signal(t):
    low = t.lower()
    score = 0
    if any(
        k in low
        for k in [
            "deadline",
            "ends",
            "until",
            "snapshot",
            "listing",
            "upgrade",
            "maintenance",
        ]
    ):
        score += 3
    if any(
        k in low
        for k in [
            "polymarket",
            "odds",
            "spread",
            "funding",
            "arb",
            "arbitrage",
            "yield",
            "vault",
        ]
    ):
        score += 3
    if LINK_RE.search(t):
        score += 2
    if re.search(r"\b\d+(\.\d+)?\b", low) or "%" in low:
        score += 2
    if any(k in low for k in ["launch", "released", "live", "new task", "quest", "check-in"]):
        score += 1
    return score


def dedupe(items):
    seen = set()
    out = []
    for it in items:
        key = it["text"].lower()
        key = re.sub(r"https?://\S+", "", key)
        key = re.sub(r"\W+", " ", key).strip()
        if key in seen:
            continue
        seen.add(key)
        out.append(it)
    return out


def format_item(it):
    text = it["text"]
    link_match = LINK_RE.search(it["text"])
    if len(text) > MAX_CHARS_PER_ITEM:
        text = text[: MAX_CHARS_PER_ITEM - 1] + "…"
    ch = it["channel"]
    link = ""
    if link_match and link_match.group(0) not in text:
        link = f" {link_match.group(0)}"
    return f"- {text}{link} ({ch})"


def build_digest(rows, start_utc, end_utc):
    source_groups = load_source_groups()
    items = []
    for channel, msg_id, date_utc, text in rows:
        t = clean_text(text)
        if not t or is_noise(t):
            continue
        items.append(
            {
                "channel": channel,
                "msg_id": msg_id,
                "date": parse_dt(date_utc),
                "text": t,
                "score": score_signal(t),
                "group": source_group(channel, source_groups),
            }
        )

    items = dedupe(items)
    grouped = {"smart": [], "core": [], "other": []}
    for item in items:
        grouped[item["group"]].append(item)

    for group_items in grouped.values():
        group_items.sort(key=lambda x: (x["score"], x["date"]), reverse=True)

    header = (
        f"Web3 Digest "
        f"({start_utc.astimezone(timezone.utc).strftime('%H:%M')}–"
        f"{end_utc.astimezone(timezone.utc).strftime('%H:%M')} UTC)"
    )

    if not any(grouped.values()):
        return header + "\n\nНет новых сигналов."

    block_specs = [
        ("smart", "🧠 Smart"),
        ("core", "💎 Core"),
        ("other", "🗂 Other"),
    ]
    blocks = []
    for group_key, title in block_specs:
        block_items = grouped[group_key][:MAX_ITEMS_PER_BLOCK]
        if not block_items:
            continue
        body = "\n".join(format_item(it) for it in block_items)
        blocks.append(f"{title}\n{body}")

    return header + "\n\n" + "\n\n".join(blocks)


async def resolve_target_by_name(client, target_name):
    # Prefer exact name match among dialogs
    async for dialog in client.iter_dialogs():
        if dialog.name == target_name:
            return dialog.entity
    # Fallback: try entity resolution (username/peer id)
    return await client.get_entity(target_name)


async def run():
    con = open_db()
    init_state(con)

    end = now_utc()
    last_sent_iso = get_state(con, "digest:last_sent_utc", "")

    if last_sent_iso:
        start = parse_dt(last_sent_iso)
        if start > end:
            start = end - timedelta(hours=6)
        if end - start > timedelta(days=2):
            start = end - timedelta(hours=12)
    else:
        start = end - timedelta(hours=6)

    cur = con.cursor()
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

    digest = build_digest(rows, start, end)

    client = TelegramClient(SESSION, API_ID, API_HASH)
    await client.start()

    target_entity = await resolve_target_by_name(client, TG_TARGET)
    await client.send_message(target_entity, digest)

    set_state(con, "digest:last_sent_utc", end.isoformat())
    con.close()
    await client.disconnect()


if __name__ == "__main__":
    import asyncio

    asyncio.run(run())
