import csv
import subprocess
import sys

import pytest

from splitledger import LedgerStore, split_equal
from splitledger.cli import main


def test_init_creates_a_household(tmp_path, capsys):
    path = tmp_path / "house.db"
    assert main(["init", str(path), "dee", "ana", "ben"]) == 0
    assert capsys.readouterr().out.splitlines() == [f"created {path} for ana, ben, dee", f"next: splitledger serve {path}"]
    with LedgerStore.open(path) as store:
        assert store.load().members == ("ana", "ben", "dee")


def test_init_refuses_an_existing_file(tmp_path, capsys):
    path = tmp_path / "house.db"
    path.write_text("keep me")
    assert main(["init", str(path), "ana"]) == 1
    assert "already exists" in capsys.readouterr().err
    assert path.read_text() == "keep me"


def test_init_rejects_duplicate_members(tmp_path, capsys):
    assert main(["init", str(tmp_path / "house.db"), "ana", "ana"]) == 1
    assert "unique" in capsys.readouterr().err


def test_export(tmp_path, capsys):
    path = tmp_path / "house.db"
    with LedgerStore.create(path, ["ana", "ben"]) as store:
        store.add_expense("snacks", "ana", 1000, split_equal(1000, ["ana", "ben"]))
    out = tmp_path / "ledger.csv"
    assert main(["export", str(path), str(out)]) == 0
    rows = list(csv.DictReader(out.open(encoding="utf-8-sig")))
    assert [(r["member"], r["balance_change"]) for r in rows] == [("ana", "5.00"), ("ben", "-5.00")]


def test_export_refuses_to_overwrite_the_database(tmp_path, capsys):
    path = tmp_path / "house.db"
    LedgerStore.create(path, ["ana"]).close()
    assert main(["export", str(path), str(path)]) == 1
    assert "refusing to export over the ledger database" in capsys.readouterr().err


def test_serve_missing_file(tmp_path, capsys):
    assert main(["serve", str(tmp_path / "missing.db")]) == 1
    assert "does not exist" in capsys.readouterr().err


def test_serve_only_listens_on_this_computer():
    with pytest.raises(SystemExit) as exc:
        main(["serve", "house.db", "--host", "0.0.0.0"])
    assert exc.value.code == 2


def test_installed_command_runs(tmp_path):
    result = subprocess.run(
        [sys.executable, "-m", "splitledger.cli", "init", str(tmp_path / "h.db"), "ana", "ben"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0
    assert "created" in result.stdout
