import hashlib
import hmac
import json
import os
import re
import ssl
import time
from datetime import datetime, timezone
import urllib.error
import urllib.parse
import urllib.request
import uuid
from functools import wraps
from pathlib import Path

try:
    import certifi
    SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    SSL_CONTEXT = ssl.create_default_context()

import psycopg
from psycopg.rows import dict_row
from flask import Flask, abort, flash, g, redirect, render_template, request, session, url_for
from werkzeug.middleware.proxy_fix import ProxyFix

BASE_DIR = Path(__file__).parent
DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://ledger:ledger@localhost:5433/ledger")
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
BOT_USERNAME = os.environ.get("TELEGRAM_BOT_USERNAME", "")
DEV_LOGIN = os.environ.get("DEV_LOGIN") == "1"
AUTH_MAX_AGE = 24 * 3600  # reject Telegram auth payloads older than this

FINISHES = {"nonfoil": "Non-foil", "foil": "Foil", "etched": "Etched foil"}
USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{3,32}$")
MAX_CARDS_PER_LOAN = 50

app = Flask(__name__)
# Behind a tunnel / reverse proxy: trust X-Forwarded-Proto/Host so external URLs
# (the Telegram widget's auth URL) use the public https address.
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)
app.config.update(
    SECRET_KEY=os.environ.get("SECRET_KEY") or os.urandom(32),
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SECURE=os.environ.get("SESSION_COOKIE_SECURE") == "1",
)


# --- database ---------------------------------------------------------------

def get_db():
    if "db" not in g:
        g.db = psycopg.connect(DATABASE_URL, row_factory=dict_row)
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    with psycopg.connect(DATABASE_URL) as db:
        # Serialize schema setup when several workers start at once.
        db.execute("SELECT pg_advisory_xact_lock(hashtext('tcg-ledger schema'))")
        db.execute((BASE_DIR / "schema.sql").read_text())


# --- auth -------------------------------------------------------------------

def verify_telegram_auth(data):
    """Check a Telegram Login Widget payload, per https://core.telegram.org/widgets/login#checking-authorization"""
    data = dict(data)
    received_hash = data.pop("hash", "")
    if not BOT_TOKEN or not received_hash:
        return False
    check_string = "\n".join(f"{k}={data[k]}" for k in sorted(data))
    secret = hashlib.sha256(BOT_TOKEN.encode()).digest()
    expected = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, received_hash):
        return False
    try:
        return time.time() - int(data.get("auth_date", 0)) <= AUTH_MAX_AGE
    except ValueError:
        return False


def merge_users(db, keep_id, drop_id):
    """Move everything owned by placeholder `drop_id` onto `keep_id`, then delete it."""
    db.execute("UPDATE loans SET lender_id = %s WHERE lender_id = %s", (keep_id, drop_id))
    db.execute("UPDATE loans SET borrower_id = %s WHERE borrower_id = %s", (keep_id, drop_id))
    db.execute("UPDATE loans SET created_by = %s WHERE created_by = %s", (keep_id, drop_id))
    db.execute("DELETE FROM users WHERE id = %s", (drop_id,))


def upsert_telegram_user(tg):
    """Create or update the user for a verified Telegram identity; returns users.id."""
    db = get_db()
    tg_id = int(tg["id"])
    username = tg.get("username") or None
    profile = (tg.get("first_name"), tg.get("last_name"), tg.get("photo_url"))

    user = db.execute("SELECT id FROM users WHERE telegram_id = %s", (tg_id,)).fetchone()
    placeholder = None
    if username:
        placeholder = db.execute(
            "SELECT id FROM users WHERE lower(username) = lower(%s) AND telegram_id IS NULL", (username,)
        ).fetchone()

    if user is None and placeholder is not None:
        # First login of someone who was already recorded by @username: claim it.
        user_id = placeholder["id"]
        db.execute("UPDATE users SET telegram_id = %s WHERE id = %s", (tg_id, user_id))
    elif user is None:
        user_id = db.execute("INSERT INTO users (telegram_id) VALUES (%s) RETURNING id", (tg_id,)).fetchone()["id"]
    else:
        user_id = user["id"]
        if placeholder is not None:
            # Known user changed their @username to one somebody had pre-registered.
            merge_users(db, user_id, placeholder["id"])

    if username:
        # Another real account may still hold this username (they renamed since); release it.
        db.execute("UPDATE users SET username = NULL WHERE lower(username) = lower(%s) AND id <> %s", (username, user_id))
    db.execute(
        "UPDATE users SET username = %s, first_name = %s, last_name = %s, photo_url = %s WHERE id = %s",
        (username, *profile, user_id),
    )
    db.commit()
    return user_id


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if g.user is None:
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapped


