import re
import string
from pathlib import Path

import pytest

import app as ledger
import i18n

ROOT = Path(__file__).resolve().parent.parent
STR = r'"((?:[^"\\]|\\.)*)"'
SOURCES = [ROOT / "app.py", *ROOT.glob("templates/*.html"), *ROOT.glob("static/*.js")]


def calls(pattern):
    found = set()
    for path in SOURCES:
        found |= set(re.findall(pattern, path.read_text()))
    return found


def placeholders(text):
    return {name for _, name, _, _ in string.Formatter().parse(text) if name}


def test_every_ui_string_has_a_ukrainian_translation():
    singles = (calls(r"(?<![\w.])(?:_|t)\(\s*" + STR) | calls(r"bot_text\(\s*\w+,\s*" + STR)
               | set(ledger.FINISHES.values()))
    plurals = calls(r"(?<![\w.])(?:_n|tn)\(\s*" + STR)
    assert singles and plurals
    assert sorted(singles - i18n.UK.keys()) == []
    assert sorted(plurals - i18n.UK_PLURAL.keys()) == []


def test_translations_keep_placeholders():
    for en, uk in i18n.UK.items():
        assert placeholders(uk) == placeholders(en), en
    for en, forms in i18n.UK_PLURAL.items():
        assert len(forms) == 3, en
        for form in forms:
            assert placeholders(form) == placeholders(en), en


@pytest.mark.parametrize("n, form", [
    (0, 2), (1, 0), (2, 1), (4, 1), (5, 2), (11, 2), (12, 2), (14, 2), (21, 0), (22, 1), (25, 2), (101, 0), (111, 2),
])
def test_ukrainian_plural_rule(n, form):
    assert i18n.plural_index("uk", n) == form


def test_ngettext_ukrainian():
    assert i18n.ngettext("uk", "{n} card", "{n} cards", 21) == "21 карта"
    assert i18n.ngettext("uk", "{n} card", "{n} cards", 3) == "3 карти"
    assert i18n.ngettext("uk", "{n} card", "{n} cards", 11) == "11 карт"
    assert i18n.ngettext("en", "{n} card", "{n} cards", 1) == "1 card"


def test_language_follows_browser(make_client):
    page = make_client().get("/login", headers={"Accept-Language": "uk-UA,uk;q=0.9,en;q=0.5"}).get_data(as_text=True)
    assert '<html lang="uk">' in page and "Знайте, де кожна ваша карта." in page
    assert 'data-lang="uk"' in page and "lang=uk" in page.replace("&amp;", "&")
    page = make_client().get("/login", headers={"Accept-Language": "de"}).get_data(as_text=True)
    assert '<html lang="en">' in page


def test_language_switch_sets_cookie_and_wins_over_browser(make_client):
    client = make_client("alice")
    resp = client.get("/lang/uk", headers={"Referer": "http://localhost/loans/new"})
    assert resp.headers["Location"] == "http://localhost/loans/new"
    assert "lang=uk" in resp.headers["Set-Cookie"]
    page = client.get("/", headers={"Accept-Language": "en"}).get_data(as_text=True)
    assert "Позичено іншим" in page and '"lang": "uk"' in page   # JS catalog injected too
    assert client.get("/lang/xx").status_code == 404


def test_language_switch_ignores_foreign_referrer(make_client):
    resp = make_client().get("/lang/uk", headers={"Referer": "https://evil.example/"})
    assert resp.headers["Location"] == "/"


def test_flash_messages_and_dates_are_translated(make_client):
    from werkzeug.datastructures import MultiDict
    client = make_client("alice")
    client.get("/lang/uk")
    data = MultiDict([("direction", "lent"), ("other", "@bob"),
                      ("scryfall_id", "bolt-m10"), ("finish", "foil"), ("quantity", "2"),
                      ("scryfall_id", "sol-ring-cmm"), ("finish", "etched"), ("quantity", "1")])
    page = client.post("/loans/new", data=data, follow_redirects=True).get_data(as_text=True)
    assert "Позику записано: 3 карти." in page
    assert "Сьогодні" in page and "Фойл" in page and "Етчед-фойл" in page
    assert "3 карти не повернуті" in page and "у 1 особи" in page
    page = client.post("/loans/new", data={"direction": "lent", "other": "@bob", "scryfall_id": "bolt-m10",
                                           "finish": "etched", "quantity": "1"}, follow_redirects=True)
    assert "не виходила з покриттям «етчед-фойл»" in page.get_data(as_text=True)


def test_ago_in_ukrainian(make_client):
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    with ledger.app.test_request_context():
        ledger.g.lang = "uk"
        assert ledger.ago(now - timedelta(days=1)) == "вчора"
        assert ledger.ago(now - timedelta(days=3)) == "3 дні тому"
        assert ledger.ago(now - timedelta(days=5)) == "5 днів тому"
        assert ledger.ago(datetime(2025, 10, 3, tzinfo=timezone.utc)) == "3 жовт. 2025"
