"""Company research stage."""

from __future__ import annotations

import json

from ..contracts import CompanyConfig, CompanyResearch
from .common import Conversation, request_model


async def run_research(
    conversation: Conversation,
    company: CompanyConfig,
) -> CompanyResearch:
    prompt = f"""
Research this company using the browser and return a concise working understanding.

Curated starting context:
{json.dumps({
    "company_name": company.name,
    "website": company.website,
    "participant_role": company.role,
    "industry": company.industry,
    "stated_workflow_challenge": company.workflow_challenge,
    "desired_outcome": company.desired_outcome,
    "proof_goal": company.proof_goal,
    "proof_fields": company.proof_fields,
    "scenario_notice": company.scenario_notice,
}, ensure_ascii=False, indent=2)}

Do not present the curated context as independently verified. Put every verified fact
in `facts` with one or more source URLs, list the source labels and URLs in `sources`,
and put every inference in `assumptions`. Return only JSON matching this schema:
{json.dumps(CompanyResearch.model_json_schema(), ensure_ascii=False)}
""".strip()
    return await request_model(
        conversation,
        prompt,
        CompanyResearch,
        label="research",
    )
