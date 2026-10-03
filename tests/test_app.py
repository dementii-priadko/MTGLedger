import hashlib
import hmac
import time

from werkzeug.datastructures import MultiDict

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
    assert fake_scryfall == [["bolt-m10"]]


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
    assert b"found on Scryfall" in lend(make_client("alice"), card="nope").data


def test_scryfall_down(make_client, monkeypatch):
    def down(_ids):
        raise ledger.ScryfallUnavailable
    monkeypatch.setattr(ledger, "scryfall_cards", down)
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
                                   "request_access": ["write"], "return_to": ["https://ledger.example/login"],
                                   "lang": ["en"]}


# --- home page summary & grouping -------------------------------------------

def test_summary_counts_outstanding_cards_and_people(make_client):
    alice = make_client("alice")
    lend(alice, other="@bob", quantity="2")
    lend(alice, other="@carol", card="sol-ring-cmm")
    lend(alice, other="@dave", direction="borrowed", quantity="3")
    alice.post("/loans/2/return")
    page = alice.get("/").get_data(as_text=True)
    assert "cards with 1 person" in page        # carol's loan was returned
    assert "cards from 1 person" in page
    assert page.count('class="tile ') == 2       # returned one hidden by default
    assert alice.get("/?show=all").get_data(as_text=True).count('class="tile ') == 3


def test_loans_grouped_per_person_with_action_labels(make_client):
    alice = make_client("alice")
    lend(alice, other="@bob")
    lend(alice, other="@bob", card="sol-ring-cmm")
    lend(alice, other="@carol", direction="borrowed")
    page = alice.get("/").get_data(as_text=True)
    assert page.count('class="person-group"') == 2
    assert "2 cards out" in page
    assert "Got it back" in page and "Gave it back" in page


def test_new_loan_prefill_from_query(make_client):
    page = make_client("alice").get("/loans/new?direction=borrowed&other=bob").get_data(as_text=True)
    assert 'value="borrowed" checked' in page
    assert 'value="bob"' in page


def test_new_loan_redirects_to_matching_tab(make_client):
    resp = make_client("alice").post("/loans/new", data={
        "direction": "borrowed", "other": "@bob", "scryfall_id": "bolt-m10", "finish": "nonfoil", "quantity": "1"})
    assert resp.headers["Location"] == "/#borrowed"


# --- multi-card loans -------------------------------------------------------

def lend_many(client, cards, other="@bob", direction="lent"):
    """cards: [(scryfall_id, finish, quantity), ...]"""
    data = [("direction", direction), ("other", other)]
    for sid, finish, qty in cards:
        data += [("scryfall_id", sid), ("finish", finish), ("quantity", str(qty))]
    return client.post("/loans/new", data=MultiDict(data), follow_redirects=True)


def test_multi_card_loan_creates_one_row_per_card_with_shared_batch(make_client, query, fake_scryfall):
    resp = lend_many(make_client("alice"), [("bolt-m10", "foil", 2), ("sol-ring-cmm", "etched", 1)])
    assert b"Loan recorded: 3 cards" in resp.data
    rows = query("""SELECT c.name, c.finish, l.quantity, l.batch_id FROM loans l JOIN cards c ON c.id = l.card_id
                    ORDER BY l.id""")
    assert [r[:3] for r in rows] == [("Lightning Bolt", "foil", 2), ("Sol Ring", "etched", 1)]
    assert rows[0][3] is not None and rows[0][3] == rows[1][3]
    assert fake_scryfall == [["bolt-m10", "sol-ring-cmm"]]  # one batched lookup


def test_separate_loans_get_different_batches(make_client, query):
    alice = make_client("alice")
    lend(alice)
    lend(alice, card="sol-ring-cmm")
    assert query("SELECT count(DISTINCT batch_id) FROM loans") == [(2,)]


def test_duplicate_lines_are_merged(make_client, query):
    lend_many(make_client("alice"), [("bolt-m10", "nonfoil", 2), ("bolt-m10", "nonfoil", 3), ("bolt-m10", "foil", 1)])
    assert query("""SELECT c.finish, l.quantity FROM loans l JOIN cards c ON c.id = l.card_id
                    ORDER BY c.finish""") == [("foil", 1), ("nonfoil", 5)]


