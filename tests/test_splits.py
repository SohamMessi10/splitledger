import random

import pytest
from hypothesis import given
from hypothesis import strategies as st

from splitledger import MAX_CENTS, MoneyError, SplitError, split_equal, split_exact, split_shares

# --- equal ------------------------------------------------------------------


def test_ten_dollars_three_ways_loses_no_cent():
    assert split_equal(1000, ["ana", "ben", "cal"]) == {"ana": 334, "ben": 333, "cal": 333}


def test_leftover_cents_go_to_lowest_ids():
    assert split_equal(1002, ["dee", "cal", "ben", "ana"]) == {"ana": 251, "ben": 251, "cal": 250, "dee": 250}


def test_even_split_has_no_leftover():
    assert split_equal(900, ["ana", "ben", "cal"]) == {"ana": 300, "ben": 300, "cal": 300}


def test_fewer_cents_than_members():
    assert split_equal(2, ["ana", "ben", "cal"]) == {"ana": 1, "ben": 1, "cal": 0}


def test_zero_total():
    assert split_equal(0, ["ana", "ben"]) == {"ana": 0, "ben": 0}


def test_single_member_gets_everything():
    assert split_equal(1234, ["ana"]) == {"ana": 1234}


def test_result_is_ordered_by_member_id():
    assert list(split_equal(1000, ["cal", "ana", "ben"])) == ["ana", "ben", "cal"]


def test_duplicate_members_are_rejected():
    with pytest.raises(SplitError, match="listed more than once: 'ana'"):
        split_equal(1000, ["ana", "ben", "ana"])


def test_no_members():
    with pytest.raises(SplitError, match="at least one member"):
        split_equal(1000, [])


# --- shares -----------------------------------------------------------------


def test_shares_in_proportion():
    # Rent of 1000.00 where ana's room counts double.
    assert split_shares(100_000, {"ana": 2, "ben": 1, "cal": 1}) == {"ana": 50_000, "ben": 25_000, "cal": 25_000}


def test_leftover_goes_to_largest_remainder_not_alphabetical():
    # Exact shares: ana 1/3 * 100 = 33.33, ben 2/3 * 100 = 66.67. Ben's fraction is larger.
    assert split_shares(100, {"ana": 1, "ben": 2}) == {"ana": 33, "ben": 67}


def test_tied_remainders_break_by_member_id():
    assert split_shares(100, {"cal": 1, "ben": 1, "ana": 1}) == {"ana": 34, "ben": 33, "cal": 33}


@pytest.mark.parametrize("weight", [0, -1, 1.5, True, "2", None])
def test_invalid_share_weights(weight):
    with pytest.raises(SplitError, match="whole number of at least 1"):
        split_shares(1000, {"ana": 1, "ben": weight})


@pytest.mark.parametrize("member", ["", " ", " ana", "ana ", 7])
def test_invalid_member_ids(member):
    with pytest.raises(SplitError, match="member id"):
        split_shares(1000, {member: 1})


def test_negative_or_non_int_total_is_rejected():
    with pytest.raises(MoneyError):
        split_shares(-100, {"ana": 1})
    with pytest.raises(MoneyError):
        split_shares(10.0, {"ana": 1})


# --- exact ------------------------------------------------------------------


def test_exact_amounts_that_add_up():
    assert split_exact(1000, {"ben": 400, "ana": 600, "cal": 0}) == {"ana": 600, "ben": 400, "cal": 0}


def test_exact_amounts_short_of_total():
    with pytest.raises(SplitError, match=r"add up to 9\.99, which is 0\.01 short of the total of 10\.00"):
        split_exact(1000, {"ana": 500, "ben": 499})


def test_exact_amounts_over_total():
    with pytest.raises(SplitError, match=r"0\.50 over the total"):
        split_exact(1000, {"ana": 1050, "ben": 0})


def test_exact_negative_amount():
    with pytest.raises(SplitError, match="amount for 'ben'.*must not be negative"):
        split_exact(1000, {"ana": 1100, "ben": -100})


# --- invariants, checked on many random inputs (Hypothesis) -----------------

member_ids = st.text(alphabet="abcdefghij", min_size=1, max_size=4)
totals = st.integers(min_value=0, max_value=MAX_CENTS)
share_maps = st.dictionaries(member_ids, st.integers(min_value=1, max_value=50), min_size=1, max_size=12)


@given(totals, share_maps)
def test_shares_always_add_up_to_the_total(total, shares):
    assert sum(split_shares(total, shares).values()) == total


@given(totals, share_maps)
def test_each_share_is_within_one_cent_of_exact(total, shares):
    weight_sum = sum(shares.values())
    for member, cents in split_shares(total, shares).items():
        exact_times_sum = total * shares[member]  # exact share, scaled by weight_sum
        assert cents * weight_sum <= exact_times_sum + weight_sum  # at most 1 cent above
        assert cents * weight_sum > exact_times_sum - weight_sum  # less than 1 cent below


@given(totals, share_maps, st.randoms())
def test_input_order_never_changes_the_result(total, shares, rnd):
    items = list(shares.items())
    rnd.shuffle(items)
    assert split_shares(total, dict(items)) == split_shares(total, shares)


@given(totals, st.lists(member_ids, min_size=1, max_size=12, unique=True))
def test_equal_split_differs_by_at_most_one_cent(total, members):
    values = split_equal(total, members).values()
    assert max(values) - min(values) <= 1
    assert sum(values) == total


@given(totals, st.lists(member_ids, min_size=1, max_size=12, unique=True))
def test_split_is_repeatable(total, members):
    assert split_equal(total, members) == split_equal(total, list(reversed(members)))


def test_many_random_exact_splits_conserve_money():
    rnd = random.Random(0)
    for _ in range(200):
        parts = [rnd.randint(0, 10_000) for _ in range(rnd.randint(1, 6))]
        amounts = {f"m{i}": cents for i, cents in enumerate(parts)}
        assert sum(split_exact(sum(parts), amounts).values()) == sum(parts)
