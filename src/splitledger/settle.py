"""Suggest transfers that bring every balance to zero.

Two methods:

- Greedy: the member who owes the most pays the member who is owed the most, as
  much as possible, and repeat. Every transfer clears at least one member, so it
  never needs more than (members with a nonzero balance - 1) transfers. It is
  fast, but it does not always find the fewest transfers.

- Exact: the fewest transfers equals (members with a nonzero balance) minus
  (the largest number of separate groups whose balances each sum to zero),
  because each such group can settle internally with one transfer fewer than
  its size, and no group can do better. Finding that partition is NP-hard in
  general, but a household is small: dynamic programming over subsets handles
  up to EXACT_LIMIT members quickly.

`settle` uses the exact method when the group is small enough and greedy
otherwise, and says which one it used.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache

from splitledger.money import check_cents, format_cents

# Members with a nonzero balance. The exact method visits about 3**n subset pairs,
# so 12 means roughly half a million steps: well under a second.
EXACT_LIMIT = 12


class SettleError(ValueError):
    """Balances that can't be settled, for example because they don't sum to zero."""


@dataclass(frozen=True)
class Transfer:
    sender: str
    receiver: str
    amount: int  # cents, always positive

    def __str__(self) -> str:
        return f"{self.sender} pays {self.receiver} {format_cents(self.amount)}"


@dataclass(frozen=True)
class Settlement:
    transfers: tuple[Transfer, ...]
    method: str  # "exact" (fewest possible transfers) or "greedy" (at most n - 1)


def settle(balances: Mapping[str, int]) -> Settlement:
    """Suggest transfers that settle everyone, using the fewest transfers when feasible."""
    _check(balances)
    nonzero = [m for m in sorted(balances) if balances[m] != 0]
    if len(nonzero) <= EXACT_LIMIT:
        return Settlement(settle_exact(balances), "exact")
    return Settlement(settle_greedy(balances), "greedy")


def settle_greedy(balances: Mapping[str, int]) -> tuple[Transfer, ...]:
    """Largest debtor pays largest creditor until everyone is at zero.

    Ties go to the lowest member id, so the result is always the same.
    """
    _check(balances)
    remaining = {m: c for m, c in balances.items() if c != 0}
    transfers: list[Transfer] = []
    while remaining:
        # Each transfer clears at least one member. If only debtors or only creditors are
        # left, the input was unbalanced; stop rather than loop forever.
        if not any(c < 0 for c in remaining.values()) or not any(c > 0 for c in remaining.values()):
            raise SettleError("balances can't be settled: debts and credits don't match")
        debtor = min(remaining, key=lambda m: (remaining[m], m))  # most negative
        creditor = min(remaining, key=lambda m: (-remaining[m], m))  # most positive
        amount = min(-remaining[debtor], remaining[creditor])
        transfers.append(Transfer(debtor, creditor, amount))
        for member, change in ((debtor, amount), (creditor, -amount)):
            remaining[member] += change
            if remaining[member] == 0:
                del remaining[member]
    return _ordered(transfers)


def settle_exact(balances: Mapping[str, int]) -> tuple[Transfer, ...]:
    """The fewest possible transfers, via the largest partition into zero-sum groups."""
    _check(balances)
    members = [m for m in sorted(balances) if balances[m] != 0]
    if len(members) > EXACT_LIMIT:
        raise SettleError(
            f"{len(members)} members have a balance; the exact method supports up to {EXACT_LIMIT}"
        )
    values = [balances[m] for m in members]
    count = len(members)
    subset_sum = [0] * (1 << count)
    for mask in range(1, 1 << count):
        lowest = (mask & -mask).bit_length() - 1
        subset_sum[mask] = subset_sum[mask & (mask - 1)] + values[lowest]

    @lru_cache(maxsize=None)
    def most_groups(mask: int) -> int:
        """Largest number of zero-sum groups that `mask` can be split into."""
        if mask == 0:
            return 0
        lowest = mask & -mask  # every group containing the lowest member; avoids counting twice
        best = 0
        sub = mask
        while sub:
            if sub & lowest and subset_sum[sub] == 0:
                best = max(best, 1 + most_groups(mask ^ sub))
            sub = (sub - 1) & mask
        return best

    transfers: list[Transfer] = []
    mask = (1 << count) - 1
    while mask:
        # Re-walk the choice that achieved the optimum, picking the first one in a fixed
        # order so the answer is deterministic.
        target = most_groups(mask)
        lowest = mask & -mask
        sub = mask
        while sub:
            if sub & lowest and subset_sum[sub] == 0 and 1 + most_groups(mask ^ sub) == target:
                break
            sub = (sub - 1) & mask
        if sub == 0:
            # No zero-sum group contains this member, so the balances can't be settled.
            raise SettleError("balances can't be settled: debts and credits don't match")
        group = {members[i]: values[i] for i in range(count) if sub >> i & 1}
        # Greedy inside one zero-sum group always uses at most (size - 1) transfers.
        transfers.extend(settle_greedy(group))
        mask ^= sub
    most_groups.cache_clear()
    return _ordered(transfers)


def apply_transfers(balances: Mapping[str, int], transfers: tuple[Transfer, ...] | list[Transfer]) -> dict[str, int]:
    """Balances after the transfers are paid. Paying raises the sender and lowers the receiver."""
    result = dict(balances)
    for transfer in transfers:
        result[transfer.sender] += transfer.amount
        result[transfer.receiver] -= transfer.amount
    return result


def _check(balances: Mapping[str, int]) -> None:
    for member, cents in balances.items():
        try:
            check_cents(cents, allow_negative=True)
        except ValueError as exc:
            raise SettleError(f"balance for {member!r}: {exc}") from None
    total = sum(balances.values())
    if total != 0:
        raise SettleError(f"balances must sum to zero to be settled, but they sum to {format_cents(total)}")


def _ordered(transfers: list[Transfer]) -> tuple[Transfer, ...]:
    return tuple(sorted(transfers, key=lambda t: (t.sender, t.receiver)))