def test_multi_card_loan_is_all_or_nothing(make_client, query):
    resp = lend_many(make_client("alice"), [("bolt-m10", "nonfoil", 1), ("bolt-m10", "etched", 1)])
    assert b"Lightning Bolt (M10) doesn" in resp.data
    assert query("SELECT count(*) FROM loans") == [(0,)]
    assert query("SELECT count(*) FROM cards") == [(0,)]


def test_loan_needs_at_least_one_card(make_client):
    assert b"Add at least one card" in lend_many(make_client("alice"), []).data


def test_loan_card_limit(make_client):
    cards = [(f"id-{i}", "nonfoil", 1) for i in range(ledger.MAX_CARDS_PER_LOAN + 1)]
    assert b"at most" in lend_many(make_client("alice"), cards).data


def test_scryfall_cards_skips_non_uuid_ids(monkeypatch):
    monkeypatch.setattr(ledger.urllib.request, "urlopen", lambda *a, **k: (_ for _ in ()).throw(AssertionError))
    assert ledger.scryfall_cards(["not-a-uuid"]) == {}


# --- partial & multi-card returns -------------------------------------------

def loan_rows(query):
    return query("""SELECT c.name, l.quantity, l.status, l.batch_id IS NOT NULL FROM loans l
                    JOIN cards c ON c.id = l.card_id ORDER BY l.id""")


def test_partial_return_splits_row(make_client, query):
    alice = make_client("alice")
    lend_many(alice, [("bolt-m10", "nonfoil", 4)])
    resp = alice.post("/loans/1/return", data={"quantity": "3"}, follow_redirects=True)
    assert b"Marked 3 cards as returned" in resp.data
    assert loan_rows(query) == [("Lightning Bolt", 1, "borrowed", True), ("Lightning Bolt", 3, "returned", True)]
    same = query("""SELECT count(DISTINCT (created_at, batch_id, card_id, lender_id, borrower_id, created_by))
                    FROM loans""")
    assert same == [(1,)]  # the returned part keeps the original loan's identity
    page = alice.get("/").get_data(as_text=True)
    assert '<span class="stat-value">1</span>' in page and "card with 1 person" in page


def test_return_without_quantity_returns_everything(make_client, query):
    alice = make_client("alice")
    lend(alice, quantity="4")
    alice.post("/loans/1/return")
    assert [r[1:3] for r in loan_rows(query)] == [(4, "returned")]


def test_return_quantity_is_clamped(make_client, query):
    alice = make_client("alice")
    lend(alice, quantity="2")
    alice.post("/loans/1/return", data={"quantity": "10"})
    assert [r[1:3] for r in loan_rows(query)] == [(2, "returned")]
    assert alice.post("/loans/1/return", data={"quantity": "abc"}).status_code == 400


def test_undo_partial_return_merges_back(make_client, query):
    alice = make_client("alice")
    lend(alice, quantity="4")
    alice.post("/loans/1/return", data={"quantity": "1"})
    alice.post("/loans/1/return", data={"quantity": "2"})
    assert [r[1:3] for r in loan_rows(query)] == [(1, "borrowed"), (1, "returned"), (2, "returned")]
    alice.post("/loans/3/reopen")
    assert [r[1:3] for r in loan_rows(query)] == [(3, "borrowed"), (1, "returned")]
    alice.post("/loans/1/return")      # rest comes back...
    alice.post("/loans/1/reopen")      # ...undo with no sibling left: row simply reopens
    assert [r[1:3] for r in loan_rows(query)] == [(3, "borrowed"), (1, "returned")]


def test_partial_return_of_legacy_row_gets_batch(make_client, query):
    alice = make_client("alice")
    lend(alice, quantity="3")
    query("UPDATE loans SET batch_id = NULL RETURNING id")
    alice.post("/loans/1/return", data={"quantity": "1"})
    assert query("SELECT count(DISTINCT batch_id), count(*) FROM loans WHERE batch_id IS NOT NULL") == [(1, 2)]
    alice.post("/loans/2/reopen")
    assert [r[1:3] for r in loan_rows(query)] == [(3, "borrowed")]


