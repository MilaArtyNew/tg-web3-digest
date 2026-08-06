"""
Combined entry point for Railway:
- Collector: every hour at :30
- Sender: 08:00, 11:00, 14:00, 17:00 Asia/Jerusalem
- Exporter: daily at 01:00 UTC — SQLite → /data/sources/*.md
- API server: HTTP on $PORT — serves /data/sources/ for tg-notebooklm
"""
import asyncio
import logging
import os
import sqlite3
import threading
from datetime import datetime, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
import pytz

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

SEND_HOURS = [int(h) for h in os.environ.get("SEND_HOURS", "8,11,14,17").split(",")]
TZ = pytz.timezone(os.environ.get("TZ_DIGEST", "Asia/Jerusalem"))
DB_PATH = os.environ.get("DB_PATH", "/data/tg_digest.sqlite3")
# Legacy raw sender is disabled by default; Hermes LLM digest crons deliver
# synthesized digests. Set RAW_DIGEST_ENABLED=true only for temporary fallback.
RAW_DIGEST_ENABLED = os.environ.get("RAW_DIGEST_ENABLED", "false").lower() in {"1", "true", "yes", "on"}


def state_set(key, value):
    try:
        con = sqlite3.connect(DB_PATH, timeout=30)
        con.execute(
            "CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        con.execute(
            "INSERT INTO state(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        con.commit()
        con.close()
    except Exception:
        log.exception("Failed to write state %s", key)


def utc_now_iso():
    return datetime.now(timezone.utc).isoformat()


async def run_collector():
    from tg_digest_collector import run
    await run()


async def run_sender():
    from tg_digest_sender import run
    await run()


async def collector_job():
    state_set("collector:last_started_utc", utc_now_iso())
    try:
        log.info("Collector: starting poll")
        await run_collector()
        state_set("collector:last_ok_utc", utc_now_iso())
        state_set("collector:last_error", "")
        log.info("Collector: poll done")
    except Exception as e:
        state_set("collector:last_error_utc", utc_now_iso())
        state_set("collector:last_error", repr(e)[:1000])
        log.error("Collector error: %r", e)


async def sender_job():
    try:
        log.info("Sender: starting digest")
        await run_sender()
        log.info("Sender: done")
    except Exception as e:
        log.error("Sender error: %r", e)


async def exporter_job():
    try:
        log.info("Exporter: starting")
        from tg_digest_exporter import run
        run()
        log.info("Exporter: done")
    except Exception as e:
        log.error("Exporter error: %r", e)


async def main():
    # HTTP API server in background thread (serves /data/sources/ for tg-notebooklm)
    from tg_digest_api import start_server, register_collect_callback, register_send_callback
    loop = asyncio.get_event_loop()
    register_send_callback(loop, sender_job)
    register_collect_callback(loop, collector_job)
    threading.Thread(target=start_server, daemon=True).start()

    scheduler = AsyncIOScheduler(timezone=TZ)

    scheduler.add_job(collector_job, "cron", minute=30, misfire_grace_time=600)
    log.info("Collector scheduled every hour at :30")

    if RAW_DIGEST_ENABLED:
        for hour in SEND_HOURS:
            scheduler.add_job(sender_job, "cron", hour=hour, minute=0, misfire_grace_time=600)
            log.info("Raw sender scheduled at %02d:00 %s", hour, TZ.zone)
    else:
        log.info("Raw sender disabled; Hermes LLM digest crons are expected to deliver summaries")

    scheduler.add_job(exporter_job, "cron", hour=1, minute=0,
                      timezone=pytz.utc, misfire_grace_time=1800)
    log.info("Exporter scheduled daily at 01:00 UTC")

    scheduler.start()

    asyncio.create_task(collector_job())
    asyncio.create_task(exporter_job())  # export on startup too

    while True:
        await asyncio.sleep(3600)


if __name__ == "__main__":
    asyncio.run(main())
