"""Daily backup of all active loans, sent as a CSV file to a Telegram user via the bot.

    python backup.py --daemon   run forever, sending every day at BACKUP_TIME (BACKUP_TZ)
    python backup.py --now      send one backup immediately
    python backup.py --stdout   print the CSV instead of sending it

Config (env): DATABASE_URL, TELEGRAM_BOT_TOKEN, and the recipient as either BACKUP_CHAT_ID or
BACKUP_TELEGRAM_USERNAME (who must have logged in to the app once, so we know their chat id).
Optional: BACKUP_TIME (default 09:00), BACKUP_TZ (default Europe/Kyiv).
"""

import argparse
import csv
import io
import json
import logging
import os
import ssl
import sys
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import psycopg
from psycopg.rows import dict_row

try:
    import certifi
    SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    SSL_CONTEXT = ssl.create_default_context()

log = logging.getLogger("backup")

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://ledger:ledger@localhost:5433/ledger")
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TZ = ZoneInfo(os.environ.get("BACKUP_TZ", "Europe/Kyiv"))
RETRY_DELAYS = [60, 300, 900, 3600]  # seconds between attempts when sending fails

COLUMNS = [
    "loan_id", "batch_id", "lent_at", "lender_username", "lender_name", "borrower_username", "borrower_name",
    "card_name", "set_code", "set_name", "collector_number", "finish", "quantity", "scryfall_id",
]

ACTIVE_LOANS = """
    SELECT l.id AS loan_id, l.batch_id, l.created_at AS lent_at,
           lu.username AS lender_username, concat_ws(' ', lu.first_name, lu.last_name) AS lender_name,
           bu.username AS borrower_username, concat_ws(' ', bu.first_name, bu.last_name) AS borrower_name,
           c.name AS card_name, upper(c.set_code) AS set_code, c.set_name, c.collector_number, c.finish,
           l.quantity, c.external_id AS scryfall_id
    FROM loans l
    JOIN cards c ON c.id = l.card_id
    JOIN users lu ON lu.id = l.lender_id
    JOIN users bu ON bu.id = l.borrower_id
    WHERE l.status = 'borrowed'
    ORDER BY l.created_at, l.id
"""


def safe_cell(value):
    # Keep spreadsheets from running user-provided text as a formula.
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.astimezone(TZ).isoformat(timespec="seconds")
    text = str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


def build_csv(rows):
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(COLUMNS)
    for row in rows:
        writer.writerow([safe_cell(row[col]) for col in COLUMNS])
    # Without the BOM, Excel reads it as cp1251/latin-1 and garbles Cyrillic names.
    return out.getvalue().encode("utf-8-sig")


def resolve_chat_id(db):
    chat_id = os.environ.get("BACKUP_CHAT_ID")
    if chat_id:
        return int(chat_id)
    username = os.environ.get("BACKUP_TELEGRAM_USERNAME", "").lstrip("@")
    if not username:
        return None
    row = db.execute(
        "SELECT telegram_id FROM users WHERE lower(username) = lower(%s) AND telegram_id IS NOT NULL", (username,)
    ).fetchone()
    if row is None:
        raise RuntimeError(f"@{username} has never logged in to the app, so their Telegram chat id is unknown")
    return row["telegram_id"]


def multipart(fields, files):
    boundary = uuid.uuid4().hex
    parts = []
    for name, value in fields.items():
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())
    for name, (filename, content, mime) in files.items():
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'
                     f"Content-Type: {mime}\r\n\r\n".encode() + content + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def send_document(chat_id, filename, content, caption):
    body, content_type = multipart(
        {"chat_id": chat_id, "caption": caption},
        {"document": (filename, content, "text/csv")},
    )
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendDocument", data=body, method="POST",
        headers={"Content-Type": content_type},
    )
    try:
        with urllib.request.urlopen(req, timeout=30, context=SSL_CONTEXT) as resp:
            result = json.load(resp)
    except urllib.error.HTTPError as e:
        # Telegram explains failures (e.g. "bot was blocked by the user") in the JSON body.
        raise RuntimeError(f"Telegram API error {e.code}: {e.read().decode(errors='replace')[:300]}") from None
    if not result.get("ok"):
        raise RuntimeError(f"Telegram API error: {result}")


def run_backup():
    if not BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set")
    with psycopg.connect(DATABASE_URL, row_factory=dict_row) as db:
        chat_id = resolve_chat_id(db)
        if chat_id is None:
            raise RuntimeError("No recipient: set BACKUP_TELEGRAM_USERNAME or BACKUP_CHAT_ID")
        rows = db.execute(ACTIVE_LOANS).fetchall()
    today = datetime.now(TZ).date().isoformat()
    cards = sum(r["quantity"] for r in rows)
    caption = f"Card Ledger backup · {today}\n{len(rows)} active loan rows · {cards} cards out"
    send_document(chat_id, f"card-ledger-active-{today}.csv", build_csv(rows), caption)
    return f"sent {len(rows)} rows ({cards} cards) to chat {chat_id}"


def next_run(now, at):
    """The next moment matching the HH:MM in `at`, strictly after `now` (both in TZ)."""
    hour, minute = map(int, at.split(":"))
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return target if target > now else target + timedelta(days=1)


def backup_with_retries():
    for attempt, delay in enumerate([0, *RETRY_DELAYS], start=1):
        if delay:
            log.info("retrying in %ss", delay)
            time.sleep(delay)
        try:
            log.info("backup %s", run_backup())
            return True
        except Exception:
            log.exception("backup attempt %d failed", attempt)
    return False


def daemon():
    if not (os.environ.get("BACKUP_CHAT_ID") or os.environ.get("BACKUP_TELEGRAM_USERNAME")):
        log.warning("no backup recipient configured; daily backups are disabled")
        while True:
            time.sleep(86400)
    at = os.environ.get("BACKUP_TIME", "09:00")
    while True:
        target = next_run(datetime.now(TZ), at)
        log.info("next backup at %s", target.isoformat())
        # Short naps, so a suspended host or a clock change doesn't push the backup back by hours.
        while (remaining := (target - datetime.now(TZ)).total_seconds()) > 0:
            time.sleep(min(remaining, 300))
        backup_with_retries()


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stderr)
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--daemon", action="store_true")
    mode.add_argument("--now", action="store_true")
    mode.add_argument("--stdout", action="store_true")
    args = parser.parse_args()
    if args.daemon:
        daemon()
    elif args.now:
        print(run_backup())
    else:
        with psycopg.connect(DATABASE_URL, row_factory=dict_row) as db:
            sys.stdout.buffer.write(build_csv(db.execute(ACTIVE_LOANS).fetchall()))


if __name__ == "__main__":
    main()
