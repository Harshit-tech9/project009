from __future__ import annotations

import asyncio
import json
import hashlib
from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from pydantic import ValidationError

from architect.client import (
    Agent37Client,
    Agent37Error,
    BudgetExhaustedError,
    ConfigurationError,
    RuntimeSettings,
    load_settings,
    redact_secrets,
)
from architect.contracts import (
    AgentBlueprintResult,
    AssessmentEvent,
    CompanyConfig,
    CompanyResearch,
    DocumentExtraction,
    DocumentInput,
    ExtractedField,
    FileEntry,
    InstanceInfo,
    InterviewProgress,
    Opportunity,
    OpportunitySet,
    ProofResult,
    ProofWorkspaceAction,
    ProgressSink,
    ResearchFact,
    ResearchSource,
    SourceReference,
    TemplateInfo,
    TurnResponse,
    Usage,
)
from architect.events import JsonlProgressSink, write_text
from architect.persona import ScriptedPhase0Interaction, load_companies
from architect.pipeline import run_assessment
from architect.stages.common import Conversation, StageValidationError, request_model
from architect.stages.interview import run_interview
from architect.stages.opportunities import rank_opportunities
import run_assessment as cli
from sample_docs.generate_samples import generate_samples


def workflow_data() -> dict[str, Any]:
    return {
        "trigger": "Purchase request above INR 200,000",
        "owner": "Two procurement analysts",
        "frequency": "About 40 comparisons per month",
        "systems": ["Outlook", "Excel", "SAP Business One"],
        "inputs": ["Vendor quotations", "Purchase request"],
        "steps": ["Collect", "Extract", "Compare", "Approve"],
        "failure_points": ["Manual re-keying", "Missing terms"],
        "success_definition": "Lowest landed cost within the lead-time constraint",
        "human_approval": "Procurement manager approval above INR 500,000",
    }


def research_data() -> dict[str, Any]:
    return {
        "company_name": "Meridian Industrial Supply",
        "website": "https://meridianindustrial.com",
        "summary": "Industrial equipment distributor serving manufacturers.",
        "facts": [
            {
                "label": "Business",
                "value": "Industrial equipment distribution",
                "source_urls": ["https://meridianindustrial.com/about"],
            }
        ],
        "sources": [
            {
                "label": "About",
                "url": "https://meridianindustrial.com/about",
            }
        ],
        "assumptions": ["Procurement is primarily email-driven."],
    }


def opportunities_data() -> dict[str, Any]:
    def opportunity(
        item_id: str,
        title: str,
        business_value: int,
        feasibility: int,
        readiness: int,
        complexity: int,
        risk: int,
        time_to_pilot: int,
    ) -> dict[str, Any]:
        return {
            "id": item_id,
            "title": title,
            "description": f"{title} description",
            "scores": {
                "business_value": business_value,
                "feasibility": feasibility,
                "data_readiness": readiness,
                "integration_complexity": complexity,
                "operational_risk": risk,
                "time_to_pilot": time_to_pilot,
            },
            "rationale": "Grounded in the confirmed workflow.",
            "assumptions": ["Historical quotes are available."],
            "risks": ["Extraction quality varies by format."],
            "pilot_weeks_min": 4,
            "pilot_weeks_max": 6,
        }

    return {
        "opportunities": [
            opportunity(
                "vendor-quotation-comparison",
                "Vendor Quotation Comparison Agent",
                5,
                5,
                5,
                1,
                1,
                1,
            ),
            opportunity("vendor-chaser", "Vendor Response Chaser", 3, 5, 4, 2, 2, 1),
            opportunity("spend-analytics", "Spend Analytics Agent", 4, 3, 2, 4, 3, 5),
        ]
    }


def blueprint_data() -> dict[str, Any]:
    return {
        "opportunity_id": "vendor-quotation-comparison",
        "title": "Quote Comparison Agent",
        "business": {
            "workflow_owner": "Procurement",
            "current_process": "Manual re-keying",
            "target_outcome": "Decision-ready comparison",
            "completion_event": "Manager approves a vendor",
            "quality_gate": "All values sourced and uncertain values flagged",
            "expected_volume": "40 comparisons per month",
        },
        "agent": {
            "objective": "Compare vendor quotations",
            "trigger": "Quotations are available",
            "inputs": ["PDF", "XLSX", "PNG"],
            "reasoning_steps": ["Identify", "Extract", "Normalize", "Compare", "Flag"],
            "tool_calls": ["Browser", "Terminal", "Vision"],
            "human_handoff": "Manager reviews the recommendation",
            "escalation": "Any price below 0.80 confidence",
        },
        "technical": {
            "model_requirements": ["Vision-capable model"],
            "document_processing": ["Agent-native PDF, XLSX, and image reading"],
            "apis_and_systems": ["Future read-only SAP export"],
            "authentication": "Future least-privilege service credentials",
            "audit_and_monitoring": "Source references and lifecycle events",
        },
        "economic": {
            "currency": "INR",
            "estimated_cost_per_execution": 10,
            "estimated_cost_per_accepted_outcome": 14,
            "current_manual_effort": "Most of one day",
            "expected_human_review_effort": "Under five minutes",
            "assumptions": ["40 executions per month; estimate is not guaranteed."],
        },
        "pilot": {
            "scope": "One bearing category and three vendors",
            "sample_data_needed": "12 historical quotation sets",
            "integrations_required": ["Read-only SAP export"],
            "success_criteria": ["At least 90% field accuracy", "Under five minutes"],
            "timeline": "Four to six weeks",
            "production_gaps": ["Live SAP integration", "Multi-currency support"],
        },
        "risks": ["Poor scans may reduce accuracy."],
        "assumptions": ["No autonomous purchasing action."],
    }


