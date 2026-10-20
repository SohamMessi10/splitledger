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
interface                      chosen in the interface milestone (small web UI or API)
```

The core has no I/O, so every rule is tested directly and exhaustively. Storage and the
interface are thin layers on top.

## Tech choices

| Choice | Why |
|---|---|
| Python 3.11+ | Same toolchain as the first project. |
| `int` cents | Exact arithmetic, simple to store in SQLite as `INTEGER`. |
| pytest + Hypothesis | Hypothesis generates many random expenses to try to break the invariants above. |
| SQLite (`sqlite3`) | Built into Python, one file, transactions; no server or paid service. |

## Milestones

1. Money parsing and formatting, and the three split rules, with property tests.
2. Ledger model: expenses, reimbursements, and balances.
3. Settlement suggestions.
4. SQLite persistence.
5. Corrections (reversal entries) and CSV export.
6. Interface.
7. CI, README with a four-roommate demo, and limitations.

## Out of scope for the MVP

Multiple currencies, recurring expenses, receipt uploads, user accounts, and shared online access.
