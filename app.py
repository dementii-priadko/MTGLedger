import base64
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
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

import i18n
import psycopg
from psycopg.rows import dict_row
from flask import Flask, abort, flash, g, redirect, render_template, request, session, url_for
from urllib.parse import urlsplit

from werkzeug.middleware.proxy_fix import ProxyFix

BASE_DIR = Path(__file__).parent
DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://ledger:ledger@localhost:5433/ledger")
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
BOT_USERNAME = os.environ.get("TELEGRAM_BOT_USERNAME", "")
DEV_LOGIN = os.environ.get("DEV_LOGIN") == "1"
AUTH_MAX_AGE = 3600  # reject Telegram auth payloads older than this (they end up in logs/history)
# Bot login: "Log in with Telegram" opens the bot, which replies with a signed link to /auth/telegram.
# Needs the bot's webhook (registered at startup when PUBLIC_URL and the secret are set).
WEBHOOK_SECRET = os.environ.get("TELEGRAM_WEBHOOK_SECRET", "")
PUBLIC_URL = os.environ.get("PUBLIC_URL", "").rstrip("/")
BOT_LOGIN = bool(BOT_TOKEN and BOT_USERNAME and WEBHOOK_SECRET)

log = logging.getLogger("ledger")

FINISHES = {"nonfoil": "Non-foil", "foil": "Foil", "etched": "Etched foil"}
USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{3,32}$")
MAX_CARDS_PER_LOAN = 50
# Telegram ids allowed into /admin (comma-separated).
ADMIN_TELEGRAM_IDS = {int(x) for x in os.environ.get("ADMIN_TELEGRAM_IDS", "").replace(" ", "").split(",")
                      if x.lstrip("-").isdigit()}

app = Flask(__name__)
# Behind nginx: trust X-Forwarded-Proto so external URLs (Telegram auth URL, og: tags) use https.
# Not X-Forwarded-Host: nginx doesn't set it, so it would be attacker-controlled.
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1)
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

def telegram_auth_hash(data):
    """The Login Widget signature: HMAC-SHA256 of the sorted key=value lines, keyed by SHA256(bot token)."""
    check_string = "\n".join(f"{k}={data[k]}" for k in sorted(data))
    secret = hashlib.sha256(BOT_TOKEN.encode()).digest()
    return hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()


def safe_path(path):
    """A same-site path to redirect to after login, or None."""
    if isinstance(path, str) and path.startswith("/") and not path.startswith(("//", "/\\")) and "\\" not in path:
        return path
    return None


def verify_telegram_auth(data):
    """Check a Telegram Login Widget payload, per https://core.telegram.org/widgets/login#checking-authorization"""
    data = dict(data)
    received_hash = data.pop("hash", "")
    data.pop("redirect", None)  # ours, not Telegram's: not signed (it's limited to same-site paths instead)
    if not BOT_TOKEN or not received_hash:
        return False
    if not hmac.compare_digest(telegram_auth_hash(data), received_hash):
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
    db.execute(
        """INSERT INTO contacts (user_id, contact_id, hidden)
           SELECT CASE WHEN user_id = %(drop)s THEN %(keep)s ELSE user_id END,
                  CASE WHEN contact_id = %(drop)s THEN %(keep)s ELSE contact_id END, hidden
           FROM contacts WHERE %(drop)s IN (user_id, contact_id) AND %(keep)s NOT IN (user_id, contact_id)
           ON CONFLICT DO NOTHING""",
        {"keep": keep_id, "drop": drop_id},
    )
    db.execute("DELETE FROM users WHERE id = %s", (drop_id,))  # its own contacts rows cascade


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
        # Bot logins carry no photo, so keep the one we have.
        "UPDATE users SET username = %s, first_name = %s, last_name = %s, photo_url = COALESCE(%s, photo_url) "
        "WHERE id = %s",
        (username, *profile, user_id),
    )
    db.commit()
    return user_id


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if g.user is None:
            # Remember where they were going (GET pages only) so the bot login can bring them back.
            wanted = request.full_path.rstrip("?") if request.method == "GET" and request.path != "/" else None
            return redirect(url_for("login", next=wanted))
        return view(*args, **kwargs)
    return wrapped


def is_admin(user):
    return bool(user and user["telegram_id"] in ADMIN_TELEGRAM_IDS)


def admin_required(view):
    """Admins only; everyone else gets a 404 so the page's existence isn't revealed."""
    @wraps(view)
    @login_required
    def wrapped(*args, **kwargs):
        if not is_admin(g.user):
            abort(404)
        return view(*args, **kwargs)
    return wrapped


# --- request hardening -------------------------------------------------------

def same_origin(url):
    """True if `url` points at this site (scheme + host), e.g. an Origin or Referer header."""
    ours, theirs = urlsplit(request.host_url), urlsplit(url)
    return (theirs.scheme, theirs.netloc) == (ours.scheme, ours.netloc)


@app.before_request
def reject_cross_origin_writes():
    # SameSite=Lax cookies alone don't stop sibling subdomains (same "site"), so check where
    # state-changing requests come from. Requests without Origin/Referer are allowed.
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return
    source = request.headers.get("Origin") or request.headers.get("Referer")
    if source is not None and not same_origin(source):
        abort(403)


def back_or_index():
    """Redirect to the page the user came from, but only within this site."""
    ref = request.referrer
    return redirect(ref if ref and same_origin(ref) else url_for("index"))


@app.before_request
def make_csp_nonce():
    g.csp_nonce = secrets.token_urlsafe(16)


