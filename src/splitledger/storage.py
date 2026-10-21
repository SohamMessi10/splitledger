"""Save a household's ledger in a SQLite file.

One database file holds one household. The database stores entries, never
balances: balances are always recomputed from the history, exactly as in memory.

Integrity is checked in three places:
1. The Ledger validates every new entry before it is written.
2. The schema's CHECK and FOREIGN KEY constraints reject impossible rows, even
   ones written by hand with another SQLite tool.
3. Loading replays every stored entry through the Ledger's validation, so a
   damaged file is reported instead of producing wrong balances.

Schema history:
  1: members, entries (expense, reimbursement), shares.
  2: entries can also be a reversal that cancels an earlier entry. Version 1 files
     are upgraded when opened, after a backup copy is saved next to them.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path

from splitledger.export import write_csv
from splitledger.ledger import Expense, Ledger, LedgerError, Reimbursement, Reversal

SCHEMA_VERSION = 2

# Kept separate so the version 1 -> 2 migration builds exactly the same table.
_ENTRIES_TABLE = """
CREATE TABLE {name} (
    entry_id    INTEGER PRIMARY KEY CHECK (entry_id > 0),
    kind        TEXT NOT NULL CHECK (kind IN ('expense', 'reimbursement', 'reversal')),
    amount      INTEGER CHECK (amount > 0),
    from_member TEXT REFERENCES members (member_id),  -- payer, or reimbursement sender
    to_member   TEXT REFERENCES members (member_id),  -- reimbursement receiver only
    description TEXT NOT NULL DEFAULT '',             -- expense description, note, or reversal reason
    reverses    INTEGER UNIQUE REFERENCES entries (entry_id),  -- UNIQUE: reversed at most once
    recorded_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    CHECK (
        (kind = 'expense' AND amount IS NOT NULL AND from_member IS NOT NULL
            AND to_member IS NULL AND reverses IS NULL AND length(description) > 0)
        OR (kind = 'reimbursement' AND amount IS NOT NULL AND from_member IS NOT NULL
            AND to_member IS NOT NULL AND to_member <> from_member AND reverses IS NULL)
        OR (kind = 'reversal' AND amount IS NULL AND from_member IS NULL AND to_member IS NULL
            AND reverses IS NOT NULL AND reverses < entry_id AND length(description) > 0)
    )
) STRICT"""

_SCHEMA = """
CREATE TABLE members (
    member_id TEXT PRIMARY KEY CHECK (length(member_id) > 0 AND member_id = trim(member_id))
) STRICT;

{entries}

