from contextlib import nullcontext

import httpx
from sqlalchemy import select

from agent import main
from agent.checkpoints import open_saver
from agent.swarm_work import SwarmWork
from agent.tests.test_agent import StubModel, replies
from backend import agent_service
from backend.db import swarm_claims, swarm_runs
from backend.eval_sources import evaluation_copies, handler_references
from backend.main import create_app
from backend.tests.handler_session import SignedClient
from backend.tests.test_swarm import dispatch


def evaluation(client, run_id):
    return client.post(
        "/api/swarm/runs",
        json={
            "run_id": run_id,
            "kind": "evaluation",
            "concurrency": 5,
            "model": "stub",
            "reasoning_effort": None,
            "dataset": "held_out",
            "size": "tiny",
        },
    )


def test_start_forwards_evaluation_selection_without_creating_run(
    tmp_path, monkeypatch
):
    seen = []

    def forward(url, *, json, timeout):
        seen.append((url, json, timeout))
        return httpx.Response(202, json={"run_id": "evaluation-id"})

    monkeypatch.setattr(agent_service.httpx, "post", forward)
    app = create_app(tmp_path / "claims.sqlite3")
    with SignedClient(app) as client:
        payload = {"dataset": "held_out", "size": "tiny", "concurrency": 5}
        response = client.post("/api/evaluations", json=payload)
        assert response.status_code == 202
        assert response.json() == {"run_id": "evaluation-id"}
        assert seen == [("http://127.0.0.1:8002/evaluations", payload, 2)]
        assert (
            client.post("/api/evaluations", json={**payload, "size": "one"}).status_code
            == 422
        )
        with app.state.store.connection() as connection:
            assert connection.scalar(select(swarm_runs.c.run_id)) is None


def test_held_copy_finishes_and_clears_checkpoint_without_signoff(
    tmp_path, monkeypatch
):
    app = create_app(tmp_path / "claims.sqlite3")
    with SignedClient(app) as client:
        original = client.get("/api/claims").json()
        received = []
        generate = StubModel._generate

        def capture(model, messages, **kwargs):
            received.extend(str(message.content) for message in messages)
            return generate(model, messages, **kwargs)

        monkeypatch.setattr(StubModel, "_generate", capture)

        def api(method, path, body=None):
            response = client.request(method, path, json=body)
            response.raise_for_status()
            return response.json()

        monkeypatch.setattr(main, "request", api)

        def scripted(claim, thread, *, run_id):
            return main.run(
                claim, thread, StubModel(messages=iter(replies(1250))), run_id=run_id
            )

        assert evaluation(client, "sample").status_code == 201
        copies = api("GET", "/api/swarm/runs/sample/work")
        assert len(copies) == 5
        first = copies[0]
        with app.state.store.connection(write=True) as connection:
            source = connection.scalar(
                select(evaluation_copies.c.source_id).where(
                    evaluation_copies.c.copy_id == first
                )
            )
            connection.execute(
                handler_references.update()
                .where(handler_references.c.source_id == source)
                .values(decision="approve", payout_cents=987654321)
            )
        worker = SwarmWork(api, lambda claim: nullcontext(True), scripted)
        assert worker.work_claim(first, "sample") == "decided"
        assert received
        assert all("987654321" not in content for content in received)
        path = f"/api/claims/{first}"
        copy = api("GET", path)
        assert copy["decision"]["payout_amount"] == 1250
        assert copy["payment"] is None
        assert client.post(f"{path}/sign-off", json={}).is_error
        thread = api("GET", f"{path}/runs")[0]["thread_id"]
        with open_saver() as saver:
            assert saver.get_tuple({"configurable": {"thread_id": thread}}) is None
        assert api("GET", "/api/claims") == original


def test_stopped_copies_are_not_admitted_to_live_dispatch(tmp_path):
    app = create_app(tmp_path / "claims.sqlite3")
    with SignedClient(app) as client:
        assert evaluation(client, "abandoned").status_code == 201
        base = "/api/swarm/runs/abandoned"
        copies = client.get(f"{base}/work").json()
        first = copies[0]
        thread = client.post(f"{base}/claims/{first}/activate").json()["thread_id"]
        recovery = client.post("/api/swarm/runs/recover").json()
        assert recovery == {"evaluation_threads": [thread]}
        assert client.get("/api/work").json()["claims"] == []
        assert client.post(f"/api/claims/{first}/runs").status_code == 409
        assert dispatch(client, "next-dispatch").status_code == 201
        ids = client.get("/api/swarm/runs/next-dispatch/work").json()
        assert len(ids) == 25
        assert set(copies).isdisjoint(ids)
        with app.state.store.connection() as connection:
            assert (
                connection.scalar(
                    select(swarm_runs.c.status).where(
                        swarm_runs.c.run_id == "abandoned"
                    )
                )
                == "stopped"
            )
            assert (
                connection.scalar(
                    select(swarm_claims.c.thread_id).where(
                        swarm_claims.c.claim_id == first
                    )
                )
                == thread
            )
