import sqlite3

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from splitledger import (
    SCHEMA_VERSION,
    LedgerError,
    LedgerStore,
    StoreError,
    settle,
    split_equal,
    split_shares,
)

MEMBERS = ["ana", "ben", "cal", "dee"]


@pytest.fixture
def path(tmp_path):
    return tmp_path / "house.db"


@pytest.fixture
def store(path):
    with LedgerStore.create(path, MEMBERS) as store:
        yield store


def raw(path):
    """A plain connection, for inspecting or tampering with the file directly."""
    conn = sqlite3.connect(path, isolation_level=None)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


# --- create and open --------------------------------------------------------


def test_new_household_starts_at_zero(store):
    assert store.balances() == {"ana": 0, "ben": 0, "cal": 0, "dee": 0}
    assert store.load().entries == ()


def test_entries_survive_closing_and_reopening(path):
    with LedgerStore.create(path, MEMBERS) as store:
        store.add_expense("groceries", "ana", 8430, split_equal(8430, MEMBERS))
        store.add_reimbursement("cal", "ana", 2107, "venmo")
        before = store.load()

    with LedgerStore.open(path) as store:
        after = store.load()
    assert after.entries == before.entries
    assert after.balances() == {"ana": 4215, "ben": -2108, "cal": 0, "dee": -2107}
    assert after.statement("cal") == before.statement("cal")


def test_create_refuses_to_overwrite(path):
    path.write_text("my notes")
    with pytest.raises(StoreError, match="already exists"):
        LedgerStore.create(path, MEMBERS)
    assert path.read_text() == "my notes"


def test_create_validates_members_before_writing_anything(path):
    with pytest.raises(LedgerError, match="unique"):
        LedgerStore.create(path, ["ana", "ana"])
    assert not path.exists()


def test_open_missing_file_does_not_create_one(path):
    with pytest.raises(StoreError, match="does not exist"):
        LedgerStore.open(path)
    assert not path.exists()  # plain sqlite3.connect would have created an empty database


def test_open_directory(tmp_path):
    with pytest.raises(StoreError, match="does not exist"):
        LedgerStore.open(tmp_path)


def test_open_file_that_is_not_a_database(path):
    path.write_text("order_id,amount\n1,5\n" * 100)
    with pytest.raises(StoreError, match="not a SQLite database"):
        LedgerStore.open(path)


def test_open_other_sqlite_database(path):
    raw(path).execute("CREATE TABLE notes (text TEXT)")
    with pytest.raises(StoreError, match="not a SplitLedger file"):
        LedgerStore.open(path)


def test_open_newer_schema_version(path):
    LedgerStore.create(path, MEMBERS).close()
    raw(path).execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
    with pytest.raises(StoreError, match=f"schema version {SCHEMA_VERSION + 1}"):
        LedgerStore.open(path)


def test_paths_with_unusual_characters(tmp_path):
    odd = tmp_path / "our house #2 ?=50%.db"
    with LedgerStore.create(odd, MEMBERS) as store:
        store.add_reimbursement("ben", "ana", 100)
    with LedgerStore.open(odd) as store:
        assert store.balances()["ana"] == -100


# --- writing ----------------------------------------------------------------


def test_rejected_entry_writes_nothing(store, path):
    with pytest.raises(LedgerError, match="not a member"):
        store.add_expense("pizza", "zoe", 1000, {"ana": 1000})
    with pytest.raises(LedgerError, match="two different members"):
        store.add_reimbursement("ana", "ana", 100)
    conn = raw(path)
    assert conn.execute("SELECT count(*) FROM entries").fetchone() == (0,)
    assert conn.execute("SELECT count(*) FROM shares").fetchone() == (0,)


def test_failed_insert_rolls_back_the_whole_expense(store, path, monkeypatch):
    # Simulate the disk failing after the entry row but before its shares are written.
    real_conn = store._conn

    class FailingShares:
        def __getattr__(self, name):
            return getattr(real_conn, name)

        def executemany(self, sql, rows):
            raise sqlite3.OperationalError("disk I/O error (simulated)")

    monkeypatch.setattr(store, "_conn", FailingShares())
    with pytest.raises(sqlite3.OperationalError):
        store.add_expense("groceries", "ana", 1000, split_equal(1000, MEMBERS))
    monkeypatch.undo()

    assert raw(path).execute("SELECT count(*) FROM entries").fetchone() == (0,)
    store.add_expense("groceries", "ana", 1000, split_equal(1000, MEMBERS))  # store still usable
    assert store.load().entries[0].entry_id == 1


def test_two_connections_see_each_others_entries(path):
    LedgerStore.create(path, MEMBERS).close()
    with LedgerStore.open(path) as first, LedgerStore.open(path) as second:
        first.add_reimbursement("ben", "ana", 100)
        second.add_reimbursement("cal", "ana", 200)  # must not reuse entry id 1
        assert [e.entry_id for e in first.load().entries] == [1, 2]
        assert first.balances()["ana"] == -300


def test_settling_through_the_store(store):
    store.add_expense("rent", "ana", 240_000, split_shares(240_000, {"ana": 2, "ben": 1, "cal": 1, "dee": 1}))
    store.add_expense("groceries", "ben", 8430, split_equal(8430, MEMBERS))
    for t in settle(store.balances()).transfers:
        store.add_reimbursement(t.sender, t.receiver, t.amount, "settle up")
    assert set(store.balances().values()) == {0}