def test_return_several_at_once(make_client, query):
    alice = make_client("alice")
    lend_many(alice, [("bolt-m10", "nonfoil", 4), ("sol-ring-cmm", "foil", 1), ("bolt-m10", "foil", 2)])
    bob = make_client("bob")
    resp = bob.post("/returns", data=MultiDict([("loan_id", "1"), ("qty_1", "2"), ("loan_id", "2")]),
                    follow_redirects=True)
    assert b"Marked 3 cards as returned" in resp.data
    assert query("""SELECT c.name, c.finish, l.quantity, l.status FROM loans l JOIN cards c ON c.id = l.card_id
                    ORDER BY l.id""") == [
        ("Lightning Bolt", "nonfoil", 2, "borrowed"),
        ("Sol Ring", "foil", 1, "returned"),
        ("Lightning Bolt", "foil", 2, "borrowed"),
        ("Lightning Bolt", "nonfoil", 2, "returned"),
    ]


def test_return_several_is_all_or_nothing(make_client, query):
    lend(make_client("alice"))                      # loan 1: alice -> bob
    lend(make_client("carol"), other="@dave")       # loan 2: not bob's
    bob = make_client("bob")
    assert bob.post("/returns", data=MultiDict([("loan_id", "1"), ("loan_id", "2")])).status_code == 404
    assert query("SELECT status FROM loans ORDER BY id") == [("borrowed",), ("borrowed",)]


def test_return_several_needs_selection(make_client):
    resp = make_client("alice").post("/returns", data={}, follow_redirects=True)
    assert b"Select the cards that came back" in resp.data


# --- clearing returned records ----------------------------------------------

def bob_id(query):
    return query("SELECT id FROM users WHERE username = 'bob'")[0][0]


def test_clear_returned_hides_only_for_me_and_keeps_active(make_client, query):
    alice = make_client("alice")
    lend_many(alice, [("bolt-m10", "nonfoil", 3), ("sol-ring-cmm", "foil", 1)])
    alice.post("/loans/1/return", data={"quantity": "1"})   # row 3: 1 Bolt returned, 2 still out
    alice.post("/loans/2/return")                           # Sol Ring returned
    page = alice.get("/?show=all").get_data(as_text=True)
    assert "Delete 2 returned records" in page

    resp = alice.post(f"/people/{bob_id(query)}/clear-returned", data={"direction": "lent"}, follow_redirects=True)
    assert b"Deleted 2 returned records." in resp.data
    page = alice.get("/?show=all").get_data(as_text=True)
    assert page.count('class="tile ') == 1 and "Delete 2 returned" not in page   # only the 2 Bolts still out
    # bob still sees the full history
    assert make_client("bob").get("/?show=all").get_data(as_text=True).count('class="tile ') == 3
    assert query("SELECT count(*) FROM loans") == [(3,)]


def test_rows_cleared_by_both_sides_are_deleted(make_client, query):
    alice = make_client("alice")
    lend(alice)
    alice.post("/loans/1/return")
    bob = make_client("bob")
    alice_id = query("SELECT id FROM users WHERE username = 'alice'")[0][0]
    alice.post(f"/people/{bob_id(query)}/clear-returned", data={"direction": "lent"})
    assert query("SELECT count(*) FROM loans") == [(1,)]
    bob.post(f"/people/{alice_id}/clear-returned", data={"direction": "borrowed"})
    assert query("SELECT count(*) FROM loans") == [(0,)]


def test_clearing_cannot_touch_active_loans_or_other_people(make_client, query):
    lend(make_client("alice"))                                # active alice -> bob
    lend(make_client("carol"), other="@bob")                  # carol -> bob
    make_client("carol").post("/loans/2/return")
    bob = make_client("bob")
    alice_id = query("SELECT id FROM users WHERE username = 'alice'")[0][0]
    bob.post(f"/people/{alice_id}/clear-returned", data={"direction": "borrowed"})
    assert query("SELECT id, hidden_by_lender, hidden_by_borrower FROM loans ORDER BY id") == [
        (1, False, False), (2, False, False)]
    assert bob.post(f"/people/{alice_id}/clear-returned", data={"direction": "sideways"}).status_code == 400


def test_undo_by_other_side_brings_cleared_record_back(make_client, query):
    alice = make_client("alice")
    lend(alice)
    alice.post("/loans/1/return")
    alice.post(f"/people/{bob_id(query)}/clear-returned", data={"direction": "lent"})
    make_client("bob").post("/loans/1/reopen")               # bob says: actually I still have it
    assert query("SELECT status, hidden_by_lender FROM loans") == [("borrowed", False)]
    assert "Lightning Bolt" in alice.get("/").get_data(as_text=True)


