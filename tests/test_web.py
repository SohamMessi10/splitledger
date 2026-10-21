import re

import pytest
from fastapi.testclient import TestClient

from splitledger import LedgerStore
from splitledger.web import create_app

MEMBERS = ["ana", "ben", "cal", "dee"]
BASE = "http://127.0.0.1"  # requests look like a browser on this computer


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "house.db"
    LedgerStore.create(path, MEMBERS).close()
    return path


@pytest.fixture
def client(db):
    with TestClient(create_app(db), base_url=BASE) as client:
        yield client


def add_groceries(client):
    body = {"description": "groceries", "payer": "ana", "amount": "84.30",
            "split": {"type": "equal", "members": MEMBERS}}
    response = client.post("/api/expenses", json=body)
    assert response.status_code == 201
    return response.json()


def alert(html):
    match = re.search(r'role="alert">([^<]*)', html)
    return match.group(1) if match else None


# --- JSON API ---------------------------------------------------------------


def test_add_expense_returns_the_split(client):
    entry = add_groceries(client)
    assert entry == {
        "entry_id": 1, "reversed": False, "kind": "expense", "description": "groceries", "payer": "ana",
        "amount": "84.30", "split": {"ana": "21.08", "ben": "21.08", "cal": "21.07", "dee": "21.07"},
    }
    assert client.get("/api/balances").json() == {"ana": "63.22", "ben": "-21.08", "cal": "-21.07", "dee": "-21.07"}


def test_shares_and_exact_splits(client):
    rent = {"description": "rent", "payer": "ben", "amount": "2400",
            "split": {"type": "shares", "shares": {"ana": 2, "ben": 1, "cal": 1, "dee": 1}}}
    takeout = {"description": "takeout", "payer": "cal", "amount": "45",
               "split": {"type": "exact", "amounts": {"cal": "20", "dee": "25.00"}}}
    assert client.post("/api/expenses", json=rent).json()["split"]["ana"] == "960.00"
    assert client.post("/api/expenses", json=takeout).json()["split"] == {"cal": "20.00", "dee": "25.00"}


def test_reimbursement_and_statement(client):
    add_groceries(client)
    payment = client.post("/api/reimbursements", json={"sender": "cal", "receiver": "ana", "amount": "21.07", "note": "venmo"})
    assert payment.status_code == 201
    assert client.get("/api/statement/cal").json()[-1] == {
        "entry_id": 2, "text": "paid ana back 21.07 (venmo)", "change": "21.07", "running_balance": "0.00",
    }


def test_settlement_and_recording_it(client):
    add_groceries(client)
    plan = client.get("/api/settlement").json()
    assert plan["method"] == "exact"
    for t in plan["transfers"]:
        assert client.post("/api/reimbursements", json={**t, "note": "settle up"}).status_code == 201
    assert set(client.get("/api/balances").json().values()) == {"0.00"}
    assert client.get("/api/settlement").json()["transfers"] == []


def test_reverse_an_entry(client):
    add_groceries(client)
    response = client.post("/api/entries/1/reverse", json={"reason": "entered twice"})
    assert response.status_code == 201
    assert response.json() == {"entry_id": 2, "reversed": False, "kind": "reversal", "reverses": 1, "reason": "entered twice"}
    assert client.get("/api/entries").json()[0]["reversed"] is True
    assert set(client.get("/api/balances").json().values()) == {"0.00"}


def test_members_and_entries(client):
    assert client.get("/api/members").json() == MEMBERS
    assert client.get("/api/entries").json() == []


def test_csv_export(client):
    add_groceries(client)
    response = client.get("/export.csv")
    assert response.headers["content-type"] == "text/csv; charset=utf-8"
    assert 'filename="splitledger.csv"' in response.headers["content-disposition"]
    assert response.text.splitlines()[0].startswith("entry_id,kind,description")


def test_openapi_docs_are_served(client):
    assert client.get("/docs").status_code == 200
    assert "/api/expenses" in client.get("/openapi.json").json()["paths"]


# --- API errors -------------------------------------------------------------


