"""Strict contracts shared by the CLI, orchestration, and future frontend adapter."""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ApiModel(BaseModel):
    """Upstream Agent37 objects may add fields without breaking this client."""

    model_config = ConfigDict(extra="ignore")


class Usage(StrictModel):
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    cost_usd: float | None = Field(default=None, ge=0)


class AgentContext(ApiModel):
    used_tokens: int = Field(ge=0)
    window_tokens: int = Field(gt=0)


class AgentErrorDetail(ApiModel):
    code: str
    message: str
    param: str | None = None
    hint: str | None = None
    response_id: str | None = None


class InstanceInfo(ApiModel):
    id: str
    status: str
    template: str
    url: str
    image_digest: str | None = None
    resources: dict[str, int] = Field(default_factory=dict)
    metadata: dict[str, Any] | None = None


class TemplateInfo(ApiModel):
    name: str
    scope: str
    agents: list[str] = Field(default_factory=list)
    image_ref: str | None = None
    description: str | None = None
    default_port: int | None = None


class FileEntry(ApiModel):
    name: str
    path: str
    type: str
    size: int | None = Field(default=None, ge=0)
    modified: float
    hidden: bool


class TurnResponse(ApiModel):
    id: str
    session_id: str
    status: Literal["completed", "failed", "cancelled", "in_progress"]
    output_text: str = ""
    usage: Usage | None = None
    context: AgentContext | None = None
    error: AgentErrorDetail | None = None
    created: int | None = None
    agent: str | None = None
    model: str | None = None
    provider: str | None = None
    metadata: dict[str, Any] | None = None


class PersonaEntry(StrictModel):
    topic: str
    patterns: list[str] = Field(min_length=1)
    answer: str


class PersonaConfig(StrictModel):
    fallback: str
    knowledge_base: list[PersonaEntry] = Field(min_length=1)


DEFAULT_PROOF_FIELDS = [
    "item",
    "quantity",
    "unit_price",
    "freight",
    "tax",
    "total_landed_cost",
    "lead_time",
    "payment_terms",
    "warranty",
]


class AssessmentRequirements(StrictModel):
    company_name: str = Field(min_length=2, max_length=160)
    website: str = Field(min_length=8, max_length=500)
    participant_role: str = Field(min_length=2, max_length=160)
    industry: str = Field(min_length=2, max_length=160)
    workflow_challenge: str = Field(min_length=10, max_length=4000)
    desired_outcome: str = Field(min_length=5, max_length=2000)
    proof_goal: str = Field(min_length=5, max_length=2000)
    proof_fields: list[str] = Field(min_length=1, max_length=12)

    @field_validator("website")
    @classmethod
    def validate_website(cls, value: str) -> str:
        value = value.strip()
        if not value.startswith(("https://", "http://")):
            raise ValueError("website must start with http:// or https://")
        return value

    @field_validator("proof_fields")
    @classmethod
    def validate_proof_fields(cls, values: list[str]) -> list[str]:
        cleaned = [" ".join(value.strip().split()) for value in values]
        if any(not value or len(value) > 80 for value in cleaned):
            raise ValueError("proof fields must contain between 1 and 80 characters")
        if len({value.casefold() for value in cleaned}) != len(cleaned):
            raise ValueError("proof fields must be unique")
        return cleaned


class CompanyConfig(StrictModel):
    key: str
    name: str
    website: str
    role: str
    industry: str
    workflow_challenge: str
    scenario_notice: str
    desired_outcome: str = "Improve the confirmed workflow with a human-reviewable agent."
    proof_goal: str = "Compare uploaded examples and surface discrepancies for human review."
    proof_fields: list[str] = Field(default_factory=lambda: list(DEFAULT_PROOF_FIELDS))
    persona: PersonaConfig | None = None


class ResearchSource(StrictModel):
    label: str
    url: str

    @field_validator("url")
    @classmethod
    def validate_http_url(cls, value: str) -> str:
        if not value.startswith(("https://", "http://")):
            raise ValueError("source URLs must use http or https")
        return value


class ResearchFact(StrictModel):
    label: str
    value: str
    source_urls: list[str] = Field(min_length=1)

    @field_validator("source_urls")
    @classmethod
    def validate_source_urls(cls, values: list[str]) -> list[str]:
        if any(not value.startswith(("https://", "http://")) for value in values):
            raise ValueError("fact source URLs must use http or https")
        return values


