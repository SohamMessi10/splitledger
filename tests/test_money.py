import pytest
from hypothesis import given
from hypothesis import strategies as st

from splitledger import MAX_CENTS, MoneyError, check_cents, format_cents, parse_amount


@pytest.mark.parametrize(
    "text, cents",
    [("0", 0), ("12", 1200), ("12.3", 1230), ("12.34", 1234), ("0.05", 5), ("007.10", 710), ("1000000000", MAX_CENTS)],
)
def test_valid_amounts(text, cents):
    assert parse_amount(text) == cents


@pytest.mark.parametrize(
    "text",
    [
        "", " ", "12.345", "12.", ".5", "-1", "+1", "1,000", "$5", "1e3", " 5", "5 ",
        "1_000", "nan", "inf", "١٢", "12.3.4", "1000000000.01",
    ],
)
def test_invalid_amounts_are_rejected_not_guessed(text):
    with pytest.raises(MoneyError):
        parse_amount(text)


def test_too_many_decimals_explains_the_format():
    with pytest.raises(MoneyError, match="up to two decimal places"):
        parse_amount("12.345")


def test_non_text_input_is_rejected():
    with pytest.raises(MoneyError, match="must be text"):
        parse_amount(12.34)


@pytest.mark.parametrize("cents, text", [(0, "0.00"), (5, "0.05"), (1234, "12.34"), (-5, "-0.05"), (-1234, "-12.34"), (100, "1.00")])
def test_format(cents, text):
    assert format_cents(cents) == text


@given(st.integers(min_value=0, max_value=MAX_CENTS))
def test_format_then_parse_round_trips(cents):
    assert parse_amount(format_cents(cents)) == cents


@pytest.mark.parametrize("value", [True, 1.0, "5", None])
def test_check_cents_rejects_non_integers(value):
    with pytest.raises(MoneyError, match="must be an int"):
        check_cents(value)


def test_check_cents_limits():
    assert check_cents(MAX_CENTS) == MAX_CENTS
    assert check_cents(-5, allow_negative=True) == -5
    with pytest.raises(MoneyError, match="must not be negative"):
        check_cents(-1)
    with pytest.raises(MoneyError, match="above the limit"):
        check_cents(MAX_CENTS + 1)
