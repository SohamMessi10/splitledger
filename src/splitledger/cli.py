"""Command line: create a household file and run the web app.

    splitledger init house.db ana ben cal dee
    splitledger serve house.db            # then open http://127.0.0.1:8000
    splitledger export house.db ledger.csv
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from splitledger.ledger import LedgerError
from splitledger.storage import LedgerStore, StoreError

LOOPBACK = {"127.0.0.1", "localhost"}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="splitledger", description="Shared expenses for a household.")
    commands = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")

    init = commands.add_parser("init", help="create a new household file")
    init.add_argument("path", help="file to create, for example house.db")
    init.add_argument("members", nargs="+", help="member names, for example ana ben cal")

    serve = commands.add_parser("serve", help="run the web app for a household file")
    serve.add_argument("path", help="an existing household file")
    serve.add_argument("--port", type=int, default=8000, help="port to listen on (default 8000)")
    serve.add_argument(
        "--host",
        default="127.0.0.1",
        choices=sorted(LOOPBACK),
        help="only this computer can connect; the app has no logins, so it never listens on the network",
    )

    export = commands.add_parser("export", help="write the ledger as CSV")
    export.add_argument("path", help="an existing household file")
    export.add_argument("output", help="CSV file to write")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "init":
            LedgerStore.create(args.path, args.members).close()
            print(f"created {args.path} for {', '.join(sorted(args.members))}")
            print(f"next: splitledger serve {args.path}")
        elif args.command == "export":
            with LedgerStore.open(args.path) as store:
                store.export_csv(args.output)
            print(f"wrote {args.output}")
        else:
            return _serve(args.path, args.host, args.port)
    except (StoreError, LedgerError, OSError) as exc:
        print(f"splitledger: error: {exc}", file=sys.stderr)
        return 1
    return 0


def _serve(path: str, host: str, port: int) -> int:
    try:
        import uvicorn

        from splitledger.web import create_app
    except ModuleNotFoundError as exc:
        print(f"splitledger: error: the web app needs extra packages ({exc.name}); "
              'install them with: python -m pip install -e ".[web]"', file=sys.stderr)
        return 1
    app = create_app(path)
    print(f"SplitLedger for {path}: open http://{host}:{port} (Ctrl+C to stop)")
    uvicorn.run(app, host=host, port=port, log_level="warning")
    return 0


if __name__ == "__main__":
    sys.exit(main())