def proof_data() -> dict[str, Any]:
    fields = [
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
    documents = {
        "vendor_a_quote.pdf": {
            "format": "pdf",
            "vendor": "Apex Bearing Works",
            "values": ["Bearing 6205", 500, 412, 0, 18, 206000, 12, "Net 30", 12],
            "locators": ["page 1"] * 9,
        },
        "vendor_b_quote.xlsx": {
            "format": "xlsx",
            "vendor": "Beacon Industrial Components",
            "values": ["Bearing 6205", 500, 438, 0, 18, 219000, 7, "Net 15", None],
            "locators": [
                "Quotation!B5",
                "Quotation!B6",
                "Quotation!B7",
                "Quotation!B8",
                "Quotation!B10",
                "Quotation!B9",
                "Quotation!B11",
                "Quotation!B12",
                "Quotation!B13",
            ],
        },
        "vendor_c_quote.png": {
            "format": "png",
            "vendor": "Crest Motion Supplies",
            "values": ["Bearing 6205", 500, 429, None, 18, 214500, 10, "Net 45", 18],
            "locators": [f"image region: field {field}" for field in fields],
        },
    }
    extractions: list[dict[str, Any]] = []
    comparison_rows = [{"field": field, "cells": []} for field in fields]
    for name, document in documents.items():
        extracted_fields = []
        for index, field in enumerate(fields):
            value = document["values"][index]
            needs_review = value is None or (
                name == "vendor_c_quote.png" and field == "total_landed_cost"
            )
            source = {
                "document_name": name,
                "locator": document["locators"][index],
                "evidence": f"{field}: {value}",
            }
            extracted_fields.append(
                {
                    "name": field,
                    "raw_value": None if value is None else str(value),
                    "normalized_value": value,
                    "unit": "INR" if field in {"unit_price", "freight", "total_landed_cost"} else None,
                    "confidence": 0.0 if value is None else 0.95,
                    "review_required": needs_review,
                    "sources": [source],
                }
            )
            comparison_rows[index]["cells"].append(
                {
                    "document_name": name,
                    "value": value,
                    "confidence": 0.0 if value is None else 0.95,
                    "is_best": field == "unit_price" and name == "vendor_a_quote.pdf",
                    "review_required": needs_review,
                    "source": source,
                }
            )
        extractions.append(
            {
                "document_name": name,
                "document_format": document["format"],
                "vendor_name": document["vendor"],
                "fields": extracted_fields,
                "confidence": 0.92,
                "review_required": any(field["review_required"] for field in extracted_fields),
            }
        )
    return {
        "steps_completed": ["identify", "extract", "normalize", "compare", "flag", "generate"],
        "extractions": extractions,
        "comparison_rows": comparison_rows,
        "discrepancies": ["Vendor C subtotal conflicts with quantity times unit price."],
        "review_flags": [
            {
                "category": "missing",
                "message": "Vendor B warranty is not stated.",
                "documents": ["vendor_b_quote.xlsx"],
            }
        ],
        "recommendation": "Recommend Vendor A, subject to procurement-manager review.",
    }


def make_turn(index: int, output: str, *, status: str = "completed", error: dict | None = None) -> TurnResponse:
    return TurnResponse.model_validate(
        {
            "id": f"response-{index}",
            "session_id": "session-1",
            "status": status,
            "output_text": output,
            "usage": {"input_tokens": 10, "output_tokens": 5, "cost_usd": 0.01},
            "context": {"used_tokens": index * 15, "window_tokens": 128000},
            "error": error,
        }
    )


class FakeAgent37Client:
    def __init__(self, outputs: list[str]) -> None:
        self.outputs = list(outputs)
        self.turn_session_ids: list[str | None] = []
        self.uploaded: list[tuple[Path, str]] = []
        self.deleted = False
        self.healthy = False
        self.template_verified = False
        self.operations: list[str] = []

    async def verify_template(self, template: str) -> TemplateInfo:
        assert template == "agent37-hermes@2026.07.02b"
        self.template_verified = True
        return TemplateInfo(
            name="agent37-hermes",
            scope="system",
            agents=["hermes"],
            image_ref="ghcr.io/agent37-platform/hermes:2026.07.02b",
            description="Hermes general agent: browser, code, files.",
            default_port=3737,
        )

    async def create_instance(self, template: str, budget: int, metadata: dict[str, str]) -> InstanceInfo:
        assert "@" in template
        assert budget > 0
        assert metadata["project"] == "project009"
        return InstanceInfo(
            id="abc123def4",
            status="running",
            template=template,
            url="https://abc123def4.agent37.app",
            image_digest="sha256:test",
            resources={"cpu": 2, "memory": 4, "disk": 6},
        )

    async def wait_until_healthy(self, instance_url: str) -> None:
        assert instance_url.endswith("agent37.app")
        self.healthy = True

    async def send_turn(
        self,
        instance_url: str,
        input: str,
        *,
        session_id: str | None = None,
        files: list[str] | None = None,
    ) -> TurnResponse:
        del instance_url, input, files
        self.operations.append("turn")
        self.turn_session_ids.append(session_id)
        output = self.outputs.pop(0)
        return make_turn(len(self.turn_session_ids), output)

    async def upload_file(self, instance_url: str, local_path: Path, remote_path: str) -> FileEntry:
        del instance_url
        self.operations.append("upload")
        self.uploaded.append((local_path, remote_path))
        return FileEntry(
            name=local_path.name,
            path=remote_path.replace("~/", "/home/user/"),
            type="file",
            size=local_path.stat().st_size,
            modified=1,
            hidden=False,
        )

    async def delete_instance(self, instance_id: str) -> None:
        assert instance_id == "abc123def4"
        self.deleted = True


class StaticInteraction:
    def __init__(self, documents: list[DocumentInput]) -> None:
        self.documents = documents
        self.answer_count = 0

    async def confirm_research(self, research: CompanyResearch) -> CompanyResearch:
        return research

    async def answer_interview(self, question: str, progress: InterviewProgress) -> str:
        del question, progress
        self.answer_count += 1
        return "A scripted answer"

    async def select_opportunity(self, opportunities: OpportunitySet, recommended_id: str) -> str:
        del opportunities
        return recommended_id

    async def get_documents(self) -> list[DocumentInput]:
        return self.documents

    async def confirm_blueprint(self, blueprint: AgentBlueprintResult) -> None:
        del blueprint

    async def next_proof_action(
        self, blueprint: AgentBlueprintResult
    ) -> ProofWorkspaceAction:
        del blueprint
        return ProofWorkspaceAction(kind="run", documents=self.documents)

    async def record_proof_reply(self, content: str) -> None:
        del content


class ChatThenSkipInteraction(StaticInteraction):
    def __init__(self) -> None:
        super().__init__([])
        self.proof_actions = 0
        self.proof_replies: list[str] = []

    async def next_proof_action(
        self, blueprint: AgentBlueprintResult
    ) -> ProofWorkspaceAction:
        del blueprint
        self.proof_actions += 1
        if self.proof_actions == 1:
            return ProofWorkspaceAction(
                kind="message", message="How will low-confidence values be handled?"
            )
        return ProofWorkspaceAction(kind="skip")

    async def record_proof_reply(self, content: str) -> None:
        self.proof_replies.append(content)


class CollectingSink(ProgressSink):
    def __init__(self) -> None:
        self.events: list[AssessmentEvent] = []

    async def emit(self, event: AssessmentEvent) -> None:
        self.events.append(event)


@pytest.fixture
def company() -> CompanyConfig:
    path = Path(__file__).parents[1] / "config" / "companies.yaml"
    return load_companies(path)["meridian"]


@pytest.fixture
def documents(tmp_path: Path) -> list[DocumentInput]:
    items = []
    for name, file_format in [
        ("vendor_a_quote.pdf", "pdf"),
        ("vendor_b_quote.xlsx", "xlsx"),
        ("vendor_c_quote.png", "png"),
    ]:
        path = tmp_path / name
        path.write_bytes(f"fixture-{name}".encode())
        items.append(DocumentInput(name=name, format=file_format, local_path=path))
    return items


def complete_outputs() -> list[str]:
    return [
        "Ready.",
        json.dumps(research_data()),
        json.dumps(
            {
                "kind": "question",
                "question": "What is the manual effort and labor cost baseline?",
                "confirmed_topics": ["trigger", "owner", "frequency", "systems", "approval"],
            }
        ),
        json.dumps(
            {
                "kind": "complete",
                "confirmed_topics": [
                    "trigger",
                    "owner",
                    "frequency",
                    "systems",
                    "approval",
                    "economic baseline",
                ],
                "summary": workflow_data(),
            }
        ),
        json.dumps(opportunities_data()),
        json.dumps(blueprint_data()),
        json.dumps(proof_data()),
        json.dumps(proof_data()),
    ]


def test_settings_require_key_and_environment_wins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    dotenv = tmp_path / ".env"
    dotenv.write_text(
        "AGENT37_API_KEY=sk_live_file\n"
        "AGENT37_TEMPLATE=agent37-hermes@file-version\n"
        "AGENT37_BUDGET_CREDIT_MICROS=5\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("AGENT37_API_KEY", "sk_live_process")
    monkeypatch.setenv("AGENT37_TEMPLATE", "agent37-hermes@process-version")
    monkeypatch.setenv("AGENT37_BUDGET_CREDIT_MICROS", "1000000")
    settings = load_settings(dotenv)
    assert settings.api_key == "sk_live_process"
    assert settings.template == "agent37-hermes@process-version"
    assert settings.budget_credit_micros == 1000000
    assert "sk_live_process" not in repr(settings)

    monkeypatch.delenv("AGENT37_API_KEY")
    empty = tmp_path / "empty.env"
    empty.write_text("", encoding="utf-8")
    with pytest.raises(ConfigurationError, match="AGENT37_API_KEY is missing"):
        load_settings(empty)


@pytest.mark.asyncio
async def test_client_uses_plane_specific_headers_and_never_repr_key(tmp_path: Path) -> None:
    requests: list[httpx.Request] = []
    health_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal health_calls
        requests.append(request)
        if request.url.path.endswith("/templates/agent37-hermes@2026.07.02b"):
            return httpx.Response(
                200,
                json={
                    "name": "agent37-hermes",
                    "scope": "system",
                    "agents": ["hermes"],
                    "image_ref": "ghcr.io/agent37-platform/hermes:2026.07.02b",
                    "description": "Hermes general agent: browser, code, files.",
                    "default_port": 3737,
                },
            )
        if request.url.path == "/v1/instances" and request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": "abc123def4",
                            "status": "running",
                            "template": "agent37-hermes@2026.07.02b",
                            "url": "https://abc123def4.agent37.app",
                            "image_digest": "sha256:test",
                            "resources": {"cpu": 2, "memory": 4, "disk": 6},
                            "metadata": {"assessment_id": "assessment-1"},
                        }
                    ]
                },
            )
        if request.url.host == "api.agent37.test" and request.method == "POST":
            return httpx.Response(
                201,
                json={
                    "id": "abc123def4",
                    "status": "running",
                    "template": "agent37-hermes@2026.07.02b",
                    "url": "https://abc123def4.agent37.app",
                    "image_digest": "sha256:test",
                    "resources": {"cpu": 2, "memory": 4, "disk": 6},
                    "status_reason": None,
                },
            )
        if request.url.path.endswith("/v1/health"):
            health_calls += 1
            if health_calls == 1:
                return httpx.Response(
                    503,
                    json={
                        "error": {
                            "code": "container_unreachable",
                            "message": "starting",
                        }
                    },
                )
            return httpx.Response(200, json={"ok": True, "healthy": True})
        if request.url.path.endswith("/v1/responses"):
            return httpx.Response(
                200,
                json={
                    "id": "response-1",
                    "session_id": "session-1",
                    "status": "completed",
                    "output_text": "ready",
                },
            )
        if request.url.path.endswith("/v1/files/content"):
            return httpx.Response(
                200,
                json={
                    "name": "sample.pdf",
                    "path": "/home/user/sample.pdf",
                    "type": "file",
                    "size": 3,
                    "modified": 1788361165566.1006,
                    "hidden": False,
                },
            )
        if request.method == "DELETE":
            return httpx.Response(200, json={"id": "abc123def4", "deleted": True})
        raise AssertionError(f"unexpected request {request.method} {request.url}")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        client = Agent37Client(
            "sk_live_supersecret",
            hosting_base_url="https://api.agent37.test/v1",
            http_client=http_client,
        )
        template_info = await client.verify_template("agent37-hermes@2026.07.02b")
        assert template_info.image_ref.endswith(":2026.07.02b")
        recovered = await client.find_instance_by_metadata(
            "assessment_id", "assessment-1"
        )
        assert recovered is not None and recovered.id == "abc123def4"
        instance = await client.create_instance(
            "agent37-hermes@2026.07.02b", 1000000, {"company_key": "meridian"}
        )
        await client.wait_until_healthy(instance.url, timeout_seconds=1, poll_interval_seconds=0)
        turn = await client.send_turn(instance.url, "hello")
        assert turn.session_id == "session-1"
        local = tmp_path / "sample.pdf"
        local.write_bytes(b"pdf")
        uploaded = await client.upload_file(instance.url, local, "~/sample.pdf")
        assert uploaded.modified == pytest.approx(1788361165566.1006)
        await client.delete_instance(instance.id)
        assert "sk_live_supersecret" not in repr(client)

    hosting_requests = [request for request in requests if request.url.host == "api.agent37.test"]
    instance_requests = [request for request in requests if request.url.host == "abc123def4.agent37.app"]
    assert all(request.headers.get("Authorization") == "Bearer sk_live_supersecret" for request in hosting_requests)
    assert all("X-Agent37-Key" not in request.headers for request in hosting_requests)
    assert all(request.headers.get("X-Agent37-Key") == "sk_live_supersecret" for request in instance_requests)
    assert all("Authorization" not in request.headers for request in instance_requests)
    assert health_calls == 2


