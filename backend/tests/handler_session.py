"""Signed-in test clients and the corresponding offline public key."""

import time
from uuid import uuid4

import jwt
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient
from jwt.algorithms import ECAlgorithm

PROJECT = "http://handler-auth.test"
KEY = ec.generate_private_key(ec.SECP256R1())
PUBLIC = ECAlgorithm.to_jwk(KEY.public_key(), as_dict=True)
DANA = "dana.whitfield@undercover.example"
MARCUS = "marcus.oyelaran@undercover.example"
PRIYA = "priya.raman@undercover.example"


def token(email: str = DANA, **claims) -> str:
    payload = {
        "iss": f"{PROJECT}/auth/v1",
        "exp": int(time.time()) + 3600,
        "sub": str(uuid4()),
        "aud": "authenticated",
        "email": email,
        **claims,
    }
    return jwt.encode(payload, KEY, algorithm="ES256", headers={"kid": "offline"})


def bearer(email: str = DANA, **claims) -> dict[str, str]:
    return {"Authorization": f"Bearer {token(email, **claims)}"}


class SignedClient(TestClient):
    def __init__(self, app):
        super().__init__(app, headers=bearer())
