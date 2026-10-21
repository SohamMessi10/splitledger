"""Reversals (in memory and stored), the schema 1 -> 2 upgrade, and CSV export."""

import csv
import io
import shutil
import sqlite3
from collections import Counter
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from splitledger import (
    SCHEMA_VERSION,
    Ledger,
    LedgerError,
    LedgerStore,
    Reversal,
    StoreError,
    split_equal,
    split_shares,
    to_csv,
    write_csv,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"
MEMBERS = ["ana", "ben", "cal", "dee"]


@pytest.fixture
def ledger():
    ledger = Ledger(MEMBERS)
    ledger.add_expense("groceries", "ana", 8000, split_equal(8000, MEMBERS))  # entry 1
    ledger.add_reimbursement("ben", "ana", 2000, "venmo")  # entry 2
    return ledger


# --- reversals in memory ----------------------------------------------------


def test_reversing_an_expense_restores_the_balances(ledger):
    before = Ledger(MEMBERS)
    before.add_reimbursement("ben", "ana", 2000, "venmo")
    reversal = ledger.add_reversal(1, "entered twice")
    assert isinstance(reversal, Reversal)
    assert (reversal.entry_id, reversal.reverses, reversal.reason) == (3, 1, "entered twice")
    assert ledger.balances() == before.balances()


def test_reversing_a_reimbursement(ledger):
    ledger.add_reversal(2, "payment bounced")
    assert ledger.balances() == {"ana": 6000, "ben": -2000, "cal": -2000, "dee": -2000}


def test_history_keeps_the_original_and_the_reversal(ledger):
    ledger.add_reversal(1, "wrong amount")
    assert [type(e).__name__ for e in ledger.entries] == ["Expense", "Reimbursement", "Reversal"]
    assert ledger.reversed_ids() == {1}


def test_statement_shows_the_reversal_and_its_reason(ledger):
    ledger.add_reversal(1, "wrong amount")
    lines = ledger.statement("cal")
    assert [(l.change, l.text) for l in lines] == [
        (-2000, "share of 'groceries' paid by ana: 20.00"),
        (2000, "reversal of entry 1 (wrong amount): share of 'groceries' paid by ana: 20.00"),
    ]
    assert lines[-1].running_balance == 0


@pytest.mark.parametrize(
    "entry_id, reason, message",
    [
        (9, "x", "there is no entry 9"),
        (0, "x", "there is no entry 0"),
        (True, "x", "there is no entry True"),
        ("1", "x", "there is no entry '1'"),
        (1, "   ", "reason must not be empty"),
    ],
)
def test_invalid_reversals(ledger, entry_id, reason, message):
    with pytest.raises(LedgerError, match=message):
        ledger.add_reversal(entry_id, reason)
    assert len(ledger.entries) == 2


def test_an_entry_can_only_be_reversed_once(ledger):
    ledger.add_reversal(1, "mistake")
    with pytest.raises(LedgerError, match="already been reversed"):
        ledger.add_reversal(1, "mistake again")


def test_a_reversal_cannot_be_reversed(ledger):
    ledger.add_reversal(1, "mistake")
    with pytest.raises(LedgerError, match="itself a reversal; record the original item again"):
        ledger.add_reversal(3, "undo the undo")


# --- reversals in the database ----------------------------------------------


@pytest.fixture
def store(tmp_path):
    with LedgerStore.create(tmp_path / "house.db", MEMBERS) as store:
        store.add_expense("groceries", "ana", 8000, split_equal(8000, MEMBERS))
        yield store


def test_stored_reversal_survives_reopening(store):
    store.add_reversal(1, "entered twice")
    path = store.path
    store.close()
    with LedgerStore.open(path) as reopened:
        assert set(reopened.balances().values()) == {0}
        assert reopened.load().reversed_ids() == {1}


def test_database_rejects_a_second_reversal_of_the_same_entry(store):
    store.add_reversal(1, "first")
    conn = sqlite3.connect(store.path)
    with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
        conn.execute("INSERT INTO entries (entry_id, kind, description, reverses) VALUES (3, 'reversal', 'again', 1)")


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO entries (entry_id, kind, description, reverses) VALUES (2, 'reversal', '', 1)",  # no reason
        "INSERT INTO entries (entry_id, kind, description) VALUES (2, 'reversal', 'x')",  # reverses nothing
        "INSERT INTO entries (entry_id, kind, amount, description, reverses) VALUES (2, 'reversal', 5, 'x', 1)",
        "INSERT INTO entries (entry_id, kind, amount, from_member, description, reverses) "
        "VALUES (2, 'expense', 5, 'ana', 'x', 1)",  # an expense can't reverse anything
    ],
    ids=["no-reason", "no-target", "reversal-with-amount", "expense-with-reverses"],
)
def test_schema_rejects_impossible_reversal_rows(store, sql):
    conn = sqlite3.connect(store.path)
    conn.execute("PRAGMA foreign_keys = ON")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(sql)


