"""The immutable configuration used to decide claims in a run."""

from pydantic import BaseModel


class RunConfiguration(BaseModel):
    version: int
    model: str
    reasoning_effort: str
    instructions: str
