import hashlib
import hmac
import time

import psycopg
from werkzeug.datastructures import MultiDict

import app as ledger


def report(client, cards=(("bolt-m10", "nonfoil", "1"),)):
    data = MultiDict([("direction", "missing"), ("other", "")])
    for sid, finish, qty in cards:
        data.add("scryfall_id", sid)
        data.add("finish", finish)
        data.add("quantity", qty)
    return client.post("/loans/new", data=data, follow_redirects=True)


def trade(a, b_username):
    """Record a loan from client `a` to `b_username`, which makes them contacts."""
    resp = a.post("/loans/new", data={"direction": "lent", "other": b_username, "scryfall_id": "sol-ring-cmm",
                                      "finish": "nonfoil", "quantity": "1"})
    assert resp.status_code == 302


def missing_rows(query):
    return query("SELECT u.username, c.name, m.quantity FROM missing_cards m JOIN users u ON u.id = m.owner_id "
                 "JOIN cards c ON c.id = m.card_id ORDER BY m.id")


def test_report_missing_needs_no_person(make_client, query):
    resp = report(make_client("kolya"), [("bolt-m10", "nonfoil", "2"), ("sol-ring-cmm", "foil", "1")])
    assert b"Added 3 cards to Missing" in resp.data
    assert missing_rows(query) == [("kolya", "Lightning Bolt", 2), ("kolya", "Sol Ring", 1)]
    assert query("SELECT count(*) FROM loans") == [(0,)]
    assert query("SELECT count(*) FROM users") == [(1,)]  # no placeholder person created


def test_reporting_the_same_card_again_adds_up(make_client, query):
    kolya = make_client("kolya")
    report(kolya, [("bolt-m10", "nonfoil", "1")])
    report(kolya, [("bolt-m10", "nonfoil", "2")])
    assert missing_rows(query) == [("kolya", "Lightning Bolt", 3)]


def test_report_missing_validates_cards(make_client, query):
    resp = report(make_client("kolya"), [("bolt-m10", "etched", "1")])
    assert b"doesn&#39;t come in etched foil" in resp.data
    assert missing_rows(query) == []


def test_contacts_see_the_board_strangers_dont(make_client):
    kolya = make_client("kolya")
    report(kolya)
    trade(kolya, "petya")
    page = make_client("petya").get("/missing").data.decode()
    assert "Lightning Bolt" in page and "@kolya" in page and "/missing/1/claim" in page
    assert "Lightning Bolt" not in make_client("stranger").get("/missing").data.decode()
    # The owner sees their own cards with "Found it" instead.
    own = make_client("kolya").get("/missing").data.decode()
    assert "/missing/1/found" in own and "/missing/1/claim" not in own


def test_contacts_outlive_cleared_loans(make_client, query):
    kolya, petya = make_client("kolya"), make_client("petya")
    trade(kolya, "petya")
    report(kolya)
    kolya.post("/loans/1/return")
    kolya.post("/people/2/clear-returned", data={"direction": "lent"})
    petya.post("/people/1/clear-returned", data={"direction": "borrowed"})
    assert query("SELECT count(*) FROM loans") == [(0,)]
    assert b"Lightning Bolt" in petya.get("/missing").data


def test_claim_turns_it_into_a_loan(make_client, query):
    kolya = make_client("kolya")
    report(kolya)
    trade(kolya, "petya")
    petya = make_client("petya")
    resp = petya.post("/missing/1/claim", follow_redirects=True)
    assert b"Lightning Bolt from @kolya is now in your ledger" in resp.data
    assert missing_rows(query) == []
    assert query("""SELECT c.name, lu.username, bu.username, cu.username, l.quantity, l.status FROM loans l
                    JOIN cards c ON c.id = l.card_id
                    JOIN users lu ON lu.id = l.lender_id JOIN users bu ON bu.id = l.borrower_id
                    JOIN users cu ON cu.id = l.created_by WHERE c.name = 'Lightning Bolt'""") == [
        ("Lightning Bolt", "kolya", "petya", "petya", 1, "borrowed")]
    assert b"Lightning Bolt" in make_client("kolya").get("/").data