@pytest.mark.parametrize(
    "body, status, detail",
    [
        ({"amount": 84.3}, 422, "valid string"),  # a JSON number: floats are refused
        ({"amount": "84.305"}, 422, "up to two decimal places"),
        ({"amount": "0"}, 422, "more than 0.00"),
        ({"payer": "zoe"}, 422, "payer 'zoe' is not a member"),
        ({"ammount": "5"}, 422, "Extra inputs are not permitted"),  # typo in a field name
        ({"split": {"type": "equal", "members": []}}, 422, "at least 1 item"),
        ({"split": {"type": "evenly", "members": MEMBERS}}, 422, "union_tag_invalid"),
        ({"split": {"type": "exact", "amounts": {"ana": "80"}}}, 422, "4.30 short of the total"),
        ({"split": {"type": "shares", "shares": {"ana": 0}}}, 422, "whole number of at least 1"),
        ({"description": ""}, 422, "description must not be empty"),
    ],
)
def test_invalid_expenses(client, body, status, detail):
    request = {"description": "groceries", "payer": "ana", "amount": "84.30",
               "split": {"type": "equal", "members": MEMBERS}} | body
    response = client.post("/api/expenses", json=request)
    assert response.status_code == status
    assert detail in str(response.json()["detail"])
    assert client.get("/api/entries").json() == []


def test_unknown_member_statement_is_404(client):
    assert client.get("/api/statement/zoe").status_code == 404


@pytest.mark.parametrize("entry_id, status", [(99, 404), (0, 404)])
def test_reversing_a_missing_entry_is_404(client, entry_id, status):
    assert client.post(f"/api/entries/{entry_id}/reverse", json={"reason": "x"}).status_code == status


def test_reversing_twice_is_422(client):
    add_groceries(client)
    client.post("/api/entries/1/reverse", json={"reason": "once"})
    response = client.post("/api/entries/1/reverse", json={"reason": "twice"})
    assert response.status_code == 422
    assert "already been reversed" in response.json()["detail"]


def test_form_encoded_body_is_refused_by_the_json_api(client):
    response = client.post("/api/reimbursements", data={"sender": "ben", "receiver": "ana", "amount": "5"})
    assert response.status_code == 422
    assert client.get("/api/entries").json() == []


# --- local-only protections -------------------------------------------------


def test_cross_site_post_is_refused(client):
    response = client.post(
        "/reimbursements",
        data={"sender": "ben", "receiver": "ana", "amount": "999"},
        headers={"Origin": "http://evil.example"},
    )
    assert response.status_code == 403
    assert client.get("/api/entries").json() == []


def test_cross_site_api_post_is_refused(client):
    response = client.post(
        "/api/reimbursements",
        json={"sender": "ben", "receiver": "ana", "amount": "999"},
        headers={"Origin": "http://evil.example"},
    )
    assert response.status_code == 403


def test_same_origin_browser_post_is_allowed(client):
    response = client.post(
        "/reimbursements",
        data={"sender": "ben", "receiver": "ana", "amount": "5"},
        headers={"Origin": BASE},
        follow_redirects=False,
    )
    assert response.status_code == 303


def test_cross_site_reads_are_not_blocked_but_send_no_cors_permission(client):
    # A foreign page may send a GET, but without CORS headers its script can't read the reply.
    response = client.get("/api/balances", headers={"Origin": "http://evil.example"})
    assert response.status_code == 200
    assert "access-control-allow-origin" not in response.headers


def test_unexpected_host_header_is_refused(db):
    with TestClient(create_app(db), base_url="http://attacker.example") as client:
        assert client.get("/api/balances").status_code == 400


def test_app_refuses_to_start_without_a_valid_file(tmp_path):
    from splitledger import StoreError

    with pytest.raises(StoreError, match="does not exist"):
        create_app(tmp_path / "missing.db")


def test_damaged_file_gives_a_clear_500(client, db):
    import sqlite3

    add_groceries(client)
    conn = sqlite3.connect(db)
    conn.execute("UPDATE shares SET cents = 1 WHERE member_id = 'ben'")
    conn.commit()
    response = client.get("/api/balances")
    assert response.status_code == 500
    assert "ledger file problem" in response.json()["detail"]


# --- household page ---------------------------------------------------------


def test_empty_household_page(client):
    html = client.get("/").text
    assert "No entries yet" in html
    assert "Everyone is settled." in html
    assert len(re.findall(r'<td class="amount\s*">\s*settled\s*</td>', html)) == len(MEMBERS)


def test_page_shows_balances_settlement_and_history(client):
    add_groceries(client)
    html = client.get("/").text
    assert "is owed 63.22" in html
    assert "owes 21.08" in html
    assert re.search(r"<strong>ben</strong> pays <strong>ana</strong> 21\.08", html)
    assert "ana paid 84.30 for groceries" in html
    assert "The fewest payments that settle everyone." in html


