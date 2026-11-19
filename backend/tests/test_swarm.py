from fastapi.testclient import TestClient
from sqlalchemy import select

from agent import main
from agent.ownership import ClaimOwnership
from agent.tests.test_agent import StubModel, replies
from backend.db import runs
from backend.main import create_app
from backend.swarm import swarm_runs


def dispatch(client, run_id: str):
    return client.post(
        "/api/swarm/runs",
        json={
            "run_id": run_id,
            "kind": "dispatch",
            "concurrency": 3,
            "model": "stub",
            "reasoning_effort": None,
            "dataset": None,
            "size": None,
        },
    )


def test_membership_oldest_first_and_recover_same_attempt(tmp_path):
    app = create_app(tmp_path / "claims.sqlite3")
    with TestClient(app) as client:
        store = app.state.store
        with store.connection(write=True) as connection:
            for claim_id in ("CLM-1002", "CLM-1003"):
                claim = store.get("claims", claim_id)
                store.update(
                    connection,
                    "claims",
                    claim_id,
                    {**claim, "submitted_on": "2000-01-01"},
                )
        assert dispatch(client, "first").status_code == 201
        ids = client.get("/api/swarm/runs/first/work").json()
        assert len(ids) == 25
        assert ids[:2] == ["CLM-1002", "CLM-1003"]
        path = "/api/swarm/runs/first/claims/CLM-1002/activate"
        choice = client.post(path).json()
        assert choice["thread_id"]
        assert client.post(path).json() == choice
        assert client.post("/api/swarm/runs/recover").status_code == 200
        assert client.post(path).json() == {}
        with store.connection() as connection:
            assert connection.scalar(select(swarm_runs.c.status)) == "stopped"
        assert dispatch(client, "next").status_code == 201
        assert client.post(path.replace("first", "next")).json() == choice
        with store.connection() as connection:
            assert connection.execute(select(runs.c.thread_id)).scalars().all() == [
                choice["thread_id"]
            ]


def test_unrelated_failure_has_one_fresh_attempt(tmp_path):
    app = create_app(tmp_path / "claims.sqlite3")
    with TestClient(app) as client:
        assert dispatch(client, "trial").status_code == 201
        base = "/api/swarm/runs/trial/claims/CLM-1001"
        first = client.post(f"{base}/activate").json()["thread_id"]
        fatal = {"error": "Stub provider failed"}
        assert client.post(f"{base}/{first}/finish", json=fatal).json() == {
            "retry": True,
            "status": "waiting",
        }
        second = client.post(f"{base}/activate").json()["thread_id"]
        assert second != first
        assert client.post(f"{base}/{second}/finish", json=fatal).json() == {
            "retry": False,
            "status": "needs_handler",
        }
        assert client.post(f"{base}/activate").json() == {}
        assert client.get("/api/claims/CLM-1001").json()["status"] == "needs_handler"


def test_signoff_reservation_takes_ownership_of_original_thread(tmp_path, monkeypatch):
    app = create_app(tmp_path / "claims.sqlite3")
    claim_id = "CLM-1003"
    claim = f"/api/claims/{claim_id}"
    member = f"/api/swarm/runs/trial/claims/{claim_id}"
    ownership = ClaimOwnership()
    with TestClient(app) as client:

        def request(method, path, body=None):
            response = client.request(method, path, json=body)
            if response.is_error:
                return {
                    "error": response.json()["detail"],
                    "status_code": response.status_code,
                }
            return response.json()

        monkeypatch.setattr(main, "request", request)
        assert dispatch(client, "trial").status_code == 201
        with ownership.dispatch(claim_id) as owned:
            assert owned
            thread = client.post(f"{member}/activate").json()["thread_id"]
            assert (
                main.run(
                    claim_id,
                    thread,
                    StubModel(messages=iter(replies(1250))),
                    run_id="trial",
                )
                == "Waiting for sign-off"
            )
            assert (
                client.post(f"{claim}/payment", json={"amount": 1250}).status_code
                == 403
            )
            signed = client.post(f"{claim}/sign-off", json={"handler_id": "HND-01"})
            assert signed.status_code == 200
            assert ownership.request_resume(claim_id)
            settled = client.post(f"{member}/{thread}/finish", json={"error": ""})
            assert settled.json()["status"] == "in_progress"

        with ownership.dispatch(claim_id) as owned:
            assert not owned
        with ownership.resume(claim_id):
            assert (
                main.run(claim_id, thread, StubModel(messages=iter([])))
                == "approve: $1250.00"
            )
            assert (
                client.post(
                    f"/api/work/{claim_id}/{thread}/finish", json={"error": ""}
                ).status_code
                == 200
            )
        detail = client.get(claim).json()
        assert detail["payment"]["sign_off"] == signed.json()
        assert detail["decision"]["payout_amount"] == detail["payment"]["amount"]
        assert [run["thread_id"] for run in client.get(f"{claim}/runs").json()] == [
            thread
        ]
