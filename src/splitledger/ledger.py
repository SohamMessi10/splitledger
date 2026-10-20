"""The ledger: a group's members and the entries that move money between them.

Balances are never stored. They are recomputed from the entries every time, so a
balance can always be explained line by line and can never drift out of sync
with the history that produced it.

Sign convention: a positive balance means the member is owed money; a negative
balance means they owe money. Across the group, balances always sum to zero.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType

from splitledger.money import check_cents, format_cents

MAX_DESCRIPTION = 200


class LedgerError(ValueError):
    """An entry was rejected: unknown member, bad amount, or a split that doesn't add up."""


@dataclass(frozen=True)
class Expense:
    """`payer` paid `total`; `split` says how much of it each member is responsible for."""

    entry_id: int
    description: str
    payer: str
    total: int
    split: Mapping[str, int]

    def effects(self) -> dict[str, int]:
        effects = {member: -share for member, share in self.split.items()}
        effects[self.payer] = effects.get(self.payer, 0) + self.total
        return effects


@dataclass(frozen=True)
class Reimbursement:
    """`sender` paid `amount` directly to `receiver`, outside any expense."""

    entry_id: int
    sender: str
    receiver: str
    amount: int
    note: str = ""

    def effects(self) -> dict[str, int]:
        # Paying someone back raises your balance toward zero and lowers theirs.
        return {self.sender: self.amount, self.receiver: -self.amount}


Entry = Expense | Reimbursement


@dataclass(frozen=True)
class StatementLine:
    entry_id: int
    text: str
    change: int  # how this entry moved the member's balance, in cents
    running_balance: int


class Ledger:
    """A group's members and its entries, in the order they were recorded."""

    def __init__(self, members: Iterable[str]) -> None:
        members = tuple(members)
        if not members:
            raise LedgerError("a group needs at least one member")
        for member in members:
            if not isinstance(member, str) or not member.strip() or member != member.strip():
                raise LedgerError(f"member id must be non-empty text without surrounding spaces, got {member!r}")
        if len(set(members)) != len(members):
            raise LedgerError("member ids must be unique")
        self.members = tuple(sorted(members))
        self._entries: list[Entry] = []

    @property
    def entries(self) -> tuple[Entry, ...]:
        return tuple(self._entries)

    def add_expense(self, description: str, payer: str, total: int, split: Mapping[str, int]) -> Expense:
        """Record an expense. `split` usually comes from split_equal, split_shares, or split_exact."""
        description = self._check_text(description, "description", required=True)
        self._check_member(payer, "payer")
        total = self._check_positive(total, "expense total")
        if not split:
            raise LedgerError("an expense must be split among at least one member")
        for member, share in split.items():
            self._check_member(member, "split member")
            try:
                check_cents(share)
            except ValueError as exc:
                raise LedgerError(f"share for {member!r}: {exc}") from None
        if sum(split.values()) != total:
            raise LedgerError(
                f"split adds up to {format_cents(sum(split.values()))} "
                f"but the expense total is {format_cents(total)}"
            )
        # Copy into a read-only mapping so later changes to the caller's dict can't alter history.
        frozen_split = MappingProxyType({m: split[m] for m in sorted(split)})
        entry = Expense(self._next_id(), description, payer, total, frozen_split)
        self._entries.append(entry)
        return entry

    def add_reimbursement(self, sender: str, receiver: str, amount: int, note: str = "") -> Reimbursement:
        """Record a direct payment from one member to another."""
        self._check_member(sender, "sender")
        self._check_member(receiver, "receiver")
        if sender == receiver:
            raise LedgerError("a reimbursement needs two different members")
        amount = self._check_positive(amount, "reimbursement amount")
        note = self._check_text(note, "note", required=False)
        entry = Reimbursement(self._next_id(), sender, receiver, amount, note)
        self._entries.append(entry)
        return entry

    def balances(self) -> dict[str, int]:
        """Each member's balance in cents, ordered by member id. Always sums to zero."""
        totals = {member: 0 for member in self.members}
        for entry in self._entries:
            for member, change in entry.effects().items():
                totals[member] += change
        return totals

    def statement(self, member: str) -> list[StatementLine]:
        """Every entry that changed `member`'s balance, with a running total."""
        self._check_member(member, "member")
        lines: list[StatementLine] = []
        running = 0
        for entry in self._entries:
            change = entry.effects().get(member, 0)
            if change == 0 and not _involves(entry, member):
                continue
            running += change
            lines.append(StatementLine(entry.entry_id, _describe(entry, member), change, running))
        return lines

    # --- validation helpers -------------------------------------------------

    def _check_member(self, member: str, role: str) -> None:
        if member not in self.members:
            raise LedgerError(f"{role} {member!r} is not a member of this group")

    @staticmethod
    def _check_positive(cents: int, what: str) -> int:
        try:
            check_cents(cents)
        except ValueError as exc:
            raise LedgerError(f"{what}: {exc}") from None
        if cents == 0:
            raise LedgerError(f"{what} must be more than 0.00")
        return cents

    @staticmethod
    def _check_text(text: str, what: str, *, required: bool) -> str:
        if not isinstance(text, str):
            raise LedgerError(f"{what} must be text")
        text = text.strip()
        if required and not text:
            raise LedgerError(f"{what} must not be empty")
        if len(text) > MAX_DESCRIPTION:
            raise LedgerError(f"{what} is longer than {MAX_DESCRIPTION} characters")
        return text

    def _next_id(self) -> int:
        return len(self._entries) + 1


def _involves(entry: Entry, member: str) -> bool:
    if isinstance(entry, Expense):
        return member == entry.payer or member in entry.split
    return member in (entry.sender, entry.receiver)


def _describe(entry: Entry, member: str) -> str:
    if isinstance(entry, Expense):
        share = entry.split.get(member, 0)
        if member == entry.payer:
            return (
                f"paid {format_cents(entry.total)} for {entry.description!r}, "
                f"own share {format_cents(share)}"
            )
        return f"share of {entry.description!r} paid by {entry.payer}: {format_cents(share)}"
    note = f" ({entry.note})" if entry.note else ""
    if member == entry.sender:
        return f"paid {entry.receiver} back {format_cents(entry.amount)}{note}"
    return f"received {format_cents(entry.amount)} from {entry.sender}{note}"


__all__ = ["Entry", "Expense", "Ledger", "LedgerError", "Reimbursement", "StatementLine"]