CREATE TABLE shares (
    entry_id  INTEGER NOT NULL REFERENCES entries (entry_id),
    member_id TEXT NOT NULL REFERENCES members (member_id),
    cents     INTEGER NOT NULL CHECK (cents >= 0),
    PRIMARY KEY (entry_id, member_id)
) STRICT;
""".replace("{entries}", _ENTRIES_TABLE.format(name="entries") + ";")


class StoreError(Exception):
    """The database file is missing, not a SplitLedger file, or damaged."""


class LedgerStore:
    """A household ledger backed by a SQLite file.

    Use `LedgerStore.create(path, members)` for a new household and
    `LedgerStore.open(path)` for an existing one. Close it with `close()`, or use
    it in a `with` block.
    """

    def __init__(self, connection: sqlite3.Connection, path: Path) -> None:
        self._conn = connection
        self.path = path

    # --- opening and creating -----------------------------------------------

    @classmethod
    def create(cls, path: str | Path, members: Iterable[str]) -> LedgerStore:
        """Create a new household file. Refuses to overwrite an existing file."""
        path = Path(path)
        members = list(members)
        Ledger(members)  # validate the member list before touching the disk
        if path.exists():
            raise StoreError(f"{path} already exists; open it instead of creating it")
        conn = _connect(path, mode="rwc")
        try:
            conn.execute("BEGIN")
            for statement in _SCHEMA.split(";"):
                if statement.strip():
                    conn.execute(statement)
            conn.executemany("INSERT INTO members (member_id) VALUES (?)", [(m,) for m in members])
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            conn.execute("COMMIT")
        except BaseException:
            conn.close()
            path.unlink(missing_ok=True)
            raise
        return cls(conn, path)

    @classmethod
    def open(cls, path: str | Path, *, validate: bool = True) -> LedgerStore:
        """Open an existing household file and check that it is a valid SplitLedger file.

        validate=True also replays the whole history now, so a damaged file fails
        immediately. Callers that call load() right away anyway (each web request)
        pass False to avoid replaying twice; load() runs the same validation.
        """
        path = Path(path)
        if not path.is_file():
            # sqlite3.connect would silently create a new, empty database here.
            raise StoreError(f"{path} does not exist; create a household first")
        conn = _connect(path, mode="rw")
        try:
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_schema WHERE type = 'table'")}
        except sqlite3.DatabaseError as exc:
            conn.close()
            raise StoreError(f"{path} is not a SQLite database: {exc}") from None
        readable = version == SCHEMA_VERSION or version in _MIGRATIONS
        if not readable or not {"members", "entries", "shares"} <= tables:
            conn.close()
            raise StoreError(
                f"{path} is not a SplitLedger file this version can read "
                f"(schema version {version}, expected {SCHEMA_VERSION} or older)"
            )
        if version < SCHEMA_VERSION:
            try:
                _upgrade(conn, path, version)
            except BaseException:
                conn.close()
                raise
        store = cls(conn, path)
        if validate:
            store.load()  # fail now, with a clear message, if the stored history is damaged
        return store

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> LedgerStore:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # --- reading ------------------------------------------------------------

    def load(self) -> Ledger:
        """Rebuild the ledger by replaying every stored entry through its validation."""
        return self._load(self._conn)

    @staticmethod
    def _load(conn: sqlite3.Connection) -> Ledger:
        members = [row[0] for row in conn.execute("SELECT member_id FROM members ORDER BY member_id")]
        try:
            ledger = Ledger(members)
        except LedgerError as exc:
            raise StoreError(f"stored member list is invalid: {exc}") from None

        shares: dict[int, dict[str, int]] = {}
        for entry_id, member, cents in conn.execute(
            "SELECT entry_id, member_id, cents FROM shares ORDER BY entry_id, member_id"
        ):
            shares.setdefault(entry_id, {})[member] = cents

        rows = conn.execute(
            "SELECT entry_id, kind, amount, from_member, to_member, description, reverses "
            "FROM entries ORDER BY entry_id"
        )
        for entry_id, kind, amount, from_member, to_member, description, reverses in rows:
            try:
                if kind == "reversal":
                    if entry_id in shares:
                        raise LedgerError("a reversal must not have shares")
                    entry = ledger.add_reversal(reverses, description)
                elif kind == "expense":
                    entry = ledger.add_expense(description, from_member, amount, shares.pop(entry_id, {}))
                else:
                    if entry_id in shares:
                        raise LedgerError("a reimbursement must not have shares")
                    entry = ledger.add_reimbursement(from_member, to_member, amount, description)
            except LedgerError as exc:
                raise StoreError(f"stored entry {entry_id} is invalid: {exc}") from None
            if entry.entry_id != entry_id:
                raise StoreError(f"stored entry ids are not consecutive (expected {entry.entry_id}, found {entry_id})")
        if shares:
            raise StoreError(f"shares refer to missing entries: {sorted(shares)}")
        return ledger

    def balances(self) -> dict[str, int]:
        return self.load().balances()

    # --- writing ------------------------------------------------------------

    def add_expense(self, description: str, payer: str, total: int, split: Mapping[str, int]) -> Expense:
        """Validate and save an expense and its shares in one transaction."""
        with self._write() as (conn, ledger):
            entry = ledger.add_expense(description, payer, total, split)
            conn.execute(
                "INSERT INTO entries (entry_id, kind, amount, from_member, description) VALUES (?, 'expense', ?, ?, ?)",
                (entry.entry_id, entry.total, entry.payer, entry.description),
            )
            conn.executemany(
                "INSERT INTO shares (entry_id, member_id, cents) VALUES (?, ?, ?)",
                [(entry.entry_id, member, cents) for member, cents in entry.split.items()],
            )
        return entry

    def add_reimbursement(self, sender: str, receiver: str, amount: int, note: str = "") -> Reimbursement:
        """Validate and save a reimbursement in one transaction."""
        with self._write() as (conn, ledger):
            entry = ledger.add_reimbursement(sender, receiver, amount, note)
            conn.execute(
                "INSERT INTO entries (entry_id, kind, amount, from_member, to_member, description) "
                "VALUES (?, 'reimbursement', ?, ?, ?, ?)",
                (entry.entry_id, entry.amount, entry.sender, entry.receiver, entry.note),
            )
        return entry

    def add_reversal(self, entry_id: int, reason: str) -> Reversal:
        """Cancel an earlier entry, keeping it in the history."""
        with self._write() as (conn, ledger):
            entry = ledger.add_reversal(entry_id, reason)
            conn.execute(
                "INSERT INTO entries (entry_id, kind, description, reverses) VALUES (?, 'reversal', ?, ?)",
                (entry.entry_id, entry.reason, entry.reverses),
            )
        return entry

    def export_csv(self, path: str | Path) -> None:
        """Write the ledger as CSV. Refuses to overwrite the database file itself."""
        if Path(path).resolve() == self.path.resolve():
            raise StoreError(f"refusing to export over the ledger database {self.path}; choose another file name")
        write_csv(self.load(), path)

    def _write(self) -> _WriteTransaction:
        return _WriteTransaction(self._conn)


class _WriteTransaction:
    """BEGIN IMMEDIATE ... COMMIT, rolling back on any error.

    IMMEDIATE takes the write lock before reading, so two programs adding entries
    to the same file at once can't both read entry 7 as "next" and collide.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def __enter__(self) -> tuple[sqlite3.Connection, Ledger]:
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            return self._conn, LedgerStore._load(self._conn)
        except BaseException:
            self._conn.execute("ROLLBACK")
            raise

    def __exit__(self, exc_type, exc, tb) -> None:
        self._conn.execute("ROLLBACK" if exc_type else "COMMIT")


