import hashlib
import hmac
import time

import app as ledger


def signed(payload, token="123:test-token"):
    check = "\n".join(f"{k}={payload[k]}" for k in sorted(payload))
    digest = hmac.new(hashlib.sha256(token.encode()).digest(), check.encode(), hashlib.sha256).hexdigest()
    return {**payload, "hash": digest}


def tg_payload(**extra):
    return {"id": "42", "first_name": "Bob", "username": "bob", "auth_date": str(int(time.time())), **extra}


def lend(client, other="@bob", card="bolt-m10", finish="nonfoil", direction="lent", quantity="1"):
    return client.post("/loans/new", follow_redirects=True, data={
        "direction": direction, "other": other, "scryfall_id": card, "finish": finish, "quantity": quantity,
    })


# --- auth -------------------------------------------------------------------

def test_requires_login(make_client):
    assert make_client().get("/").headers["Location"] == "/login"


def test_telegram_login_valid(make_client, query):
    client = make_client()
    resp = client.get("/auth/telegram", query_string=signed(tg_payload()))
    assert resp.headers["Location"] == "/"
    assert client.get("/").status_code == 200
    assert query("SELECT telegram_id, username, first_name FROM users") == [(42, "bob", "Bob")]


def test_telegram_login_tampered(make_client):
    payload = signed(tg_payload())
    payload["first_name"] = "Eve"
    resp = make_client().get("/auth/telegram", query_string=payload)
    assert resp.headers["Location"] == "/login"


def test_telegram_login_wrong_token(make_client):
    resp = make_client().get("/auth/telegram", query_string=signed(tg_payload(), token="999:other"))
    assert resp.headers["Location"] == "/login"


def test_telegram_login_expired(make_client):
    old = str(int(time.time()) - ledger.AUTH_MAX_AGE - 60)
    resp = make_client().get("/auth/telegram", query_string=signed(tg_payload(auth_date=old)))
    assert resp.headers["Location"] == "/login"


def test_placeholder_claimed_on_first_login(make_client, query):
    alice = make_client("alice")
    assert b"Loan recorded" in lend(alice, other="@Bob").data
    bob = make_client()
    bob.get("/auth/telegram", query_string=signed(tg_payload()))
    assert query("SELECT telegram_id FROM users WHERE lower(username) = 'bob'") == [(42,)]
    assert query("SELECT count(*) FROM users") == [(2,)]
    page = bob.get("/").data
    assert b"Lightning Bolt" in page and b"@alice" in page


def test_username_change_merges_placeholder(make_client, query):
    bob = make_client()
    bob.get("/auth/telegram", query_string=signed(tg_payload(username="bob_old")))
    lend(make_client("alice"), other="@bob_new")
    bob.get("/auth/telegram", query_string=signed(tg_payload(username="bob_new")))
    assert query("SELECT count(*) FROM users") == [(2,)]
    assert b"Lightning Bolt" in bob.get("/").data


# --- loans ------------------------------------------------------------------

def test_create_loan_stores_card_and_ledger(make_client, query):
    alice = make_client("alice")
    resp = lend(alice, finish="foil", quantity="2")
    assert b"Loan recorded" in resp.data
    assert query("SELECT name, set_code, finish, image_url FROM cards") == [
        ("Lightning Bolt", "m10", "foil", "https://img/bolt.jpg")]
    assert query("SELECT quantity, status FROM loans") == [(2, "borrowed")]
    assert b"Lent to others" in resp.data and b"@bob" in resp.data


def test_borrowed_direction(make_client, query):
    lend(make_client("alice"), direction="borrowed")
    assert query("""SELECT lu.username, bu.username FROM loans
                    JOIN users lu ON lu.id = lender_id JOIN users bu ON bu.id = borrower_id""") == [("bob", "alice")]


def test_card_row_reused(make_client, query, fake_scryfall):
    alice = make_client("alice")
    lend(alice)
    lend(alice, other="@carol")
    assert query("SELECT count(*) FROM cards") == [(1,)]
    assert fake_scryfall == ["bolt-m10"]


def test_same_printing_different_finish_is_different_card(make_client, query):
    alice = make_client("alice")
    lend(alice, card="sol-ring-cmm", finish="nonfoil")
    lend(alice, card="sol-ring-cmm", finish="etched")
    assert query("SELECT finish, image_url FROM cards ORDER BY id") == [
        ("nonfoil", "https://img/sol.jpg"), ("etched", "https://img/sol.jpg")]


def test_rejects_unavailable_finish(make_client, query):
    assert b"come in etched" in lend(make_client("alice"), finish="etched").data
    assert query("SELECT count(*) FROM loans") == [(0,)]


def test_rejects_unknown_card(make_client):
    assert b"not found" in lend(make_client("alice"), card="nope").data


def test_scryfall_down(make_client, monkeypatch):
    def down(_id):
        raise ledger.ScryfallUnavailable
    monkeypatch.setattr(ledger, "scryfall_card", down)
    assert b"reach Scryfall" in lend(make_client("alice")).data


def test_rejects_self_loan(make_client, query):
    assert b"yourself" in lend(make_client("alice"), other="@ALICE").data
    assert query("SELECT count(*) FROM loans") == [(0,)]


def test_rejects_bad_quantity(make_client):
    assert b"Quantity" in lend(make_client("alice"), quantity="0").data


# --- status changes & permissions ------------------------------------------

def test_return_and_reopen_by_other_party(make_client, query):
    lend(make_client("alice"))
    bob = make_client("bob")
    assert bob.post("/loans/1/return").status_code == 302
    assert query("SELECT status, returned_at IS NOT NULL FROM loans") == [("returned", True)]
    assert b"Lightning Bolt" not in bob.get("/").data
    assert b"Returned" in bob.get("/?show=all").data
    bob.post("/loans/1/reopen")
    assert query("SELECT status, returned_at FROM loans") == [("borrowed", None)]


def test_only_creator_can_delete(make_client, query):
    alice = make_client("alice")
    lend(alice)
    assert make_client("bob").post("/loans/1/delete").status_code == 403
    assert alice.post("/loans/1/delete").status_code == 302
    assert query("SELECT count(*) FROM loans") == [(0,)]


def test_strangers_get_404(make_client):
    lend(make_client("alice"))
    carol = make_client("carol")
    for action in ("return", "reopen", "delete"):
        assert carol.post(f"/loans/1/{action}").status_code == 404


def test_logout(make_client):
    alice = make_client("alice")
    alice.post("/logout")
    assert alice.get("/").status_code == 302


def test_login_page_has_redirect_fallback(make_client):
    from urllib.parse import parse_qs, urlparse
    page = make_client().get("/login", base_url="https://ledger.example").get_data(as_text=True)
    href = page.split('class="alt-login"><a href="')[1].split('"')[0].replace("&amp;", "&")
    url = urlparse(href)
    assert url.netloc == "oauth.telegram.org" and url.path == "/auth"
    assert parse_qs(url.query) == {"bot_id": ["123"], "origin": ["https://ledger.example"],
                                   "request_access": ["write"], "return_to": ["https://ledger.example/login"]}
