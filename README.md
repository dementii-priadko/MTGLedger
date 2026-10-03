# Card Ledger

Track which TCG cards you've lent to / borrowed from friends. Login via Telegram. Currently supports Magic: The Gathering (card data from Scryfall).

## Run

Everything in Docker (Postgres + app on http://localhost:3000):

```sh
cp .env.example .env   # fill in TELEGRAM_BOT_TOKEN, TELEGRAM_BOT_USERNAME, SECRET_KEY
docker compose up -d --build
```

Or only Postgres in Docker, app locally (with auto-reload):

```sh
docker compose up -d db          # published on localhost:5433 (override with POSTGRES_PORT)
pip install -r requirements.txt
env $(grep -v '^#' .env) python3 app.py    # bash/zsh; fish: env (grep -v '^#' .env) python3 app.py
                                           # -> http://localhost:3000
```

The schema in `schema.sql` is applied automatically on startup. Data lives in the `pgdata` volume;
`docker compose down -v` wipes it.

Telegram login setup: in @BotFather run `/setdomain` for your bot and enter the domain the app is served from.
Telegram doesn't accept `localhost`, so for local testing either use a tunnel (e.g. `cloudflared tunnel --url http://localhost:3000`)
or set `DEV_LOGIN=1` in `.env` to get a username-only login form (never enable this in production).

Other env vars: `DATABASE_URL` (default `postgresql://ledger:ledger@localhost:5433/ledger`), `POSTGRES_PASSWORD`,
`APP_PORT`, `SESSION_COOKIE_SECURE=1` when served over HTTPS.

## Data model (`schema.sql`)

- `users`: Telegram accounts. Recording a loan with an `@username` who hasn't logged in yet creates a placeholder, which they claim on first login.
- `cards`: one printing (Scryfall id) in one finish (`nonfoil` / `foil` / `etched`), tagged with `game` so other TCGs can be added.
- `loans`: the ledger, one row per card. `card_id`, `lender_id`, `borrower_id`, `quantity`, `created_at`, `status` (`borrowed` / `returned`), `returned_at`.
  Cards recorded together in one loan share a `batch_id`; each card can still be returned on its own.

Either party can mark a loan returned (or undo). Only whoever created the entry can delete it.

## Tests

```sh
docker compose up -d db
docker compose exec db psql -U ledger -c 'CREATE DATABASE ledger_test'   # once
pip install -r requirements.txt -r requirements-dev.txt
python -m pytest
```

Tests use a real Postgres (`ledger_test`, override with `DATABASE_URL`) and fake the Scryfall API.

## CI/CD

`.github/workflows/ci-cd.yml`: every push / PR runs the tests and a Docker build. Pushes to `main`
then deploy to https://ledger.beznadijka.dev: the code is rsynced to `/opt/ledger.beznadijka.dev` on the
server and `docker compose -p ledger up -d --build` is run there. The server's `.env` is never touched.

Required repository secrets:
- `DEPLOY_SSH_KEY`: private SSH key authorized for root on the server
- `DEPLOY_KNOWN_HOSTS`: the server's host key line(s), so the runner can verify it

## Languages

English and Ukrainian. The language follows the browser's `Accept-Language` until the user picks one
(login page or account menu), which is stored in a `lang` cookie.

Strings live in `i18n.py`: English source text is the key, `UK` holds plain translations and
`UK_PLURAL` the three Ukrainian plural forms. Use `_("…")` / `_n("…", "…", n)` in Python and templates,
`t("…")` / `tn("…", "…", n)` in browser scripts. `tests/test_i18n.py` fails if a string has no translation.

## Daily backup

The `backup` service (`backup.py --daemon`) sends a CSV of all active loans to a Telegram user every day
at `BACKUP_TIME` (default 09:00) in `BACKUP_TZ` (default Europe/Kyiv). Set `BACKUP_TELEGRAM_USERNAME`
(the person must have logged in once, with permission for the bot to message them) or `BACKUP_CHAT_ID`
in the server's `.env`; without either the service stays idle. Failed sends are retried with backoff.

Send one right away: `docker compose -p ledger exec backup python backup.py --now`.
Print the CSV instead: `python backup.py --stdout`.

## Person page

Clicking a person on the home page opens `/people/<id>`: everything you lent them and borrowed from them
on one screen, with the same return/clear actions and shortcuts to record new loans with them. Only
available for people you have loans with.

## Clearing returned records

Each person can delete returned records with someone from their own ledger (trash icon next to the
person). The other side keeps their copy, so marking a loan returned and deleting it can't erase it for
the lender; rows cleared by both sides are deleted from the database.
