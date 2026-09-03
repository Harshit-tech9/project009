"""Send one visible streaming message to an existing Agent37 Hermes instance."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import httpx

from architect.client import load_settings, redact_secrets


ROOT = Path(__file__).resolve().parent


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "instance_url",
        help="Existing instance URL, for example https://abc123.agent37.app",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    settings = load_settings(ROOT / ".env")
    endpoint = f"{args.instance_url.rstrip('/')}/v1/responses"
    payload = {
        "input": "Hi Hermes",
        "stream": True,
        "reasoning_effort": "low",
    }

    # Never print this dictionary: it contains AGENT37_API_KEY.
    headers = {
        "X-Agent37-Key": settings.api_key,
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
    }

    print(f"POST {endpoint}")
    print(json.dumps(payload, indent=2))
    print("\nStreaming Agent37 events:\n")

    timeout = httpx.Timeout(connect=20, read=None, write=65, pool=20)
    try:
        with httpx.Client(timeout=timeout) as client:
            with client.stream("POST", endpoint, headers=headers, json=payload) as response:
                print(f"HTTP {response.status_code}")
                response.raise_for_status()
                for line in response.iter_lines():
                    if line:
                        print(line, flush=True)
        return 0
    except Exception as exc:
        print(
            f"Agent37 call failed: {type(exc).__name__}: "
            f"{redact_secrets(str(exc), settings.api_key)}",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
