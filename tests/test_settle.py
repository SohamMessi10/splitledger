import pytest
from hypothesis import given
from hypothesis import strategies as st

from splitledger import (
    EXACT_LIMIT,
    Ledger,
    SettleError,
    Transfer,
    apply_transfers,
    settle,
    settle_exact,
    settle_greedy,
    split_equal,
    split_shares,
)

# ana owes 60, ben owes 50; cal, dee, eli are owed 20, 40, 50 (all in cents).
GREEDY_LOSES = {"ana": -6000, "ben": -5000, "cal": 2000, "dee": 4000, "eli": 5000}


# --- the counterexample -----------------------------------------------------


def test_greedy_is_not_always_minimal():
    # Greedy pairs the biggest debtor with the biggest creditor and leaves two small debts.
    assert settle_greedy(GREEDY_LOSES) == (
        Transfer("ana", "cal", 1000),
        Transfer("ana", "eli", 5000),
        Transfer("ben", "cal", 1000),
        Transfer("ben", "dee", 4000),
    )


def test_exact_finds_the_three_transfer_settlement():
    # ben -> eli is a zero-sum pair on its own, which greedy never notices.
    assert settle_exact(GREEDY_LOSES) == (
        Transfer("ana", "cal", 2000),
        Transfer("ana", "dee", 4000),
        Transfer("ben", "eli", 5000),
    )


def test_settle_reports_which_method_it_used():
    result = settle(GREEDY_LOSES)
    assert result.method == "exact"
    assert len(result.transfers) == 3


# --- simple cases -----------------------------------------------------------


def test_nothing_to_settle():
    assert settle({"ana": 0, "ben": 0}).transfers == ()
    assert settle({}).transfers == ()


def test_one_debt():
    assert settle({"ana": 500, "ben": -500}).transfers == (Transfer("ben", "ana", 500),)


def test_one_creditor_many_debtors():
    transfers = settle({"ana": 6000, "ben": -2000, "cal": -2000, "dee": -2000}).transfers
    assert transfers == (Transfer("ben", "ana", 2000), Transfer("cal", "ana", 2000), Transfer("dee", "ana", 2000))


def test_transfer_text():
    assert str(Transfer("ben", "ana", 2107)) == "ben pays ana 21.07"


def test_greedy_ties_break_by_member_id():
    assert settle_greedy({"cal": -100, "ben": -100, "ana": 200}) == (
        Transfer("ben", "ana", 100),
        Transfer("cal", "ana", 100),
    )


# --- invalid input ----------------------------------------------------------


@pytest.mark.parametrize("function", [settle, settle_exact, settle_greedy])
def test_balances_must_sum_to_zero(function):
    with pytest.raises(SettleError, match=r"must sum to zero.*sum to 0\.01"):
        function({"ana": 501, "ben": -500})


@pytest.mark.parametrize("value", [1.5, True, "5"])
def test_balances_must_be_integer_cents(value):
    with pytest.raises(SettleError, match="balance for 'ben'"):
        settle({"ana": 0, "ben": value})


def test_exact_refuses_groups_above_the_limit():
    balances = {f"m{i:02d}": (100 if i % 2 else -100) for i in range(EXACT_LIMIT + 2)}
    with pytest.raises(SettleError, match="supports up to"):
        settle_exact(balances)


def test_large_groups_fall_back_to_greedy():
    balances = {f"m{i:02d}": (100 if i % 2 else -100) for i in range(EXACT_LIMIT + 2)}
    result = settle(balances)
    assert result.method == "greedy"
    assert set(apply_transfers(balances, result.transfers).values()) == {0}


def test_zero_balances_do_not_count_toward_the_limit():
    balances = {f"z{i:02d}": 0 for i in range(50)} | {"ana": 100, "ben": -100}
    assert settle(balances).method == "exact"


# --- with the ledger --------------------------------------------------------