CSP = "; ".join([
    "default-src 'self'",
    "script-src 'self' 'nonce-{nonce}' https://telegram.org",
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
    "font-src https://fonts.gstatic.com",
    "img-src 'self' data: https://cards.scryfall.io https://svgs.scryfall.io https://t.me https://*.telesco.pe",
    "connect-src 'self' https://api.scryfall.com",
    "frame-src https://oauth.telegram.org",
    "frame-ancestors 'none'",
    "form-action 'self'",
    "base-uri 'none'",
    "object-src 'none'",
])


@app.after_request
def security_headers(resp):
    resp.headers["Content-Security-Policy"] = CSP.format(nonce=g.get("csp_nonce", ""))
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    resp.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    if request.is_secure:
        resp.headers["Strict-Transport-Security"] = "max-age=31536000"
    return resp


# --- localization ------------------------------------------------------------

def _(text, **kwargs):
    return i18n.gettext(g.get("lang", i18n.DEFAULT), text, **kwargs)


def _n(singular, plural, n, **kwargs):
    return i18n.ngettext(g.get("lang", i18n.DEFAULT), singular, plural, n, **kwargs)


app.jinja_env.globals.update(_=_, _n=_n, languages=i18n.LANGUAGES)


@app.before_request
def select_language():
    # An explicit choice (cookie) wins; otherwise follow the browser.
    lang = request.cookies.get("lang")
    if lang not in i18n.LANGUAGES:
        lang = request.accept_languages.best_match(list(i18n.LANGUAGES)) or i18n.DEFAULT
    g.lang = lang


@app.context_processor
def inject_i18n():
    return {"js_i18n": i18n.js_catalog(g.lang)}


@app.route("/lang/<code>")
def set_language(code):
    if code not in i18n.LANGUAGES:
        abort(404)
    resp = back_or_index()
    resp.set_cookie("lang", code, max_age=365 * 24 * 3600, samesite="Lax", httponly=True,
                    secure=app.config["SESSION_COOKIE_SECURE"])
    return resp


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
            "lang": g.lang,
        })
    bot_login_url = bot_login_link(request.args.get("next")) if BOT_LOGIN else None
    return render_template("login.html", bot_username=BOT_USERNAME, dev_login=DEV_LOGIN,
                           redirect_login_url=redirect_login_url, bot_login_url=bot_login_url)


# --- login through the bot -------------------------------------------------------

def bot_login_link(next_path=None):
    """t.me deep link; the start parameter carries the page to come back to (base64url, Telegram's charset)."""
    path = safe_path(next_path) or "/"
    encoded = base64.urlsafe_b64encode(path.encode()).decode().rstrip("=")
    param = f"login_{encoded}" if len(encoded) <= 58 else "login"   # start parameters are capped at 64 chars
    return f"https://t.me/{BOT_USERNAME}?start={param}"


def decode_start_path(param):
    encoded = param[len("login_"):] if param.startswith("login_") else ""
    try:
        path = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)).decode()
    except ValueError:
        return "/"
    return safe_path(path) or "/"


def login_button(text, path):
    """A Telegram login button: Telegram asks "Log in to <domain> as <name>?" and then opens the URL
    with the user's signed identity appended (same signature as the Login Widget)."""
    base = PUBLIC_URL or request.host_url.rstrip("/")
    return {"text": text, "login_url": {"url": f"{base}{url_for('auth_telegram', redirect=path)}",
                                        "request_write_access": True}}


def telegram_api(method, **params):
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{BOT_TOKEN}/{method}", data=json.dumps(params).encode(), method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=10, context=SSL_CONTEXT) as resp:
        result = json.load(resp)
    if not result.get("ok"):
        raise RuntimeError(f"Telegram {method} failed: {result}")
    return result.get("result")


def bot_text(lang, text, **kwargs):
    return i18n.gettext(lang, text, **kwargs)


def bot_lang(tg_user):
    return "uk" if (tg_user or {}).get("language_code", "").startswith("uk") else i18n.DEFAULT


@app.route("/telegram/webhook", methods=["POST"])
def telegram_webhook():
    secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    if not WEBHOOK_SECRET or not hmac.compare_digest(secret, WEBHOOK_SECRET):
        abort(403)
    try:
        handle_bot_update(request.get_json(silent=True) or {})
    except Exception:
        # Answer 200 anyway so Telegram doesn't retry the same update forever.
        log.exception("failed to handle Telegram update")
    return {"ok": True}


def handle_bot_update(update):
    message = update.get("message")
    if not message or message.get("chat", {}).get("type") != "private" or not message.get("from"):
        return
    user, chat_id = message["from"], message["chat"]["id"]
    lang = bot_lang(user)
    text = (message.get("text") or "").strip()
    command, _, arg = text.partition(" ")
    if command == "/login" or (command == "/start" and arg.startswith("login")):
        telegram_api(
            "sendMessage", chat_id=chat_id,
            text=bot_text(lang, "Tap the button to log in to Card Ledger."),
            reply_markup={"inline_keyboard": [[login_button(bot_text(lang, "Log in to Card Ledger"),
                                                            decode_start_path(arg))]]},
        )
    else:
        telegram_api(
            "sendMessage", chat_id=chat_id,
            text=bot_text(lang, "Hi! This bot logs you in to Card Ledger. Send /login any time to get a login link."),
            reply_markup={"inline_keyboard": [[{"text": bot_text(lang, "Open Card Ledger"),
                                                "url": PUBLIC_URL or request.host_url}]]},
        )


def register_webhook():
    """Point the bot's updates at us (production only: needs PUBLIC_URL)."""
    if not (BOT_LOGIN and PUBLIC_URL):
        return
    try:
        telegram_api("setWebhook", url=f"{PUBLIC_URL}/telegram/webhook", secret_token=WEBHOOK_SECRET,
                     allowed_updates=["message"], drop_pending_updates=True)
        telegram_api("setMyCommands", commands=[{"command": "login", "description": "Log in to Card Ledger"}])
    except Exception:
        log.exception("could not register the Telegram webhook")


