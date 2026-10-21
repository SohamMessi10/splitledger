"""Web interface: a JSON API and a household page, served by FastAPI.

Run it with `splitledger serve house.db` (see cli.py). It is a local,
single-household app with no accounts, so it protects itself the way local
tools should:

- It listens on 127.0.0.1 by default, so other machines can't reach it.
- TrustedHostMiddleware rejects requests whose Host header isn't localhost.
  That blocks DNS rebinding, where a malicious site points its own domain at
  127.0.0.1 to read your local app.
- Requests that change data must come from this app's own pages: a POST with a
  foreign Origin is refused (403). Otherwise any website you visit could
  silently submit a form to your local ledger (cross-site request forgery).
- The JSON API only accepts `application/json`, which a cross-site page can't
  send without a CORS preflight, and this app never grants CORS.

Amounts cross the API as text ("12.34"), never JSON numbers: a JSON number
arrives in Python as a float, which is exactly what integer cents avoid.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import urlencode

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, ConfigDict, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from splitledger.export import to_csv
from splitledger.ledger import Expense, LedgerError, Reimbursement, Reversal
from splitledger.money import MoneyError, format_cents, parse_amount
from splitledger.settle import SettleError, settle
from splitledger.splits import SplitError, split_equal, split_exact, split_shares
from splitledger.storage import LedgerStore, StoreError

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
LOCAL_HOSTS = ["localhost", "127.0.0.1"]

# Errors caused by the request's content: the user can fix them, so they are 422s.
USER_ERRORS = (LedgerError, MoneyError, SplitError, SettleError)


# --- request bodies for the JSON API ------------------------------------------


class _Strict(BaseModel):
    # extra="forbid": a misspelled field ("ammount") is an error, not silently ignored.
    # strict=True: no type coercion, so 12.34 (a number) is rejected where "12.34" is expected.
    model_config = ConfigDict(extra="forbid", strict=True)


class EqualSplit(_Strict):
    type: Literal["equal"]
    members: list[str] = Field(min_length=1)


class SharesSplit(_Strict):
    type: Literal["shares"]
    shares: dict[str, int] = Field(min_length=1)


class ExactSplit(_Strict):
    type: Literal["exact"]
    amounts: dict[str, str] = Field(min_length=1)


class ExpenseIn(_Strict):
    description: str
    payer: str
    amount: str
    split: Annotated[EqualSplit | SharesSplit | ExactSplit, Field(discriminator="type")]


class ReimbursementIn(_Strict):
    sender: str
    receiver: str
    amount: str
    note: str = ""


class ReversalIn(_Strict):
    reason: str


# --- shared logic ---------------------------------------------------------------


def build_split(kind: str, total: int, members: list[str], shares: dict[str, int], amounts: dict[str, str]) -> dict[str, int]:
    """Turn the chosen split method and its inputs into cents per member."""
    if kind == "equal":
        return split_equal(total, members)
    if kind == "shares":
        return split_shares(total, shares)
    if kind == "exact":
        cents = {}
        for member, text in amounts.items():
            try:
                cents[member] = parse_amount(text)
            except MoneyError as exc:
                raise SplitError(f"amount for {member}: {exc}") from None
        return split_exact(total, cents)
    raise SplitError(f"unknown split type {kind!r}; use equal, shares, or exact")


def entry_json(entry: Expense | Reimbursement | Reversal, reversed_ids: set[int]) -> dict:
    base = {"entry_id": entry.entry_id, "reversed": entry.entry_id in reversed_ids}
    if isinstance(entry, Expense):
        return base | {
            "kind": "expense", "description": entry.description, "payer": entry.payer,
            "amount": format_cents(entry.total),
            "split": {member: format_cents(cents) for member, cents in entry.split.items()},
        }
    if isinstance(entry, Reimbursement):
        return base | {
            "kind": "reimbursement", "sender": entry.sender, "receiver": entry.receiver,
            "amount": format_cents(entry.amount), "note": entry.note,
        }
    return base | {"kind": "reversal", "reverses": entry.reverses, "reason": entry.reason}


def describe(entry: Expense | Reimbursement | Reversal) -> str:
    if isinstance(entry, Expense):
        return f"{entry.payer} paid {format_cents(entry.total)} for {entry.description}"
    if isinstance(entry, Reimbursement):
        note = f" ({entry.note})" if entry.note else ""
        return f"{entry.sender} paid {entry.receiver} {format_cents(entry.amount)}{note}"
    return f"Reversed entry {entry.reverses}: {entry.reason}"


# --- the app ----------------------------------------------------------------------


def get_store(request: Request) -> Iterator[LedgerStore]:
    """One connection per request: SQLite connections can't be shared across the
    worker threads FastAPI uses for these endpoints."""
    store = LedgerStore.open(request.app.state.db_path)
    try:
        yield store
    finally:
        store.close()


# Defined at module level: with `from __future__ import annotations`, FastAPI resolves
# these hints by name from the module, so an alias inside create_app() isn't found.
Store = Annotated[LedgerStore, Depends(get_store)]


def create_app(db_path: str | Path, allowed_hosts: list[str] | None = None) -> FastAPI:
    """Build the app for one household file. The file must already exist."""
    db_path = Path(db_path)
    LedgerStore.open(db_path).close()  # fail at startup, not on the first request

    app = FastAPI(
        title="SplitLedger",
        description="Shared expenses for a household, correct to the cent. Amounts are strings like \"12.34\".",
        version="0.1.0",
    )
    app.state.db_path = db_path
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=allowed_hosts or LOCAL_HOSTS)

    @app.middleware("http")
    async def same_origin_writes(request: Request, call_next):
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            origin = request.headers.get("origin")
            # Browsers send Origin on cross-site form posts. A missing Origin means a
            # non-browser client such as curl, which a malicious website can't drive.
            if origin is not None and origin not in _own_origins(request):
                return JSONResponse({"detail": "cross-site request refused"}, status_code=403)
        return await call_next(request)

    @app.exception_handler(StoreError)
    async def store_error(request: Request, exc: StoreError):
        return JSONResponse({"detail": f"ledger file problem: {exc}"}, status_code=500)

    # --- JSON API -----------------------------------------------------------------

    @app.get("/api/members")
    def members(store: Store) -> list[str]:
        return list(store.load().members)

    @app.get("/api/balances")
    def balances(store: Store) -> dict[str, str]:
        """Positive means the member is owed money; negative means they owe."""
        return {member: format_cents(cents) for member, cents in store.balances().items()}

    @app.get("/api/entries")
    def entries(store: Store) -> list[dict]:
        ledger = store.load()
        return [entry_json(e, ledger.reversed_ids()) for e in ledger.entries]

    @app.get("/api/statement/{member}")
    def statement(member: str, store: Store) -> list[dict]:
        ledger = store.load()
        if member not in ledger.members:
            raise HTTPException(404, f"{member!r} is not a member of this household")
        return [
            {"entry_id": l.entry_id, "text": l.text, "change": format_cents(l.change),
             "running_balance": format_cents(l.running_balance)}
            for l in ledger.statement(member)
        ]

    @app.get("/api/settlement")
    def settlement(store: Store) -> dict:
        result = settle(store.balances())
        return {
            "method": result.method,
            "transfers": [
                {"sender": t.sender, "receiver": t.receiver, "amount": format_cents(t.amount)}
                for t in result.transfers
            ],
        }

    @app.post("/api/expenses", status_code=201)
    def add_expense(body: ExpenseIn, store: Store) -> dict:
        with _user_errors():
            total = parse_amount(body.amount)
            split = body.split
            split_cents = build_split(
                split.type, total,
                getattr(split, "members", []), getattr(split, "shares", {}), getattr(split, "amounts", {}),
            )
            entry = store.add_expense(body.description, body.payer, total, split_cents)
        return entry_json(entry, set())

    @app.post("/api/reimbursements", status_code=201)
    def add_reimbursement(body: ReimbursementIn, store: Store) -> dict:
        with _user_errors():
            entry = store.add_reimbursement(body.sender, body.receiver, parse_amount(body.amount), body.note)
        return entry_json(entry, set())

    @app.post("/api/entries/{entry_id}/reverse", status_code=201)
    def reverse(entry_id: int, body: ReversalIn, store: Store) -> dict:
        ledger = store.load()
        if not 1 <= entry_id <= len(ledger.entries):
            raise HTTPException(404, f"there is no entry {entry_id}")
        with _user_errors():
            entry = store.add_reversal(entry_id, body.reason)
        return entry_json(entry, set())

    @app.get("/export.csv")
    def export(store: Store) -> Response:
        return Response(
            to_csv(store.load()),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="splitledger.csv"'},
        )

    # --- household page (HTML forms, no JavaScript needed) -------------------------

    def page(request: Request, store: LedgerStore, *, error: str = "", notice: str = "", form: dict | None = None, status: int = 200):
        ledger = store.load()
        result = settle(ledger.balances())
        reversed_ids = ledger.reversed_ids()
        return TEMPLATES.TemplateResponse(
            request,
            "index.html",
            {
                "members": ledger.members,
                "balances": [(m, c, format_cents(c)) for m, c in ledger.balances().items()],
                "transfers": [(t.sender, t.receiver, format_cents(t.amount)) for t in result.transfers],
                "method": result.method,
                "entries": [
                    (e.entry_id, describe(e), e.entry_id in reversed_ids, isinstance(e, Reversal))
                    for e in reversed(ledger.entries)
                ],
                "error": error,
                "notice": notice,
                "form": form or {},
            },
            status_code=status,
        )

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request, store: Store, notice: str = ""):
        return page(request, store, notice=notice[:200])

    # Reading a form is async-only, but SQLite work must stay on one thread. So each
    # handler awaits the form, then runs the database work in one worker thread with a
    # connection opened there (run_in_threadpool), instead of on the event loop.

    @app.post("/expenses", response_class=HTMLResponse)
    async def submit_expense(request: Request):
        form = await request.form()
        return await run_in_threadpool(_expense_from_form, request, form)

    @app.post("/reimbursements", response_class=HTMLResponse)
    async def submit_reimbursement(request: Request):
        form = await request.form()
        return await run_in_threadpool(_reimbursement_from_form, request, form)

    @app.post("/entries/{entry_id}/reverse", response_class=HTMLResponse)
    async def submit_reversal(entry_id: int, request: Request):
        form = await request.form()
        return await run_in_threadpool(_reversal_from_form, request, entry_id, form)

    def _expense_from_form(request: Request, form) -> Response:
        values = {key: form.get(key, "") for key in form.keys()}
        chosen = form.getlist("member")
        with LedgerStore.open(db_path) as store:
            try:
                total = parse_amount(str(form.get("amount", "")).strip())
                kind = str(form.get("split_type", "equal"))
                shares: dict[str, int] = {}
                amounts: dict[str, str] = {}
                for member in store.load().members:
                    raw_share = str(form.get(f"share_{member}", "")).strip()
                    raw_exact = str(form.get(f"exact_{member}", "")).strip()
                    if kind == "shares" and raw_share not in ("", "0"):
                        if not raw_share.isdigit():
                            raise SplitError(f"share for {member} must be a whole number")
                        shares[member] = int(raw_share)
                    if kind == "exact" and raw_exact:
                        amounts[member] = raw_exact
                split = build_split(kind, total, list(chosen), shares, amounts)
                entry = store.add_expense(str(form.get("description", "")), str(form.get("payer", "")), total, split)
            except USER_ERRORS as exc:
                values["member"] = list(chosen)
                return page(request, store, error=str(exc), form=values, status=422)
        return _back(f"Added {entry.description}.")

    def _reimbursement_from_form(request: Request, form) -> Response:
        with LedgerStore.open(db_path) as store:
            try:
                amount = parse_amount(str(form.get("amount", "")).strip())
                entry = store.add_reimbursement(
                    str(form.get("sender", "")), str(form.get("receiver", "")), amount, str(form.get("note", ""))
                )
            except USER_ERRORS as exc:
                return page(request, store, error=str(exc), form={f"r_{k}": v for k, v in form.items()}, status=422)
        return _back(f"Recorded {entry.sender} paying {entry.receiver} {format_cents(entry.amount)}.")

    def _reversal_from_form(request: Request, entry_id: int, form) -> Response:
        with LedgerStore.open(db_path) as store:
            try:
                store.add_reversal(entry_id, str(form.get("reason", "")))
            except USER_ERRORS as exc:
                return page(request, store, error=str(exc), status=422)
        return _back(f"Reversed entry {entry_id}.")

    return app


def _back(notice: str) -> RedirectResponse:
    # 303 See Other after a successful POST: reloading the page won't resubmit the form.
    return RedirectResponse("/?" + urlencode({"notice": notice}), status_code=303)


def _own_origins(request: Request) -> set[str]:
    host = request.headers.get("host", "")
    return {f"http://{host}", f"https://{host}"}


@contextmanager
def _user_errors() -> Iterator[None]:
    try:
        yield
    except USER_ERRORS as exc:
        raise HTTPException(422, str(exc)) from None


__all__ = ["create_app"]