def test_partial_claim_leaves_the_rest_on_the_board(make_client, query):
    kolya = make_client("kolya")
    report(kolya, [("bolt-m10", "nonfoil", "4")])
    trade(kolya, "petya")
    make_client("petya").post("/missing/1/claim", data={"quantity": "3"})
    assert missing_rows(query) == [("kolya", "Lightning Bolt", 1)]
    assert query("SELECT l.quantity FROM loans l JOIN cards c ON c.id = l.card_id WHERE c.name = 'Lightning Bolt'") == [(3,)]


def test_cannot_claim_your_own_card(make_client, query):
    kolya = make_client("kolya")
    report(kolya)
    assert kolya.post("/missing/1/claim").status_code == 400
    assert missing_rows(query) == [("kolya", "Lightning Bolt", 1)]
    assert query("SELECT count(*) FROM loans") == [(0,)]


def test_strangers_cannot_claim(make_client, query):
    report(make_client("kolya"))
    assert make_client("stranger").post("/missing/1/claim").status_code == 404
    assert missing_rows(query) == [("kolya", "Lightning Bolt", 1)]


def test_hide_and_show_a_person(make_client, query):
    kolya, petya = make_client("kolya"), make_client("petya")
    report(kolya)
    trade(kolya, "petya")
    assert b"Hidden people" not in petya.get("/missing").data
    petya.post("/missing/people/1/hide")
    page = petya.get("/missing").data.decode()
    assert "Lightning Bolt" not in page and "Hidden people" in page and "/missing/people/1/show" in page
    assert petya.post("/missing/1/claim").status_code == 404  # hidden cards can't be claimed either
    assert b"Lightning Bolt" in kolya.get("/missing").data  # hiding is one-sided
    petya.post("/missing/people/1/show")
    assert b"Lightning Bolt" in petya.get("/missing").data


def test_cannot_hide_a_non_contact(make_client):
    make_client("kolya")
    assert make_client("stranger").post("/missing/people/1/hide").status_code == 404


def test_contacts_are_backfilled_from_existing_loans(make_client, query):
    trade(make_client("kolya"), "petya")
    with psycopg.connect(ledger.DATABASE_URL) as db:
        db.execute("DELETE FROM contacts")
    ledger.init_db()
    assert query("SELECT user_id, contact_id FROM contacts ORDER BY 1") == [(1, 2), (2, 1)]


def test_placeholder_merge_keeps_contacts(make_client, query):
    # petya logs in as @petya_old, kolya records a loan with the placeholder @petya_new, petya renames.
    token = "123:test-token"
    def signed(username):
        payload = {"id": "42", "first_name": "Petya", "username": username, "auth_date": str(int(time.time()))}
        check = "\n".join(f"{k}={payload[k]}" for k in sorted(payload))
        return {**payload, "hash": hmac.new(hashlib.sha256(token.encode()).digest(), check.encode(),
                                            hashlib.sha256).hexdigest()}
    petya = make_client()
    petya.get("/auth/telegram", query_string=signed("petya_old"))
    kolya = make_client("kolya")
    report(kolya)
    trade(kolya, "petya_new")
    petya.get("/auth/telegram", query_string=signed("petya_new"))
    assert b"Lightning Bolt" in petya.get("/missing").data


def test_owner_marks_found(make_client, query):
    kolya = make_client("kolya")
    report(kolya, [("bolt-m10", "nonfoil", "3")])
    kolya.post("/missing/1/found", data={"quantity": "1"})
    assert missing_rows(query) == [("kolya", "Lightning Bolt", 2)]
    kolya.post("/missing/1/found")
    assert missing_rows(query) == []


def test_only_the_owner_can_mark_found(make_client, query):
    kolya = make_client("kolya")
    report(kolya)
    trade(kolya, "petya")
    assert make_client("petya").post("/missing/1/found").status_code == 403
    assert missing_rows(query) == [("kolya", "Lightning Bolt", 1)]


def test_unknown_missing_card_is_404(make_client):
    client = make_client("petya")
    assert client.post("/missing/5/claim").status_code == 404
    assert client.post("/missing/99999999999999999999/found").status_code == 404


def test_board_requires_login(make_client):
    assert make_client().get("/missing").headers["Location"].startswith("/login")
