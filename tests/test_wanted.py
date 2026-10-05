from werkzeug.datastructures import MultiDict

from test_missing import trade


def ask(client, cards=(("bolt-m10", "nonfoil", "1"),), any_printing=None):
    data = MultiDict([("direction", "wanted"), ("other", "")])
    for sid, finish, qty in cards:
        data.add("scryfall_id", sid)
        data.add("finish", finish)
        data.add("quantity", qty)
        if any_printing is not None:
            data.add("any_printing", "1" if any_printing else "0")
    return client.post("/loans/new", data=data, follow_redirects=True)


def wanted_rows(query):
    return query("SELECT u.username, c.name, w.quantity FROM wanted_cards w JOIN users u ON u.id = w.owner_id "
                 "JOIN cards c ON c.id = w.card_id ORDER BY w.id")


def offer_rows(query):
    return query("SELECT o.wanted_id, u.username, o.quantity FROM wanted_offers o JOIN users u ON u.id = o.lender_id "
                 "ORDER BY o.id")


def bolt_loans(query):
    return query("""SELECT lu.username, bu.username, cu.username, l.quantity FROM loans l
                    JOIN cards c ON c.id = l.card_id JOIN users lu ON lu.id = l.lender_id
                    JOIN users bu ON bu.id = l.borrower_id JOIN users cu ON cu.id = l.created_by
                    WHERE c.name = 'Lightning Bolt' ORDER BY l.id""")


def setup(make_client, qty="1"):
    """sasha asks for Lightning Bolts; kolya and petya have traded with sasha."""
    sasha = make_client("sasha")
    ask(sasha, [("bolt-m10", "nonfoil", qty)])
    trade(sasha, "kolya")
    trade(sasha, "petya")
    return sasha, make_client("kolya"), make_client("petya")


def test_ask_needs_no_person_and_adds_up(make_client, query):
    sasha = make_client("sasha")
    assert b"Added 2 cards to your list" in ask(sasha, [("bolt-m10", "nonfoil", "2")]).data
    ask(sasha, [("bolt-m10", "nonfoil", "1")])
    assert wanted_rows(query) == [("sasha", "Lightning Bolt", 3)]
    assert query("SELECT count(*) FROM loans") == [(0,)]


def test_contacts_see_requests_strangers_dont(make_client):
    sasha, kolya, _petya = setup(make_client)
    page = kolya.get("/wanted").data.decode()
    assert "Lightning Bolt" in page and "/wanted/1/offer" in page
    assert "Lightning Bolt" not in make_client("stranger").get("/wanted").data.decode()
    assert "/wanted/1/remove" in sasha.get("/wanted").data.decode()


def test_offer_waits_for_the_requester(make_client, query):
    sasha, kolya, petya = setup(make_client, qty="2")
    assert b"Offer sent. @sasha will see it." in kolya.post("/wanted/1/offer", data={"quantity": "5"},
                                                             follow_redirects=True).data
    assert offer_rows(query) == [(1, "kolya", 2)]  # capped at what was asked for
    assert bolt_loans(query) == []
    # The requester sees it (and the nav dot); the offerer sees their own; other contacts don't.
    own = sasha.get("/wanted").data.decode()
    assert "@kolya" in own and "/offers/1/accept" in own and "offer-dot" in own
    assert "You offered ×2" in kolya.get("/wanted").data.decode()
    assert "@kolya" not in petya.get("/wanted").data.decode()
    # Offering again replaces the quantity.
    kolya.post("/wanted/1/offer", data={"quantity": "1"})
    assert offer_rows(query) == [(1, "kolya", 1)]


def test_accept_records_the_loan(make_client, query):
    sasha, kolya, petya = setup(make_client)
    kolya.post("/wanted/1/offer")
    petya.post("/wanted/1/offer")
    resp = sasha.post("/offers/1/accept", follow_redirects=True)
    assert b"Lightning Bolt from @kolya is now in your ledger" in resp.data
    assert bolt_loans(query) == [("kolya", "sasha", "sasha", 1)]
    assert wanted_rows(query) == [] and offer_rows(query) == []  # fulfilled: the other offer goes too


def test_partial_accept_keeps_the_rest(make_client, query):
    sasha, kolya, petya = setup(make_client, qty="4")
    kolya.post("/wanted/1/offer", data={"quantity": "3"})
    petya.post("/wanted/1/offer", data={"quantity": "2"})
    sasha.post("/offers/1/accept")
    assert wanted_rows(query) == [("sasha", "Lightning Bolt", 1)]
    assert offer_rows(query) == [(1, "petya", 2)]
    sasha.post("/offers/2/accept")  # only 1 still wanted
    assert bolt_loans(query) == [("kolya", "sasha", "sasha", 3), ("petya", "sasha", "sasha", 1)]
    assert wanted_rows(query) == []