@pytest.mark.asyncio
async def test_turn_retries_only_explicit_rate_limit_failures() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        del request
        calls += 1
        if calls < 3:
            return httpx.Response(
                200,
                json={
                    "id": f"response-{calls}",
                    "session_id": "session-1",
                    "status": "failed",
                    "error": {"code": "rate_limited", "message": "retry later"},
                },
            )
        return httpx.Response(
            200,
            json={
                "id": "response-3",
                "session_id": "session-1",
                "status": "completed",
                "output_text": "done",
            },
        )

    async def no_sleep(_: float) -> None:
        return None

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = Agent37Client(
            "sk_live_secret", http_client=http_client, sleep=no_sleep, max_attempts=3
        )
        response = await client.send_turn("https://abc.agent37.app", "test")
    assert response.output_text == "done"
    assert calls == 3


@pytest.mark.asyncio
async def test_http_200_agent_failure_is_not_treated_as_success() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(
            200,
            json={
                "id": "response-1",
                "session_id": "session-1",
                "status": "failed",
                "error": {"code": "browser_failed", "message": "page unavailable"},
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = Agent37Client("sk_live_secret", http_client=http_client)
        with pytest.raises(Agent37Error, match="browser_failed"):
            await client.send_turn("https://abc.agent37.app", "test")


@pytest.mark.asyncio
async def test_client_maps_quota_exhaustion() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(
            200,
            json={
                "id": "response-1",
                "session_id": "session-1",
                "status": "failed",
                "output_text": "",
                "error": {"code": "quota_exhausted", "message": "instance budget spent"},
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = Agent37Client("sk_live_secret", http_client=http_client)
        with pytest.raises(BudgetExhaustedError) as error:
            await client.send_turn("https://abc.agent37.app", "test")
    assert error.value.code == "budget_exhausted"


def test_secret_redaction_covers_text_artifacts(tmp_path: Path) -> None:
    path = tmp_path / "artifact.txt"
    write_text(path, "failure used sk_live_should_not_escape")
    assert "sk_live_should_not_escape" not in path.read_text(encoding="utf-8")
    assert redact_secrets("Bearer sk_live_abc123") == "Bearer [REDACTED]"


@pytest.mark.asyncio
async def test_json_validation_retries_once(tmp_path: Path) -> None:
    client = FakeAgent37Client(["not-json", json.dumps(research_data())])
    conversation = Conversation(client, "https://instance", tmp_path)
    result = await request_model(
        conversation,
        "research",
        CompanyResearch,
        label="research",
    )
    assert result.company_name == "Meridian Industrial Supply"
    assert len(client.turn_session_ids) == 2

    failing = FakeAgent37Client(["not-json", "still-not-json"])
    with pytest.raises(StageValidationError, match="invalid output twice"):
        await request_model(
            Conversation(failing, "https://instance", tmp_path / "failed"),
            "research",
            CompanyResearch,
            label="research",
        )


@pytest.mark.asyncio
async def test_persona_matching_and_fallback(company: CompanyConfig, tmp_path: Path) -> None:
    interaction = ScriptedPhase0Interaction(company, tmp_path)
    assert "40" in await interaction.answer_interview(
        "How often does this happen per month?",
        InterviewProgress(question_number=1),
    )
    assert "not sure" in (
        await interaction.answer_interview(
            "What color is the warehouse gate?",
            InterviewProgress(question_number=2),
        )
    ).lower()
    compound = await interaction.answer_interview(
        "Who is responsible and what is the volume?",
        InterviewProgress(question_number=3),
    )
    assert "Two procurement analysts" in compound
    assert "40 groups" in compound
    process_and_failures = await interaction.answer_interview(
        "Who performs each current step and what are the main failure points?",
        InterviewProgress(question_number=4),
    )
    assert "re-keys nine comparison fields" in process_and_failures
    assert "missed pricing discrepancies" in process_and_failures
    economics = await interaction.answer_interview(
        "What is the manual effort and labor cost baseline?",
        InterviewProgress(question_number=5),
    )
    assert "75 analyst minutes" in economics
    assert "INR 900" in economics


@pytest.mark.asyncio
async def test_scripted_interaction_selects_the_proof_opportunity(
    company: CompanyConfig, tmp_path: Path
) -> None:
    opportunities = rank_opportunities(
        OpportunitySet.model_validate(opportunities_data())
    ).model_copy(update={"recommended_id": "vendor-chaser"})
    interaction = ScriptedPhase0Interaction(company, tmp_path)
    selected = await interaction.select_opportunity(opportunities, "vendor-chaser")
    assert selected == "vendor-quotation-comparison"


def test_ranking_is_deterministic() -> None:
    generated = OpportunitySet.model_validate(opportunities_data())
    ranked = rank_opportunities(generated)
    assert [item.id for item in ranked.opportunities] == [
        "vendor-quotation-comparison",
        "vendor-chaser",
        "spend-analytics",
    ]
    assert [item.rank for item in ranked.opportunities] == [1, 2, 3]
    assert ranked.recommended_id == "vendor-quotation-comparison"


def test_opportunities_accept_customer_specific_workflow_ids() -> None:
    payload = opportunities_data()
    payload["opportunities"][0]["id"] = "another-opportunity"
    validated = OpportunitySet.model_validate(payload)
    assert validated.opportunities[0].id == "another-opportunity"


@pytest.mark.asyncio
async def test_interview_stops_at_six_questions(
    company: CompanyConfig, tmp_path: Path
) -> None:
    directives = []
    for number in range(1, 8):
        directives.append(
            json.dumps(
                {
                    "kind": "question",
                    "question": f"Question {number}?",
                    "confirmed_topics": [f"topic-{number}"],
                }
            )
        )
    directives.append(json.dumps(workflow_data()))
    client = FakeAgent37Client(directives)
    interaction = StaticInteraction([])
    research = CompanyResearch.model_validate(research_data())
    result = await run_interview(
        Conversation(client, "https://instance", tmp_path),
        company,
        research,
        interaction,
    )
    assert result.question_count == 6
    assert interaction.answer_count == 6
    assert [message.content for message in result.messages if message.role == "agent"][-1] == "Question 6?"


@pytest.mark.asyncio
async def test_interview_forces_economic_question_before_early_completion(
    company: CompanyConfig, tmp_path: Path
) -> None:
    client = FakeAgent37Client(
        [
            json.dumps(
                {
                    "kind": "complete",
                    "confirmed_topics": ["workflow"],
                    "summary": workflow_data(),
                }
            ),
            json.dumps(
                {
                    "kind": "question",
                    "question": "How many minutes of manual effort and review are required?",
                    "confirmed_topics": ["workflow"],
                }
            ),
            json.dumps(
                {
                    "kind": "complete",
                    "confirmed_topics": ["workflow", "economic baseline"],
                    "summary": workflow_data(),
                }
            ),
        ]
    )
    interaction = StaticInteraction([])
    result = await run_interview(
        Conversation(client, "https://instance", tmp_path),
        company,
        CompanyResearch.model_validate(research_data()),
        interaction,
    )

    assert result.question_count == 1
    assert interaction.answer_count == 1
    assert result.messages[0].content.startswith("How many minutes")


@pytest.mark.asyncio
async def test_pipeline_runs_one_session_writes_events_and_cleans_up(
    company: CompanyConfig,
    documents: list[DocumentInput],
    tmp_path: Path,
) -> None:
    client = FakeAgent37Client(complete_outputs())
    interaction = StaticInteraction(documents)
    sink = CollectingSink()
    run_dir = tmp_path / "run"
    result = await run_assessment(
        company,
        interaction,
        sink,
        client,
        run_dir=run_dir,
        template="agent37-hermes@2026.07.02b",
        budget_credit_micros=1000000,
        assessment_id="browser-owned-id",
    )

    assert result.status == "completed"
    assert result.assessment_id == "browser-owned-id"
    assert result.session_id == "session-1"
    assert client.template_verified and client.healthy and client.deleted
    assert client.turn_session_ids[0] is None
    assert all(session_id == "session-1" for session_id in client.turn_session_ids[1:])
    assert len(client.uploaded) == 3
    assert client.operations[-5:] == ["upload", "upload", "upload", "turn", "turn"]
    assert (run_dir / "assessment.json").is_file()
    assert (run_dir / "interview_transcript.json").is_file()
    assert (run_dir / "parsed" / "proof.json").is_file()
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["instance_url"] == "https://abc123def4.agent37.app"
    assert manifest["resources"] == {"cpu": 2, "memory": 4, "disk": 6}
    serialized = json.loads((run_dir / "assessment.json").read_text(encoding="utf-8"))
    assert len(serialized["opportunities"]["opportunities"]) == 3
    assert set(serialized["blueprint"]) >= {
        "business",
        "agent",
        "technical",
        "economic",
        "pilot",
        "risks",
        "assumptions",
    }
    assert serialized["proof"]["steps_completed"] == [
        "identify",
        "extract",
        "normalize",
        "compare",
        "flag",
        "generate",
    ]
    assert [event.sequence for event in sink.events] == list(range(1, len(sink.events) + 1))
    assert [event.event for event in sink.events] == [
        "workspace_starting",
        "assessment_started",
        "research_started",
        "company_research.completed",
        "interview_started",
        "workflow_interview.completed",
        "opportunities_started",
        "opportunities.generated",
        "opportunity.selected",
        "blueprint_started",
        "blueprint.generated",
        "blueprint.confirmed",
        "proof_started",
        "document_processed",
        "document_processed",
        "document_processed",
        "proof_completed",
        "results_preparing",
        "agent_deleted",
        "assessment_completed",
    ]
    assert result.total_input_tokens == 80
    assert result.known_cost_usd == pytest.approx(0.08)


@pytest.mark.asyncio
async def test_proof_chat_reuses_session_and_skip_creates_blueprint_only_result(
    company: CompanyConfig,
    tmp_path: Path,
) -> None:
    client = FakeAgent37Client(complete_outputs()[:6] + ["Values below 80% confidence are flagged for review."])
    interaction = ChatThenSkipInteraction()
    sink = CollectingSink()
    run_dir = tmp_path / "skip-run"

    result = await run_assessment(
        company,
        interaction,
        sink,
        client,
        run_dir=run_dir,
        template="agent37-hermes@2026.07.02b",
        budget_credit_micros=1000000,
        assessment_id="skip-assessment",
    )

    assert result.status == "completed"
    assert result.proof_status == "skipped"
    assert result.proof is None
    assert interaction.proof_replies == [
        "Values below 80% confidence are flagged for review."
    ]
    assert client.uploaded == []
    assert client.turn_session_ids[0] is None
    assert all(session_id == "session-1" for session_id in client.turn_session_ids[1:])
    events = [event.event for event in sink.events]
    assert events.count("assessment_completed") == 1
    assert events.index("agent_deleted") < events.index("assessment_completed")
    assert "proof_chat.started" in events
    assert "proof_chat.completed" in events
    assert "proof_skipped" in events
    serialized = json.loads((run_dir / "assessment.json").read_text(encoding="utf-8"))
    assert serialized["proof_status"] == "skipped"
    assert serialized["proof"] is None


@pytest.mark.asyncio
async def test_pipeline_recovers_an_ambiguous_create_without_replaying_it(
    company: CompanyConfig,
    documents: list[DocumentInput],
    tmp_path: Path,
) -> None:
    class AmbiguousCreateClient(FakeAgent37Client):
        def __init__(self, outputs: list[str]) -> None:
            super().__init__(outputs)
            self.create_calls = 0
            self.recovery_calls = 0

        async def create_instance(
            self, template: str, budget: int, metadata: dict[str, str]
        ) -> InstanceInfo:
            del template, budget, metadata
            self.create_calls += 1
            raise Agent37Error(
                "ambiguous_transport",
                "create response timed out and was not replayed",
            )

        async def find_instance_by_metadata(
            self, key: str, value: str
        ) -> InstanceInfo | None:
            assert key == "assessment_id"
            assert value
            self.recovery_calls += 1
            return InstanceInfo(
                id="abc123def4",
                status="running",
                template="agent37-hermes@2026.07.02b",
                url="https://abc123def4.agent37.app",
                image_digest="sha256:test",
                resources={"cpu": 2, "memory": 4, "disk": 6},
                metadata={"assessment_id": value},
            )

    client = AmbiguousCreateClient(complete_outputs())
    result = await run_assessment(
        company,
        StaticInteraction(documents),
        CollectingSink(),
        client,
        run_dir=tmp_path / "recovered-run",
        template="agent37-hermes@2026.07.02b",
        budget_credit_micros=1000000,
    )
    assert result.status == "completed"
    assert client.create_calls == 1
    assert client.recovery_calls == 1
    assert client.deleted


@pytest.mark.asyncio
async def test_pipeline_deletes_instance_after_stage_failure(
    company: CompanyConfig,
    documents: list[DocumentInput],
    tmp_path: Path,
) -> None:
    client = FakeAgent37Client(["Ready.", "bad", "still bad"])
    sink = CollectingSink()
    result = await run_assessment(
        company,
        StaticInteraction(documents),
        sink,
        client,
        run_dir=tmp_path / "failed-run",
        template="agent37-hermes@2026.07.02b",
        budget_credit_micros=1000000,
    )
    assert result.status == "failed"
    assert client.deleted
    assert sink.events[-2].event == "agent_deleted"
    assert sink.events[-1].event == "assessment_failed"
    assert "stage_validation_failed" in (result.failure_reason or "")


@pytest.mark.asyncio
async def test_pipeline_cancellation_is_terminal_and_cleans_up(
    company: CompanyConfig,
    documents: list[DocumentInput],
    tmp_path: Path,
) -> None:
    entered_interaction = asyncio.Event()

    class BlockingInteraction(StaticInteraction):
        async def confirm_research(self, research: CompanyResearch) -> CompanyResearch:
            entered_interaction.set()
            await asyncio.Event().wait()
            return research

    client = FakeAgent37Client(["Ready.", json.dumps(research_data())])
    sink = CollectingSink()
    task = asyncio.create_task(
        run_assessment(
            company,
            BlockingInteraction(documents),
            sink,
            client,
            run_dir=tmp_path / "cancelled-run",
            template="agent37-hermes@2026.07.02b",
            budget_credit_micros=1000000,
        )
    )
    await asyncio.wait_for(entered_interaction.wait(), timeout=2)
    task.cancel()
    result = await task

    assert result.status == "cancelled"
    assert client.deleted
    assert [event.event for event in sink.events][-2:] == [
        "agent_deleted",
        "assessment_cancelled",
    ]


@pytest.mark.asyncio
async def test_cleanup_failure_makes_run_unsuccessful(
    company: CompanyConfig,
    documents: list[DocumentInput],
    tmp_path: Path,
) -> None:
    class CleanupFailureClient(FakeAgent37Client):
        async def delete_instance(self, instance_id: str) -> None:
            del instance_id
            raise Agent37Error("delete_failed", "cleanup endpoint unavailable")

    client = CleanupFailureClient(complete_outputs())
    sink = CollectingSink()
    result = await run_assessment(
        company,
        StaticInteraction(documents),
        sink,
        client,
        run_dir=tmp_path / "cleanup-failed-run",
        template="agent37-hermes@2026.07.02b",
        budget_credit_micros=1000000,
    )
    assert result.status == "failed"
    assert result.failure_reason == "cleanup_failed"
    assert result.cleanup_error == "delete_failed: cleanup endpoint unavailable"
    assert "agent_deleted" not in [event.event for event in sink.events]
    assert [event.event for event in sink.events].count("assessment_failed") == 1


@pytest.mark.asyncio
async def test_all_mode_continues_after_one_company_failure(
    company: CompanyConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    second_company = company.model_copy(
        update={"key": "meridian-two", "name": "Meridian Two"}
    )
    calls: list[str] = []

    class DummyClient:
        def __init__(self, api_key: str) -> None:
            assert api_key == "sk_live_process"

        async def __aenter__(self) -> "DummyClient":
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

    async def fake_run(company: CompanyConfig, *args: object, **kwargs: object) -> object:
        del args, kwargs
        calls.append(company.key)
        return SimpleNamespace(
            status="failed" if len(calls) == 1 else "completed",
            known_cost_usd=0.0,
        )

    monkeypatch.setattr(
        cli,
        "load_settings",
        lambda path: RuntimeSettings(
            api_key="sk_live_process",
            template="agent37-hermes@2026.07.02b",
            budget_credit_micros=1000000,
        ),
    )
    monkeypatch.setattr(
        cli,
        "load_companies",
        lambda path: {company.key: company, second_company.key: second_company},
    )
    monkeypatch.setattr(cli, "Agent37Client", DummyClient)
    monkeypatch.setattr(cli, "JsonlProgressSink", lambda path: CollectingSink())
    monkeypatch.setattr(cli, "run_assessment", fake_run)

    exit_code = await cli._run(
        Namespace(
            company=None,
            all=True,
            budget_credit_micros=None,
            template=None,
        )
    )
    assert calls == ["meridian", "meridian-two"]
    assert exit_code == 1


@pytest.mark.asyncio
async def test_jsonl_sink_has_ordered_secret_free_events(tmp_path: Path) -> None:
    sink = JsonlProgressSink(tmp_path / "events.jsonl")
    event = AssessmentEvent(
        event_id="event-1",
        sequence=1,
        event="assessment_failed",
        stage="results",
        status="failed",
        timestamp="2026-09-02T00:00:00Z",
        assessment_id="assessment-1",
        company_key="meridian",
        properties={"reason": "bad sk_live_do_not_store"},
    )
    await sink.emit(event)
    payload = json.loads((tmp_path / "events.jsonl").read_text(encoding="utf-8"))
    assert payload["sequence"] == 1
    assert "sk_live" not in json.dumps(payload)


def test_generated_samples_are_real_formats(tmp_path: Path) -> None:
    outputs = generate_samples(tmp_path)
    assert [path.name for path in outputs] == [
        "vendor_a_quote.pdf",
        "vendor_b_quote.xlsx",
        "vendor_c_quote.png",
    ]
    assert outputs[0].read_bytes().startswith(b"%PDF")
    assert outputs[1].read_bytes().startswith(b"PK")
    assert outputs[2].read_bytes().startswith(b"\x89PNG")
    expected = json.loads((tmp_path / "expected_results.json").read_text(encoding="utf-8"))
    assert len(expected["quotes"]) == 3
    first_hashes = [hashlib.sha256(path.read_bytes()).hexdigest() for path in outputs]
    regenerated = generate_samples(tmp_path)
    second_hashes = [hashlib.sha256(path.read_bytes()).hexdigest() for path in regenerated]
    assert first_hashes == second_hashes


def test_low_confidence_unsourced_values_must_be_flagged() -> None:
    with pytest.raises(ValidationError, match="require review"):
        ExtractedField(
            name="warranty",
            raw_value=None,
            normalized_value=None,
            confidence=0.2,
            review_required=False,
            sources=[],
        )

    flagged_field = ExtractedField(
        name="warranty",
        raw_value=None,
        normalized_value=None,
        confidence=0.2,
        review_required=True,
        sources=[],
    )
    with pytest.raises(ValidationError, match="documents require review"):
        DocumentExtraction(
            document_name="vendor_a_quote.pdf",
            document_format="pdf",
            vendor_name="Vendor A",
            fields=[flagged_field],
            confidence=0.9,
            review_required=False,
        )


@pytest.mark.parametrize(
    ("document_name", "locator"),
    [
        ("quote.pdf", "top table"),
        ("quote.xlsx", "unit-price row"),
        ("quote.png", "middle table"),
    ],
)
def test_format_specific_source_locators_are_required(
    document_name: str, locator: str
) -> None:
    with pytest.raises(ValidationError, match="source locators"):
        SourceReference(
            document_name=document_name,
            locator=locator,
            evidence="Unit price 412",
        )


def test_comparison_must_reuse_the_extraction_locator() -> None:
    payload = json.loads(json.dumps(proof_data()))
    payload["comparison_rows"][0]["cells"][1]["source"]["locator"] = "Quotation!B99"
    with pytest.raises(ValidationError, match="reuse the corresponding extraction locator"):
        ProofResult.model_validate(payload)
