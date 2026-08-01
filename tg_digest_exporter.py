"""
Daily exporter: SQLite → per-day markdown files in /data/sources/
Called from main.py scheduler, daily at 01:00.
"""
import logging
import os
import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path

log = logging.getLogger(__name__)

DB_PATH = os.environ.get("DB_PATH", "/data/tg_digest.sqlite3")
SOURCES_DIR = os.environ.get("SOURCES_DIR", "/data/sources")
MAX_PER_FILE = 500

# Retention policy: keep a rolling 10-day window. When a new day is exported,
# prune the oldest day first, then write/refresh the current day. This avoids a
# transient 11th day on Railway's small Volume.
RETENTION_DAYS = int(os.environ.get("RETENTION_DAYS", "10"))

# Legacy env compatibility: Railway may still have EXPORT_DAYS=30 or an old
# EXPORT_DAYS_HARD_CAP=8 from the incident recovery. The effective retention is
# controlled by RETENTION_DAYS so stale env cannot silently lower or raise the
# rolling window Liudmila requested.
_LEGACY_EXPORT_DAYS = os.environ.get("EXPORT_DAYS")
_LEGACY_EXPORT_DAYS_HARD_CAP = os.environ.get("EXPORT_DAYS_HARD_CAP")
EXPORT_DAYS = RETENTION_DAYS


def export_date_from_filename(path: Path) -> str:
    return path.name.split("--part", 1)[0].removesuffix(".md")


def prune_old_exports(today=None) -> int:
    today = today or datetime.now(timezone.utc).date()
    keep_dates = {(today - timedelta(days=days_back)).isoformat() for days_back in range(EXPORT_DAYS)}
    src = Path(SOURCES_DIR)
    if not src.exists():
        return 0
    removed = 0
    for path in src.glob("*.md"):
        date_part = export_date_from_filename(path)
        if date_part not in keep_dates:
            try:
                path.unlink()
                removed += 1
            except OSError:
                log.exception("Failed to remove old export %s", path)
    if removed:
        log.info("Pruned %d old exported source files", removed)
    return removed


def prune_old_messages(con, today=None) -> int:
    today = today or datetime.now(timezone.utc).date()
    cutoff = today - timedelta(days=EXPORT_DAYS - 1)
    cutoff_iso = f"{cutoff.isoformat()}T00:00:00+00:00"
    cur = con.execute("DELETE FROM messages WHERE date_utc < ?", (cutoff_iso,))
    con.commit()
    removed = cur.rowcount if cur.rowcount is not None else 0
    if removed:
        log.info("Pruned %d old SQLite messages before %s", removed, cutoff_iso)
    return removed


def remove_existing_export_files(date_str: str) -> int:
    """Remove stale parts for a day before writing a fresh export for that day."""
    src = Path(SOURCES_DIR)
    if not src.exists():
        return 0
    removed = 0
    for path in src.glob(f"{date_str}*.md"):
        if export_date_from_filename(path) != date_str:
            continue
        path.unlink()
        removed += 1
    return removed


def export_day(con, date_str: str) -> int:
    rows = con.execute(
        "SELECT channel, date_utc, text FROM messages "
        "WHERE date_utc >= ? AND date_utc < ? ORDER BY channel, date_utc",
        (f"{date_str}T00:00:00+00:00", f"{date_str}T23:59:59+00:00"),
    ).fetchall()
    if not rows:
        remove_existing_export_files(date_str)
        return 0

    remove_existing_export_files(date_str)
    parts = [rows[i: i + MAX_PER_FILE] for i in range(0, len(rows), MAX_PER_FILE)]
    for part_idx, part in enumerate(parts):
        suffix = f"--part{part_idx + 1}" if len(parts) > 1 else ""
        filepath = os.path.join(SOURCES_DIR, f"{date_str}{suffix}.md")

        channels: dict = {}
        for channel, date_utc, text in part:
            channels.setdefault(channel, []).append((date_utc, text))

        lines = [f"# Telegram digest — {date_str}{suffix}", ""]
        for channel, msgs in sorted(channels.items()):
            lines.append(f"## Channel: {channel}")
            for date_utc, text in msgs:
                try:
                    time_str = datetime.fromisoformat(date_utc).strftime("%H:%M UTC")
                except Exception:
                    time_str = date_utc
                lines.append(f"### {time_str}")
                lines.append(text.strip())
                lines.append("")
            lines.append("")

        os.makedirs(SOURCES_DIR, exist_ok=True)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        log.info("Exported %s: %d messages", os.path.basename(filepath), len(part))

    return len(rows)


def run():
    if not os.path.exists(DB_PATH):
        log.warning("DB not found: %s", DB_PATH)
        return
    con = sqlite3.connect(DB_PATH, timeout=30)
    con.execute("PRAGMA journal_mode=WAL")
    today = datetime.now(timezone.utc).date()
    # Rotate first: remove the oldest day/files/rows before writing today's export.
    prune_old_exports(today)
    prune_old_messages(con, today)
    total = 0
    for days_back in range(EXPORT_DAYS):
        date_str = (today - timedelta(days=days_back)).isoformat()
        total += export_day(con, date_str)
    con.close()
    log.info("Export done: %d messages across %d days", total, EXPORT_DAYS)
