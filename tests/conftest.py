import os

# Must be set before the app module is imported (it reads config and creates the schema at import).
os.environ.setdefault("DATABASE_URL", "postgresql://ledger:ledger@localhost:5433/ledger_test")
os.environ["TELEGRAM_BOT_TOKEN"] = "123:test-token"
os.environ["TELEGRAM_BOT_USERNAME"] = "test_bot"
os.environ["DEV_LOGIN"] = "1"
os.environ["TELEGRAM_WEBHOOK_SECRET"] = "test-webhook-secret"
os.environ["ADMIN_TELEGRAM_IDS"] = "8017026444"
os.environ["SECRET_KEY"] = "test"

import psycopg
import pytest

import app as ledger

PRINTINGS = {
    "bolt-m10": {"object": "card", "id": "bolt-m10", "name": "Lightning Bolt", "set": "m10",
                 "set_name": "Magic 2010", "collector_number": "146", "finishes": ["nonfoil", "foil"],
                 "image_uris": {"normal": "https://img/bolt.jpg"}},
    "bolt-2xm": {"object": "card", "id": "bolt-2xm", "name": "Lightning Bolt", "set": "2xm",
                 "set_name": "Double Masters", "collector_number": "129", "finishes": ["nonfoil", "foil"],
                 "image_uris": {"normal": "https://img/bolt2.jpg"}},
    "sol-ring-cmm": {"object": "card", "id": "sol-ring-cmm", "name": "Sol Ring", "set": "cmm",
                     "set_name": "Commander Masters", "collector_number": "410",
                     "finishes": ["nonfoil", "foil", "etched"],
                     "card_faces": [{"image_uris": {"normal": "https://img/sol.jpg"}}]},
}


@pytest.fixture(autouse=True)
def clean_db():
    with psycopg.connect(ledger.DATABASE_URL) as db:
        db.execute("TRUNCATE loans, missing_cards, wanted_offers, wanted_cards, contacts, cards, users RESTART IDENTITY CASCADE")
    yield


@pytest.fixture(autouse=True)
def fake_scryfall(monkeypatch):
    calls = []

    def fake(scryfall_ids):
        calls.append(list(scryfall_ids))
        return {sid: PRINTINGS[sid] for sid in scryfall_ids if sid in PRINTINGS}

    monkeypatch.setattr(ledger, "scryfall_cards", fake)
    return calls


@pytest.fixture
def make_client():
    def make(username=None):
        client = ledger.app.test_client()
        if username:
            assert client.post("/auth/dev", data={"username": username}).status_code == 302
        return client
    return make


@pytest.fixture
def query():
    def run(sql, params=()):
        with psycopg.connect(ledger.DATABASE_URL) as db:
            return db.execute(sql, params).fetchall()
    return run