@app.route("/auth/telegram")
def auth_telegram():
    if not verify_telegram_auth(request.args):
        flash(_("Telegram login could not be verified."), "error")
        return redirect(url_for("login"))
    session.clear()
    session["user_id"] = upsert_telegram_user(request.args)
    session.permanent = True
    # Login buttons from the bot carry the page to return to.
    return redirect(safe_path(request.args.get("redirect")) or url_for("index"))


@app.route("/auth/dev", methods=["POST"])
def auth_dev():
    """Local-only login that skips Telegram (enabled with DEV_LOGIN=1)."""
    if not DEV_LOGIN:
        abort(404)
    username = request.form.get("username", "").strip().lstrip("@")
    if not USERNAME_RE.match(username):
        flash(_("Enter a valid username."), "error")
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
            raise ValueError(_("Couldn't reach Scryfall, try again in a moment."))
        for sid, finish in missing:
            card = fetched.get(sid)
            if not card or card.get("object") != "card":
                raise ValueError(_("A card wasn't found on Scryfall. Remove it and pick it again."))
            if finish not in card.get("finishes", []):
                raise ValueError(_("{card} ({set}) doesn't come in {finish}.", card=card["name"],
                                   set=card.get("set", "").upper(), finish=_(FINISHES[finish]).lower()))
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
    """Read the card lines of a new-loan form; returns ([(scryfall_id, finish, qty, any_printing), ...], error).

    `any_printing` lines ("Any version", wanted cards only) still name a printing: it stands in for the card.
    """
    ids, finishes, quantities = form.getlist("scryfall_id"), form.getlist("finish"), form.getlist("quantity")
    anys = form.getlist("any_printing") or ["0"] * len(ids)
    if not (len(ids) == len(finishes) == len(quantities) == len(anys)):
        return [], _("The card list was malformed, please try again.")
    lines = {}
    for sid, finish, qty, any_printing in zip(ids, finishes, quantities, anys):
        sid = sid.strip()
        if not sid:
            continue
        if finish not in FINISHES:
            return [], _("Invalid finish.")
        try:
            qty = int(qty)
        except ValueError:
            qty = 0
        if not 1 <= qty <= 99:
            return [], _("Quantity must be between 1 and 99.")
        # The same printing+finish added twice becomes one line.
        key = (sid, finish, any_printing == "1")
        lines[key] = min(99, lines.get(key, 0) + qty)
    if not lines:
        return [], _("Add at least one card.")
    if len(lines) > MAX_CARDS_PER_LOAN:
        return [], _n("A loan can have at most {n} different card.", "A loan can have at most {n} different cards.",
                      MAX_CARDS_PER_LOAN)
    return [(sid, finish, qty, any_printing) for (sid, finish, any_printing), qty in lines.items()], None


# --- ledger -----------------------------------------------------------------

def get_or_create_user_by_username(username):
    db = get_db()
    row = db.execute("SELECT id FROM users WHERE lower(username) = lower(%s)", (username,)).fetchone()
    if row:
        return row["id"]
    return db.execute("INSERT INTO users (username) VALUES (%s) RETURNING id", (username,)).fetchone()["id"]


def add_contacts(db, a, b):
    """Record that two people traded (both directions), so they see each other's Missing cards."""
    db.execute("INSERT INTO contacts (user_id, contact_id) VALUES (%s, %s), (%s, %s) ON CONFLICT DO NOTHING",
               (a, b, b, a))


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
        return _("today")
    if days == 1:
        return _("yesterday")
    if days < 30:
        return _n("{n} day ago", "{n} days ago", days)
    months = i18n.MONTHS_SHORT.get(g.lang)
    if months:
        return f"{ts.day} {months[ts.month - 1]} {ts.year}"
    return ts.strftime("%-d %b %Y")


