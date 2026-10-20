"""Split an expense among members, to the exact cent.

Every function returns {member: cents} whose values add up to exactly `total`.
Results are ordered by member id and never depend on the order members were
given in, so the same expense always splits the same way.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from splitledger.money import check_cents, format_cents


class SplitError(ValueError):
    """The split request is invalid, for example exact amounts that don't add up."""


def split_equal(total: int, members: Iterable[str]) -> dict[str, int]:
    """Split as evenly as possible. Leftover cents go one each to the lowest member ids.

    split_equal(1000, ["ana", "ben", "cal"]) == {"ana": 334, "ben": 333, "cal": 333}
    """
    members = list(members)
    if len(set(members)) != len(members):
        raise SplitError(f"members listed more than once: {_duplicates(members)}")
    return split_shares(total, {member: 1 for member in members})


def split_shares(total: int, shares: Mapping[str, int]) -> dict[str, int]:
    """Split in proportion to whole-number shares, such as {"ana": 2, "ben": 1}.

    Uses the largest remainder method: everyone gets their exact share rounded
    down, then the leftover cents go one at a time to the members whose exact
    share had the largest fractional part. Ties go to the lowest member id.
    """
    check_cents(total)
    _check_members(shares)
    for member, weight in shares.items():
        if isinstance(weight, bool) or not isinstance(weight, int) or weight < 1:
            raise SplitError(f"share for {member!r} must be a whole number of at least 1, got {weight!r}")

    weight_sum = sum(shares.values())
    # Exact share for member m is total * weight / weight_sum. Integer division gives the
    # rounded-down part and the remainder, without any floating point.
    floor_share: dict[str, int] = {}
    remainder: dict[str, int] = {}
    for member, weight in shares.items():
        floor_share[member], remainder[member] = divmod(total * weight, weight_sum)

    leftover = total - sum(floor_share.values())  # always fewer cents than members
    by_priority = sorted(shares, key=lambda m: (-remainder[m], m))
    for member in by_priority[:leftover]:
        floor_share[member] += 1
    return _ordered(floor_share)


def split_exact(total: int, amounts: Mapping[str, int]) -> dict[str, int]:
    """Use amounts given per member, which must add up to the total exactly.

    A member may have 0 (they were part of the expense but owe nothing).
    """
    check_cents(total)
    _check_members(amounts)
    for member, cents in amounts.items():
        try:
            check_cents(cents)
        except ValueError as exc:
            raise SplitError(f"amount for {member!r}: {exc}") from None
    given = sum(amounts.values())
    if given != total:
        difference = total - given
        word = "short of" if difference > 0 else "over"
        raise SplitError(
            f"exact amounts add up to {format_cents(given)}, which is "
            f"{format_cents(abs(difference))} {word} the total of {format_cents(total)}"
        )
    return _ordered(dict(amounts))


def _check_members(mapping: Mapping[str, int]) -> None:
    if not mapping:
        raise SplitError("a split needs at least one member")
    for member in mapping:
        if not isinstance(member, str) or not member.strip() or member != member.strip():
            raise SplitError(f"member id must be non-empty text without surrounding spaces, got {member!r}")


def _ordered(split: dict[str, int]) -> dict[str, int]:
    return {member: split[member] for member in sorted(split)}


def _duplicates(members: list[str]) -> str:
    seen: set[str] = set()
    repeated = sorted({m for m in members if m in seen or seen.add(m)})
    return ", ".join(repr(m) for m in repeated)