class CompanyResearch(StrictModel):
    company_name: str
    website: str
    summary: str
    facts: list[ResearchFact] = Field(min_length=1)
    sources: list[ResearchSource] = Field(min_length=1)
    assumptions: list[str]


class ResearchCorrection(StrictModel):
    target: Literal["summary", "fact"]
    fact_label: str | None = None
    corrected_value: str = Field(min_length=1, max_length=4000)
    provenance: Literal["user_provided"] = "user_provided"

    @model_validator(mode="after")
    def validate_target(self) -> "ResearchCorrection":
        if self.target == "fact" and not self.fact_label:
            raise ValueError("fact corrections require fact_label")
        if self.target == "summary" and self.fact_label is not None:
            raise ValueError("summary corrections cannot include fact_label")
        return self


class ResearchReview(StrictModel):
    research: CompanyResearch
    corrections: list[ResearchCorrection] = Field(default_factory=list)


class WorkflowSummary(StrictModel):
    trigger: str
    owner: str
    frequency: str
    systems: list[str] = Field(min_length=1)
    inputs: list[str] = Field(min_length=1)
    steps: list[str] = Field(min_length=1)
    failure_points: list[str] = Field(min_length=1)
    success_definition: str
    human_approval: str


class InterviewDirective(StrictModel):
    kind: Literal["question", "complete"]
    question: str | None = None
    confirmed_topics: list[str] = Field(default_factory=list)
    summary: WorkflowSummary | None = None

    @model_validator(mode="after")
    def validate_kind_fields(self) -> "InterviewDirective":
        if self.kind == "question" and not self.question:
            raise ValueError("a question directive requires question")
        if self.kind == "complete" and self.summary is None:
            raise ValueError("a complete directive requires summary")
        return self


class InterviewMessage(StrictModel):
    role: Literal["agent", "participant"]
    content: str


class InterviewProgress(StrictModel):
    question_number: int = Field(ge=1, le=6)
    max_questions: Literal[6] = 6
    confirmed_topics: list[str] = Field(default_factory=list)


class InterviewResult(StrictModel):
    question_count: int = Field(ge=0, le=6)
    confirmed_topics: list[str]
    messages: list[InterviewMessage]
    summary: WorkflowSummary


class OpportunityScores(StrictModel):
    business_value: int = Field(ge=1, le=5)
    feasibility: int = Field(ge=1, le=5)
    data_readiness: int = Field(ge=1, le=5)
    integration_complexity: int = Field(ge=1, le=5)
    operational_risk: int = Field(ge=1, le=5)
    time_to_pilot: int = Field(ge=1, le=5)


class Opportunity(StrictModel):
    id: str
    title: str
    description: str
    scores: OpportunityScores
    rationale: str
    assumptions: list[str]
    risks: list[str]
    pilot_weeks_min: int = Field(ge=1)
    pilot_weeks_max: int = Field(ge=1)
    ranking_score: float | None = None
    rank: int | None = Field(default=None, ge=1, le=3)

    @model_validator(mode="after")
    def validate_pilot_range(self) -> "Opportunity":
        if self.pilot_weeks_max < self.pilot_weeks_min:
            raise ValueError("pilot_weeks_max must be >= pilot_weeks_min")
        return self


class OpportunitySet(StrictModel):
    opportunities: list[Opportunity] = Field(min_length=3, max_length=3)
    recommended_id: str | None = None
    selected_id: str | None = None

    @model_validator(mode="after")
    def validate_ids(self) -> "OpportunitySet":
        ids = [opportunity.id for opportunity in self.opportunities]
        if len(set(ids)) != 3:
            raise ValueError("opportunity ids must be unique")
        for field_name in ("recommended_id", "selected_id"):
            value = getattr(self, field_name)
            if value is not None and value not in ids:
                raise ValueError(f"{field_name} must reference an opportunity")
        return self


class BusinessBlueprint(StrictModel):
    workflow_owner: str
    current_process: str
    target_outcome: str
    completion_event: str
    quality_gate: str
    expected_volume: str


class AgentBlueprint(StrictModel):
    objective: str
    trigger: str
    inputs: list[str]
    reasoning_steps: list[str]
    tool_calls: list[str]
    human_handoff: str
    escalation: str


class TechnicalBlueprint(StrictModel):
    model_requirements: list[str]
    document_processing: list[str]
    apis_and_systems: list[str]
    authentication: str
    audit_and_monitoring: str


