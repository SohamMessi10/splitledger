"""Create the demo household used in the README: four roommates, one month.

    python examples/seed_demo.py demo.db
    splitledger serve demo.db

All names and amounts are made up.
"""

from __future__ import annotations

import sys

from splitledger import LedgerStore, parse_amount, split_equal, split_exact, split_shares

ROOMMATES = ["ana", "ben", "cal", "dee"]


def seed(path: str) -> LedgerStore:
    store = LedgerStore.create(path, ROOMMATES)
    rent = parse_amount("2400.00")
    # Ana has the large bedroom, so her share of the rent counts double.
    store.add_expense("rent (October)", "ana", rent, split_shares(rent, {"ana": 2, "ben": 1, "cal": 1, "dee": 1}))
    groceries = parse_amount("84.30")
    store.add_expense("groceries", "ben", groceries, split_equal(groceries, ROOMMATES))
    internet = parse_amount("60.00")
    # Ana works from campus and opted out of the internet plan.
    store.add_expense("internet", "cal", internet, split_equal(internet, ["ben", "cal", "dee"]))
    # Ben accidentally entered the groceries twice; the duplicate is reversed, not deleted.
    store.add_expense("groceries", "ben", groceries, split_equal(groceries, ROOMMATES))
    store.add_reversal(4, "entered twice")
    takeout = parse_amount("45.00")
    store.add_expense("takeout", "dee", takeout, split_exact(takeout, {"cal": parse_amount("20.00"), "dee": parse_amount("25.00")}))
    store.add_reimbursement("dee", "ana", parse_amount("200.00"), "venmo")
    return store


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: python examples/seed_demo.py DEMO.db")
    with seed(sys.argv[1]) as store:
        print(f"created {sys.argv[1]} with {len(store.load().entries)} entries")