def test_recording_the_suggested_transfers_settles_the_ledger():
    ledger = Ledger(["ana", "ben", "cal", "dee"])
    ledger.add_expense("rent", "ana", 240_000, split_shares(240_000, {"ana": 2, "ben": 1, "cal": 1, "dee": 1}))
    ledger.add_expense("groceries", "ben", 8430, split_equal(8430, ledger.members))
    ledger.add_expense("internet", "cal", 6000, split_equal(6000, ["ben", "cal", "dee"]))

    for transfer in settle(ledger.balances()).transfers:
        ledger.add_reimbursement(transfer.sender, transfer.receiver, transfer.amount, "settle up")
    assert set(ledger.balances().values()) == {0}


# --- invariants (Hypothesis) ------------------------------------------------


@st.composite
def balance_sets(draw, max_members=10):
    names = draw(st.lists(st.sampled_from("abcdefghijklmn"), min_size=1, max_size=max_members, unique=True))
    values = [draw(st.integers(-50_000, 50_000)) for _ in names[:-1]]
    values.append(-sum(values))
    if draw(st.booleans()):  # small values create many zero-sum groups, the hard case
        values = [draw(st.integers(-3, 3)) for _ in names[:-1]]
        values.append(-sum(values))
    return dict(zip(names, values))


@given(balance_sets())
def test_every_method_settles_everyone(balances):
    for transfers in (settle(balances).transfers, settle_exact(balances), settle_greedy(balances)):
        assert set(apply_transfers(balances, transfers).values()) <= {0}


@given(balance_sets())
def test_transfers_only_flow_from_debtors_to_creditors(balances):
    for transfers in (settle_exact(balances), settle_greedy(balances)):
        for t in transfers:
            assert t.amount > 0
            assert balances[t.sender] < 0 < balances[t.receiver]


@given(balance_sets())
def test_never_more_than_n_minus_one_transfers(balances):
    nonzero = sum(1 for v in balances.values() if v)
    bound = max(nonzero - 1, 0)
    assert len(settle_greedy(balances)) <= bound
    assert len(settle_exact(balances)) <= bound


@given(balance_sets())
def test_exact_is_never_worse_than_greedy(balances):
    assert len(settle_exact(balances)) <= len(settle_greedy(balances))


@given(balance_sets(), st.randoms())
def test_input_order_does_not_change_the_result(balances, rnd):
    items = list(balances.items())
    rnd.shuffle(items)
    assert settle(dict(items)) == settle(balances)


def _partitions(items):
    """Every way to split `items` into non-empty groups (independent of settle.py)."""
    if not items:
        yield []
        return
    first, rest = items[0], items[1:]
    for partition in _partitions(rest):
        yield [[first]] + partition
        for i in range(len(partition)):
            yield partition[:i] + [[first] + partition[i]] + partition[i + 1 :]


def brute_force_minimum(balances):
    values = [v for v in balances.values() if v]
    most = max(sum(1 for group in p if sum(group) == 0) for p in _partitions(values)) if values else 0
    return len(values) - most


@given(balance_sets(max_members=7))
def test_exact_matches_an_independent_brute_force(balances):
    assert len(settle_exact(balances)) == brute_force_minimum(balances)


def test_brute_force_agrees_on_the_counterexample():
    assert brute_force_minimum(GREEDY_LOSES) == 3


def test_settling_stops_instead_of_looping_if_validation_is_bypassed(monkeypatch):
    # Regression test: unbalanced input that got past _check used to make both
    # settle_greedy and settle_exact loop forever. It must stop with an error instead.
    import importlib

    # `splitledger.settle` the function shadows the module of the same name, so import it by path.
    settle_module = importlib.import_module("splitledger.settle")

    monkeypatch.setattr(settle_module, "_check", lambda balances: None)
    for function in (settle_module.settle_greedy, settle_module.settle_exact):
        for unbalanced in ({"ana": 500, "ben": -300}, {"ana": -500, "ben": 300}, {"ana": 100}):
            with pytest.raises(SettleError, match="debts and credits don't match"):
                function(unbalanced)
