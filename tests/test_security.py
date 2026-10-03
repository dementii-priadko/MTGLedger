"""Attack-style tests: each asserts the secure behavior for one class of vulnerability."""
import time

import pytest
from werkzeug.datastructures import MultiDict

import app as ledger
from test_app import lend, signed

XSS = '<img src=x onerror=alert(1)>"\'><script>alert(2)</script>'


def tg_login(client, **fields):
    payload = {"id": "77", "first_name": "Mallory", "auth_date": str(int(time.time())), **fields}
    return client.get("/auth/telegram", query_string=signed(payload))


# --- XSS ----------------------------------------------------------------------

def test_telegram_profile_fields_are_escaped_everywhere(make_client, query):
    mallory = make_client()
    tg_login(mallory, first_name=XSS, last_name=XSS, username="mallory")
    alice = make_client("alice")
    lend(alice, other="@mallory")
    [(mallory_id,)] = query("SELECT id FROM users WHERE username = 'mallory'")
    pages = [alice.get("/"), alice.get(f"/people/{mallory_id}"), alice.get("/loans/new"), mallory.get("/"),
             mallory.get("/?show=all")]
    for page in pages:
        html = page.get_data(as_text=True)
        assert "<script>alert(2)</script>" not in html and "<img src=x onerror" not in html


def test_reflected_form_values_are_escaped(make_client):
    alice = make_client("alice")
    resp = alice.post("/loans/new", data={"direction": "lent", "other": "@bob", "cart_json": XSS,
                                          "scryfall_id": "nope", "finish": "nonfoil", "quantity": "1"})
    html = resp.get_data(as_text=True)
    assert "<script>alert(2)</script>" not in html
    html = alice.get("/loans/new", query_string={"other": XSS, "direction": XSS}).get_data(as_text=True)
    assert "<script>alert(2)</script>" not in html and "<img src=x onerror" not in html


# --- request forgery ------------------------------------------------------------

def test_cross_origin_posts_are_rejected(make_client, query):
    alice = make_client("alice")
    lend(alice)
    for origin in ("https://evil.example", "https://mtg.beznadijka.dev", "null"):
        resp = alice.post("/loans/1/return", headers={"Origin": origin})
        assert resp.status_code == 403, origin
    resp = alice.post("/loans/1/return", headers={"Referer": "https://evil.example/page"})
    assert resp.status_code == 403
    assert query("SELECT status FROM loans") == [("borrowed",)]
    # Same-origin requests (and ones without Origin/Referer, e.g. strict privacy settings) still work.
    assert alice.post("/loans/1/return", headers={"Origin": "http://localhost"}).status_code == 302
    alice.post("/loans/1/reopen")
    assert query("SELECT status FROM loans") == [("borrowed",)]


def test_no_open_redirect_via_referer(make_client):
    alice = make_client("alice")
    lend(alice)
    resp = alice.post("/loans/1/return", headers={"Referer": "https://evil.example/phish"})
    assert "evil.example" not in resp.headers.get("Location", "")
    resp = alice.post("/loans/1/reopen", headers={"Referer": "http://localhost/people/2?show=all"})
    assert resp.headers["Location"] == "http://localhost/people/2?show=all"


def test_forwarded_host_is_not_trusted(make_client):
    html = make_client().get("/login", headers={"X-Forwarded-Host": "evil.example"}).get_data(as_text=True)
    assert "evil.example" not in html


# --- auth ---------------------------------------------------------------------------

def test_old_telegram_payloads_cannot_be_replayed(make_client):
    old = str(int(time.time()) - 2 * 3600)    # e.g. a login URL copied from logs or browser history
    client = make_client()
    resp = client.get("/auth/telegram", query_string=signed({"id": "77", "first_name": "M", "auth_date": old}))
    assert resp.headers["Location"] == "/login"


def test_session_cookie_flags():
    assert ledger.app.config["SESSION_COOKIE_HTTPONLY"] and ledger.app.config["SESSION_COOKIE_SAMESITE"] == "Lax"


# --- robustness ------------------------------------------------------------------

@pytest.mark.parametrize("path, method", [
    ("/people/99999999999999999999", "get"),
    ("/loans/99999999999999999999/return", "post"),
    ("/loans/99999999999999999999/delete", "post"),
])
def test_out_of_range_ids_are_404_not_500(make_client, path, method):
    assert getattr(make_client("alice"), method)(path).status_code == 404


def test_out_of_range_ids_in_bulk_return(make_client):
    resp = make_client("alice").post("/returns", data=MultiDict([("loan_id", "99999999999999999999")]))
    assert resp.status_code == 404


# --- headers ------------------------------------------------------------------------

def test_security_headers(make_client):
    resp = make_client().get("/login", base_url="https://ledger.example")
    h = resp.headers
    csp = h.get("Content-Security-Policy", "")
    assert "frame-ancestors 'none'" in csp and "script-src" in csp and "'unsafe-inline'" not in csp.split("script-src")[1].split(";")[0]
    assert h.get("X-Content-Type-Options") == "nosniff"
    assert h.get("Referrer-Policy") == "strict-origin-when-cross-origin"
    assert "max-age=" in h.get("Strict-Transport-Security", "")
    assert h.get("X-Frame-Options") == "DENY"