def test_schema_rejects_a_reversal_of_a_later_entry(store):
    # Entry 5 exists, so only the "reverses < entry_id" rule can reject this row
    # (an earlier version of this test was really rejected by the foreign key).
    conn = sqlite3.connect(store.path)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("INSERT INTO entries (entry_id, kind, amount, from_member, description) VALUES (5, 'expense', 100, 'ana', 'x')")
    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
        conn.execute("INSERT INTO entries (entry_id, kind, description, reverses) VALUES (2, 'reversal', 'x', 5)")


def test_reversal_of_a_reversal_written_by_hand_is_caught_on_load(store):
    store.add_reversal(1, "first")
    conn = sqlite3.connect(store.path)
    conn.execute("INSERT INTO entries (entry_id, kind, description, reverses) VALUES (3, 'reversal', 'undo', 2)")
    conn.commit()
    with pytest.raises(StoreError, match="stored entry 3 is invalid: entry 2 is itself a reversal"):
        store.load()


# --- upgrading a schema version 1 file --------------------------------------
# tests/fixtures/house_v1.db was written by the milestone 4 code (schema version 1).


@pytest.fixture
def v1_copy(tmp_path):
    path = tmp_path / "house.db"
    shutil.copy(FIXTURES / "house_v1.db", path)
    return path


def test_fixture_really_is_version_1(v1_copy):
    assert sqlite3.connect(v1_copy).execute("PRAGMA user_version").fetchone() == (1,)


def test_opening_a_v1_file_upgrades_it_and_keeps_every_entry(v1_copy):
    with LedgerStore.open(v1_copy) as store:
        ledger = store.load()
    assert [type(e).__name__ for e in ledger.entries] == ["Expense", "Expense", "Reimbursement"]
    assert ledger.balances() == {"ana": 93892, "ben": -41678, "cal": -2107, "dee": -50107}  # as written by v1
    conn = sqlite3.connect(v1_copy)
    assert conn.execute("PRAGMA user_version").fetchone() == (SCHEMA_VERSION,)
    assert conn.execute("SELECT recorded_at FROM entries WHERE entry_id = 1").fetchone() == ("2026-10-19T12:00:00Z",)


def test_upgrade_saves_an_untouched_backup_first(v1_copy):
    LedgerStore.open(v1_copy).close()
    backup = v1_copy.with_name("house.db.v1.bak")
    conn = sqlite3.connect(backup)
    assert conn.execute("PRAGMA user_version").fetchone() == (1,)
    assert conn.execute("SELECT count(*) FROM entries").fetchone() == (3,)


def test_upgraded_file_accepts_reversals(v1_copy):
    with LedgerStore.open(v1_copy) as store:
        store.add_reversal(2, "groceries were for a party")
        assert store.balances() == {"ana": 96000, "ben": -48000, "cal": 0, "dee": -48000}


def test_upgrade_keeps_foreign_keys_working(v1_copy):
    with LedgerStore.open(v1_copy) as store:
        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
            store._conn.execute("INSERT INTO shares (entry_id, member_id, cents) VALUES (99, 'ana', 1)")
        assert store._conn.execute("PRAGMA foreign_key_check").fetchall() == []


def test_upgrade_happens_only_once(v1_copy):
    LedgerStore.open(v1_copy).close()
    LedgerStore.open(v1_copy).close()  # already version 2: no second backup needed
    assert sorted(p.name for p in v1_copy.parent.iterdir()) == ["house.db", "house.db.v1.bak"]


