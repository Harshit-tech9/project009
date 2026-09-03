"""Adaptive, maximum-six-question workflow interview."""

from __future__ import annotations

import json
import re

from ..contracts import (
    AssessmentInteraction,
    CompanyConfig,
    CompanyResearch,
    InterviewDirective,
    InterviewMessage,
    InterviewProgress,
    InterviewResult,
    ResearchCorrection,
    WorkflowSummary,
)
from .common import Conversation, request_model


_ECONOMIC_BASELINE_TERMS = re.compile(
    r"\b(?:manual effort|processing time|time spent|how long|minutes?|hours?|"
    r"labou?r|loaded cost|cost baseline|economic baseline|economics|roi)\b",
    flags=re.IGNORECASE,
)


def _asks_for_economic_baseline(question: str) -> bool:
    return bool(_ECONOMIC_BASELINE_TERMS.search(question))


def _merge_topics(current: list[str], additions: list[str]) -> list[str]:
    merged = list(current)
    for topic in additions:
        if topic not in merged:
            merged.append(topic)
    return merged


async def run_interview(
    conversation: Conversation,
    company: CompanyConfig,
    research: CompanyResearch,
    interaction: AssessmentInteraction,
    research_corrections: list[ResearchCorrection] | None = None,
) -> InterviewResult:
    context = {
        "participant_role": company.role,
        "stated_challenge": company.workflow_challenge,
        "desired_outcome": company.desired_outcome,
        "proof_goal": company.proof_goal,
        "proof_fields": company.proof_fields,
        "research": research.model_dump(mode="json"),
        "user_provided_research_corrections": [
            correction.model_dump(mode="json")
            for correction in (research_corrections or [])
        ],
    }
    prompt = f"""
Begin the workflow interview from this context:
{json.dumps(context, ensure_ascii=False, indent=2)}

    Ask only for missing operational facts needed to confirm trigger, owner, frequency,
    systems, inputs, steps, failure points, success definition, human approval, and a
    measurable economic baseline (manual processing/review time, loaded labor cost,
    correction rate, or explicitly unknown values). You must ask and receive an answer
    about that economic baseline before returning `complete`.
Return either one question or a completed summary. Never ask more than one question
in this turn. Return only JSON matching this schema:
{json.dumps(InterviewDirective.model_json_schema(), ensure_ascii=False)}
""".strip()
    directive = await request_model(
        conversation,
        prompt,
        InterviewDirective,
        label="interview-start",
    )

    question_count = 0
    economic_baseline_answered = False
    confirmed_topics: list[str] = []
    messages: list[InterviewMessage] = []

    while question_count < 6:
        if directive.kind == "complete":
            if economic_baseline_answered:
                break
            directive = await request_model(
                conversation,
                f"""
The workflow summary is otherwise ready, but the required economic baseline has not
been asked. Ask exactly one question covering manual processing/review time, loaded
labor cost, correction effort, or whether those values are unknown. Do not return a
completed summary in this turn. Return only JSON matching:
{json.dumps(InterviewDirective.model_json_schema(), ensure_ascii=False)}
""".strip(),
                InterviewDirective,
                label="interview-economic-baseline",
            )
            if directive.kind != "question":
                raise ValueError(
                    "Hermes completed the interview without asking the required "
                    "economic-baseline question"
                )

        question_count += 1
        confirmed_topics = _merge_topics(confirmed_topics, directive.confirmed_topics)
        question = directive.question or ""
        economic_baseline_answered = (
            economic_baseline_answered or _asks_for_economic_baseline(question)
        )
        messages.append(InterviewMessage(role="agent", content=question))
        answer = await interaction.answer_interview(
            question,
            InterviewProgress(
                question_number=question_count,
                confirmed_topics=list(confirmed_topics),
            ),
        )
        messages.append(InterviewMessage(role="participant", content=answer))
        follow_up = f"""
Participant answer to question {question_count} of at most 6:
{answer}

        Update the confirmed topics. If enough is known, return kind `complete` with the full
        workflow summary. Otherwise ask exactly one new question. The economic baseline
        requirement has been answered: {str(economic_baseline_answered).lower()}.
        Do not complete while it is false. Return only JSON matching:
{json.dumps(InterviewDirective.model_json_schema(), ensure_ascii=False)}
""".strip()
        directive = await request_model(
            conversation,
            follow_up,
            InterviewDirective,
            label=f"interview-{question_count:02d}",
        )

    confirmed_topics = _merge_topics(confirmed_topics, directive.confirmed_topics)
    if directive.kind == "complete" and directive.summary is not None:
        summary = directive.summary
    else:
        summary_prompt = f"""
The six-question interview limit has been reached. Do not ask another question.
Produce the best confirmed workflow summary from the full conversation. Clearly keep
unknowns as uncertainty inside the relevant text rather than inventing facts.
Return only JSON matching this schema:
{json.dumps(WorkflowSummary.model_json_schema(), ensure_ascii=False)}
""".strip()
        summary = await request_model(
            conversation,
            summary_prompt,
            WorkflowSummary,
            label="interview-summary",
        )

    return InterviewResult(
        question_count=question_count,
        confirmed_topics=confirmed_topics,
        messages=messages,
        summary=summary,
    )
