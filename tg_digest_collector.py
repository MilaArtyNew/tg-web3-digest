import asyncio
import os
import sqlite3
from datetime import timezone

from telethon import TelegramClient
from telethon.errors import FloodWaitError
from telethon.sessions import StringSession

API_ID = int(os.environ["TG_API_ID"])
API_HASH = os.environ["TG_API_HASH"]
_session_str = os.environ.get("TG_SESSION_STRING") or os.environ.get("TG_SESSION_COLLECTOR")
SESSION = StringSession(_session_str) if _session_str and len(_session_str) > 50 else (
    _session_str or os.environ.get("TG_SESSION", "reader_session")
)

CHANNELS = [c.strip() for c in os.environ["TG_CHANNELS"].split(",") if c.strip()]
DB_PATH = os.environ.get("DB_PATH", "tg_digest.sqlite3")
ALL_CHANNELS_MODE = len(CHANNELS) == 1 and CHANNELS[0].lower() in {"all", "*"}
SEED_EMPTY_CHANNELS = os.environ.get("SEED_EMPTY_CHANNELS", "true").lower() in {"1", "true", "yes", "on"}


def open_db():
    con = sqlite3.connect(DB_PATH, timeout=30)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA busy_timeout=30000")
    return con


def init_db():
    con = open_db()
    cur = con.cursor()
    cur.execute(
        """
    CREATE TABLE IF NOT EXISTS messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        channel TEXT NOT NULL,
        msg_id INTEGER NOT NULL,
        date_utc TEXT NOT NULL,
        text TEXT NOT NULL,
        UNIQUE(channel, msg_id)
    )
    """
    )
    cur.execute(
        """
    CREATE TABLE IF NOT EXISTS state (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )
    """
    )
    con.commit()
    con.close()


def get_last_id(con, channel):
    cur = con.cursor()
    cur.execute("SELECT value FROM state WHERE key = ?", (f"last_id:{channel}",))
    row = cur.fetchone()
    return int(row[0]) if row else 0


def set_last_id(con, channel, last_id):
    cur = con.cursor()
    cur.execute(
        "INSERT INTO state(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (f"last_id:{channel}", str(last_id)),
    )
    con.commit()


def save_message(con, channel, msg_id, dt, text):
    cur = con.cursor()
    cur.execute(
        "INSERT OR IGNORE INTO messages(channel, msg_id, date_utc, text) VALUES (?, ?, ?, ?)",
        (channel, msg_id, dt, text),
    )


def utc_iso(dt):
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


async def run():
    """Run a single collection pass and return."""
    init_db()
    client = TelegramClient(SESSION, API_ID, API_HASH)
    await client.start()

    try:
        con = open_db()
        sources = []
        if ALL_CHANNELS_MODE:
            async for dialog in client.iter_dialogs():
                if not dialog.is_channel:
                    continue
                if not getattr(dialog.entity, "broadcast", False):
                    continue
                label = dialog.entity.username or dialog.name or str(dialog.id)
                key = str(dialog.id)
                sources.append((key, label, dialog.entity))
        else:
            for ch in CHANNELS:
                entity = await client.get_entity(ch)
                label = getattr(entity, "username", None) or ch
                key = ch
                sources.append((key, label, entity))

        for channel_key, channel_label, entity in sources:
            last_id = get_last_id(con, channel_key)
            if last_id == 0 and SEED_EMPTY_CHANNELS:
                latest_id = 0
                async for msg in client.iter_messages(entity, limit=1):
                    latest_id = msg.id
                    break
                if latest_id:
                    set_last_id(con, channel_key, latest_id)
                continue

            new_last = last_id
            try:
                async for msg in client.iter_messages(entity, min_id=last_id, reverse=True):
                    if not msg.message:
                        continue
                    text = msg.message.strip()
                    if not text:
                        continue
                    save_message(con, channel_label, msg.id, utc_iso(msg.date), text)
                    if msg.id > new_last:
                        new_last = msg.id
            except FloodWaitError as e:
                await asyncio.sleep(int(e.seconds) + 5)
            if new_last != last_id:
                set_last_id(con, channel_key, new_last)

        con.commit()
        con.close()
    finally:
        await client.disconnect()


if __name__ == "__main__":
    asyncio.run(run())
