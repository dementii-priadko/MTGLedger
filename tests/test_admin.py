import time

import pytest

import app as ledger
from test_app import lend, signed


@pytest.fixture
def admin(make_client):
    client = make_client()
    payload = {"id": "8017026444", "first_name": "Dem", "username": "beznadijka", "auth_date": str(int(time.time()))}
    assert client.get("/auth/telegram", query_string=signed(payload)).headers["Location"] == "/"
    return client


def uid(query, username):
    return query("SELECT id FROM users WHERE lower(username) = lower(%s)", (username,))[0][0]


def log_actions(query):
    return [row[0] for row in query("SELECT action FROM admin_log ORDER BY id")]


def test_only_admins_get_in(make_client, admin, query):
    alice = make_client("alice")
    lend(alice, other="@typo")
    pid = uid(query, "typo")
    for client, expected in ((make_client(), 302), (alice, 404)):
        assert client.get("/admin").status_code == expected
        for action, data in (("rename", {"username": "x_y_z"}), ("merge", {"into": "alice"}), ("delete", {})):
            assert client.post(f"/admin/users/{pid}/{action}", data=data).status_code == expected
        assert client.post("/admin/loans/1/delete").status_code == expected
        assert client.post("/admin/backup").status_code == expected
    assert "Admin panel" not in alice.get("/").get_data(as_text=True)
    assert query("SELECT count(*) FROM loans") == [(1,)] and log_actions(query) == []


def test_admin_overview(admin):
    lend(admin, other="@bob", quantity="3")
    page = admin.get("/admin").get_data(as_text=True)
    assert "Admin panel" in admin.get("/").get_data(as_text=True)          # menu link
    assert "@beznadijka" in page and "@bob" in page and "placeholder" in page and "Lightning Bolt" in page
    assert '<span class="stat-value">3</span><span class="muted">Cards out</span>' in page


def test_rename_placeholder(admin, make_client, query):
    lend(make_client("alice"), other="@bobb")
    pid = uid(query, "bobb")
    resp = admin.post(f"/admin/users/{pid}/rename", data={"username": "@bob"}, follow_redirects=True)
    assert b"Renamed @bobb to @bob." in resp.data
    assert query("SELECT username FROM users WHERE id = %s", (pid,)) == [("bob",)]
    assert b"already exists" in admin.post(f"/admin/users/{pid}/rename", data={"username": "alice"},
                                           follow_redirects=True).data
    assert b"valid username" in admin.post(f"/admin/users/{pid}/rename", data={"username": "a b"},
                                           follow_redirects=True).data
    assert log_actions(query) == ["rename_placeholder"]


def test_merge_placeholder_into_real_user(admin, make_client, query):
    alice = make_client("alice")
    lend(alice, other="@bob_old", quantity="2")
    bob = make_client("bob_new")
    pid = uid(query, "bob_old")
    resp = admin.post(f"/admin/users/{pid}/merge", data={"into": "@Bob_New"}, follow_redirects=True)
    assert b"Merged @bob_old into @bob_new." in resp.data
    assert query("SELECT count(*) FROM users WHERE username = 'bob_old'") == [(0,)]
    assert "Lightning Bolt" in bob.get("/#borrowed").get_data(as_text=True)
    assert log_actions(query) == ["merge_placeholder"]


def test_merge_refuses_people_who_share_loans_and_real_users(admin, make_client, query):
    alice = make_client("alice")
    lend(alice, other="@alice2")
    pid = uid(query, "alice2")
    resp = admin.post(f"/admin/users/{pid}/merge", data={"into": "alice"}, follow_redirects=True)
    assert b"can\xe2\x80\x99t be merged" in resp.data and query("SELECT count(*) FROM loans") == [(1,)]
    assert b"No user @nobody" in admin.post(f"/admin/users/{pid}/merge", data={"into": "nobody"},
                                           follow_redirects=True).data
    # Real (logged-in) accounts can't be renamed, merged or deleted from here.
    for action in ("rename", "merge", "delete"):
        assert admin.post(f"/admin/users/{uid(query, 'alice')}/{action}", data={"username": "zz_zz", "into": "alice2"}
                          ).status_code == 404


def test_delete_placeholder_only_without_loans(admin, make_client, query):
    alice = make_client("alice")
    lend(alice, other="@ghost")
    pid = uid(query, "ghost")
    assert b"still has loans" in admin.post(f"/admin/users/{pid}/delete", follow_redirects=True).data
    alice.post("/loans/1/delete")
    assert b"Deleted @ghost." in admin.post(f"/admin/users/{pid}/delete", follow_redirects=True).data
    assert query("SELECT count(*) FROM users WHERE id = %s", (pid,)) == [(0,)]


def test_admin_delete_loan_is_logged(admin, make_client, query):
    lend(make_client("alice"), other="@bob", quantity="2")
    assert b"Loan deleted." in admin.post("/admin/loans/1/delete", follow_redirects=True).data
    assert query("SELECT count(*) FROM loans") == [(0,)]
    [(action, details)] = query("SELECT action, details FROM admin_log")
    assert action == "delete_loan" and details["card"] == "Lightning Bolt" and details["lender"] == "@alice"
    assert admin.post("/admin/loans/1/delete").status_code == 404
    assert "delete_loan" in admin.get("/admin").get_data(as_text=True)


def test_manual_backup(admin, monkeypatch, query):
    import backup
    monkeypatch.setenv("BACKUP_CHAT_ID", "42")
    monkeypatch.setattr(backup, "run_backup", lambda: "sent 0 rows (0 cards) to chat 42")
    assert b"Backup sent." in admin.post("/admin/backup", follow_redirects=True).data
    monkeypatch.setattr(backup, "run_backup", lambda: (_ for _ in ()).throw(RuntimeError("bot was blocked")))
    assert b"Backup failed: bot was blocked" in admin.post("/admin/backup", follow_redirects=True).data
    assert log_actions(query) == ["backup"]


def test_admin_ids_parsing(monkeypatch):
    assert ledger.ADMIN_TELEGRAM_IDS == {8017026444}
