# AGENTS.md

Guide for coding agents working on this repo. Read this before changing anything; `README.md` has the
user-facing setup notes.

## What this is

**Card Ledger**: a small web app where friends record which Magic: The Gathering cards they lent to or
borrowed from each other, down to the exact printing and finish. Login is via Telegram. Live at
https://ledger.beznadijka.dev with real users and data, so treat production with care.

Stack: Python 3.12, Flask (server-rendered Jinja templates), PostgreSQL 17 via psycopg 3 (plain SQL,
no ORM), vanilla JS/CSS (no build step), Docker Compose, gunicorn. Card data comes from the
[Scryfall API](https://scryfall.com/docs/api).

## Layout

| Path | What |
|---|---|
| `app.py` | The whole web app: config, auth, Scryfall lookups, routes, template helpers |
| `schema.sql` | Database schema, applied on every app start (must stay idempotent) |
| `i18n.py` | Translation catalog (English keys → Ukrainian) and plural rules |
| `backup.py` | Daily CSV backup of active loans, sent via the Telegram bot (separate compose service) |
| `templates/` | `base.html` layout; `index.html` home; `person.html` one person; `new_loan.html`; `login.html`; `_macros.html` shared tile/dialog/action macros (import `with context`) |
| `static/` | `style.css` (all styles, light/dark tokens), `ledger.js` (home + person page), `new_loan.js` (loan form), `og-image.png` (link preview) |
| `tests/` | pytest suite against a real Postgres; Scryfall is faked |
| `docker-compose.yml` | `db` (Postgres), `app` (gunicorn), `backup` (daily job) |
| `.github/workflows/ci-cd.yml` | Tests + Docker build on every push/PR; deploy to the server on `main` |

## Domain model (`schema.sql`)

- **users**: a Telegram account. `telegram_id` NULL means a *placeholder*: someone recorded a loan with an
  `@username` who has never logged in. On their first login they claim it (`upsert_telegram_user`).
  Usernames are unique case-insensitively (`lower(username)` index); always compare with `lower()`.
- **cards**: one printing (Scryfall id in `external_id`) in one finish (`nonfoil`/`foil`/`etched`). `game`
  is always `'mtg'` for now; it exists so other TCGs can be added.
- **loans**: the ledger, **one row per card line**: `card_id`, `lender_id`, `borrower_id`, `quantity`,
  `status` (`borrowed`/`returned`), `created_at`, `returned_at`, `created_by`, plus:
  - `batch_id`: rows recorded together in one "loan" share it.
  - `hidden_by_lender` / `hidden_by_borrower`: per-side "deleted from my ledger" flags for *returned* rows.

### Invariants and rules (keep these when changing behavior)

- **Partial returns split rows.** Returning 2 of 4 leaves the row with 2 `borrowed` and inserts a
  `returned` row with the same card, people, `created_at`, `created_by` and `batch_id`. Undo (`reopen`)
  folds a returned row back into its outstanding sibling in the same batch. See `return_loans` / `reopen`.
- **Either party** may mark returned or undo. **Only the creator** may hard-delete a row (`delete_loan`).
- **"Clear returned" is per side, never global.** It only sets the viewer's `hidden_by_*` flag; a row is
  deleted only when both sides have cleared it. Reopening resets both flags. This prevents a borrower from
  marking a loan returned and erasing the lender's record. Don't add a both-sides bulk delete.
- Multi-card submissions are **all-or-nothing** (one transaction); duplicate printing+finish lines merge.
- Card validation happens server-side through one Scryfall `/cards/collection` call
  (`scryfall_cards`); only v4 UUIDs are sent (Scryfall 400s on others).
- The person page (`/people/<id>`) 404s unless the viewer has loans with that person: no user browsing.
- Telegram login: HMAC check in `verify_telegram_auth`, payloads older than 24h rejected. There is also a
  full-page redirect flow (`oauth.telegram.org/auth` → `/login#tgAuthResult=…`) for browsers where the
  widget iframe fails (Firefox-based browsers).

## Schema changes

There is no migration tool. `init_db()` runs `schema.sql` on every start (guarded by an advisory lock),
so every statement must be idempotent: `CREATE … IF NOT EXISTS`, `ALTER TABLE … ADD COLUMN IF NOT
EXISTS …` appended at the end. Never edit or reorder existing statements in a way that would fail or
change data on an already-populated database.

## Localization (required for every UI string)

- Python/templates: `_("English text", name=…)` and `_n("singular", "plural", n)`. Browser JS:
  `t("English text", {name})` / `tn("singular", "plural", n)` (injected in `base.html`).
- Add the Ukrainian translation to `UK` (or the three plural forms one/few/many to `UK_PLURAL`) in
  `i18n.py`. Placeholders must match. `tests/test_i18n.py` scans `app.py`, templates and `static/*.js`
  for `_("…")`/`t("…")` calls (double-quoted string literals) and fails on any missing translation, so
  CI will block the deploy.
- Inserting HTML into a translated sentence: translate with a placeholder token and `|replace` it with
  the markup (see the `<time>` example in `_macros.html`), so autoescaping stays intact.
- Card and set names come from Scryfall and stay in English.
- The display font (Cinzel) has no Cyrillic; `--font-display` falls back to Cormorant SC for it.

## Running and testing locally

```sh
docker compose up -d db                       # Postgres on localhost:5433
docker compose exec db psql -U ledger -c 'CREATE DATABASE ledger_test'   # once
uv venv .venv && uv pip install -p .venv -r requirements.txt -r requirements-dev.txt
.venv/bin/python -m pytest -q                 # whole suite, a few seconds
DEV_LOGIN=1 SECRET_KEY=dev .venv/bin/python app.py   # http://localhost:3000, username-only dev login
```

- Use the `.venv`; the global Python on the dev Mac has a broken pytest plugin and no CA certificates
  (the app uses `certifi` for that reason).
- Tests truncate tables between runs. Don't leave a DB connection/transaction open in a test (use the
  `query` fixture): it blocks the next test's `TRUNCATE` and the suite hangs.
