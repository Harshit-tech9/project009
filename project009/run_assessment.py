"""CLI entry point for Project009 Phase 0 assessments."""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path

from architect.client import (
    Agent37Client,
    ConfigurationError,
    load_settings,
    redact_secrets,
    validate_template_pin,
)
from architect.events import JsonlProgressSink
from architect.persona import ScriptedPhase0Interaction, load_companies
from architect.pipeline import run_assessment


ROOT = Path(__file__).resolve().parent


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the BlaiseLogic Agentic AI Architect Phase 0 prototype."
    )
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--company", help="Company key from config/companies.yaml")
    target.add_argument("--all", action="store_true", help="Run every configured company sequentially")
    parser.add_argument(
        "--budget-credit-micros",
        type=int,
        help="Per-instance one-time managed-spend headroom in USD micros",
    )
    parser.add_argument(
        "--template",
        help="Pinned full-browser template, for example agent37-hermes@2026.07.02b",
    )
    return parser


async def _run(args: argparse.Namespace) -> int:
    settings = load_settings(ROOT / ".env")
    template = validate_template_pin(args.template or settings.template)
    budget = (
        args.budget_credit_micros
        if args.budget_credit_micros is not None
        else settings.budget_credit_micros
    )
    if budget <= 0:
        raise ConfigurationError("--budget-credit-micros must be greater than zero")

    companies = load_companies(ROOT / "config" / "companies.yaml")
    if args.all:
        selected = list(companies.values())
    else:
        if args.company not in companies:
            choices = ", ".join(sorted(companies))
            raise ConfigurationError(f"Unknown company {args.company!r}. Available: {choices}")
        selected = [companies[args.company]]

    results = []
    async with Agent37Client(settings.api_key) as client:
        for company in selected:
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            run_dir = ROOT / "runs" / f"{company.key}-{timestamp}"
            interaction = ScriptedPhase0Interaction(company, ROOT / "sample_docs")
            sink = JsonlProgressSink(run_dir / "events.jsonl")
            result = await run_assessment(
                company,
                interaction,
                sink,
                client,
                run_dir=run_dir,
                template=template,
                budget_credit_micros=budget,
            )
            results.append((result, run_dir))
            print(
                f"{company.key}: {result.status}; "
                f"known cost ${result.known_cost_usd:.6f}; artifacts {run_dir}"
            )

    return 1 if any(result.status != "completed" for result, _ in results) else 0


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        return asyncio.run(_run(args))
    except KeyboardInterrupt:
        print("Assessment interrupted; cleanup was requested.", file=sys.stderr)
        return 130
    except ConfigurationError as exc:
        print(f"Configuration error: {redact_secrets(str(exc))}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(
            f"Unexpected failure: {type(exc).__name__}: {redact_secrets(str(exc))}",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