# --- person page ------------------------------------------------------------

def user_id(query, username):
    return query("SELECT id FROM users WHERE username = %s", (username,))[0][0]


def test_person_page_shows_both_directions(make_client, query):
    alice = make_client("alice")
    lend_many(alice, [("bolt-m10", "foil", 2)], other="@bob")
    lend_many(alice, [("sol-ring-cmm", "etched", 1)], other="@bob", direction="borrowed")
    lend(alice, other="@carol", card="sol-ring-cmm")                     # someone else: not on bob's page
    page = alice.get(f"/people/{user_id(query, 'bob')}").get_data(as_text=True)
    assert "You lent to @bob" in page and "You borrowed from @bob" in page
    assert page.count('class="tile ') == 2
    assert "Lightning Bolt" in page and "Sol Ring" in page
    assert 'href="https://t.me/bob"' in page
    assert "/loans/new?direction=lent&amp;other=bob" in page and "/loans/new?direction=borrowed&amp;other=bob" in page


def test_person_page_returned_toggle_and_clear(make_client, query):
    alice = make_client("alice")
    lend(alice, quantity="3")
    alice.post("/loans/1/return", data={"quantity": "1"})
    pid = user_id(query, "bob")
    assert alice.get(f"/people/{pid}").get_data(as_text=True).count('class="tile ') == 1
    page = alice.get(f"/people/{pid}?show=all").get_data(as_text=True)
    assert page.count('class="tile ') == 2 and "Delete 1 returned record" in page
    alice.post(f"/people/{pid}/clear-returned", data={"direction": "lent"})
    assert alice.get(f"/people/{pid}?show=all").get_data(as_text=True).count('class="tile ') == 1


def test_person_page_only_for_people_you_dealt_with(make_client, query):
    lend(make_client("alice"))                                   # alice <-> bob
    make_client("carol")
    carol = make_client("carol")
    assert carol.get(f"/people/{user_id(query, 'bob')}").status_code == 404
    assert carol.get("/people/99999").status_code == 404
    alice = make_client("alice")
    assert alice.get(f"/people/{user_id(query, 'alice')}").headers["Location"] == "/"


def test_person_page_survives_clearing_everything(make_client, query):
    alice = make_client("alice")
    lend(alice)
    alice.post("/loans/1/return")
    pid = user_id(query, "bob")
    alice.post(f"/people/{pid}/clear-returned", data={"direction": "lent"})
    page = alice.get(f"/people/{pid}")
    assert page.status_code == 200 and b"Nothing lent to @bob right now." in page.data


def test_home_links_to_person_page(make_client, query):
    alice = make_client("alice")
    lend(alice)
    assert f'href="/people/{user_id(query, "bob")}"' in alice.get("/").get_data(as_text=True)


def test_person_page_has_search(make_client, query):
    alice = make_client("alice")
    lend(alice)
    page = alice.get(f"/people/{user_id(query, 'bob')}").get_data(as_text=True)
    assert 'id="filter"' in page and "Search cards or sets" in page
    assert page.count("data-filter-scope") == 2 and 'class="no-matches' in page


# --- link previews ----------------------------------------------------------

def test_link_preview_tags_for_logged_out_crawler(make_client):
    crawler = make_client()
    headers = {"User-Agent": "TelegramBot (like TwitterBot)"}
    assert crawler.get("/", headers=headers).headers["Location"] == "/login"   # what Telegram follows
    page = crawler.get("/login", headers=headers, base_url="https://ledger.example").get_data(as_text=True)
    assert '<meta property="og:title" content="Card Ledger — who has your cards">' in page
    assert 'property="og:description" content="Keep track of the Magic cards' in page
    assert '<meta property="og:image" content="https://ledger.example/static/og-image.png">' in page
    assert '<meta name="twitter:card" content="summary">' in page
    assert crawler.get("/static/og-image.png").status_code == 200


def test_link_preview_follows_language(make_client):
    page = make_client().get("/login", headers={"Accept-Language": "uk"}).get_data(as_text=True)
    assert 'content="Card Ledger — у кого ваші карти"' in page and 'content="uk_UA"' in page