def test_existing_backup_blocks_the_upgrade_and_leaves_the_file_alone(v1_copy):
    backup = v1_copy.with_name("house.db.v1.bak")
    backup.write_text("an older backup the user wants to keep")
    with pytest.raises(StoreError, match="backup .* already exists"):
        LedgerStore.open(v1_copy)
    assert backup.read_text() == "an older backup the user wants to keep"
    assert sqlite3.connect(v1_copy).execute("PRAGMA user_version").fetchone() == (1,)


def test_failed_upgrade_is_rolled_back_on_the_same_connection(v1_copy, monkeypatch):
    # Closing a connection also discards unfinished work, so check through the same,
    # still-open connection that the upgrade itself rolled back.
    from splitledger import storage

    def broken(conn):
        conn.execute("DROP TABLE entries")
        raise RuntimeError("simulated crash during upgrade")

    monkeypatch.setitem(storage._MIGRATIONS, 1, broken)
    conn = storage._connect(v1_copy, mode="rw")
    with pytest.raises(RuntimeError):
        storage._upgrade(conn, v1_copy, 1)
    assert conn.execute("PRAGMA user_version").fetchone() == (1,)
    assert conn.execute("SELECT count(*) FROM entries").fetchone() == (3,)
    assert conn.execute("PRAGMA foreign_keys").fetchone() == (1,)  # switched back on
    conn.close()


def test_failed_upgrade_rolls_back_to_version_1(v1_copy, monkeypatch):
    from splitledger import storage

    def broken(conn):
        conn.execute("DROP TABLE entries")  # half-done work that must be undone
        raise RuntimeError("simulated crash during upgrade")

    monkeypatch.setitem(storage._MIGRATIONS, 1, broken)
    with pytest.raises(RuntimeError, match="simulated crash"):
        LedgerStore.open(v1_copy)
    conn = sqlite3.connect(v1_copy)
    assert conn.execute("PRAGMA user_version").fetchone() == (1,)
    assert conn.execute("SELECT count(*) FROM entries").fetchone() == (3,)


# --- CSV export -------------------------------------------------------------


def parse(text):
    return list(csv.DictReader(io.StringIO(text)))


def to_cents(text):
    sign = -1 if text.startswith("-") else 1
    whole, frac = text.lstrip("-").split(".")
    return sign * (int(whole) * 100 + int(frac))


def test_csv_columns_and_rows(ledger):
    rows = parse(to_csv(ledger))
    assert list(rows[0]) == [
        "entry_id", "kind", "description", "paid_by", "paid_to", "amount",
        "member", "share", "balance_change", "reverses", "reversed",
    ]
    assert rows[0] == {
        "entry_id": "1", "kind": "expense", "description": "groceries", "paid_by": "ana", "paid_to": "",
        "amount": "80.00", "member": "ana", "share": "20.00", "balance_change": "60.00",
        "reverses": "", "reversed": "",
    }
    assert [(r["entry_id"], r["member"], r["balance_change"]) for r in rows[4:]] == [
        ("2", "ana", "-20.00"),
        ("2", "ben", "20.00"),
    ]


def test_csv_marks_reversed_entries_and_their_reversals(ledger):
    ledger.add_reversal(1, "wrong amount")
    rows = parse(to_csv(ledger))
    assert {r["reversed"] for r in rows if r["entry_id"] == "1"} == {"yes"}
    reversal_rows = [r for r in rows if r["kind"] == "reversal"]
    assert {r["reverses"] for r in reversal_rows} == {"1"}
    assert reversal_rows[0]["description"] == "wrong amount (reverses: groceries)"
    assert reversal_rows[1]["share"] == "-20.00"


def test_payer_outside_the_split_gets_a_row():
    ledger = Ledger(MEMBERS)
    ledger.add_expense("gift", "ana", 3000, split_equal(3000, ["cal", "dee"]))
    rows = parse(to_csv(ledger))
    assert [(r["member"], r["share"], r["balance_change"]) for r in rows] == [
        ("ana", "0.00", "30.00"), ("cal", "15.00", "-15.00"), ("dee", "15.00", "-15.00"),
    ]


