"""Export a ledger to CSV for spreadsheets.

One row per member per entry, showing how that entry moved that member's
balance. Summing `balance_change` per member in a spreadsheet gives exactly the
balances SplitLedger shows, so the export can be checked independently.
"""

from __future__ import annotations

import csv
import io
import os
import tempfile
from pathlib import Path

from splitledger.ledger import Expense, Ledger, Reimbursement, Reversal
from splitledger.money import format_cents

COLUMNS = (
    "entry_id",
    "kind",
    "description",
    "paid_by",
    "paid_to",
    "amount",
    "member",
    "share",
    "balance_change",
    "reverses",
    "reversed",
)

# Spreadsheet programs treat a cell starting with one of these as a formula, so a
# description such as =HYPERLINK("http://evil.example", "click") would run when the
# file is opened. This is "CSV injection" (OWASP). Text cells that start with one
# get a leading apostrophe, which spreadsheets show as plain text.
_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def to_csv(ledger: Ledger) -> str:
    """The ledger as CSV text. The same ledger always gives the same text."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(COLUMNS)
    reversed_ids = ledger.reversed_ids()
    for entry in ledger.entries:
        original = entry.original if isinstance(entry, Reversal) else entry
        if isinstance(original, Expense):
            description, paid_by, paid_to, amount = original.description, original.payer, "", original.total
        else:
            description, paid_by, paid_to, amount = original.note, original.sender, original.receiver, original.amount
        if isinstance(entry, Reversal):
            description = f"{entry.reason} (reverses: {description})" if description else entry.reason
        effects = entry.effects()
        for member in sorted(_members_of(original)):
            share = original.split.get(member, 0) if isinstance(original, Expense) else 0
            if isinstance(entry, Reversal):
                share = -share
            writer.writerow(
                (
                    entry.entry_id,
                    _kind(entry),
                    _safe_text(description),
                    _safe_text(paid_by),
                    _safe_text(paid_to),
                    format_cents(amount),
                    _safe_text(member),
                    format_cents(share) if isinstance(original, Expense) else "",
                    format_cents(effects.get(member, 0)),
                    entry.reverses if isinstance(entry, Reversal) else "",
                    "yes" if entry.entry_id in reversed_ids else "",
                )
            )
    return buffer.getvalue()


def write_csv(ledger: Ledger, path: str | Path) -> None:
    """Write the CSV atomically: a crash leaves either the old file or the complete new one."""
    path = Path(path)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        # utf-8-sig adds the byte-order mark Excel needs to show names like "José" correctly.
        with os.fdopen(fd, "w", encoding="utf-8-sig", newline="") as handle:
            handle.write(to_csv(ledger))
        os.replace(tmp_name, path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise


def _members_of(entry: Expense | Reimbursement) -> set[str]:
    if isinstance(entry, Expense):
        return set(entry.split) | {entry.payer}
    return {entry.sender, entry.receiver}


def _kind(entry: Expense | Reimbursement | Reversal) -> str:
    if isinstance(entry, Reversal):
        return "reversal"
    return "expense" if isinstance(entry, Expense) else "reimbursement"


def _safe_text(text: str) -> str:
    return "'" + text if text.startswith(_FORMULA_START) else text
