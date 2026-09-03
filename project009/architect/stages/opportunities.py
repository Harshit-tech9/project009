"""Opportunity generation and deterministic local ranking."""

from __future__ import annotations

import json

from ..contracts import (
    AssessmentInteraction,
    CompanyConfig,
    CompanyResearch,
    InterviewResult,
    Opportunity,
    OpportunitySet,
    ResearchCorrection,
)
from .common import Conversation, request_model


def score_opportunity(opportunity: Opportunity) -> float:
    scores = opportunity.scores
    return float(
        scores.business_value
        + scores.feasibility
        + scores.data_readiness
        + (6 - scores.integration_complexity)
        + (6 - scores.operational_risk)
        + (6 - scores.time_to_pilot)
    )


def rank_opportunities(generated: OpportunitySet) -> OpportunitySet:
    scored = [
        opportunity.model_copy(update={"ranking_score": score_opportunity(opportunity)})
        for opportunity in generated.opportunities
    ]
    original_positions = {opportunity.id: index for index, opportunity in enumerate(scored)}
    ranked = sorted(
        scored,
        key=lambda opportunity: (
            -float(opportunity.ranking_score or 0),
            -opportunity.scores.business_value,
            -opportunity.scores.feasibility,
            original_positions[opportunity.id],
        ),
    )
    ranked = [opportunity.model_copy(update={"rank": index}) for index, opportunity in enumerate(ranked, 1)]
    return OpportunitySet(
        opportunities=ranked,
        recommended_id=ranked[0].id,
        selected_id=None,
    )


async def run_opportunities(
    conversation: Conversation,
    company: CompanyConfig,
    research: CompanyResearch,
    interview: InterviewResult,
    interaction: AssessmentInteraction,
    research_corrections: list[ResearchCorrection] | None = None,
) -> OpportunitySet:
    context = {
        "research": research.model_dump(mode="json"),
        "user_provided_research_corrections": [
            correction.model_dump(mode="json")
            for correction in (research_corrections or [])
        ],
        "workflow": interview.summary.model_dump(mode="json"),
        "customer_requirements": {
            "desired_outcome": company.desired_outcome,
            "proof_goal": company.proof_goal,
            "proof_fields": company.proof_fields,
        },
    }
    prompt = f"""
Generate exactly three distinct, practical agent opportunities for this confirmed
workflow and the customer's stated outcome. Each opportunity must remain grounded in
their requirements. At least one must be testable with the customer's uploaded sample
documents against their stated proof goal and fields. Do not force a quotation,
procurement, or vendor workflow unless the customer context actually calls for it.

Context:
{json.dumps(context, ensure_ascii=False, indent=2)}

Use stable kebab-case ids. Scores are integers 1-5. Higher is better for business
value, feasibility, and data readiness; lower is better for integration complexity,
operational risk, and time to pilot. Leave ranking_score, rank, recommended_id, and
selected_id null or omit them; ranking is computed locally. Return only JSON matching:
{json.dumps(OpportunitySet.model_json_schema(), ensure_ascii=False)}
""".strip()
    generated = await request_model(
        conversation,
        prompt,
        OpportunitySet,
        label="opportunities",
    )
    ranked = rank_opportunities(generated)
    recommended_id = ranked.recommended_id or ranked.opportunities[0].id
    selected_id = await interaction.select_opportunity(ranked, recommended_id)
    if selected_id not in {opportunity.id for opportunity in ranked.opportunities}:
        raise ValueError("interaction selected an unknown opportunity id")
    return ranked.model_copy(update={"selected_id": selected_id})
