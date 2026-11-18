import sqlite3
from contextlib import closing

from backend.finance import Finance
from backend.reseed import reseed
from backend.schemas import DecisionInput
from backend.storage import Store
from backend.tests.test_finance import decision


def test_reseed_preserves_backup_and_restores_waiting(tmp_path):
    path = tmp_path / "trial.sqlite3"
    assert reseed(path) is None
    checkpoints = path.with_name(path.name + ".checkpoints.sqlite3")
    with closing(sqlite3.connect(checkpoints)) as connection:
        connection.execute("CREATE TABLE snapshots (thread_id TEXT)")
        connection.execute("INSERT INTO snapshots VALUES ('before-reseed')")
        connection.commit()
    store = Store(path)
    Finance(store).decide("CLM-1001", DecisionInput.model_validate(decision()))
    backup = reseed(path)
    assert backup is not None
    assert backup.exists()
    assert not checkpoints.exists()
    (sidecar_backup,) = tmp_path.glob("*.checkpoints.sqlite3.*.bak")
    with closing(sqlite3.connect(sidecar_backup)) as connection:
        assert connection.execute("SELECT thread_id FROM snapshots").fetchone() == (
            "before-reseed",
        )
    with sqlite3.connect(backup) as connection:
        assert (
            connection.execute(
                "SELECT count(*) FROM records WHERE kind='decisions'"
            ).fetchone()[0]
            == 1
        )
    assert len(store.queue()) == 25
    assert all(c.status == "waiting" for c in store.queue())