@app.before_request
def load_user():
    g.user = None
    if "user_id" in session:
        g.user = get_db().execute("SELECT * FROM users WHERE id = %s", (session["user_id"],)).fetchone()
        if g.user is None:
            session.clear()


@app.route("/login")
def login():
    if g.user is not None:
        return redirect(url_for("index"))
    # Full-page redirect flow; Telegram returns to /login#tgAuthResult=..., which the widget
    # script forwards to /auth/telegram. Works where the embedded iframe can't (e.g. Firefox
    # with third-party cookie isolation).
    redirect_login_url = None
    if BOT_TOKEN:
        origin = request.host_url.rstrip("/")
        redirect_login_url = "https://oauth.telegram.org/auth?" + urllib.parse.urlencode({
            "bot_id": BOT_TOKEN.split(":")[0],
            "origin": origin,
            "request_access": "write",
            "return_to": origin + url_for("login"),
        })
    return render_template("login.html", bot_username=BOT_USERNAME, dev_login=DEV_LOGIN,
                           redirect_login_url=redirect_login_url)


@app.route("/auth/telegram")
def auth_telegram():
    if not verify_telegram_auth(request.args):
        flash("Telegram login could not be verified.", "error")
        return redirect(url_for("login"))
    session.clear()
    session["user_id"] = upsert_telegram_user(request.args)
    session.permanent = True
    return redirect(url_for("index"))


@app.route("/auth/dev", methods=["POST"])
def auth_dev():
    """Local-only login that skips Telegram (enabled with DEV_LOGIN=1)."""
    if not DEV_LOGIN:
        abort(404)
    username = request.form.get("username", "").strip().lstrip("@")
    if not USERNAME_RE.match(username):
        flash("Enter a valid username.", "error")
        return redirect(url_for("login"))
    # Deterministic fake Telegram id so the same name logs into the same account.
    fake_id = -int(hashlib.sha256(username.lower().encode()).hexdigest()[:12], 16)
    session.clear()
    session["user_id"] = upsert_telegram_user({"id": fake_id, "username": username, "first_name": username})
    return redirect(url_for("index"))


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("login"))


# --- scryfall ---------------------------------------------------------------

class ScryfallUnavailable(Exception):
    pass


