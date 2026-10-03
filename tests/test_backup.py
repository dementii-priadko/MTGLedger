import csv
import io
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from werkzeug.datastructures import MultiDict

import backup

KYIV = ZoneInfo("Europe/Kyiv")


def parse(content):
    assert content.startswith("﻿".encode())
    return list(csv.DictReader(io.StringIO(content.decode("utf-8-sig"))))


def test_csv_neutralizes_formulas():
    row = {col: "" for col in backup.COLUMNS}
    row.update(lender_name="=HYPERLINK(\"http://x\")", borrower_name="+1", card_name="-Lotus", set_name="@SUM(1)",
               quantity=2, lent_at=datetime(2026, 10, 3, 18, 0, tzinfo=ZoneInfo("UTC")))
    [out] = parse(backup.build_csv([row]))
    assert out["lender_name"].startswith("'=") and out["borrower_name"] == "'+1"
    assert out["card_name"] == "'-Lotus" and out["set_name"] == "'@SUM(1)"
    assert out["quantity"] == "2"
    assert out["lent_at"] == "2026-10-03T21:00:00+03:00"   # shown in BACKUP_TZ


@pytest.mark.parametrize("now, expected", [
    (datetime(2026, 10, 3, 8, 59, tzinfo=KYIV), datetime(2026, 10, 3, 9, 0, tzinfo=KYIV)),
    (datetime(2026, 10, 3, 9, 0, tzinfo=KYIV), datetime(2026, 10, 4, 9, 0, tzinfo=KYIV)),
    (datetime(2026, 10, 3, 23, 30, tzinfo=KYIV), datetime(2026, 10, 4, 9, 0, tzinfo=KYIV)),
])
def test_next_run(now, expected):
    assert backup.next_run(now, "09:00") == expected


def test_run_backup_sends_only_active_loans(make_client, monkeypatch, query):
    alice = make_client("alice")
    alice.post("/loans/new", data=MultiDict([
        ("direction", "lent"), ("other", "@bob"),
        ("scryfall_id", "bolt-m10"), ("finish", "foil"), ("quantity", "4"),
        ("scryfall_id", "sol-ring-cmm"), ("finish", "nonfoil"), ("quantity", "1"),
    ]))
    alice.post("/loans/1/return", data={"quantity": "1"})   # 3 Bolts still out
    alice.post("/loans/2/return")                           # Sol Ring back

    sent = {}
    monkeypatch.setattr(backup, "send_document",
                        lambda chat_id, filename, content, caption: sent.update(locals()))
    monkeypatch.setenv("BACKUP_TELEGRAM_USERNAME", "@ALICE")
    summary = backup.run_backup()

    [(alice_tg_id,)] = query("SELECT telegram_id FROM users WHERE username = 'alice'")
    assert sent["chat_id"] == alice_tg_id
    assert sent["filename"].startswith("card-ledger-active-") and sent["filename"].endswith(".csv")
    assert "1 active loan rows · 3 cards out" in sent["caption"]
    [row] = parse(sent["content"])
    assert (row["card_name"], row["finish"], row["quantity"]) == ("Lightning Bolt", "foil", "3")
    assert (row["lender_username"], row["borrower_username"]) == ("alice", "bob")
    assert row["batch_id"] and row["scryfall_id"] == "bolt-m10"
    assert "3 cards" in summary


def test_recipient_must_have_logged_in(make_client, monkeypatch):
    make_client("alice").post("/loans/new", data={"direction": "lent", "other": "@bob", "scryfall_id": "bolt-m10",
                                                  "finish": "nonfoil", "quantity": "1"})
    monkeypatch.setattr(backup, "send_document", lambda *a: pytest.fail("should not send"))
    monkeypatch.setenv("BACKUP_TELEGRAM_USERNAME", "bob")    # placeholder, never logged in
    with pytest.raises(RuntimeError, match="never logged in"):
        backup.run_backup()


def test_explicit_chat_id_wins(monkeypatch):
    monkeypatch.setenv("BACKUP_CHAT_ID", "42")
    monkeypatch.setenv("BACKUP_TELEGRAM_USERNAME", "whoever")
    sent = {}
    monkeypatch.setattr(backup, "send_document", lambda chat_id, *a: sent.update(chat_id=chat_id))
    backup.run_backup()
    assert sent["chat_id"] == 42


def test_send_document_builds_multipart(monkeypatch):
    captured = {}

    class Resp(io.BytesIO):
        def __enter__(self): return self
        def __exit__(self, *a): pass

    def fake_urlopen(req, timeout, context):
        captured.update(url=req.full_url, body=req.data, ctype=req.headers["Content-type"])
        return Resp(b'{"ok": true}')

    monkeypatch.setattr(backup.urllib.request, "urlopen", fake_urlopen)
    backup.send_document(42, "x.csv", b"a,b\r\n", "hello")
    assert captured["url"].endswith("/sendDocument") and "multipart/form-data; boundary=" in captured["ctype"]
    assert b'name="chat_id"\r\n\r\n42' in captured["body"]
    assert b'filename="x.csv"\r\nContent-Type: text/csv\r\n\r\na,b\r\n' in captured["body"]
