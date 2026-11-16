import sqlite3

from backend.finance import Finance
from backend.reseed import reseed
from backend.schemas import DecisionInput
from backend.storage import Store
from backend.tests.test_finance import decision


def test_reseed_preserves_backup_and_restores_waiting(tmp_path):
    path = tmp_path / "trial.sqlite3"
    assert reseed(path) is None
    store = Store(path)
    Finance(store).decide("CLM-1001", DecisionInput.model_validate(decision()))
    backup = reseed(path)
    assert backup is not None
    assert backup.exists()
    with sqlite3.connect(backup) as connection:
        assert (
            connection.execute(
                "SELECT count(*) FROM records WHERE kind='decisions'"
            ).fetchone()[0]
            == 1
        )
    assert len(store.queue()) == 25
    assert all(c.status == "waiting" for c in store.queue())
