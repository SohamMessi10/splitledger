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

## Commands verified

| Where | Python | Command | Result |
|---|---|---|---|
| Claude's Linux workspace | 3.13 | `python -m pytest` | 73 passed (milestone 1) |
| GitHub Actions | 3.11–3.14, macOS 3.13 | `tests` workflow on `6de6783` | passed (25 s) |
| Claude's Linux workspace | 3.13 | `python -m pytest`, dev and ci profiles | 111 passed (milestone 2) |
| Claude's Linux workspace | 3.13 | `python -m pytest`, dev and ci profiles | 137 passed (milestone 3) |

## Next

- Milestone 4: SQLite persistence, so a household's ledger survives between runs.

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
