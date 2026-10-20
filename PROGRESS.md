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

## Commands verified

| Where | Python | Command | Result |
|---|---|---|---|
| Claude's Linux workspace | 3.13 | `python -m pytest` | 73 passed (milestone 1) |
| GitHub Actions | 3.11–3.14, macOS 3.13 | `tests` workflow on `6de6783` | passed (25 s) |
| Claude's Linux workspace | 3.13 | `python -m pytest`, dev and ci profiles | 111 passed (milestone 2) |

## Next

- Milestone 3: settlement. Suggest transfers that bring every balance to zero.

## Concepts to review
- Why money is stored as integer cents, not floats.
- Largest remainder method, and why ties are broken by member id.
- Property-based testing with Hypothesis: invariants instead of single examples.
- Deriving state from an append-only history instead of storing totals.
- Why the ledger copies the split into a read-only mapping.
