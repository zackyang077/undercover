"""Coordinate dispatch workers and separately scheduled claim resumes."""

from collections.abc import Iterator
from contextlib import contextmanager
from threading import Condition


class ClaimOwnership:
    def __init__(self):
        self.changed = Condition()
        self.active: set[str] = set()
        self.pending_resumes: set[str] = set()

    def request_resume(self, claim_id: str) -> bool:
        """Reserve the resume before scheduling it on an executor."""
        with self.changed:
            if claim_id in self.pending_resumes:
                return False
            self.pending_resumes.add(claim_id)
            return True

    @contextmanager
    def dispatch(self, claim_id: str) -> Iterator[bool]:
        """Take an available claim while giving any queued resume priority."""
        with self.changed:
            owned = claim_id not in self.active and claim_id not in self.pending_resumes
            if owned:
                self.active.add(claim_id)
        try:
            yield owned
        finally:
            if owned:
                with self.changed:
                    self.active.remove(claim_id)
                    self.changed.notify_all()

    @contextmanager
    def resume(self, claim_id: str) -> Iterator[None]:
        """Wait for the current worker before continuing a reserved claim."""
        with self.changed:
            self.changed.wait_for(lambda: claim_id not in self.active)
            self.active.add(claim_id)
        try:
            yield
        finally:
            with self.changed:
                self.active.remove(claim_id)
                self.pending_resumes.remove(claim_id)
                self.changed.notify_all()