def group_by_person(loans, role, returned_counts):
    """Group loans by the other party (`role` is 'lender' or 'borrower'), most cards outstanding first.

    `returned_counts` maps person id -> number of returned records still in the viewer's ledger.
    """
    groups = {}
    for loan in loans:
        pid = loan[f"{role}_id"]
        group = groups.setdefault(pid, {
            "id": pid,
            "returned_rows": returned_counts.get(pid, 0),
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
    me = g.user["id"]
    borrowed = db.execute(
        f"""{LOAN_SELECT} WHERE l.borrower_id = %s AND NOT l.hidden_by_borrower {status_clause}
            ORDER BY l.status, l.created_at DESC""",
        (me,),
    ).fetchall()
    lent = db.execute(
        f"""{LOAN_SELECT} WHERE l.lender_id = %s AND NOT l.hidden_by_lender {status_clause}
            ORDER BY l.status, l.created_at DESC""",
        (me,),
    ).fetchall()
    returned = {"lent": {}, "borrowed": {}}
    for row in db.execute(
        """SELECT CASE WHEN lender_id = %(me)s THEN 'lent' ELSE 'borrowed' END AS side,
                  CASE WHEN lender_id = %(me)s THEN borrower_id ELSE lender_id END AS other, count(*) AS n
           FROM loans
           WHERE status = 'returned'
             AND ((lender_id = %(me)s AND NOT hidden_by_lender) OR (borrower_id = %(me)s AND NOT hidden_by_borrower))
           GROUP BY 1, 2""",
        {"me": me},
    ):
        returned[row["side"]][row["other"]] = row["n"]
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
        lent_groups=group_by_person(lent, "borrower", returned["lent"]),
        borrowed_groups=group_by_person(borrowed, "lender", returned["borrowed"]),
        stats=stats,
        show_all=show_all,
        finishes=FINISHES,
    )


@app.route("/people/<int:person_id>")
@login_required
def person_page(person_id):
    """Everything between the current user and one person: what you lent them and what you borrowed."""
    me = g.user["id"]
    if person_id == me:
        return redirect(url_for("index"))
    db = get_db()
    params = {"me": me, "p": person_id}
    # Only people you have (or had) loans with; no browsing of other users.
    if not db.execute(
        """SELECT 1 FROM loans WHERE (lender_id = %(me)s AND borrower_id = %(p)s)
                                  OR (lender_id = %(p)s AND borrower_id = %(me)s) LIMIT 1""",
        params,
    ).fetchone():
        abort(404)
    other = db.execute("SELECT * FROM users WHERE id = %s", (person_id,)).fetchone()

    show_all = request.args.get("show") == "all"
    status_clause = "" if show_all else "AND l.status = 'borrowed'"
    lent = db.execute(
        f"""{LOAN_SELECT} WHERE l.lender_id = %(me)s AND l.borrower_id = %(p)s AND NOT l.hidden_by_lender
            {status_clause} ORDER BY l.status, l.created_at DESC""",
        params,
    ).fetchall()
    borrowed = db.execute(
        f"""{LOAN_SELECT} WHERE l.borrower_id = %(me)s AND l.lender_id = %(p)s AND NOT l.hidden_by_borrower
            {status_clause} ORDER BY l.status, l.created_at DESC""",
        params,
    ).fetchall()
    returned = db.execute(
        """SELECT count(*) FILTER (WHERE lender_id = %(me)s AND NOT hidden_by_lender) AS lent,
                  count(*) FILTER (WHERE borrower_id = %(me)s AND NOT hidden_by_borrower) AS borrowed
           FROM loans
           WHERE status = 'returned' AND ((lender_id = %(me)s AND borrower_id = %(p)s)
                                       OR (lender_id = %(p)s AND borrower_id = %(me)s))""",
        params,
    ).fetchone()

    def side(loans, role, returned_rows):
        groups = group_by_person(loans, role, {person_id: returned_rows})
        if groups:
            return groups[0]
        return {"id": person_id, "username": other["username"], "first_name": other["first_name"],
                "photo_url": other["photo_url"], "loans": [], "out": 0, "open_batches": [], "open_rows": 0,
                "returned_rows": returned_rows}

    return render_template(
        "person.html",
        other=other,
        lent=side(lent, "borrower", returned["lent"]),
        borrowed=side(borrowed, "lender", returned["borrowed"]),
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

        # "missing" (lent out, but to whom?) and "wanted" (looking to borrow) go on the board, not to a person.
        on_board = direction in BOARD_TABLES
        if direction not in ("lent", "borrowed") and not on_board:
            error = _("Choose whether you lent or borrowed the cards.")
        elif not on_board and not USERNAME_RE.match(other):
            error = _("Enter the other person's Telegram @username.")
        elif not on_board and g.user["username"] and other.lower() == g.user["username"].lower():
            error = _("You can't lend cards to yourself.")

        if error is None:
            try:
                card_ids = get_or_create_mtg_cards([(sid, finish) for sid, finish, *_rest in lines])
            except ValueError as e:
                error = str(e)

        if error is None and on_board:
            for card_id, (_sid, _finish, qty, any_printing) in zip(card_ids, lines):
                post_to_board(direction, card_id, qty, any_printing and direction == "wanted")
            db.commit()
            total = sum(line[2] for line in lines)
            if direction == "missing":
                flash(_n("Added {n} card to Missing.", "Added {n} cards to Missing.", total), "ok")
            else:
                flash(_n("Added {n} card to your list.", "Added {n} cards to your list.", total), "ok")
            return redirect(url_for(direction))

        if error is None:
            other_id = get_or_create_user_by_username(other)
            me = g.user["id"]
            lender, borrower = (me, other_id) if direction == "lent" else (other_id, me)
            batch_id = uuid.uuid4()
            with db.cursor() as cur:
                cur.executemany(
                    """INSERT INTO loans (card_id, lender_id, borrower_id, quantity, created_by, batch_id)
                       VALUES (%s, %s, %s, %s, %s, %s)""",
                    [(card_id, lender, borrower, line[2], me, batch_id) for card_id, line in zip(card_ids, lines)],
                )
            add_contacts(db, lender, borrower)
            db.commit()
            total = sum(line[2] for line in lines)
            flash(_n("Loan recorded: {n} card.", "Loan recorded: {n} cards.", total), "ok")
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


# --- the board: missing and wanted cards ---------------------------------------------
# Two tabs with the same shape: cards people post for their contacts to see, one row per owner + card.
# Missing = lent out, lost track of to whom; Wanted ("Looking for") = would like to borrow.

BOARD_TABLES = {"missing": "missing_cards", "wanted": "wanted_cards"}
# Cards the viewer sees: their own and those of contacts they haven't hidden.
BOARD_VISIBLE = "(owner_id = %(me)s OR owner_id IN (SELECT contact_id FROM contacts WHERE user_id = %(me)s AND NOT hidden))"


def post_to_board(tab, card_id, qty, any_printing=False):
    """Add copies to the viewer's Missing or Looking-for list, adding up with what's already there."""
    db, me = get_db(), g.user["id"]
    if any_printing:
        # "Any version" requests are one per card name, whichever printing stood in for it.
        if db.execute(
            """UPDATE wanted_cards w SET quantity = w.quantity + %(qty)s FROM cards c, cards new
               WHERE w.owner_id = %(me)s AND w.any_printing AND c.id = w.card_id AND new.id = %(card)s
                 AND c.name = new.name
               RETURNING w.id""",
            {"qty": qty, "me": me, "card": card_id},
        ).fetchone():
            return
        db.execute("INSERT INTO wanted_cards (card_id, owner_id, quantity, any_printing) VALUES (%s, %s, %s, true)",
                   (card_id, me, qty))
        return
    table = BOARD_TABLES[tab]
    key = "owner_id, card_id, any_printing" if tab == "wanted" else "owner_id, card_id"
    db.execute(
        f"""INSERT INTO {table} (card_id, owner_id, quantity) VALUES (%s, %s, %s)
            ON CONFLICT ({key}) DO UPDATE SET quantity = {table}.quantity + EXCLUDED.quantity""",
        (card_id, me, qty),
    )


@app.route("/missing")
@login_required
def missing():
    """Cards your contacts lent out and lost track of, so you can check whether you have one."""
    return render_board("missing")


@app.route("/wanted")
@login_required
def wanted():
    """Cards your contacts would like to borrow, so you can offer yours."""
    return render_board("wanted")


def render_board(tab):
    db, me = get_db(), g.user["id"]
    rows = db.execute(
        f"""SELECT b.*, c.name AS card_name, c.set_code, c.set_name, c.collector_number, c.image_url, c.finish,
                   u.username, u.first_name, u.photo_url
            FROM {BOARD_TABLES[tab]} b JOIN cards c ON c.id = b.card_id JOIN users u ON u.id = b.owner_id
            WHERE {BOARD_VISIBLE}
            ORDER BY b.created_at DESC""",
        {"me": me},
    ).fetchall()
    groups = {}
    for row in rows:
        group = groups.setdefault(row["owner_id"], {
            "id": row["owner_id"], "username": row["username"], "first_name": row["first_name"],
            "photo_url": row["photo_url"], "cards": [], "total": 0,
        })
        group["cards"].append(row)
        group["total"] += row["quantity"]
    ordered = sorted(groups.values(), key=lambda grp: (grp["id"] != me, -grp["total"]))
    # Offers the viewer may see: all offers on their own wanted cards, and their own offers on others'.
    offers = {}
    if tab == "wanted":
        for offer in db.execute(
            """SELECT o.*, u.username, u.first_name,
                      oc.set_code AS offer_set, oc.collector_number AS offer_number, oc.finish AS offer_finish
               FROM wanted_offers o
               JOIN wanted_cards w ON w.id = o.wanted_id JOIN users u ON u.id = o.lender_id
               LEFT JOIN cards oc ON oc.id = o.card_id
               WHERE w.owner_id = %(me)s OR o.lender_id = %(me)s ORDER BY o.created_at""",
            {"me": me},
        ):
            offers.setdefault(offer["wanted_id"], []).append(offer)
    counts = db.execute(
        f"""SELECT (SELECT COALESCE(SUM(quantity), 0) FROM missing_cards WHERE {BOARD_VISIBLE}) AS missing,
                   (SELECT COALESCE(SUM(quantity), 0) FROM wanted_cards WHERE {BOARD_VISIBLE}) AS wanted""",
        {"me": me},
    ).fetchone()
    hidden = db.execute(
        """SELECT u.id, u.username, u.first_name FROM contacts c JOIN users u ON u.id = c.contact_id
           WHERE c.user_id = %s AND c.hidden ORDER BY lower(COALESCE(u.username, u.first_name))""",
        (me,),
    ).fetchall()
    return render_template("board.html", tab=tab, groups=ordered, offers=offers, counts=counts, hidden=hidden,
                           finishes=FINISHES)


@app.context_processor
def inject_pending_offers():
    """Offers waiting on the viewer's wanted cards, flagged on the board link in the top bar."""
    if not g.get("user"):
        return {}
    return {"pending_offers": get_db().execute(
        "SELECT count(*) AS n FROM wanted_offers o JOIN wanted_cards w ON w.id = o.wanted_id WHERE w.owner_id = %s",
        (g.user["id"],),
    ).fetchone()["n"]}


def visible_to_me(owner_id):
    """Whether the viewer sees this owner's board cards (a contact they haven't hidden)."""
    return get_db().execute(
        "SELECT 1 FROM contacts WHERE user_id = %s AND contact_id = %s AND NOT hidden", (g.user["id"], owner_id)
    ).fetchone() is not None


def take_missing(missing_id, requested):
    """Lock a Missing-board row and take `requested` copies off it (None = all); returns (row, quantity taken)."""
    db = get_db()
    row = db.execute(
        """SELECT m.*, c.name AS card_name, u.username, u.first_name
           FROM missing_cards m JOIN cards c ON c.id = m.card_id JOIN users u ON u.id = m.owner_id
           WHERE m.id = %s FOR UPDATE OF m""",
        (missing_id,),
    ).fetchone()
    if row is None:
        abort(404)
    qty = row["quantity"] if requested is None else max(1, min(requested, row["quantity"]))
    if qty == row["quantity"]:
        db.execute("DELETE FROM missing_cards WHERE id = %s", (missing_id,))
    else:
        db.execute("UPDATE missing_cards SET quantity = quantity - %s WHERE id = %s", (qty, missing_id))
    return row, qty


@app.route("/missing/<int:missing_id>/claim", methods=["POST"])
@login_required
def claim_missing(missing_id):
    """"It's with me": the card moves off the board into both ledgers, borrowed by the viewer from its owner."""
    row, qty = take_missing(missing_id, parse_quantity(request.form.get("quantity")))
    me = g.user["id"]
    if row["owner_id"] == me:
        get_db().rollback()
        abort(400)
    if not visible_to_me(row["owner_id"]):
        get_db().rollback()
        abort(404)
    db = get_db()
    db.execute(
        """INSERT INTO loans (card_id, lender_id, borrower_id, quantity, created_by, batch_id)
           VALUES (%s, %s, %s, %s, %s, %s)""",
        (row["card_id"], row["owner_id"], me, qty, me, uuid.uuid4()),
    )
    db.commit()
    flash(_("{card} from {name} is now in your ledger.", card=row["card_name"],
            name=person(row["username"], row["first_name"])), "ok")
    return back_or_index()


@app.route("/missing/<int:missing_id>/found", methods=["POST"])
@login_required
def found_missing(missing_id):
    """The owner found it (or remembered who has it): take it off the board."""
    row, _qty = take_missing(missing_id, parse_quantity(request.form.get("quantity")))
    if row["owner_id"] != g.user["id"]:
        get_db().rollback()
        abort(403)
    get_db().commit()
    flash(_("Removed from Missing."), "ok")
    return back_or_index()


@app.route("/board/people/<int:person_id>/<any(hide, show):action>", methods=["POST"])
@login_required
def toggle_board_person(person_id, action):
    """Stop (or start again) seeing one contact's cards on the board (both tabs)."""
    db = get_db()
    row = db.execute(
        """UPDATE contacts SET hidden = %s WHERE user_id = %s AND contact_id = %s
           RETURNING (SELECT COALESCE('@' || username, first_name) FROM users WHERE id = contact_id) AS name""",
        (action == "hide", g.user["id"], person_id),
    ).fetchone()
    if row is None:
        abort(404)
    db.commit()
    if action == "hide":
        flash(_("You won’t see {name}’s cards here any more.", name=row["name"]), "ok")
    return back_or_index()


# --- wanted cards and offers ----------------------------------------------------------

def wanted_or_404(wanted_id):
    row = get_db().execute(
        """SELECT w.*, c.name AS card_name, u.username, u.first_name
           FROM wanted_cards w JOIN cards c ON c.id = w.card_id JOIN users u ON u.id = w.owner_id
           WHERE w.id = %s""",
        (wanted_id,),
    ).fetchone()
    if row is None:
        abort(404)
    return row


@app.route("/wanted/<int:wanted_id>/offer", methods=["POST"])
@login_required
def offer_wanted(wanted_id):
    """"I can lend it": offer copies to the requester, who confirms before anything is recorded."""
    row, me = wanted_or_404(wanted_id), g.user["id"]
    if row["owner_id"] == me:
        abort(400)
    if not visible_to_me(row["owner_id"]):
        abort(404)
    qty = parse_quantity(request.form.get("quantity"))
    qty = row["quantity"] if qty is None else max(1, min(qty, row["quantity"]))
    db = get_db()
    card_id = None
    if row["any_printing"]:
        # Any version will do: the lender says which printing and finish they're lending.
        sid, finish = request.form.get("scryfall_id", "").strip(), request.form.get("finish")
        if not sid or finish not in FINISHES:
            abort(400)
        try:
            [card_id] = get_or_create_mtg_cards([(sid, finish)])
        except ValueError as e:
            db.rollback()
            flash(str(e), "error")
            return back_or_index()
        name = db.execute("SELECT name FROM cards WHERE id = %s", (card_id,)).fetchone()["name"]
        if name.lower() != row["card_name"].lower():
            db.rollback()
            abort(400)  # a printing of some other card
    db.execute(
        """INSERT INTO wanted_offers (wanted_id, lender_id, card_id, quantity) VALUES (%s, %s, %s, %s)
           ON CONFLICT (wanted_id, lender_id) DO UPDATE SET card_id = EXCLUDED.card_id, quantity = EXCLUDED.quantity""",
        (wanted_id, me, card_id, qty),
    )
    db.commit()
    flash(_("Offer sent. {name} will see it.", name=person(row["username"], row["first_name"])), "ok")
    return back_or_index()


@app.route("/wanted/<int:wanted_id>/withdraw", methods=["POST"])
@login_required
def withdraw_offer(wanted_id):
    db = get_db()
    db.execute("DELETE FROM wanted_offers WHERE wanted_id = %s AND lender_id = %s", (wanted_id, g.user["id"]))
    db.commit()
    return back_or_index()


@app.route("/wanted/<int:wanted_id>/remove", methods=["POST"])
@login_required
def remove_wanted(wanted_id):
    """The requester doesn't need it any more (offers on it go too)."""
    if wanted_or_404(wanted_id)["owner_id"] != g.user["id"]:
        abort(403)
    db = get_db()
    db.execute("DELETE FROM wanted_cards WHERE id = %s", (wanted_id,))
    db.commit()
    flash(_("Removed from your list."), "ok")
    return back_or_index()


def own_offer_or_404(offer_id):
    """Lock an offer on one of the viewer's wanted cards (and that card)."""
    row = get_db().execute(
        """SELECT o.id, o.wanted_id, o.lender_id, o.quantity, w.owner_id, w.quantity AS wanted_quantity,
                  COALESCE(o.card_id, w.card_id) AS loan_card_id, c.name AS card_name, u.username, u.first_name
           FROM wanted_offers o JOIN wanted_cards w ON w.id = o.wanted_id
           JOIN cards c ON c.id = w.card_id JOIN users u ON u.id = o.lender_id
           WHERE o.id = %s FOR UPDATE OF o, w""",
        (offer_id,),
    ).fetchone()
    if row is None or row["owner_id"] != g.user["id"]:
        get_db().rollback()
        abort(404)
    return row


@app.route("/offers/<int:offer_id>/accept", methods=["POST"])
@login_required
def accept_offer(offer_id):
    """The requester takes the offer: it becomes a loan (offerer -> requester) and comes off their list."""
    offer, me, db = own_offer_or_404(offer_id), g.user["id"], get_db()
    qty = min(offer["quantity"], offer["wanted_quantity"])  # the request may have shrunk since the offer
    db.execute(
        """INSERT INTO loans (card_id, lender_id, borrower_id, quantity, created_by, batch_id)
           VALUES (%s, %s, %s, %s, %s, %s)""",
        (offer["loan_card_id"], offer["lender_id"], me, qty, me, uuid.uuid4()),
    )
    add_contacts(db, offer["lender_id"], me)
    if qty == offer["wanted_quantity"]:
        db.execute("DELETE FROM wanted_cards WHERE id = %s", (offer["wanted_id"],))  # other offers go with it
    else:
        db.execute("UPDATE wanted_cards SET quantity = quantity - %s WHERE id = %s", (qty, offer["wanted_id"]))
        db.execute("DELETE FROM wanted_offers WHERE id = %s", (offer_id,))
    db.commit()
    flash(_("{card} from {name} is now in your ledger.", card=offer["card_name"],
            name=person(offer["username"], offer["first_name"])), "ok")
    return back_or_index()


@app.route("/offers/<int:offer_id>/decline", methods=["POST"])
@login_required
def decline_offer(offer_id):
    own_offer_or_404(offer_id)
    db = get_db()
    db.execute("DELETE FROM wanted_offers WHERE id = %s", (offer_id,))
    db.commit()
    return back_or_index()


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
        flash(_n("Marked {n} card as returned.", "Marked {n} cards as returned.", total), "ok")


@app.route("/loans/<int:loan_id>/return", methods=["POST"])
@login_required
def mark_returned(loan_id):
    flash_returned(return_loans({loan_id: parse_quantity(request.form.get("quantity"))}))
    return back_or_index()


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
        flash(_("Select the cards that came back."), "error")
    else:
        flash_returned(return_loans(requested))
    return back_or_index()


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
            db.execute(
                """UPDATE loans SET status = 'borrowed', returned_at = NULL,
                                    hidden_by_lender = false, hidden_by_borrower = false
                   WHERE id = %s""",
                (loan_id,),
            )
    db.commit()
    return back_or_index()


@app.route("/people/<int:person_id>/clear-returned", methods=["POST"])
@login_required
def clear_returned(person_id):
    """Remove returned records with one person from the current user's ledger.

    The other side keeps their copy (so nobody can erase a loan by marking it returned and deleting it);
    rows that both sides have cleared are deleted for good.
    """
    side = request.form.get("direction")
    if side not in ("lent", "borrowed"):
        abort(400)
    me, flag = g.user["id"], "hidden_by_lender" if side == "lent" else "hidden_by_borrower"
    lender, borrower = (me, person_id) if side == "lent" else (person_id, me)
    db = get_db()
    cleared = db.execute(
        f"""UPDATE loans SET {flag} = true
            WHERE lender_id = %s AND borrower_id = %s AND status = 'returned' AND NOT {flag}""",
        (lender, borrower),
    ).rowcount
    db.execute("DELETE FROM loans WHERE status = 'returned' AND hidden_by_lender AND hidden_by_borrower")
    db.commit()
    if cleared:
        flash(_n("Deleted {n} returned record.", "Deleted {n} returned records.", cleared), "ok")
    return back_or_index()


@app.route("/loans/<int:loan_id>/delete", methods=["POST"])
@login_required
def delete_loan(loan_id):
    loan = own_loan_or_404(loan_id)
    if loan["created_by"] != g.user["id"]:
        abort(403)
    db = get_db()
    db.execute("DELETE FROM loans WHERE id = %s", (loan_id,))
    db.commit()
    return back_or_index()



# --- admin ---------------------------------------------------------------------------

app.jinja_env.globals["is_admin"] = is_admin


def admin_log(db, action, **details):
    db.execute("INSERT INTO admin_log (admin_id, action, details) VALUES (%s, %s, %s)",
               (g.user["id"], action, json.dumps(details, default=str)))


@app.route("/admin")
@admin_required
def admin():
    db = get_db()
    stats = db.execute(
        """SELECT (SELECT count(*) FROM users WHERE telegram_id IS NOT NULL) AS users,
                  (SELECT count(*) FROM users WHERE telegram_id IS NULL) AS placeholders,
                  (SELECT count(*) FROM loans WHERE status = 'borrowed') AS active_loans,
                  (SELECT COALESCE(sum(quantity), 0) FROM loans WHERE status = 'borrowed') AS cards_out,
                  (SELECT count(*) FROM loans WHERE status = 'returned') AS returned_loans,
                  (SELECT count(*) FROM missing_cards) AS missing,
                  (SELECT count(*) FROM wanted_cards) AS wanted,
                  (SELECT count(*) FROM wanted_offers) AS offers,
                  (SELECT count(*) FROM users WHERE created_at > now() - interval '7 days') AS new_users"""
    ).fetchone()
    users = db.execute(
        """SELECT u.*,
                  COALESCE(sum(l.quantity) FILTER (WHERE l.lender_id = u.id AND l.status = 'borrowed'), 0) AS lent_out,
                  COALESCE(sum(l.quantity) FILTER (WHERE l.borrower_id = u.id AND l.status = 'borrowed'), 0) AS borrowed_out,
                  count(l.id) AS loan_rows,
                  (SELECT count(*) FROM missing_cards m WHERE m.owner_id = u.id)
                    + (SELECT count(*) FROM wanted_cards w WHERE w.owner_id = u.id) AS board_posts
           FROM users u LEFT JOIN loans l ON u.id IN (l.lender_id, l.borrower_id)
           GROUP BY u.id ORDER BY u.telegram_id IS NULL, u.created_at DESC"""
    ).fetchall()
    loans = db.execute(
        f"""{LOAN_SELECT.replace("SELECT l.*,", "SELECT l.*, cu.username AS creator_username, cu.first_name AS creator_first_name,", 1)}
            LEFT JOIN users cu ON cu.id = l.created_by
            ORDER BY l.created_at DESC, l.id DESC LIMIT 50"""
    ).fetchall()
    entries = db.execute(
        """SELECT a.*, u.username, u.first_name FROM admin_log a LEFT JOIN users u ON u.id = a.admin_id
           ORDER BY a.created_at DESC LIMIT 30"""
    ).fetchall()
    return render_template("admin.html", stats=stats, users=users, loans=loans, log=entries, finishes=FINISHES,
                           backup_ready=bool(os.environ.get("BACKUP_CHAT_ID") or os.environ.get("BACKUP_TELEGRAM_USERNAME")))


def placeholder_or_404(db, user_id):
    row = db.execute("SELECT * FROM users WHERE id = %s AND telegram_id IS NULL FOR UPDATE", (user_id,)).fetchone()
    if row is None:
        abort(404)
    return row


@app.route("/admin/users/<int:user_id>/rename", methods=["POST"])
@admin_required
def admin_rename(user_id):
    db = get_db()
    placeholder = placeholder_or_404(db, user_id)
    new = request.form.get("username", "").strip().lstrip("@")
    if not USERNAME_RE.match(new):
        flash(_("Enter a valid username."), "error")
    elif db.execute("SELECT 1 FROM users WHERE lower(username) = lower(%s) AND id <> %s", (new, user_id)).fetchone():
        flash(_("@{username} already exists — merge into it instead.", username=new), "error")
    else:
        db.execute("UPDATE users SET username = %s WHERE id = %s", (new, user_id))
        admin_log(db, "rename_placeholder", user_id=user_id, old=placeholder["username"], new=new)
        db.commit()
        flash(_("Renamed @{old} to @{new}.", old=placeholder["username"], new=new), "ok")
    return redirect(url_for("admin"))


@app.route("/admin/users/<int:user_id>/merge", methods=["POST"])
@admin_required
def admin_merge(user_id):
    db = get_db()
    placeholder = placeholder_or_404(db, user_id)
    target_name = request.form.get("into", "").strip().lstrip("@")
    target = db.execute("SELECT * FROM users WHERE lower(username) = lower(%s) AND id <> %s",
                        (target_name, user_id)).fetchone() if target_name else None
    if target is None:
        flash(_("No user @{username}.", username=target_name), "error")
    elif db.execute("SELECT 1 FROM loans WHERE (lender_id, borrower_id) IN ((%s, %s), (%s, %s)) LIMIT 1",
                    (user_id, target["id"], target["id"], user_id)).fetchone():
        # Merging would turn their loans with each other into loans with oneself.
        flash(_("@{a} and @{b} have loans with each other, so they can’t be merged.",
                a=placeholder["username"], b=target["username"]), "error")
    else:
        moved = db.execute("SELECT count(*) AS n FROM loans WHERE %s IN (lender_id, borrower_id)", (user_id,)).fetchone()["n"]
        merge_users(db, target["id"], user_id)
        admin_log(db, "merge_placeholder", placeholder=placeholder["username"], into=target["username"], loans=moved)
        db.commit()
        flash(_("Merged @{a} into @{b}.", a=placeholder["username"], b=target["username"]), "ok")
    return redirect(url_for("admin"))


@app.route("/admin/users/<int:user_id>/delete", methods=["POST"])
@admin_required
def admin_delete_placeholder(user_id):
    db = get_db()
    placeholder = placeholder_or_404(db, user_id)
    if db.execute("SELECT 1 FROM loans WHERE %s IN (lender_id, borrower_id) LIMIT 1", (user_id,)).fetchone():
        flash(_("@{username} still has loans; merge or delete those first.", username=placeholder["username"]), "error")
    else:
        db.execute("DELETE FROM users WHERE id = %s", (user_id,))
        admin_log(db, "delete_placeholder", username=placeholder["username"])
        db.commit()
        flash(_("Deleted @{username}.", username=placeholder["username"]), "ok")
    return redirect(url_for("admin"))


@app.route("/admin/loans/<int:loan_id>/delete", methods=["POST"])
@admin_required
def admin_delete_loan(loan_id):
    db = get_db()
    loan = db.execute(f"{LOAN_SELECT} WHERE l.id = %s", (loan_id,)).fetchone()
    if loan is None:
        abort(404)
    db.execute("DELETE FROM loans WHERE id = %s", (loan_id,))
    admin_log(db, "delete_loan", loan_id=loan_id, card=loan["card_name"], quantity=loan["quantity"],
              status=loan["status"], lender=person(loan["lender_username"], loan["lender_first_name"]),
              borrower=person(loan["borrower_username"], loan["borrower_first_name"]))
    db.commit()
    flash(_("Loan deleted."), "ok")
    return redirect(url_for("admin"))


@app.route("/admin/backup", methods=["POST"])
@admin_required
def admin_backup():
    import backup
    db = get_db()
    try:
        summary = backup.run_backup()
    except Exception as e:
        log.exception("manual backup failed")
        flash(_("Backup failed: {error}", error=str(e)[:200]), "error")
        return redirect(url_for("admin"))
    admin_log(db, "backup", summary=summary)
    db.commit()
    flash(_("Backup sent."), "ok")
    return redirect(url_for("admin"))


init_db()
register_webhook()

if __name__ == "__main__":
    app.run(debug=True, host=os.environ.get("HOST", "127.0.0.1"), port=int(os.environ.get("PORT", 3000)))
