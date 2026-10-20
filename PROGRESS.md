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

## Commands verified

| Where | Python | Command | Result |
|---|---|---|---|
| Claude's Linux workspace | 3.13 | `python -m pytest` | 73 passed |

## Next

- Milestone 2: ledger model. Expenses and reimbursements as entries, per-member balances that
  always sum to zero.

## Concepts to review
- Why money is stored as integer cents, not floats.
- Largest remainder method, and why ties are broken by member id.
- Property-based testing with Hypothesis: invariants instead of single examples.
