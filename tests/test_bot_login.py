"""'Log in with Telegram' via the bot: deep link → bot sends a signed /auth/telegram link → logged in."""
import base64
import time
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest

import app as ledger

SECRET = {"X-Telegram-Bot-Api-Secret-Token": "test-webhook-secret"}
TG_USER = {"id": 4242, "is_bot": False, "first_name": "Olena", "username": "olena", "language_code": "uk"}


def b64(path):
    return base64.urlsafe_b64encode(path.encode()).decode().rstrip("=")


@pytest.fixture
def bot(monkeypatch):
    sent = []
    monkeypatch.setattr(ledger, "telegram_api", lambda method, **params: sent.append((method, params)))
    return sent


def message(text, chat_type="private", user=TG_USER):
    return {"update_id": 1, "message": {"message_id": 1, "from": user, "date": int(time.time()),
                                        "chat": {"id": user["id"], "type": chat_type}, "text": text}}


def login_link_from(bot):
    method, params = bot[-1]
    assert method == "sendMessage"
    [[button]] = params["reply_markup"]["inline_keyboard"]
    return button["url"]


def test_login_page_links_to_bot(make_client):
    html = make_client().get("/login").get_data(as_text=True)
    assert f'href="https://t.me/test_bot?start=login_{b64("/")}"' in html
    html = make_client().get("/login?next=/people/7").get_data(as_text=True)
    assert f"start=login_{b64('/people/7')}" in html
    html = make_client().get("/login?next=//evil.example/x").get_data(as_text=True)
    assert f"start=login_{b64('/')}" in html


def test_protected_pages_remember_where_you_were_going(make_client):
    assert make_client().get("/people/7?show=all").headers["Location"] == "/login?next=/people/7?show%3Dall"
    assert make_client().get("/").headers["Location"] == "/login"


def test_webhook_requires_secret(make_client, bot):
    client = make_client()
    assert client.post("/telegram/webhook", json=message("/start login")).status_code == 403
    assert client.post("/telegram/webhook", json=message("/start login"),
                       headers={"X-Telegram-Bot-Api-Secret-Token": "wrong"}).status_code == 403
    assert bot == []


def test_bot_link_logs_in_and_returns_to_page(make_client, bot, query):
    client = make_client()
    resp = client.post("/telegram/webhook", json=message(f"/start login_{b64('/people/2')}"), headers=SECRET)
    assert resp.status_code == 200
    link = login_link_from(bot)
    assert "Увійти" in str(bot[-1][1])                     # localized from the user's Telegram language
    url = urlsplit(link)
    assert url.path == "/auth/telegram" and parse_qs(url.query)["redirect"] == ["/people/2"]

    browser = make_client()
    resp = browser.get(f"{url.path}?{url.query}")
    assert resp.headers["Location"] == "/people/2"
    assert query("SELECT telegram_id, username, first_name FROM users") == [(4242, "olena", "Olena")]
    assert browser.get("/").status_code == 200


def test_login_command_and_greeting(make_client, bot):
    client = make_client()
    client.post("/telegram/webhook", json=message("/login"), headers=SECRET)
    assert "/auth/telegram?" in login_link_from(bot)
    client.post("/telegram/webhook", json=message("hello"), headers=SECRET)
    assert "/auth/telegram" not in login_link_from(bot)      # just the "Open Card Ledger" button
    client.post("/telegram/webhook", json=message("/login", chat_type="group"), headers=SECRET)
    assert len(bot) == 2                                     # groups are ignored


def test_start_param_cannot_smuggle_external_redirect(make_client, bot):
    make_client().post("/telegram/webhook", json=message(f"/start login_{b64('//evil.example/x')}"), headers=SECRET)
    assert parse_qs(urlsplit(login_link_from(bot)).query)["redirect"] == ["/"]


def test_tampered_or_old_links_are_rejected(make_client, bot):
    make_client().post("/telegram/webhook", json=message("/login"), headers=SECRET)
    params = parse_qs(urlsplit(login_link_from(bot)).query)
    params = {k: v[0] for k, v in params.items()}
    for tampered in ({**params, "redirect": "https://evil.example"}, {**params, "id": "1"}):
        assert make_client().get("/auth/telegram?" + urlencode(tampered)).headers["Location"] == "/login"
    old = {k: v for k, v in params.items() if k != "hash"}
    old["auth_date"] = str(int(time.time()) - 2 * 3600)
    old["hash"] = ledger.telegram_auth_hash(old)
    assert make_client().get("/auth/telegram?" + urlencode(old)).headers["Location"] == "/login"


def test_bot_login_keeps_existing_photo(make_client, bot, query):
    from test_app import signed
    widget = {"id": "4242", "first_name": "Olena", "username": "olena", "photo_url": "https://t.me/i/userpic/1.jpg",
              "auth_date": str(int(time.time()))}
    make_client().get("/auth/telegram", query_string=signed(widget))
    make_client().post("/telegram/webhook", json=message("/login"), headers=SECRET)
    url = urlsplit(login_link_from(bot))
    make_client().get(f"{url.path}?{url.query}")
    assert query("SELECT photo_url FROM users") == [("https://t.me/i/userpic/1.jpg",)]


def test_webhook_survives_bad_updates(make_client, bot):
    client = make_client()
    for update in ({}, {"message": {"chat": {"type": "private"}}}, {"callback_query": {"id": "1"}}):
        assert client.post("/telegram/webhook", json=update, headers=SECRET).status_code == 200