class EconomicBlueprint(StrictModel):
    currency: str
    estimated_cost_per_execution: float | None = Field(default=None, ge=0)
    estimated_cost_per_accepted_outcome: float | None = Field(default=None, ge=0)
    current_manual_effort: str
    expected_human_review_effort: str
    assumptions: list[str] = Field(min_length=1)


class PilotBlueprint(StrictModel):
    scope: str
    sample_data_needed: str
    integrations_required: list[str]
    success_criteria: list[str]
    timeline: str
    production_gaps: list[str]


class AgentBlueprintResult(StrictModel):
    opportunity_id: str
    title: str
    business: BusinessBlueprint
    agent: AgentBlueprint
    technical: TechnicalBlueprint
    economic: EconomicBlueprint
    pilot: PilotBlueprint
    risks: list[str]
    assumptions: list[str]


class SourceReference(StrictModel):
    document_name: str
    locator: str
    evidence: str

    @model_validator(mode="after")
    def validate_format_specific_locator(self) -> "SourceReference":
        document_name = self.document_name.lower()
        locator = self.locator.strip()
        if document_name.endswith(".pdf") and not re.search(
            r"\bpage\s+\d+\b", locator, flags=re.IGNORECASE
        ):
            raise ValueError("PDF source locators must include a page number")
        if document_name.endswith(".xlsx") and not re.search(
            r"(?:[^!\s]+!)?\$?[A-Z]{1,3}\$?\d+", locator, flags=re.IGNORECASE
        ):
            raise ValueError("XLSX source locators must include a sheet/cell reference")
        if document_name.endswith(".png") and "region" not in locator.lower():
            raise ValueError("PNG source locators must identify an image region")
        return self


class ExtractedField(StrictModel):
    name: str
    raw_value: str | None = None
    normalized_value: str | int | float | None = None
    unit: str | None = None
    confidence: float = Field(ge=0, le=1)
    review_required: bool
    sources: list[SourceReference] = Field(default_factory=list)

    @model_validator(mode="after")
    def enforce_review_boundary(self) -> "ExtractedField":
        unsupported = self.raw_value is None and self.normalized_value is None
        if (self.confidence < 0.80 or unsupported or not self.sources) and not self.review_required:
            raise ValueError("low-confidence, unsupported, or unsourced fields require review")
        return self


class DocumentExtraction(StrictModel):
    document_name: str
    document_format: Literal["pdf", "xlsx", "png"]
    subject_name: str | None = None
    vendor_name: str | None = None
    fields: list[ExtractedField] = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)
    review_required: bool

    @model_validator(mode="after")
    def enforce_review_boundary(self) -> "DocumentExtraction":
        if not (self.subject_name or self.vendor_name):
            raise ValueError("document extraction requires a subject_name")
        if (
            self.confidence < 0.80 or any(field.review_required for field in self.fields)
        ) and not self.review_required:
            raise ValueError("low-confidence or flagged documents require review")
        return self


class ComparisonCell(StrictModel):
    document_name: str
    value: str | int | float | None
    confidence: float = Field(ge=0, le=1)
    is_best: bool = False
    review_required: bool
    source: SourceReference | None = None

    @model_validator(mode="after")
    def enforce_review_boundary(self) -> "ComparisonCell":
        if (self.value is None or self.confidence < 0.80 or self.source is None) and not self.review_required:
            raise ValueError("low-confidence, missing, or unsourced comparison cells require review")
        if self.source is not None and self.source.document_name != self.document_name:
            raise ValueError("comparison source must reference the same document as its cell")
        return self


class ComparisonRow(StrictModel):
    field: str
    cells: list[ComparisonCell] = Field(min_length=1)


class ReviewFlag(StrictModel):
    category: Literal["missing", "low_confidence", "contradiction", "calculation", "other"]
    message: str
    documents: list[str] = Field(min_length=1)


