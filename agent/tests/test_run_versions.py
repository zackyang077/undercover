"""Exercise real claim prompts, persisted run provenance and replayed checkpoints."""

from contextlib import nullcontext
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from langchain_core.exceptions import ModelTimeoutError

from agent import main, service
from agent.configuration import RunConfiguration
from agent.tests.test_agent import StubModel, replies
from backend.main import create_app
from backend.tests.handler_session import SignedClient


@pytest.fixture
def desk(tmp_path, monkeypatch):
    app = create_app(tmp_path / "versions.sqlite3")
    with SignedClient(app) as client:
        monkeypatch.setattr(main, "backend", lambda: nullcontext(client))
        store = app.state.store
        first = client.get("/api/agent/configuration/current").json()
        with store.connection(write=True) as connection:
            store.insert(
                connection,
                "configuration",
                "2",
                {
                    **first,
                    "version": 2,
                    "model": "test-candidate",
                    "reasoning_effort": "medium",
                    "instructions": "Use candidate instructions.",
                },
            )
        yield client


@pytest.mark.parametrize("kind", ["dispatch", "manual", "candidate"])
def test_run_uses_recorded_configuration(desk, monkeypatch, kind):
    models, prompts = [], []
    original = StubModel._generate

    def capture(model, messages, **kwargs):
        prompts.append(messages[0].content)
        return original(model, messages, **kwargs)

    def stub(settings):
        models.append(settings)
        return StubModel(messages=iter(replies(0, "refer")))

    def one_claim(list_claims, work, run_id, _concurrency):
        claim = list_claims()[0]
        return {claim: work(claim, run_id)}

    monkeypatch.setattr(StubModel, "_generate", capture)
    monkeypatch.setattr(main, "model", stub)
    worker = service.Worker(dispatch_runner=one_claim)
    version = 2 if kind == "candidate" else 1
    config = RunConfiguration.model_validate(
        desk.get(f"/api/agent/configuration/{version}").json()
    )
    if kind == "candidate":
        run_id = str(uuid4())
        job = service.ScheduledRun(
            run_id=run_id,
            kind="evaluation",
            model=config.model,
            reasoning_effort=config.reasoning_effort,
            dataset="evaluation",
            size="full",
            concurrency=1,
        )
        assert worker.run_swarm(job, config) == "completed"
    else:
        with TestClient(service.create_service(worker)) as agent:
            response = (
                agent.post("/dispatch", json={"concurrency": 1})
                if kind == "dispatch"
                else agent.post(
                    "/evaluations",
                    json={"concurrency": 1, "dataset": "evaluation", "size": "tiny"},
                )
            )
            response.raise_for_status()
            run_id = response.json()["run_id"]
    route = "dispatch" if kind == "dispatch" else "evaluations"
    assert desk.get(f"/api/{route}/{run_id}").json()["config_version"] == version
    assert {entry.model_name for entry in models} == {config.model}
    assert {entry.reasoning_effort for entry in models} == {config.reasoning_effort}
    assert set(prompts) == {config.instructions}


def test_resumed_model_call_uses_original_prompt_model_and_effort(desk, monkeypatch):
    model_settings, prompts = [], []
    config = RunConfiguration.model_validate(
        desk.get("/api/agent/configuration/2").json()
    )
    first = True

    def scripted(model, messages, **kwargs):
        nonlocal first
        prompts.append(messages[0].content)
        if first:
            first = False
            raise ModelTimeoutError("Transient test failure")
        return generate(model, messages, **kwargs)

    def model(settings):
        model_settings.append((settings.model_name, settings.reasoning_effort))
        return StubModel(messages=iter(replies(0, "refer")))

    generate = StubModel._generate
    monkeypatch.setattr(main, "model", model)
    monkeypatch.setattr(StubModel, "_generate", scripted)
    with pytest.raises(ModelTimeoutError):
        main.ClaimRunner(config)("CLM-1001", "recover", run_id="first")
    assert main.run("CLM-1001", "recover", run_id="second").startswith("refer:")
    assert set(model_settings) == {("test-candidate", "medium")}
    assert set(prompts) == {"Use candidate instructions."}