# --- schema upgrades --------------------------------------------------------


def _migrate_1_to_2(conn: sqlite3.Connection) -> None:
    """Rebuild `entries` with the reversal kind and the `reverses` column.

    SQLite can't change a table's CHECK constraints in place, so this follows the
    documented procedure (sqlite.org/lang_altertable.html, section 7): create the new
    table under a temporary name, copy, drop the old table, then rename the new one.
    Renaming the old table first could break the foreign keys that point at it.
    """
    conn.execute(_ENTRIES_TABLE.format(name="new_entries"))
    conn.execute(
        "INSERT INTO new_entries (entry_id, kind, amount, from_member, to_member, description, recorded_at) "
        "SELECT entry_id, kind, amount, from_member, to_member, description, recorded_at FROM entries"
    )
    conn.execute("DROP TABLE entries")
    conn.execute("ALTER TABLE new_entries RENAME TO entries")


# Each function upgrades a file from the version given as its key to the next version.
_MIGRATIONS: dict[int, Callable[[sqlite3.Connection], None]] = {1: _migrate_1_to_2}


def _upgrade(conn: sqlite3.Connection, path: Path, version: int) -> None:
    """Upgrade an older file in place, after saving a backup copy next to it."""
    backup_path = path.with_name(f"{path.name}.v{version}.bak")
    if backup_path.exists():
        raise StoreError(f"can't upgrade {path}: backup {backup_path} already exists; move it aside first")
    backup = sqlite3.connect(backup_path)
    try:
        conn.backup(backup)  # SQLite's online backup API: a consistent copy, even of a busy file
    finally:
        backup.close()

    # Foreign-key enforcement can only be switched outside a transaction.
    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            while version < SCHEMA_VERSION:
                _MIGRATIONS[version](conn)
                version += 1
            problems = conn.execute("PRAGMA foreign_key_check").fetchall()
            if problems:
                raise StoreError(f"upgrade of {path} would break references: {problems[:5]}")
            conn.execute(f"PRAGMA user_version = {version}")
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.execute("PRAGMA foreign_keys = ON")


def _connect(path: Path, mode: str) -> sqlite3.Connection:
    # A file: URI with an explicit mode: "rw" fails on a missing file instead of creating one.
    uri = f"{path.resolve().as_uri()}?mode={mode}"
    # isolation_level=None: no hidden transactions; this module issues BEGIN/COMMIT itself.
    conn = sqlite3.connect(uri, uri=True, isolation_level=None)
    conn.execute("PRAGMA foreign_keys = ON")  # off by default in SQLite, per connection
    return conn