def scryfall_cards(scryfall_ids):
    """Fetch printings by Scryfall id via the collection endpoint; returns {id: card} for those found."""
    ids = []
    for sid in dict.fromkeys(scryfall_ids):
        try:
            parsed = uuid.UUID(sid)
        except ValueError:
            continue  # not a Scryfall id at all: treat as not found
        if parsed.version == 4:  # Scryfall ids are v4; it rejects other UUIDs with a 400
            ids.append(str(parsed))
    found = {}
    for i in range(0, len(ids), 75):  # API limit per request
        body = json.dumps({"identifiers": [{"id": sid} for sid in ids[i:i + 75]]}).encode()
        req = urllib.request.Request(
            "https://api.scryfall.com/cards/collection", data=body, method="POST",
            headers={"User-Agent": "tcg-ledger/0.1", "Accept": "application/json", "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=15, context=SSL_CONTEXT) as resp:
                found.update((card["id"], card) for card in json.load(resp)["data"])
        except urllib.error.HTTPError as e:
            if e.code != 400:  # 400 = malformed identifiers; leave them unfound
                raise ScryfallUnavailable from e
        except (urllib.error.URLError, TimeoutError, ValueError, KeyError) as e:
            raise ScryfallUnavailable from e
    return found


def card_image(card):
    uris = card.get("image_uris") or (card.get("card_faces") or [{}])[0].get("image_uris") or {}
    return uris.get("normal") or uris.get("small")


def get_or_create_mtg_cards(items):
    """Map [(scryfall_id, finish), ...] to cards.id values, creating missing rows from Scryfall data."""
    db = get_db()
    ids = {}
    for sid, finish in items:
        row = db.execute(
            "SELECT id FROM cards WHERE game = 'mtg' AND external_id = %s AND finish = %s", (sid, finish)
        ).fetchone()
        if row:
            ids[sid, finish] = row["id"]

    missing = [item for item in items if item not in ids]
    if missing:
        try:
            fetched = scryfall_cards([sid for sid, _ in missing])
        except ScryfallUnavailable:
            raise ValueError("Couldn't reach Scryfall, try again in a moment.")
        for sid, finish in missing:
            card = fetched.get(sid)
            if not card or card.get("object") != "card":
                raise ValueError("A card wasn't found on Scryfall. Remove it and pick it again.")
            if finish not in card.get("finishes", []):
                raise ValueError(f"{card['name']} ({card.get('set', '').upper()}) doesn't come in "
                                 f"{FINISHES[finish].lower()}.")
            ids[sid, finish] = db.execute(
                """INSERT INTO cards (game, external_id, name, set_code, set_name, collector_number, image_url, finish)
                   VALUES ('mtg', %s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (game, external_id, finish) DO UPDATE SET name = EXCLUDED.name
                   RETURNING id""",
                (sid, card["name"], card.get("set"), card.get("set_name"),
                 card.get("collector_number"), card_image(card), finish),
            ).fetchone()["id"]
    return [ids[item] for item in items]


def parse_cart(form):
    """Read the card lines of a new-loan form; returns ([(scryfall_id, finish, qty), ...], error)."""
    ids, finishes, quantities = form.getlist("scryfall_id"), form.getlist("finish"), form.getlist("quantity")
    if not (len(ids) == len(finishes) == len(quantities)):
        return [], "The card list was malformed, please try again."
    lines = {}
    for sid, finish, qty in zip(ids, finishes, quantities):
        sid = sid.strip()
        if not sid:
            continue
        if finish not in FINISHES:
            return [], "Invalid finish."
        try:
            qty = int(qty)
        except ValueError:
            qty = 0
        if not 1 <= qty <= 99:
            return [], "Quantity must be between 1 and 99."
        # The same printing+finish added twice becomes one line.
        lines[sid, finish] = min(99, lines.get((sid, finish), 0) + qty)
    if not lines:
        return [], "Add at least one card."
    if len(lines) > MAX_CARDS_PER_LOAN:
        return [], f"A loan can have at most {MAX_CARDS_PER_LOAN} different cards."
    return [(sid, finish, qty) for (sid, finish), qty in lines.items()], None


# --- ledger -----------------------------------------------------------------

def get_or_create_user_by_username(username):
    db = get_db()
    row = db.execute("SELECT id FROM users WHERE lower(username) = lower(%s)", (username,)).fetchone()
    if row:
        return row["id"]
    return db.execute("INSERT INTO users (username) VALUES (%s) RETURNING id", (username,)).fetchone()["id"]


LOAN_SELECT = """
    SELECT l.*, c.name AS card_name, c.set_code, c.set_name, c.collector_number, c.image_url, c.finish,
           lu.username AS lender_username, lu.first_name AS lender_first_name, lu.photo_url AS lender_photo_url,
           bu.username AS borrower_username, bu.first_name AS borrower_first_name, bu.photo_url AS borrower_photo_url
    FROM loans l
    JOIN cards c ON c.id = l.card_id
    JOIN users lu ON lu.id = l.lender_id
    JOIN users bu ON bu.id = l.borrower_id
"""


@app.template_global()
def person(username, first_name=None):
    return f"@{username}" if username else (first_name or "unknown")


@app.template_global()
def avatar_hue(name):
    return int(hashlib.md5((name or "").lower().encode()).hexdigest()[:4], 16) % 360


@app.template_filter("ago")
def ago(ts):
    days = (datetime.now(timezone.utc).date() - ts.astimezone(timezone.utc).date()).days
    if days <= 0:
        return "today"
    if days == 1:
        return "yesterday"
    if days < 30:
        return f"{days} days ago"
    return ts.strftime("%-d %b %Y")


def group_by_person(loans, role):
    """Group loans by the other party (`role` is 'lender' or 'borrower'), most cards outstanding first."""
    groups = {}
    for loan in loans:
        pid = loan[f"{role}_id"]
        group = groups.setdefault(pid, {
            "username": loan[f"{role}_username"],
            "first_name": loan[f"{role}_first_name"],
            "photo_url": loan[f"{role}_photo_url"],
            "loans": [],
            "out": 0,
        })
        group["loans"].append(loan)
        if loan["status"] == "borrowed":
            group["out"] += loan["quantity"]
    for group in groups.values():
        # Outstanding cards bundled per original loan, newest first (rows arrive sorted by date).
        batches = {}
        for loan in group["loans"]:
            if loan["status"] == "borrowed":
                key = loan["batch_id"] or f"loan-{loan['id']}"
                batches.setdefault(key, {"created_at": loan["created_at"], "loans": []})["loans"].append(loan)
        group["open_batches"] = list(batches.values())
        group["open_rows"] = sum(len(b["loans"]) for b in group["open_batches"])
    return sorted(groups.values(), key=lambda g: (-g["out"], (g["username"] or g["first_name"] or "").lower()))


@app.route("/")
@login_required
def index():
    show_all = request.args.get("show") == "all"
    status_clause = "" if show_all else "AND l.status = 'borrowed'"
    db = get_db()
    borrowed = db.execute(
        f"{LOAN_SELECT} WHERE l.borrower_id = %s {status_clause} ORDER BY l.status, l.created_at DESC",
        (g.user["id"],),
    ).fetchall()
    lent = db.execute(
        f"{LOAN_SELECT} WHERE l.lender_id = %s {status_clause} ORDER BY l.status, l.created_at DESC",
        (g.user["id"],),
    ).fetchall()
    stats = db.execute(
        """SELECT COALESCE(SUM(quantity) FILTER (WHERE lender_id = %(me)s), 0) AS lent_cards,
                  COUNT(DISTINCT borrower_id) FILTER (WHERE lender_id = %(me)s) AS lent_people,
                  COALESCE(SUM(quantity) FILTER (WHERE borrower_id = %(me)s), 0) AS borrowed_cards,
                  COUNT(DISTINCT lender_id) FILTER (WHERE borrower_id = %(me)s) AS borrowed_people
           FROM loans WHERE status = 'borrowed' AND %(me)s IN (lender_id, borrower_id)""",
        {"me": g.user["id"]},
    ).fetchone()
    return render_template(
        "index.html",
        lent_groups=group_by_person(lent, "borrower"),
        borrowed_groups=group_by_person(borrowed, "lender"),
        stats=stats,
        show_all=show_all,
        finishes=FINISHES,
    )


@app.route("/loans/new", methods=["GET", "POST"])
@login_required
def new_loan():
    db = get_db()
    if request.method == "POST":
        form = request.form
        direction = form.get("direction")
        other = form.get("other", "").strip().lstrip("@")
        lines, error = parse_cart(form)

        if direction not in ("lent", "borrowed"):
            error = "Choose whether you lent or borrowed the cards."
        elif not USERNAME_RE.match(other):
            error = "Enter the other person's Telegram @username."
        elif g.user["username"] and other.lower() == g.user["username"].lower():
            error = "You can't lend cards to yourself."

        if error is None:
            try:
                card_ids = get_or_create_mtg_cards([(sid, finish) for sid, finish, _ in lines])
            except ValueError as e:
                error = str(e)

        if error is None:
            other_id = get_or_create_user_by_username(other)
            me = g.user["id"]
            lender, borrower = (me, other_id) if direction == "lent" else (other_id, me)
            batch_id = uuid.uuid4()
            with db.cursor() as cur:
                cur.executemany(
                    """INSERT INTO loans (card_id, lender_id, borrower_id, quantity, created_by, batch_id)
                       VALUES (%s, %s, %s, %s, %s, %s)""",
                    [(card_id, lender, borrower, qty, me, batch_id)
                     for card_id, (_, _, qty) in zip(card_ids, lines)],
                )
            db.commit()
            total = sum(qty for _, _, qty in lines)
            flash(f"Loan recorded: {total} card{'s' if total != 1 else ''}.", "ok")
            return redirect(url_for("index", _anchor=direction))
        db.rollback()
        flash(error, "error")

    # People you've dealt with, most recent first, offered as quick picks.
    people = db.execute(
        """SELECT u.username, u.first_name, u.photo_url, MAX(l.created_at) AS last
           FROM loans l JOIN users u ON u.id = CASE WHEN l.lender_id = %(me)s THEN l.borrower_id ELSE l.lender_id END
           WHERE %(me)s IN (l.lender_id, l.borrower_id) AND u.username IS NOT NULL
           GROUP BY u.id ORDER BY last DESC LIMIT 12""",
        {"me": g.user["id"]},
    ).fetchall()
    prefill = request.form if request.method == "POST" else request.args
    return render_template("new_loan.html", people=people, finishes=FINISHES, prefill=prefill)


def own_loan_or_404(loan_id):
    loan = get_db().execute("SELECT * FROM loans WHERE id = %s", (loan_id,)).fetchone()
    if loan is None or g.user["id"] not in (loan["lender_id"], loan["borrower_id"]):
        abort(404)
    return loan


def return_loans(requested):
    """Mark cards returned. `requested` maps loan id -> quantity coming back (None = all of it).

    Returning part of a row splits it: the row keeps the cards still out and a new 'returned' row with
    the same card, people, loan date and batch records the rest. Everything happens in one transaction;
    any loan the user isn't part of aborts the whole request. Returns the number of cards returned.
    """
    db = get_db()
    total = 0
    for loan_id in sorted(requested):  # consistent lock order
        loan = db.execute("SELECT * FROM loans WHERE id = %s FOR UPDATE", (loan_id,)).fetchone()
        if loan is None or g.user["id"] not in (loan["lender_id"], loan["borrower_id"]):
            db.rollback()
            abort(404)
        if loan["status"] != "borrowed":
            continue
        qty = requested[loan_id]
        qty = loan["quantity"] if qty is None else max(1, min(qty, loan["quantity"]))
        if qty == loan["quantity"]:
            db.execute("UPDATE loans SET status = 'returned', returned_at = now() WHERE id = %s", (loan_id,))
        else:
            # Older rows may predate batch ids; give the pair one so an undo can merge them back.
            db.execute(
                "UPDATE loans SET quantity = quantity - %s, batch_id = COALESCE(batch_id, gen_random_uuid()) "
                "WHERE id = %s",
                (qty, loan_id),
            )
            db.execute(
                """INSERT INTO loans (card_id, lender_id, borrower_id, quantity, status, created_at, returned_at,
                                      created_by, batch_id)
                   SELECT card_id, lender_id, borrower_id, %s, 'returned', created_at, now(), created_by, batch_id
                   FROM loans WHERE id = %s""",
                (qty, loan_id),
            )
        total += qty
    db.commit()
    return total


def parse_quantity(value):
    if value in (None, ""):
        return None
    try:
        return int(value)
    except ValueError:
        abort(400)


def flash_returned(total):
    if total:
        flash(f"Marked {total} card{'s' if total != 1 else ''} as returned.", "ok")


@app.route("/loans/<int:loan_id>/return", methods=["POST"])
@login_required
def mark_returned(loan_id):
    flash_returned(return_loans({loan_id: parse_quantity(request.form.get("quantity"))}))
    return redirect(request.referrer or url_for("index"))


@app.route("/returns", methods=["POST"])
@login_required
def mark_several_returned():
    """Return several cards at once: repeated `loan_id` fields, each with an optional `qty_<id>`."""
    requested = {}
    for raw in request.form.getlist("loan_id"):
        try:
            loan_id = int(raw)
        except ValueError:
            abort(400)
        requested[loan_id] = parse_quantity(request.form.get(f"qty_{loan_id}"))
    if not requested:
        flash("Select the cards that came back.", "error")
    else:
        flash_returned(return_loans(requested))
    return redirect(request.referrer or url_for("index"))


@app.route("/loans/<int:loan_id>/reopen", methods=["POST"])
@login_required
def reopen(loan_id):
    own_loan_or_404(loan_id)
    db = get_db()
    loan = db.execute("SELECT * FROM loans WHERE id = %s FOR UPDATE", (loan_id,)).fetchone()
    if loan["status"] == "returned":
        # Undoing a partial return: fold the cards back into the row that's still out, if there is one.
        sibling = None
        if loan["batch_id"]:
            sibling = db.execute(
                """SELECT id FROM loans
                   WHERE batch_id = %s AND card_id = %s AND lender_id = %s AND borrower_id = %s
                     AND status = 'borrowed' AND id <> %s
                   LIMIT 1 FOR UPDATE""",
                (loan["batch_id"], loan["card_id"], loan["lender_id"], loan["borrower_id"], loan_id),
            ).fetchone()
        if sibling:
            db.execute("UPDATE loans SET quantity = quantity + %s WHERE id = %s", (loan["quantity"], sibling["id"]))
            db.execute("DELETE FROM loans WHERE id = %s", (loan_id,))
        else:
            db.execute("UPDATE loans SET status = 'borrowed', returned_at = NULL WHERE id = %s", (loan_id,))
    db.commit()
    return redirect(request.referrer or url_for("index"))


@app.route("/loans/<int:loan_id>/delete", methods=["POST"])
@login_required
def delete_loan(loan_id):
    loan = own_loan_or_404(loan_id)
    if loan["created_by"] != g.user["id"]:
        abort(403)
    db = get_db()
    db.execute("DELETE FROM loans WHERE id = %s", (loan_id,))
    db.commit()
    return redirect(request.referrer or url_for("index"))


init_db()

if __name__ == "__main__":
    app.run(debug=True, host=os.environ.get("HOST", "127.0.0.1"), port=int(os.environ.get("PORT", 3000)))
