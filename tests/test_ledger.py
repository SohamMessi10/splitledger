from types import MappingProxyType

import pytest
from hypothesis import given
from hypothesis import strategies as st

from splitledger import (
    Expense,
    Ledger,
    LedgerError,
    Reimbursement,
    split_equal,
    split_exact,
    split_shares,
)

ROOMMATES = ["ana", "ben", "cal", "dee"]


@pytest.fixture
def ledger():
    return Ledger(ROOMMATES)


# --- group setup ------------------------------------------------------------


def test_members_are_sorted_and_start_at_zero():
    ledger = Ledger(["dee", "ana", "cal", "ben"])
    assert ledger.members == ("ana", "ben", "cal", "dee")
    assert ledger.balances() == {"ana": 0, "ben": 0, "cal": 0, "dee": 0}
    assert ledger.entries == ()


@pytest.mark.parametrize(
    "members, message",
    [([], "at least one member"), (["ana", "ana"], "unique"), (["ana", " ben"], "member id"), (["", "ben"], "member id")],
)
def test_invalid_groups(members, message):
    with pytest.raises(LedgerError, match=message):
        Ledger(members)


# --- expenses ---------------------------------------------------------------


def test_payer_is_credited_and_everyone_charged_their_share(ledger):
    ledger.add_expense("groceries", "ana", 8000, split_equal(8000, ROOMMATES))
    assert ledger.balances() == {"ana": 6000, "ben": -2000, "cal": -2000, "dee": -2000}


def test_payer_outside_the_split(ledger):
    # Ana buys a gift for ben's birthday dinner that only cal and dee share.
    ledger.add_expense("gift", "ana", 3000, split_equal(3000, ["cal", "dee"]))
    assert ledger.balances() == {"ana": 3000, "ben": 0, "cal": -1500, "dee": -1500}


def test_shares_and_exact_splits_are_accepted(ledger):
    ledger.add_expense("rent", "ben", 200_000, split_shares(200_000, {"ana": 2, "ben": 1, "cal": 1}))
    ledger.add_expense("takeout", "cal", 4500, split_exact(4500, {"cal": 2000, "dee": 2500}))
    assert ledger.balances() == {"ana": -100_000, "ben": 150_000, "cal": -47_500, "dee": -2500}


def test_entries_get_increasing_ids_and_are_frozen(ledger):
    first = ledger.add_expense("a", "ana", 100, {"ana": 100})
    second = ledger.add_reimbursement("ben", "ana", 50)
    assert (first.entry_id, second.entry_id) == (1, 2)
    assert ledger.entries == (first, second)
    with pytest.raises(AttributeError):
        first.total = 1  # frozen dataclass


def test_changing_the_callers_split_dict_does_not_change_history(ledger):
    split = {"ana": 500, "ben": 500}
    entry = ledger.add_expense("snacks", "ana", 1000, split)
    split["ana"] = 0
    assert entry.split == {"ana": 500, "ben": 500}
    assert isinstance(entry.split, MappingProxyType)
    with pytest.raises(TypeError):
        entry.split["ana"] = 0


def test_description_is_trimmed(ledger):
    assert ledger.add_expense("  milk  ", "ana", 100, {"ana": 100}).description == "milk"


@pytest.mark.parametrize(
    "kwargs, message",
    [
        ({"payer": "zoe"}, "payer 'zoe' is not a member"),
        ({"split": {"ana": 500, "zoe": 500}}, "split member 'zoe' is not a member"),
        ({"total": 0, "split": {"ana": 0}}, "must be more than 0.00"),
        ({"total": -1000}, "must not be negative"),
        ({"total": 10.0}, "must be an int"),
        ({"split": {"ana": 600, "ben": 500}}, r"split adds up to 11\.00 but the expense total is 10\.00"),
        ({"split": {"ana": 1100, "ben": -100}}, "share for 'ben'"),
        ({"split": {}}, "at least one member"),
        ({"description": "   "}, "description must not be empty"),
        ({"description": "x" * 201}, "longer than 200"),
        ({"description": None}, "must be text"),
    ],
)
def test_rejected_expenses_leave_the_ledger_unchanged(ledger, kwargs, message):
    args = {"description": "dinner", "payer": "ana", "total": 1000, "split": {"ana": 500, "ben": 500}}
    args.update(kwargs)
    with pytest.raises(LedgerError, match=message):
        ledger.add_expense(**args)
    assert ledger.entries == ()
    assert set(ledger.balances().values()) == {0}


# --- reimbursements ---------------------------------------------------------


def test_reimbursement_moves_both_balances(ledger):
    ledger.add_expense("groceries", "ana", 8000, split_equal(8000, ROOMMATES))
    ledger.add_reimbursement("ben", "ana", 2000, "venmo")
    assert ledger.balances() == {"ana": 4000, "ben": 0, "cal": -2000, "dee": -2000}


def test_overpaying_is_allowed_and_flips_the_balance(ledger):
    ledger.add_expense("snacks", "ana", 1000, {"ana": 500, "ben": 500})
    ledger.add_reimbursement("ben", "ana", 800)
    assert ledger.balances()["ben"] == 300  # ana now owes ben 3.00


