# SplitLedger

[![tests](https://github.com/SohamMessi10/splitledger/actions/workflows/tests.yml/badge.svg)](https://github.com/SohamMessi10/splitledger/actions/workflows/tests.yml)

Shared expenses for a household: who paid, who benefited, and who owes whom, correct to the cent.

> **Status:** in development. The money model, split rules, ledger with balances, and settlement suggestions are done. See
> [`docs/design.md`](docs/design.md) for the plan and [`PROGRESS.md`](PROGRESS.md) for status.

## Setup

Requires Python 3.11 or newer.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
python -m pytest
```