class ProofResult(StrictModel):
    steps_completed: list[Literal["identify", "extract", "normalize", "compare", "flag", "generate"]]
    extractions: list[DocumentExtraction] = Field(min_length=3, max_length=3)
    comparison_rows: list[ComparisonRow] = Field(min_length=1)
    discrepancies: list[str]
    review_flags: list[ReviewFlag]
    recommendation: str

    @model_validator(mode="after")
    def validate_documents(self) -> "ProofResult":
        names = [item.document_name for item in self.extractions]
        if len(set(names)) != 3:
            raise ValueError("proof must contain three unique documents")
        expected_documents = set(names)
        extraction_sources: dict[tuple[str, str], set[str]] = {}
        for extraction in self.extractions:
            for field in extraction.fields:
                if any(
                    source.document_name != extraction.document_name
                    for source in field.sources
                ):
                    raise ValueError(
                        "extraction sources must reference their extraction document"
                    )
                extraction_sources[(extraction.document_name, field.name)] = {
                    source.locator for source in field.sources
                }
        for row in self.comparison_rows:
            actual_documents = {cell.document_name for cell in row.cells}
            if len(row.cells) != 3 or actual_documents != expected_documents:
                raise ValueError("each comparison row must cover all three documents exactly once")
            for cell in row.cells:
                if cell.source is not None and cell.source.locator not in extraction_sources.get(
                    (cell.document_name, row.field), set()
                ):
                    raise ValueError(
                        "comparison sources must reuse the corresponding extraction locator"
                    )
        expected_steps = ["identify", "extract", "normalize", "compare", "flag", "generate"]
        if self.steps_completed != expected_steps:
            raise ValueError("proof steps must be complete and ordered")
        return self


class DocumentInput(StrictModel):
    name: str
    format: Literal["pdf", "xlsx", "png"]
    local_path: Path


class ProofWorkspaceAction(StrictModel):
    kind: Literal["message", "run", "skip"]
    message: str | None = Field(default=None, max_length=2000)
    documents: list[DocumentInput] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_kind(self) -> "ProofWorkspaceAction":
        if self.kind == "message" and not (self.message or "").strip():
            raise ValueError("a proof message must contain text")
        if self.kind == "run" and len(self.documents) != 3:
            raise ValueError("running proof requires three documents")
        if self.kind != "run" and self.documents:
            raise ValueError("only a run action may contain documents")
        if self.kind != "message" and self.message is not None:
            raise ValueError("only a message action may contain text")
        return self


class AssessmentEvent(StrictModel):
    event_id: str
    sequence: int = Field(ge=1)
    event: str
    stage: Literal["start", "research", "interview", "opportunities", "blueprint", "proof", "results"]
    status: Literal["started", "in_progress", "completed", "failed", "cancelled"]
    timestamp: datetime
    assessment_id: str
    company_key: str
    instance_id: str | None = None
    session_id: str | None = None
    agent_version: str | None = None
    image_digest: str | None = None
    usage: Usage | None = None
    properties: dict[str, Any] = Field(default_factory=dict)


class Assessment(StrictModel):
    assessment_id: str
    company_key: str
    company_name: str
    status: Literal["completed", "failed", "cancelled"]
    current_stage: Literal["start", "research", "interview", "opportunities", "blueprint", "proof", "results"]
    started_at: datetime
    completed_at: datetime
    instance_id: str | None = None
    instance_url: str | None = None
    session_id: str | None = None
    agent_version: str | None = None
    image_digest: str | None = None
    resources: dict[str, int] = Field(default_factory=dict)
    research: CompanyResearch | None = None
    research_corrections: list[ResearchCorrection] = Field(default_factory=list)
    interview: InterviewResult | None = None
    opportunities: OpportunitySet | None = None
    blueprint: AgentBlueprintResult | None = None
    proof: ProofResult | None = None
    proof_status: Literal["not_run", "completed", "skipped", "failed"] = "not_run"
    total_input_tokens: int = Field(default=0, ge=0)
    total_output_tokens: int = Field(default=0, ge=0)
    known_cost_usd: float = Field(default=0, ge=0)
    unreported_cost: bool = False
    failure_reason: str | None = None
    cleanup_error: str | None = None


@runtime_checkable
class AssessmentInteraction(Protocol):
    async def confirm_research(
        self, research: CompanyResearch
    ) -> ResearchReview | CompanyResearch: ...

    async def answer_interview(self, question: str, progress: InterviewProgress) -> str: ...

    async def select_opportunity(
        self, opportunities: OpportunitySet, recommended_id: str
    ) -> str: ...

    async def confirm_blueprint(self, blueprint: AgentBlueprintResult) -> None: ...

    async def next_proof_action(
        self, blueprint: AgentBlueprintResult
    ) -> ProofWorkspaceAction: ...

    async def record_proof_reply(self, content: str) -> None: ...


@runtime_checkable
class ProgressSink(Protocol):
    async def emit(self, event: AssessmentEvent) -> None: ...