@pytest.mark.parametrize(
    "sender, receiver, amount, message",
    [
        ("ana", "ana", 100, "two different members"),
        ("zoe", "ana", 100, "sender 'zoe'"),
        ("ana", "zoe", 100, "receiver 'zoe'"),
        ("ben", "ana", 0, "more than 0.00"),
        ("ben", "ana", -5, "must not be negative"),
    ],
)
def test_rejected_reimbursements(ledger, sender, receiver, amount, message):
    with pytest.raises(LedgerError, match=message):
        ledger.add_reimbursement(sender, receiver, amount)
    assert ledger.entries == ()


# --- statements -------------------------------------------------------------


def test_statement_explains_the_balance_line_by_line(ledger):
    ledger.add_expense("groceries", "ana", 8430, split_equal(8430, ROOMMATES))
    ledger.add_expense("internet", "ben", 6000, split_equal(6000, ["ben", "cal", "dee"]))
    ledger.add_reimbursement("cal", "ana", 2107, "venmo")

    lines = ledger.statement("cal")
    assert [(l.entry_id, l.change, l.running_balance, l.text) for l in lines] == [
        (1, -2107, -2107, "share of 'groceries' paid by ana: 21.07"),
        (2, -2000, -4107, "share of 'internet' paid by ben: 20.00"),
        (3, 2107, -2000, "paid ana back 21.07 (venmo)"),
    ]
    assert lines[-1].running_balance == ledger.balances()["cal"]


def test_statement_for_payer_and_receiver(ledger):
    ledger.add_expense("groceries", "ana", 8000, split_equal(8000, ROOMMATES))
    ledger.add_reimbursement("ben", "ana", 2000)
    assert [l.text for l in ledger.statement("ana")] == [
        "paid 80.00 for 'groceries', own share 20.00",
        "received 20.00 from ben",
    ]


def test_statement_skips_unrelated_entries(ledger):
    ledger.add_expense("internet", "ben", 6000, split_equal(6000, ["ben", "cal"]))
    assert ledger.statement("dee") == []


def test_statement_includes_zero_share_participation(ledger):
    ledger.add_expense("takeout", "ana", 1000, split_exact(1000, {"ana": 1000, "dee": 0}))
    [line] = ledger.statement("dee")
    assert (line.change, line.text) == (0, "share of 'takeout' paid by ana: 0.00")


def test_statement_for_non_member(ledger):
    with pytest.raises(LedgerError, match="'zoe' is not a member"):
        ledger.statement("zoe")


# --- invariants on random histories (Hypothesis) ----------------------------


@st.composite
def histories(draw):
    """A random group and a random sequence of valid expenses and reimbursements."""
    members = draw(st.lists(st.sampled_from("abcdefgh"), min_size=2, max_size=8, unique=True))
    steps = []
    for _ in range(draw(st.integers(0, 25))):
        if draw(st.booleans()):
            payer = draw(st.sampled_from(members))
            total = draw(st.integers(1, 1_000_000))
            people = draw(st.lists(st.sampled_from(members), min_size=1, unique=True))
            if draw(st.booleans()):
                split = split_equal(total, people)
            else:
                split = split_shares(total, {p: draw(st.integers(1, 5)) for p in people})
            steps.append(("expense", payer, total, split))
        else:
            sender, receiver = draw(st.lists(st.sampled_from(members), min_size=2, max_size=2, unique=True))
            steps.append(("reimburse", sender, receiver, draw(st.integers(1, 1_000_000))))
    return members, steps


def build(members, steps):
    ledger = Ledger(members)
    for step in steps:
        if step[0] == "expense":
            ledger.add_expense("item", step[1], step[2], step[3])
        else:
            ledger.add_reimbursement(step[1], step[2], step[3])
    return ledger


@given(histories())
def test_balances_always_sum_to_zero(history):
    assert sum(build(*history).balances().values()) == 0


@given(histories())
def test_each_statement_ends_at_that_members_balance(history):
    ledger = build(*history)
    balances = ledger.balances()
    for member in ledger.members:
        lines = ledger.statement(member)
        assert sum(line.change for line in lines) == balances[member]
        if lines:
            assert lines[-1].running_balance == balances[member]


@given(histories(), st.integers(1, 1_000_000))
def test_reimbursement_changes_exactly_two_balances_by_the_amount(history, amount):
    ledger = build(*history)
    sender, receiver = ledger.members[0], ledger.members[1]
    before = ledger.balances()
    ledger.add_reimbursement(sender, receiver, amount)
    after = ledger.balances()
    assert after[sender] - before[sender] == amount
    assert after[receiver] - before[receiver] == -amount
    assert all(after[m] == before[m] for m in ledger.members if m not in (sender, receiver))


@given(histories())
def test_entry_types_and_ids(history):
    ledger = build(*history)
    assert [e.entry_id for e in ledger.entries] == list(range(1, len(ledger.entries) + 1))
    assert all(isinstance(e, (Expense, Reimbursement)) for e in ledger.entries)
