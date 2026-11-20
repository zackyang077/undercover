"""Admit new round work against the persisted budget at actual worker start."""

from dataclasses import dataclass, field
from threading import Event

from agent.improver_api import RoundClient


@dataclass
class RoundEvaluation:
    client: RoundClient
    run_id: str
    cold_start: bool
    stopped: Event = field(default_factory=Event)
    reason: str = ""

    def allowed(self):
        if self.stopped.is_set():
            return False
        gate = self.client.get(f"/can-work/{self.run_id}")
        if not gate["allowed"]:
            self.reason = gate["reason"]
            self.stopped.set()
            return False
        return True

    def before_model(self):
        if not self.allowed():
            raise RuntimeError(self.reason)

    def dispatch(self, dispatcher, claim_ids, work, concurrency):
        def guarded(claim_id, run_id):
            if not self.allowed():
                return "skipped"
            return work(claim_id, run_id)

        results = {}
        remaining = list(claim_ids)
        if self.cold_start and remaining:
            first, *remaining = remaining
            results.update(dispatcher(lambda: [first], guarded, self.run_id, 1))
        if remaining:
            results.update(
                dispatcher(lambda: remaining, guarded, self.run_id, concurrency)
            )
        return results