@pytest.mark.parametrize("text", ["=1+1", "+1", "-1", "@SUM(A1)", '=HYPERLINK("http://evil.example","x")'])
def test_formula_like_text_is_neutralized(text):
    ledger = Ledger(["ana", "ben"])
    ledger.add_expense(text, "ana", 1000, split_equal(1000, ["ana", "ben"]))
    for row in parse(to_csv(ledger)):
        assert row["description"] == "'" + text
        assert row["amount"] == "10.00"  # numbers are left alone


def test_member_ids_that_look_like_formulas_are_neutralized():
    ledger = Ledger(["=cmd", "ben"])
    ledger.add_reimbursement("ben", "=cmd", 500)
    assert {r["member"] for r in parse(to_csv(ledger))} == {"'=cmd", "ben"}


def test_text_with_commas_quotes_and_newlines_round_trips():
    ledger = Ledger(["ana", "ben"])
    tricky = 'pizza, wings and "soda"\nfrom Joe\'s'
    ledger.add_expense(tricky, "ana", 1000, split_equal(1000, ["ana", "ben"]))
    assert {r["description"] for r in parse(to_csv(ledger))} == {tricky}


def test_csv_is_deterministic(ledger):
    assert to_csv(ledger) == to_csv(ledger)


def test_write_csv_uses_a_bom_for_excel(ledger, tmp_path):
    path = tmp_path / "ledger.csv"
    write_csv(ledger, path)
    assert path.read_bytes().startswith(b"\xef\xbb\xbf")
    assert path.read_text(encoding="utf-8-sig") == to_csv(ledger)
    assert [p.name for p in tmp_path.iterdir()] == ["ledger.csv"]  # no temp file left behind


def test_store_export_refuses_to_overwrite_its_own_database(store, tmp_path):
    with pytest.raises(StoreError, match="refusing to export over the ledger database"):
        store.export_csv(store.path)
    assert LedgerStore.open(store.path).balances()["ana"] == 6000  # database intact
    store.export_csv(tmp_path / "ledger.csv")
    assert (tmp_path / "ledger.csv").exists()


histories = st.lists(
    st.one_of(
        st.tuples(st.just("expense"), st.sampled_from(MEMBERS), st.integers(1, 500_000),
                  st.lists(st.sampled_from(MEMBERS), min_size=1, unique=True)),
        st.tuples(st.just("reimburse"), st.sampled_from(MEMBERS), st.sampled_from(MEMBERS), st.integers(1, 500_000)),
        st.tuples(st.just("reverse"), st.integers(1, 20)),
    ),
    max_size=20,
)


def build(ops):
    ledger = Ledger(MEMBERS)
    for op in ops:
        if op[0] == "expense":
            ledger.add_expense("item", op[1], op[2], split_shares(op[2], {m: 1 for m in op[3]}))
        elif op[0] == "reimburse" and op[1] != op[2]:
            ledger.add_reimbursement(op[1], op[2], op[3])
        elif op[0] == "reverse":
            try:
                ledger.add_reversal(op[1], "fix")
            except LedgerError:
                pass  # target missing, already reversed, or itself a reversal
    return ledger


@given(histories)
def test_csv_balance_changes_add_up_to_the_balances(ops):
    ledger = build(ops)
    totals = Counter({m: 0 for m in MEMBERS})
    for row in parse(to_csv(ledger)):
        totals[row["member"]] += to_cents(row["balance_change"])
    assert dict(totals) == ledger.balances()


@given(histories)
def test_balances_still_sum_to_zero_with_reversals(ops):
    assert sum(build(ops).balances().values()) == 0


@settings(suppress_health_check=[HealthCheck.function_scoped_fixture], max_examples=30)
@given(histories)
def test_histories_with_reversals_round_trip_through_the_database(tmp_path, ops):
    expected = build(ops)
    path = tmp_path / f"h{abs(hash(str(ops)))}.db"
    path.unlink(missing_ok=True)
    with LedgerStore.create(path, MEMBERS) as store:
        for entry in expected.entries:
            kind = type(entry).__name__
            if kind == "Expense":
                store.add_expense(entry.description, entry.payer, entry.total, entry.split)
            elif kind == "Reimbursement":
                store.add_reimbursement(entry.sender, entry.receiver, entry.amount, entry.note)
            else:
                store.add_reversal(entry.reverses, entry.reason)
    with LedgerStore.open(path) as store:
        assert store.load().entries == expected.entries
