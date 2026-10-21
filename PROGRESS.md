# Progress

## Completed

### Milestone 1: Money model and split rules
- `parse_amount` turns text into integer cents strictly (at most two decimals, no signs,
  separators, or exponents); `format_cents` turns cents back into text.
- `split_equal`, `split_shares` (largest remainder method), and `split_exact` always return
  parts that add up to the total, ordered by member id, independent of input order.
- Hypothesis property tests check the invariants on random inputs: conservation, each share
  within one cent of exact, order independence, equal splits differing by at most one cent.
- Mutation-checked by hand: 7 deliberate bugs (wrong tie-break, float rounding, unchecked exact
  sum, "12.5" parsed as 12.05, Unicode digits, `True` as a cent) each fail a test.

### Milestone 2: Ledger and balances
- `Ledger(members)` records expenses (`add_expense`) and reimbursements (`add_reimbursement`)
  as frozen, numbered entries; a rejected entry leaves the ledger unchanged.
- Balances are recomputed from entries, never stored: positive means owed, negative means owes.
- `statement(member)` explains a balance line by line with a running total.
- Hypothesis checks on random histories: balances sum to zero, each statement ends at the
  member's balance, a reimbursement changes exactly two balances by the amount.
- Mutation-checked: 7 deliberate bugs (flipped reimbursement, dropped payer share, unchecked
  split total, shared mutable split, self-reimbursement, zero amounts, missing statement lines)
  each fail a test.

### Milestone 3: Settlement
- `settle(balances)` suggests transfers that bring everyone to zero and reports its method:
  `exact` (fewest possible transfers) for up to 12 members with a nonzero balance, `greedy`
  (at most n - 1 transfers) above that.
- Exact method: fewest transfers = nonzero members minus the most zero-sum groups; found by
  dynamic programming over subsets (about 3^12 steps at the limit).
- A searched counterexample shows greedy using 4 transfers where 3 suffice.
- Tested against an independent brute force over every partition (up to 7 members), plus
  invariants: transfers settle everyone, only flow from debtors to creditors, never exceed
  n - 1, exact never worse than greedy, input order doesn't matter.
- Mutation testing found two real defects: with unbalanced input that got past validation,
  both `settle_greedy` and `settle_exact` looped forever. Both now stop with `SettleError`,
  and a regression test covers it.

### Milestone 4: SQLite persistence
- `LedgerStore.create(path, members)` and `LedgerStore.open(path)`; `add_expense`,
  `add_reimbursement`, `load()` and `balances()` read and write one household file.
- Three layers of integrity: ledger validation before writing, `STRICT` tables with CHECK and
  FOREIGN KEY constraints, and full replay validation on load and open.
- Each write is one `BEGIN IMMEDIATE` transaction; a failure part-way rolls back completely.
- Refuses to create over an existing file, or to create a file when opening a missing one.
- Mutation testing found three defenses with no test of their own: foreign keys on the store's
  connection, read-write mode never creating files, and the immediate write lock. Each now has
  a test. The concurrency test runs two real writers in separate threads and checks that the
  deferred-lock version fails with "database is locked".

### Milestone 5: Corrections, schema upgrade, and CSV export
- `add_reversal(entry_id, reason)` cancels an earlier entry by adding its negation; it can't
  reverse twice or reverse a reversal. Statements and balances include reversals.
- Schema version 2 adds reversals. Opening a version 1 file backs it up, then rebuilds
  `entries` following SQLite's documented ALTER TABLE procedure, all in one transaction.
  Tested on a real version 1 file written by the milestone 4 code.
- `to_csv` / `write_csv` / `LedgerStore.export_csv`: per-member rows that add up to the
  balances, CSV-injection protection, UTF-8 with BOM, atomic write, and a refusal to export over
  the database file.
- Mutation testing found two tests passing for the wrong reason: a "reverses a later entry"
  row was really rejected by the foreign key, and a rollback test passed only because closing the
  connection discarded the work. Both tests now isolate the rule they check.

## Commands verified

| Where | Python | Command | Result |
|---|---|---|---|
| Claude's Linux workspace | 3.13 | `python -m pytest` | 73 passed (milestone 1) |
| GitHub Actions | 3.11–3.14, macOS 3.13 | `tests` workflow on `6de6783` | passed (25 s) |
| Claude's Linux workspace | 3.13 | `python -m pytest`, dev and ci profiles | 111 passed (milestone 2) |
| Claude's Linux workspace | 3.13 | `python -m pytest`, dev and ci profiles | 137 passed (milestone 3) |
| Claude's Linux workspace | 3.11, 3.13, 3.14 (SQLite 3.45–3.53) | `python -m pytest` | 168 passed (milestone 4) |
| Claude's Linux workspace | 3.11, 3.13, 3.14 | `python -m pytest`, dev and ci profiles | 212 passed (milestone 5) |

## Next

- Milestone 6: an interface for the household (decide between a small web UI and an API first).

## Concepts to review
- Why money is stored as integer cents, not floats.
- Largest remainder method, and why ties are broken by member id.
- Property-based testing with Hypothesis: invariants instead of single examples.
- Deriving state from an append-only history instead of storing totals.
- Why the ledger copies the split into a read-only mapping.
- Greedy vs exact settlement; why the minimum is (nonzero members - zero-sum groups).
- Bitmask dynamic programming over subsets, and why only groups containing the lowest member
  are tried.
- Loop termination: why every loop needs a guaranteed way to end.
- SQLite: transactions, `BEGIN IMMEDIATE` vs deferred, foreign keys off by default, `STRICT`
  tables, URI open modes.
- Why some rules live in the database and others are checked by replaying history.
- Append-only history and reversal entries instead of edits.
- Schema migrations: version numbers, backups, SQLite's table-rebuild procedure.
- CSV injection and how to neutralize it.
