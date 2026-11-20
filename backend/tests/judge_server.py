"""Local Decisions endpoint used by the browser and SDK contract tests."""

from fastapi import APIRouter

routes = APIRouter()


@routes.post("/api/alpha/decisions", operation_id="fixture_decisions")
def decisions(body: dict):
    """Return a Jev response whose most likely grade differs from its rounded score."""
    return {
        "model": body["model"],
        "answers": {
            "grade": {
                "type": "score",
                "score": 1.42,
                "probabilities": {"0": 0.42, "1": 0.05, "2": 0.22, "3": 0.31},
            },
            "same_grounds": {"type": "noul", "noul": 0.9},
            "same_section": {"type": "noul", "noul": 0.3},
        },
        "usage": {"input_tokens": 10, "output_tokens": 2, "cost": 0.00001},
    }
