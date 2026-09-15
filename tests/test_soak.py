"""The soak harness clears its own database before a run. That cleanup used to remove the
database's *parent directory*, which is fine for the default `/tmp/helm-soak/soak.db` and
catastrophic for `--db data/helm.db` — the live deployment's own database, holding the stop
and target for every open position plus the KILL file.

These tests pin the boundary: the database goes, the directory around it stays.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

from soak import clear_database  # noqa: E402


def test_removes_the_database_and_its_sqlite_sidecars(tmp_path):
    db = tmp_path / "soak.db"
    for name in ("soak.db", "soak.db-wal", "soak.db-shm"):
        (tmp_path / name).write_text("stale")

    clear_database(db)

    assert not db.exists()
    assert not (tmp_path / "soak.db-wal").exists()
    assert not (tmp_path / "soak.db-shm").exists()


def test_never_removes_the_directory_holding_the_database(tmp_path):
    """The regression. Pointing --db at a real data directory must not empty it."""
    data = tmp_path / "data"
    data.mkdir()
    (data / "helm.db").write_text("live positions")
    (data / "KILL").write_text("")
    (data / "unrelated.json").write_text("keep me")

    clear_database(data / "helm.db")

    assert data.is_dir()
    assert not (data / "helm.db").exists()
    assert (data / "KILL").exists(), "the kill switch must survive"
    assert (data / "unrelated.json").read_text() == "keep me"


def test_creates_the_parent_directory_when_it_is_missing(tmp_path):
    db = tmp_path / "nested" / "deeper" / "soak.db"

    clear_database(db)

    assert db.parent.is_dir()
    assert not db.exists()


def test_is_a_no_op_when_nothing_is_there_yet(tmp_path):
    db = tmp_path / "soak.db"

    clear_database(db)
    clear_database(db)

    assert not db.exists()