- Scryfall is faked in tests via `scryfall_cards` (see `tests/conftest.py`); test card ids are not
  real UUIDs.
- `DEV_LOGIN=1` must never be enabled in production.

## Deployment

- Push to `main` → CI runs tests and a Docker build → deploy job rsyncs the repo to
  `/opt/ledger.beznadijka.dev` on the server and runs `docker compose -p ledger up -d --build`, then
  health-checks `/login`. Deploy secrets are GitHub repository secrets (`DEPLOY_SSH_KEY`,
  `DEPLOY_KNOWN_HOSTS`).
- The server's `.env` (bot token, `SECRET_KEY`, Postgres password, backup recipient) lives only on the
  server; rsync excludes it. Never commit `.env` or secrets.
- The host runs nginx (TLS via certbot) proxying to `127.0.0.1:3000`, alongside other unrelated apps.
  Keep container ports bound to `127.0.0.1`: Docker bypasses the ufw firewall. The app uses `ProxyFix`
  so external URLs (Telegram auth URL, `og:` tags) are `https://`.
- Telegram's login widget only works on the domain registered with BotFather (`/setdomain`), not on
  localhost.

## Backup service

`backup.py --daemon` sends a CSV of all `borrowed` rows to `BACKUP_CHAT_ID` (or the user found by
`BACKUP_TELEGRAM_USERNAME`) every day at `BACKUP_TIME` in `BACKUP_TZ`, with retries. It idles when no
recipient is configured, so local stacks never send. Cells are formula-escaped and the file has a UTF-8
BOM for Excel. Manual run on the server: `docker compose -p ledger exec backup python backup.py --now`.

## Conventions

- Match the existing style: plain SQL with `%s`/`%(name)s` params (never string-format user input into
  SQL), small functions, short comments explaining *why*.
- Keep dependencies minimal; there's no frontend build, so no npm packages.
- UI: tokens are CSS variables on `:root` with a dark-mode override; check both themes and a ~390px
  phone width. Long (Ukrainian) labels must not overflow tiles.
- Every behavior change needs a test; the suite is fast, run it before pushing.
- Commit messages: imperative summary line, short body explaining what/why.
