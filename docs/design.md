# SplitLedger design

## Problem

Roommates share groceries, rent, and utilities. One person pays, several people benefit, and
after a few weeks nobody is sure who owes whom. Spreadsheets drift: a cent is lost to rounding,
a reimbursement is entered twice, or a formula silently stops covering new rows.

SplitLedger records who paid, who benefited, and what has been paid back. It then suggests a
short list of transfers that settles everyone up. The ledger must be correct to the cent and
explainable line by line.

## Users and scope

The MVP is a **local app for one group**: one person runs it and records expenses on behalf of
everyone in the household. There are no accounts or logins, so authentication doesn't dominate
the scope. Each group uses one currency. No real bank or payment integration.

## MVP features

1. Create a group and its members.
2. Record an expense: who paid, the amount, and how it is split:
   - **equal** among chosen members,
   - **shares** (for example 2:1:1, such as a larger room paying more),
   - **exact** amounts that must add up to the total.
3. Record a reimbursement: a direct payment from one member to another.
4. Show each member's balance: positive means they are owed money, negative means they owe it.
5. Suggest transfers that bring every balance to zero.
6. Correct mistakes without erasing history (a correction is a new entry that reverses one).
7. Export the ledger to CSV.

## Invariants (what the tests must prove)

- **Splits conserve money.** The parts of every split add up exactly to the expense total.
- **Rounding is deterministic.** The same expense always splits the same way, whatever order
  members were listed in.
- **Balances sum to zero** across the group, after any sequence of expenses and reimbursements.
- **A reimbursement** raises the payer's balance and lowers the receiver's by the same amount.
- **Suggested transfers settle everyone.** Applying them leaves every balance at exactly zero.
- **Settlement claims stay honest.** The fewest transfers equals the number of members with a
  nonzero balance minus the largest number of separate zero-sum groups they can be split into.
  Finding that partition is NP-hard in general, so SplitLedger computes it exactly for up to 12
  members with a nonzero balance (dynamic programming over subsets) and uses a greedy method
  above that. Greedy never needs more than n - 1 transfers but is not always minimal; the tests
  include a concrete case where it uses 4 transfers and the exact method uses 3.

## Money model

- Amounts are stored as **integer cents** (`int`), never `float`. In floating point,
  `0.1 + 0.2 != 0.3`, and those errors accumulate over a ledger.
- User input such as `"12.34"` is parsed strictly: at most two decimal places, no signs, no
  thousands separators, no exponent. `"12.345"` is rejected rather than rounded, so the user
  decides what they meant.
- Splitting uses the **largest remainder method**: give everyone the rounded-down share, then
  hand the leftover cents, one at a time, to those with the biggest fractional remainders. Ties
  go in member-id order, so the result never depends on input order.

## Architecture

```
ledger core (pure functions)   money.py, splits.py, balances.py, settle.py
        |
storage                        SQLite through the standard-library sqlite3 module
        |
interface                      FastAPI: household page (HTML forms) and JSON API
```

The core has no I/O, so every rule is tested directly and exhaustively. Storage and the
interface are thin layers on top.

## Storage

One SQLite file holds one household. It stores entries, never balances.

| Table | Columns | Rules enforced by the database |
|---|---|---|
| `members` | `member_id` | Primary key; non-empty, no surrounding spaces. |
| `entries` | `entry_id`, `kind`, `amount`, `from_member`, `to_member`, `description`, `reverses`, `recorded_at` | `amount` is a positive integer; members must exist; an expense has a description and no receiver; a reimbursement has a receiver different from the sender; a reversal has a reason, no amount or members, and points to an earlier entry. `reverses` is UNIQUE, so an entry can be reversed only once. |
| `shares` | `entry_id`, `member_id`, `cents` | One row per member per expense; `cents` is a non-negative integer; entry and member must exist. |

All tables are `STRICT`, so SQLite rejects a value of the wrong type instead of storing it.
Foreign keys are switched on for every connection, since SQLite leaves them off by default.

"Shares add up to the total" spans several rows, so a CHECK constraint can't express it.
Instead, loading replays every stored entry through the ledger's validation, and a damaged
file is reported rather than producing wrong balances.

Writes use `BEGIN IMMEDIATE`: a writer takes the lock before reading the next entry id, so two
programs writing at once wait for each other instead of failing with "database is locked".
Opening a missing path is an error. Plain `sqlite3.connect` would silently create an empty
database there.

### Corrections

