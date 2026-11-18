"""Start the claim service or request a run from the command line."""

import argparse

import uvicorn

from agent.service import api


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--serve", action="store_true")
    group.add_argument("--claim", metavar="CLAIM_ID")
    group.add_argument("--limit", type=int, default=5)
    args = parser.parse_args()
    if args.serve:
        uvicorn.run("agent.service:app", host="127.0.0.1", port=8002)
    elif args.claim:
        print(api("POST", f"/api/claims/{args.claim}/runs")["thread_id"])
    else:
        print(
            api(
                "POST", "/api/queue/runs", {"mode": "capped", "max_claims": args.limit}
            )["queue_run_id"]
        )


if __name__ == "__main__":
    main()
