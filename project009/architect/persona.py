"""Curated company loading and deterministic Phase 0 participant behavior."""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from .contracts import (
    AgentBlueprintResult,
    AssessmentInteraction,
    CompanyConfig,
    CompanyResearch,
    DocumentInput,
    InterviewProgress,
    OpportunitySet,
    ProofWorkspaceAction,
    ResearchReview,
)


def load_companies(path: Path) -> dict[str, CompanyConfig]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    companies = raw.get("companies")
    if not isinstance(companies, dict) or not companies:
        raise ValueError("companies.yaml must contain a non-empty 'companies' mapping")
    return {
        key: CompanyConfig.model_validate({"key": key, **value})
        for key, value in companies.items()
    }


def _normalize(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", value.lower()))


class ScriptedPhase0Interaction(AssessmentInteraction):
    def __init__(self, company: CompanyConfig, sample_dir: Path) -> None:
        self.company = company
        self.sample_dir = sample_dir

    async def confirm_research(self, research: CompanyResearch) -> ResearchReview:
        return ResearchReview(research=research)

    async def answer_interview(self, question: str, progress: InterviewProgress) -> str:
        del progress
        if self.company.persona is None:
            raise ValueError("scripted assessments require a configured persona")
        normalized_question = _normalize(question)
        question_tokens = set(normalized_question.split())
        matched_answers: list[str] = []
        for entry in self.company.persona.knowledge_base:
            matched = any(
                normalized_pattern in normalized_question
                or set(normalized_pattern.split()).issubset(question_tokens)
                for pattern in entry.patterns
                if (normalized_pattern := _normalize(pattern))
            )
            if matched and entry.answer not in matched_answers:
                matched_answers.append(entry.answer)
        return (
            " ".join(matched_answers)
            if matched_answers
            else self.company.persona.fallback
        )

    async def select_opportunity(
        self, opportunities: OpportunitySet, recommended_id: str
    ) -> str:
        del recommended_id
        proof_opportunity_id = "vendor-quotation-comparison"
        if proof_opportunity_id not in {
            opportunity.id for opportunity in opportunities.opportunities
        }:
            raise ValueError("Phase 0 proof opportunity is missing")
        return proof_opportunity_id

    async def get_documents(self) -> list[DocumentInput]:
        documents = [
            DocumentInput(
                name="vendor_a_quote.pdf",
                format="pdf",
                local_path=self.sample_dir / "vendor_a_quote.pdf",
            ),
            DocumentInput(
                name="vendor_b_quote.xlsx",
                format="xlsx",
                local_path=self.sample_dir / "vendor_b_quote.xlsx",
            ),
            DocumentInput(
                name="vendor_c_quote.png",
                format="png",
                local_path=self.sample_dir / "vendor_c_quote.png",
            ),
        ]
        missing = [str(document.local_path) for document in documents if not document.local_path.is_file()]
        if missing:
            raise FileNotFoundError(
                "Sample documents are missing. Run `python sample_docs/generate_samples.py` first: "
                + ", ".join(missing)
            )
        return documents

    async def confirm_blueprint(self, blueprint: AgentBlueprintResult) -> None:
        del blueprint

    async def next_proof_action(
        self, blueprint: AgentBlueprintResult
    ) -> ProofWorkspaceAction:
        del blueprint
        return ProofWorkspaceAction(kind="run", documents=await self.get_documents())

    async def record_proof_reply(self, content: str) -> None:
        del content