History is never edited or deleted. A mistake is cancelled by a **reversal**: a new entry whose
effect on every balance is exactly the original's, negated, with a required reason. An entry can
be reversed once, and a reversal can't be reversed (record the original item again instead).
This keeps a complete audit trail, the way accounting ledgers handle corrections.

### Schema versions and upgrades

The file's schema version is stored in `PRAGMA user_version`.

| Version | Change |
|---|---|
| 1 | members, entries (expense, reimbursement), shares |
| 2 | entries gain the `reversal` kind and the `reverses` column |

Opening an older file upgrades it in place. Before changing anything, SplitLedger saves a copy
named `<file>.v<old version>.bak` using SQLite's online backup API, and refuses to upgrade if
that backup name is already taken. SQLite can't change CHECK constraints in place, so the
`entries` table is rebuilt following SQLite's documented procedure: foreign keys off, then in
one transaction create `new_entries`, copy, drop `entries`, rename, and run
`PRAGMA foreign_key_check`, then commit. A failure at any step rolls back to the old version.
The upgrade is tested on a real version 1 file written by the version 1 code
(`tests/fixtures/house_v1.db`).

### CSV export

One row per member per entry, with that entry's effect on that member's balance. Summing
`balance_change` per member reproduces the app's balances, so the export can be checked in a
spreadsheet. Text cells starting with `=`, `+`, `-`, `@`, tab, or carriage return get a leading
apostrophe, so a description like `=HYPERLINK(...)` can't run as a formula when the file is
opened (CSV injection). The file is UTF-8 with a byte-order mark, so Excel shows accented names
correctly, and it is written atomically.

## Interface

`splitledger serve house.db` runs a FastAPI app on http://127.0.0.1:8000 with two faces:

- **Household page** (`/`): balances, suggested payments with a "Mark as paid" button,
  forms to add an expense or record a payment, and the history with a reverse option per entry.
  Plain HTML forms, no JavaScript. Errors re-show the form with what was typed and a message.
  A successful post redirects with 303 so refreshing never submits twice.
- **JSON API** (`/api/...`, documented at `/docs`):

| Method and path | Purpose |
|---|---|
| `GET /api/members`, `/api/balances`, `/api/entries` | Read the household |
| `GET /api/statement/{member}` | One member's balance, line by line |
| `GET /api/settlement` | Suggested payments and the method used |
| `POST /api/expenses` | `{description, payer, amount, split: {type: equal / shares / exact, ...}}` |
| `POST /api/reimbursements` | `{sender, receiver, amount, note}` |
| `POST /api/entries/{id}/reverse` | `{reason}` |
| `GET /export.csv` | The CSV export |

Amounts are strings such as `"12.34"`. JSON numbers are rejected, because they arrive in Python
as floats. Request bodies are strict: unknown fields and wrong types are errors. User mistakes
are 422 with a message, a missing entry or member is 404, and a damaged file is 500.

Each request opens its own SQLite connection, since a connection can't be shared across
threads. Form handlers read the form asynchronously, then do all database work in one worker
thread.

**Security for a local app without accounts:** it listens only on 127.0.0.1; it rejects Host
headers other than localhost (DNS rebinding); it refuses state-changing requests whose Origin is
another site (CSRF); it grants no CORS; and templates escape all text, so a description like
`<script>` is shown, not run.

## Tech choices

| Choice | Why |
|---|---|
| Python 3.11+ | Same toolchain as the first project. |
| `int` cents | Exact arithmetic, simple to store in SQLite as `INTEGER`. |
| pytest + Hypothesis | Hypothesis generates many random expenses to try to break the invariants above. |
| SQLite (`sqlite3`) | Built into Python, one file, transactions; no server or paid service. |
| FastAPI + Jinja2 | Typed request validation, automatic API docs, server-rendered pages. Optional (`[web]` extra), so the core stays dependency-free. Pinned to releases at least two weeks old. |

## Milestones

1. Money parsing and formatting, and the three split rules, with property tests.
2. Ledger model: expenses, reimbursements, and balances.
3. Settlement suggestions.
4. SQLite persistence.
5. Corrections (reversal entries), schema upgrade, and CSV export.
6. Web interface: household page and JSON API.
7. README with a four-roommate demo, screenshots, measured performance, and limitations.

All seven are complete; see `PROGRESS.md`.

## Out of scope for the MVP

Multiple currencies, recurring expenses, receipt uploads, user accounts, and shared online access.
