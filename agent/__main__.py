"""Start the claim service or request a single claim using a handler session."""

import argparse
import os
from getpass import getpass

import httpx
import uvicorn

from agent import main as claim_agent


def handler_token() -> str:
    if token := os.getenv("HANDLER_ACCESS_TOKEN"):
        return token
    project = os.environ["VITE_SUPABASE_URL"].rstrip("/")
    response = httpx.post(
        f"{project}/auth/v1/token",
        params={"grant_type": "password"},
        headers={"apikey": os.environ["VITE_SUPABASE_PUBLISHABLE_KEY"]},
        json={
            "email": os.getenv("HANDLER_EMAIL") or input("Handler email: ").strip(),
            "password": os.getenv("HANDLER_PASSWORD") or getpass("Password: "),
        },
        timeout=10,
    )
    response.raise_for_status()
    return response.json()["access_token"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--serve", action="store_true")
    group.add_argument("claim_id", nargs="?", metavar="CLAIM_ID")
    args = parser.parse_args()
    if args.serve:
        uvicorn.run("agent.service:app", host="127.0.0.1", port=8002)
    else:
        with claim_agent.backend() as client:
            response = client.post(
                f"/api/claims/{args.claim_id}/runs",
                headers={"Authorization": f"Bearer {handler_token()}"},
            )
            response.raise_for_status()
            print(response.json()["thread_id"])


if __name__ == "__main__":
    main()