def test_expense_form_equal_split(client):
    response = client.post(
        "/expenses",
        data={"description": "groceries", "payer": "ana", "amount": "84.30", "split_type": "equal", "member": MEMBERS},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/?notice=Added+groceries."
    assert client.get("/api/balances").json()["ana"] == "63.22"


def test_expense_form_shares_and_exact(client):
    client.post("/expenses", data={"description": "rent", "payer": "ben", "amount": "2400", "split_type": "shares",
                                   "share_ana": "2", "share_ben": "1", "share_cal": "1", "share_dee": "1"})
    client.post("/expenses", data={"description": "takeout", "payer": "cal", "amount": "45", "split_type": "exact",
                                   "exact_cal": "20", "exact_dee": "25"})
    assert client.get("/api/balances").json() == {"ana": "-960.00", "ben": "1920.00", "cal": "-455.00", "dee": "-505.00"}


def test_expense_form_error_keeps_what_was_typed(client):
    response = client.post(
        "/expenses",
        data={"description": "pizza night", "payer": "cal", "amount": "12.345", "split_type": "equal", "member": ["cal", "dee"]},
    )
    assert response.status_code == 422
    assert alert(response.text) == "&#39;12.345&#39; is not an amount; use digits with up to two decimal places, like 12.34"
    assert 'value="pizza night"' in response.text
    assert 'value="12.345"' in response.text
    assert '<option value="cal" selected>' in response.text
    assert re.search(r'name="member" value="cal" checked', response.text)  # every ticked box,
    assert re.search(r'name="member" value="dee" checked', response.text)  # not just the last one
    assert not re.search(r'value="ana" checked', response.text)  # unticked boxes stay unticked


@pytest.mark.parametrize(
    "data, message",
    [
        ({"split_type": "exact", "exact_ana": "5"}, "short of the total"),
        ({"split_type": "shares", "share_ana": "two"}, "share for ana must be a whole number"),
        ({"split_type": "equal"}, "at least one member"),
        ({"payer": "zoe", "member": ["ana"]}, "is not a member"),
        ({"split_type": "thirds", "member": ["ana"]}, "unknown split type"),
    ],
)
def test_expense_form_errors(client, data, message):
    form = {"description": "x", "payer": "ana", "amount": "10", "split_type": "equal"} | data
    response = client.post("/expenses", data=form)
    assert response.status_code == 422
    assert message in alert(response.text)
    assert client.get("/api/entries").json() == []


def test_payment_form_and_its_error(client):
    ok = client.post("/reimbursements", data={"sender": "cal", "receiver": "ana", "amount": "21.07", "note": "venmo"},
                     follow_redirects=False)
    assert ok.status_code == 303
    bad = client.post("/reimbursements", data={"sender": "ana", "receiver": "ana", "amount": "5"})
    assert bad.status_code == 422
    assert "two different members" in alert(bad.text)


def test_mark_as_paid_button_settles_up(client):
    add_groceries(client)
    html = client.get("/").text
    for sender, receiver, amount in re.findall(
        r'name="sender" value="(\w+)">\s*<input type="hidden" name="receiver" value="(\w+)">\s*'
        r'<input type="hidden" name="amount" value="([\d.]+)">', html
    ):
        client.post("/reimbursements", data={"sender": sender, "receiver": receiver, "amount": amount, "note": "settle up"})
    assert "Everyone is settled." in client.get("/").text


def test_reverse_form(client):
    add_groceries(client)
    response = client.post("/entries/1/reverse", data={"reason": "entered twice"}, follow_redirects=False)
    assert response.status_code == 303
    html = client.get("/").text
    assert "Reversed entry 1: entered twice" in html
    assert 'class="reversed">ana paid 84.30 for groceries' in html
    again = client.post("/entries/1/reverse", data={"reason": "again"})
    assert again.status_code == 422


def test_user_text_is_escaped(client):
    client.post("/api/expenses", json={"description": "<script>alert(1)</script>", "payer": "ana", "amount": "1",
                                       "split": {"type": "equal", "members": ["ana"]}})
    html = client.get("/?notice=<img src=x onerror=alert(2)>").text
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "<img src=x" not in html


def test_post_redirect_get(client):
    # After a successful form post the browser is sent to a GET page, so refreshing
    # doesn't submit the same expense twice.
    response = client.post("/reimbursements", data={"sender": "ben", "receiver": "ana", "amount": "5"})
    assert response.request.method == "GET"
    assert response.url.path == "/"
    assert len(client.get("/api/entries").json()) == 1
