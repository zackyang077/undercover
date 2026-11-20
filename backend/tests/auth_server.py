"""Supabase password sign-in and public keys for isolated browser tests."""

import os
import time
from uuid import NAMESPACE_URL, uuid5

import jwt
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import APIRouter, HTTPException, Request, Response
from jwt.algorithms import ECAlgorithm
from pydantic import BaseModel

routes = APIRouter(prefix="/auth/v1")
signing_key = ec.generate_private_key(ec.SECP256R1())
fixture_users = {
    "dana.whitfield@undercover.example",
    "marcus.oyelaran@undercover.example",
    "priya.raman@undercover.example",
}


class Login(BaseModel):
    email: str
    password: str


@routes.get("/.well-known/jwks.json", operation_id="test_auth_keys")
def public_keys():
    """Publish this stand-in's ephemeral signature verification key."""
    key = ECAlgorithm.to_jwk(signing_key.public_key(), as_dict=True)
    return {"keys": [{**key, "kid": "browser", "alg": "ES256", "use": "sig"}]}


@routes.post("/token", operation_id="test_password_login")
def login(credentials: Login, request: Request, grant_type: str):
    """Issue a short-lived session for a browser-test handler."""
    email = credentials.email
    if (
        grant_type != "password"
        or email not in fixture_users
        or credentials.password != os.environ["E2E_LOGIN_PASSWORD"]
    ):
        raise HTTPException(400, "Invalid test credentials")
    subject = str(uuid5(NAMESPACE_URL, email))
    issued = int(time.time())
    issuer = str(request.base_url).rstrip("/") + "/auth/v1"
    token = jwt.encode(
        {
            "iss": issuer,
            "sub": subject,
            "email": email,
            "role": "authenticated",
            "aud": "authenticated",
            "iat": issued,
            "exp": issued + 3600,
        },
        signing_key,
        algorithm="ES256",
        headers={"kid": "browser"},
    )
    return {
        "access_token": token,
        "refresh_token": f"test-refresh-{subject}",
        "token_type": "bearer",
        "expires_in": 3600,
        "expires_at": issued + 3600,
        "user": {
            "id": subject,
            "email": email,
            "role": "authenticated",
            "aud": "authenticated",
            "app_metadata": {},
            "user_metadata": {},
        },
    }


@routes.post("/logout", operation_id="test_auth_logout")
def logout() -> Response:
    """Acknowledge the browser client's sign-out request."""
    return Response(status_code=204)
