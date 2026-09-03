"""Five-section agent blueprint generation."""

from __future__ import annotations

import json

from ..contracts import (
    AgentBlueprintResult,
    CompanyConfig,
    CompanyResearch,
    InterviewResult,
    Opportunity,
)
from .common import Conversation, StageValidationError, request_model


async def run_blueprint(
    conversation: Conversation,
    company: CompanyConfig,
    research: CompanyResearch,
    interview: InterviewResult,
    selected: Opportunity,
) -> AgentBlueprintResult:
    context = {
        "research": research.model_dump(mode="json"),
        "interview": interview.model_dump(mode="json"),
        "selected_opportunity": selected.model_dump(mode="json"),
        "customer_requirements": {
            "workflow_challenge": company.workflow_challenge,
            "desired_outcome": company.desired_outcome,
            "proof_goal": company.proof_goal,
            "proof_fields": company.proof_fields,
        },
    }
    prompt = f"""
Design the complete five-section blueprint for the selected opportunity.

Context:
{json.dumps(context, ensure_ascii=False, indent=2)}

Do not claim that any proposed integration already exists. Keep external actions
read-only or behind human approval. Economic fields are estimates and every estimate
must be supported by an assumption. Use any manual-time, loaded-labor-cost, correction,
and volume evidence from the interview to calculate and state the current per-execution
and monthly manual baseline. Do not convert currencies unless an exchange-rate source
and date are available. Agent execution and accepted-outcome costs may remain null when
provider and review costs are not known. Return only JSON matching this schema:
{json.dumps(AgentBlueprintResult.model_json_schema(), ensure_ascii=False)}
""".strip()
    blueprint = await request_model(
        conversation,
        prompt,
        AgentBlueprintResult,
        label="blueprint",
    )
    if blueprint.opportunity_id != selected.id:
        raise StageValidationError("blueprint opportunity_id does not match the selection")
    return blueprint