def test_decline_and_withdraw(make_client, query):
    sasha, kolya, petya = setup(make_client)
    kolya.post("/wanted/1/offer")
    petya.post("/wanted/1/offer")
    sasha.post("/offers/1/decline")
    petya.post("/wanted/1/withdraw")
    assert offer_rows(query) == [] and bolt_loans(query) == []
    assert wanted_rows(query) == [("sasha", "Lightning Bolt", 1)]


def test_only_the_requester_can_accept_or_decline(make_client, query):
    sasha, kolya, petya = setup(make_client)
    kolya.post("/wanted/1/offer")
    assert kolya.post("/offers/1/accept").status_code == 404
    assert petya.post("/offers/1/decline").status_code == 404
    assert offer_rows(query) == [(1, "kolya", 1)] and bolt_loans(query) == []


def test_who_can_offer(make_client, query):
    sasha, kolya, _petya = setup(make_client)
    assert sasha.post("/wanted/1/offer").status_code == 400
    assert make_client("stranger").post("/wanted/1/offer").status_code == 404
    kolya.post("/board/people/1/hide")
    assert kolya.post("/wanted/1/offer").status_code == 404
    assert make_client("kolya").post("/wanted/99999999999999999999/offer").status_code == 404
    assert offer_rows(query) == []


def test_remove_request(make_client, query):
    sasha, kolya, _petya = setup(make_client)
    kolya.post("/wanted/1/offer")
    assert kolya.post("/wanted/1/remove").status_code == 403
    sasha.post("/wanted/1/remove")
    assert wanted_rows(query) == [] and offer_rows(query) == []


def test_tabs_and_nav(make_client):
    sasha, kolya, _petya = setup(make_client)
    page = kolya.get("/missing").data.decode()
    assert 'href="/wanted"' in page and "Looking for" in page
    assert "offer-dot" not in sasha.get("/missing").data.decode()
    kolya.post("/wanted/1/offer")
    assert 'href="/wanted" class="btn btn-nav"' in sasha.get("/").data.decode()  # nav goes straight to offers


# --- any version ------------------------------------------------------------------------

def loan_printings(query):
    return query("""SELECT c.external_id, c.finish, l.quantity FROM loans l JOIN cards c ON c.id = l.card_id
                    WHERE c.name = 'Lightning Bolt' ORDER BY l.id""")


def test_any_version_requests_merge_by_card_name(make_client, query):
    sasha = make_client("sasha")
    ask(sasha, [("bolt-m10", "nonfoil", "1")], any_printing=True)
    ask(sasha, [("bolt-2xm", "nonfoil", "2")], any_printing=True)  # another printing standing in
    ask(sasha, [("bolt-m10", "nonfoil", "1")], any_printing=False)  # a specific one stays separate
    assert query("SELECT c.external_id, w.quantity, w.any_printing FROM wanted_cards w "
                 "JOIN cards c ON c.id = w.card_id ORDER BY w.id") == [("bolt-m10", 3, True), ("bolt-m10", 1, False)]
    assert "Any version" in sasha.get("/wanted").data.decode()


def test_any_version_is_only_for_requests(make_client, query):
    data = MultiDict([("direction", "lent"), ("other", "bob"), ("scryfall_id", "bolt-m10"), ("finish", "nonfoil"),
                      ("quantity", "1"), ("any_printing", "1")])
    make_client("alice").post("/loans/new", data=data)
    assert loan_printings(query) == [("bolt-m10", "nonfoil", 1)]
    assert query("SELECT count(*) FROM wanted_cards") == [(0,)]


def test_lender_picks_the_version_they_offer(make_client, query):
    sasha = make_client("sasha")
    ask(sasha, [("bolt-m10", "nonfoil", "2")], any_printing=True)
    trade(sasha, "kolya")
    kolya = make_client("kolya")
    page = kolya.get("/wanted").data.decode()
    assert "data-offer-any" in page and 'id="offer-dialog"' in page
    assert kolya.post("/wanted/1/offer", data={"quantity": "2"}).status_code == 400  # no printing chosen
    assert kolya.post("/wanted/1/offer", data={"scryfall_id": "sol-ring-cmm", "finish": "nonfoil"}).status_code == 400
    assert b"doesn&#39;t come in etched foil" in kolya.post(
        "/wanted/1/offer", data={"scryfall_id": "bolt-2xm", "finish": "etched"}, follow_redirects=True).data
    assert offer_rows(query) == []
    kolya.post("/wanted/1/offer", data={"scryfall_id": "bolt-2xm", "finish": "foil", "quantity": "2"})
    assert offer_rows(query) == [(1, "kolya", 2)]
    assert "2XM #129 · Foil" in sasha.get("/wanted").data.decode()
    sasha.post("/offers/1/accept")
    assert loan_printings(query) == [("bolt-2xm", "foil", 2)]


def test_specific_requests_lend_the_requested_printing(make_client, query):
    sasha, kolya, _petya = setup(make_client)
    kolya.post("/wanted/1/offer", data={"scryfall_id": "bolt-2xm", "finish": "foil"})  # ignored
    sasha.post("/offers/1/accept")
    assert loan_printings(query) == [("bolt-m10", "nonfoil", 1)]
