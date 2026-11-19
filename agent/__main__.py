"""Start the claim service or request a run from the command line."""

import argparse

import uvicorn

from agent.service import api


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--serve", action="store_true")
    group.add_argument("claim_id", nargs="?", metavar="CLAIM_ID")
    args = parser.parse_args()
    if args.serve:
        uvicorn.run("agent.service:app", host="127.0.0.1", port=8002)
    else:
        print(api("POST", f"/api/claims/{args.claim_id}/runs")["thread_id"])


if __name__ == "__main__":
    main()