# --- the database protects itself -------------------------------------------


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO entries (entry_id, kind, amount, from_member, description) VALUES (1, 'expense', 0, 'ana', 'x')",
        "INSERT INTO entries (entry_id, kind, amount, from_member, description) VALUES (1, 'expense', -5, 'ana', 'x')",
        "INSERT INTO entries (entry_id, kind, amount, from_member, description) VALUES (1, 'expense', 5.5, 'ana', 'x')",
        "INSERT INTO entries (entry_id, kind, amount, from_member, description) VALUES (1, 'expense', 500, 'zoe', 'x')",
        "INSERT INTO entries (entry_id, kind, amount, from_member, description) VALUES (1, 'expense', 500, 'ana', '')",
        "INSERT INTO entries (entry_id, kind, amount, from_member, to_member) VALUES (1, 'reimbursement', 500, 'ana', 'ana')",
        "INSERT INTO entries (entry_id, kind, amount, from_member) VALUES (1, 'reimbursement', 500, 'ana')",
        "INSERT INTO entries (entry_id, kind, amount, from_member) VALUES (1, 'refund', 500, 'ana')",
        "INSERT INTO shares (entry_id, member_id, cents) VALUES (99, 'ana', 5)",
        "INSERT INTO members (member_id) VALUES (' zoe')",
    ],
    ids=["zero", "negative", "fractional", "unknown-payer", "no-description", "self-reimburse",
         "no-receiver", "unknown-kind", "orphan-share", "padded-member"],
)
def test_schema_rejects_impossible_rows(store, path, sql):
    with pytest.raises(sqlite3.IntegrityError):
        raw(path).execute(sql)


def test_shares_that_dont_add_up_are_caught_on_load(store, path):
    # A cross-row rule ("shares sum to the total") can't be a CHECK constraint,
    # so a hand-edited file is caught when the history is replayed.
    store.add_expense("groceries", "ana", 1000, split_equal(1000, ["ana", "ben"]))
    raw(path).execute("UPDATE shares SET cents = 400 WHERE member_id = 'ben'")
    with pytest.raises(StoreError, match="stored entry 1 is invalid: split adds up to 9.00"):
        store.load()
    store.close()
    with pytest.raises(StoreError, match="stored entry 1 is invalid"):
        LedgerStore.open(path)


def test_gap_in_entry_ids_is_caught(store, path):
    store.add_reimbursement("ben", "ana", 100)
    raw(path).execute("UPDATE entries SET entry_id = 5")
    with pytest.raises(StoreError, match="not consecutive"):
        store.load()


def test_reimbursement_with_shares_is_caught(store, path):
    store.add_reimbursement("ben", "ana", 100)
    raw(path).execute("INSERT INTO shares VALUES (1, 'cal', 0)")
    with pytest.raises(StoreError, match="must not have shares"):
        store.load()


# --- random histories survive a round trip (Hypothesis) ---------------------

operations = st.lists(
    st.one_of(
        st.tuples(st.just("expense"), st.sampled_from(MEMBERS), st.integers(1, 500_000),
                  st.lists(st.sampled_from(MEMBERS), min_size=1, unique=True)),
        st.tuples(st.just("reimburse"), st.sampled_from(MEMBERS), st.sampled_from(MEMBERS), st.integers(1, 500_000)),
    ),
    max_size=15,
)


@settings(suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(operations)
def test_random_history_round_trips(tmp_path, ops):
    path = tmp_path / f"h{abs(hash(str(ops)))}.db"
    path.unlink(missing_ok=True)
    with LedgerStore.create(path, MEMBERS) as store:
        for op in ops:
            if op[0] == "expense":
                store.add_expense("item", op[1], op[2], split_equal(op[2], op[3]))
            elif op[1] != op[2]:
                store.add_reimbursement(op[1], op[2], op[3])
        expected = store.load()
    with LedgerStore.open(path) as store:
        loaded = store.load()
    assert loaded.entries == expected.entries
    assert loaded.balances() == expected.balances()
    assert sum(loaded.balances().values()) == 0


# --- defenses that need their own tests (found by mutation testing) ----------


def test_store_connection_enforces_foreign_keys(store):
    # SQLite turns foreign keys off by default on every new connection.
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        store._conn.execute("INSERT INTO shares (entry_id, member_id, cents) VALUES (99, 'ana', 5)")


def test_read_write_mode_never_creates_a_file(tmp_path):
    from splitledger.storage import _connect

    missing = tmp_path / "missing.db"
    with pytest.raises(sqlite3.OperationalError):
        _connect(missing, mode="rw")
    assert not missing.exists()


def test_concurrent_writers_wait_instead_of_failing(path):
    # Writer A takes the write lock and holds it while writer B tries to add an entry.
    # With BEGIN IMMEDIATE, B waits for A to commit and then succeeds as entry 2.
    # With a plain (deferred) BEGIN, B would read first and then fail with
    # "database is locked" when it tried to upgrade to a write lock.
    import threading
    import time

    LedgerStore.create(path, MEMBERS).close()
    a = LedgerStore.open(path)
    b_started, errors = threading.Event(), []

    def writer_b():
        # Like a second program: its own connection, opened in its own thread.
        with LedgerStore.open(path) as b:
            b_started.set()
            try:
                b.add_reimbursement("cal", "ana", 200)
            except Exception as exc:  # recorded so the main thread can report it
                errors.append(exc)

    thread = threading.Thread(target=writer_b)
    with a._write() as (conn, ledger):
        entry = ledger.add_reimbursement("ben", "ana", 100)
        conn.execute(
            "INSERT INTO entries (entry_id, kind, amount, from_member, to_member, description) "
            "VALUES (?, 'reimbursement', ?, ?, ?, '')",
            (entry.entry_id, entry.amount, entry.sender, entry.receiver),
        )
        thread.start()
        b_started.wait()
        time.sleep(0.3)  # give B time to collide with A's open transaction
    thread.join(timeout=10)

    assert errors == []
    assert [(e.entry_id, e.sender) for e in a.load().entries] == [(1, "ben"), (2, "cal")]
    a.close()
