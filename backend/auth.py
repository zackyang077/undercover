"""Verify a handler's Supabase session before performing a handler action."""

import os
from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from backend.schemas import Handler

Bearer = Annotated[
    HTTPAuthorizationCredentials | None, Depends(HTTPBearer(auto_error=False))
]


class HandlerAuth:
    def __init__(self):
        project = os.getenv("VITE_SUPABASE_URL", "").rstrip("/")
        self.issuer = f"{project}/auth/v1"
        self.keys = (
            jwt.PyJWKClient(f"{self.issuer}/.well-known/jwks.json", timeout=5)
            if project
            else None
        )

    def email(self, token: str) -> str:
        if self.keys is None:
            raise HTTPException(503, "Handler sign-in is not configured")
        try:
            key = self.keys.get_signing_key_from_jwt(token)
            claims = jwt.decode(
                token,
                key=key,
                algorithms=["ES256", "RS256"],
                audience="authenticated",
                issuer=self.issuer,
                options={"require": ["exp", "sub", "email"]},
            )
        except jwt.PyJWKClientConnectionError as error:
            raise HTTPException(503, "Sign-in verification is unavailable") from error
        except jwt.PyJWTError as error:
            raise HTTPException(401, "Invalid or expired sign-in") from error
        email = claims["email"]
        if not isinstance(email, str) or not email:
            raise HTTPException(401, "Missing handler email")
        return email.casefold()


def require_handler(request: Request, bearer: Bearer) -> Handler:
    if bearer is None:
        raise HTTPException(
            401, "Sign in to continue", headers={"WWW-Authenticate": "Bearer"}
        )
    email = request.app.state.auth.email(bearer.credentials)
    for saved in request.app.state.store.all("handlers"):
        if saved["email"].casefold() == email:
            return Handler.model_validate(saved)
    raise HTTPException(403, "This account is not a claims handler")


CurrentHandler = Annotated[Handler, Depends(require_handler)]


def require_senior(handler: CurrentHandler) -> Handler:
    if not handler.can_approve_over_limit:
        raise HTTPException(
            403, "This action needs a handler with over-limit approval authority"
        )
    return handler


SeniorHandler = Annotated[Handler, Depends(require_senior)]
