"""Single-process Phase 1 assessment coordination."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from architect.client import Agent37Client, ConfigurationError, load_settings, redact_secrets
from architect.contracts import (
    AssessmentEvent,
    AssessmentRequirements,
    CompanyConfig,
    ProgressSink,
)
from architect.events import JsonlProgressSink
from architect.persona import load_companies
from architect.pipeline import run_assessment
from architect.run_store import FilesystemRunStore

from .database import Database
from .interactions import ActionConflict, FrontendAssessmentInteraction


TERMINAL_EVENTS = {"assessment_completed", "assessment_failed", "assessment_cancelled"}
EVENT_ACTIVITIES = {
    "workspace_starting": "starting_workspace",
    "assessment_started": "starting_workspace",
    "research_started": "researching_company",
    "interview_started": "preparing_question",
    "opportunities_started": "ranking_opportunities",
    "blueprint_started": "preparing_blueprint",
    "proof_started": "preparing_proof",
    "proof_chat.started": "answering_proof_message",
    "proof_skipped": "preparing_results",
    "proof_completed": "preparing_results",
    "results_preparing": "preparing_results",
}
EVENT_DISPLAY_STAGES = {
    "interview_started": "research",
    "opportunities_started": "interview",
    "blueprint_started": "opportunities",
    "proof_started": "blueprint",
}


def safe_event(event: AssessmentEvent) -> dict[str, Any]:
    properties = dict(event.properties)
    properties.pop("remote_path", None)
    properties.pop("instance_id", None)
    properties.pop("cleanup_error", None)
    return {
        "event": event.event,
        "core_sequence": event.sequence,
        "stage": event.stage,
        "status": event.status,
        "timestamp": event.timestamp.isoformat(),
        "usage": event.usage.model_dump(mode="json") if event.usage else None,
        "properties": properties,
    }


def json_value(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, list):
        return [json_value(item) for item in value]
    if isinstance(value, dict):
        return {key: json_value(item) for key, item in value.items()}
    return value


class WebRunStore(FilesystemRunStore):
    def __init__(self, root: Path, database: Database, assessment_id: str) -> None:
        super().__init__(root)
        self.database = database
        self.assessment_id = assessment_id

    async def write_json(self, relative_path: str | Path, value: Any) -> None:
        await super().write_json(relative_path, value)
        path = str(relative_path).replace("\\", "/")
        payload = json_value(value)
        projections = {
            "parsed/research.json": "research",
            "parsed/research_corrections.json": "research_corrections",
            "parsed/interview.json": "interview",
            "parsed/opportunities.json": "opportunities",
            "parsed/blueprint.json": "blueprint",
            "parsed/proof.json": "proof",
        }
        if path in projections:
            await self.database.project_result(
                self.assessment_id, projections[path], payload
            )
        elif path == "assessment.json":
            await self.database.project_final(self.assessment_id, payload)


class CompositeWebProgressSink(ProgressSink):
    def __init__(self, database: Database, assessment_id: str, jsonl_path: Path) -> None:
        self.database = database
        self.assessment_id = assessment_id
        self.jsonl = JsonlProgressSink(jsonl_path)

    async def emit(self, event: AssessmentEvent) -> None:
        await self.jsonl.emit(event)
        payload = safe_event(event)
        activity = EVENT_ACTIVITIES.get(event.event)
        if event.event not in TERMINAL_EVENTS and (event.instance_id or activity):
            await self.database.set_lifecycle(
                self.assessment_id,
                "running",
                stage=EVENT_DISPLAY_STAGES.get(event.event, event.stage),
                instance_id=event.instance_id,
                activity=activity,
                proof_status=event.properties.get("proof_status"),
            )
        if event.event in TERMINAL_EVENTS:
            terminal_status = {
                "assessment_completed": "completed",
                "assessment_failed": "failed",
                "assessment_cancelled": "cancelled",
            }[event.event]
            await self.database.set_lifecycle(
                self.assessment_id,
                terminal_status,
                stage="results",
                proof_status=event.properties.get("proof_status"),
                failure_reason=event.properties.get("failure_reason"),
                cleanup_error=event.properties.get("cleanup_error"),
                terminal=True,
            )
            await self.database.append_event(self.assessment_id, "terminal", payload)
        else:
            await self.database.append_event(
                self.assessment_id, "assessment_event", payload
            )


class AssessmentCoordinator:
    def __init__(
        self,
        root: Path,
        database: Database,
        *,
        interaction_timeout_seconds: float = 900,
    ) -> None:
        self.root = root
        self.database = database
        self.interaction_timeout_seconds = interaction_timeout_seconds
        self.queue: asyncio.Queue[str] = asyncio.Queue()
        self.worker: asyncio.Task[None] | None = None
        self.active_id: str | None = None
        self.active_task: asyncio.Task[None] | None = None
        self.interactions: dict[str, FrontendAssessmentInteraction] = {}

    async def start(self) -> None:
        await self.database.initialize()
        queued, interrupted = await self.database.recovery_records()
        for item in interrupted:
            if not item.get("instance_id"):
                continue
            try:
                settings = load_settings(self.root / ".env")
                async with Agent37Client(settings.api_key) as client:
                    await client.delete_instance(item["instance_id"])
            except Exception as exc:
                await self.database.set_lifecycle(
                    item["assessment_id"],
                    "failed",
                    cleanup_error=redact_secrets(str(exc)),
                )
        for assessment_id in queued:
            await self.queue.put(assessment_id)
        self.worker = asyncio.create_task(self._worker(), name="assessment-coordinator")

    async def stop(self) -> None:
        if self.active_task and not self.active_task.done():
            await self.database.set_lifecycle(self.active_id or "", "cancelling")
            self.active_task.cancel()
            try:
                await self.active_task
            except BaseException:
                pass
        if self.worker:
            self.worker.cancel()
            try:
                await self.worker
            except BaseException:
                pass

    async def create(
        self, requirements: AssessmentRequirements | dict[str, Any]
    ) -> dict[str, Any]:
        requirements = AssessmentRequirements.model_validate(requirements)
        settings = load_settings(self.root / ".env")
        assessment_id = uuid4().hex
        company_key = f"customer-{assessment_id[:12]}"
        await self.database.create_assessment(
            assessment_id,
            company_key,
            requirements.company_name,
            settings.budget_credit_micros,
            requirements.model_dump(mode="json"),
        )
        await self.queue.put(assessment_id)
        return (await self.database.snapshot(assessment_id)) or {}

    async def _worker(self) -> None:
        while True:
            assessment_id = await self.queue.get()
            row = await self.database.get_assessment_row(assessment_id)
            if row is None or row["status"] != "queued":
                self.queue.task_done()
                continue
            self.active_id = assessment_id
            self.active_task = asyncio.create_task(self._run_one(assessment_id))
            try:
                await self.active_task
            except BaseException:
                pass
            finally:
                self.active_task = None
                self.active_id = None
                self.interactions.pop(assessment_id, None)
                self.queue.task_done()

    async def _run_one(self, assessment_id: str) -> None:
        try:
            settings = load_settings(self.root / ".env")
            row = await self.database.get_assessment_row(assessment_id)
            if row is None:
                raise ValueError("assessment not found")
            raw_requirements = json.loads(row.get("requirements_json") or "{}")
            if raw_requirements:
                requirements = AssessmentRequirements.model_validate(raw_requirements)
                company = CompanyConfig(
                    key=row["company_key"],
                    name=requirements.company_name,
                    website=requirements.website,
                    role=requirements.participant_role,
                    industry=requirements.industry,
                    workflow_challenge=requirements.workflow_challenge,
                    desired_outcome=requirements.desired_outcome,
                    proof_goal=requirements.proof_goal,
                    proof_fields=requirements.proof_fields,
                    scenario_notice=(
                        "Customer-provided requirements; validate workflow details during "
                        "the interview and keep unverified claims separate from public research."
                    ),
                )
            else:
                companies = load_companies(self.root / "config" / "companies.yaml")
                company = companies[row["company_key"]]
            await self.database.set_lifecycle(assessment_id, "running", stage="start")
            run_dir = self.root / "runtime" / "runs" / assessment_id
            store = WebRunStore(run_dir, self.database, assessment_id)
            interaction = FrontendAssessmentInteraction(
                self.database,
                assessment_id,
                timeout_seconds=self.interaction_timeout_seconds,
            )
            self.interactions[assessment_id] = interaction
            sink = CompositeWebProgressSink(
                self.database, assessment_id, run_dir / "events.jsonl"
            )
            async with Agent37Client(settings.api_key) as client:
                await run_assessment(
                    company,
                    interaction,
                    sink,
                    client,
                    run_store=store,
                    assessment_id=assessment_id,
                    template=settings.template,
                    budget_credit_micros=settings.budget_credit_micros,
                )
        except asyncio.CancelledError:
            row = await self.database.get_assessment_row(assessment_id)
            if row and row["status"] not in {"completed", "failed", "cancelled"}:
                await self.database.set_lifecycle(
                    assessment_id,
                    "cancelled",
                    stage="results",
                    failure_reason="cancelled_by_user",
                    terminal=True,
                )
                await self.database.append_event(
                    assessment_id,
                    "terminal",
                    {"status": "cancelled", "stage": "results", "failure_reason": "cancelled_by_user"},
                )
            raise
        except BaseException as exc:
            reason = redact_secrets(str(exc))
            await self.database.set_lifecycle(
                assessment_id,
                "failed",
                stage="results",
                failure_reason=reason,
                terminal=True,
            )
            await self.database.append_event(
                assessment_id,
                "terminal",
                {"status": "failed", "stage": "results", "failure_reason": reason},
            )

    async def submit_action(
        self, assessment_id: str, action_id: str, response: dict[str, Any]
    ) -> str:
        interaction = self.interactions.get(assessment_id)
        if interaction is None:
            raise ActionConflict("assessment is not waiting in this server process")
        return await interaction.submit(action_id, response)

    async def cancel(self, assessment_id: str) -> str:
        row = await self.database.get_assessment_row(assessment_id)
        if row is None:
            raise KeyError(assessment_id)
        if row["status"] in {"completed", "failed", "cancelled"}:
            return row["status"]
        if row["status"] == "queued":
            await self.database.set_lifecycle(
                assessment_id, "cancelled", stage="results",
                failure_reason="cancelled_by_user", terminal=True,
            )
            await self.database.append_event(
                assessment_id, "terminal",
                {"status": "cancelled", "stage": "results", "failure_reason": "cancelled_by_user"},
            )
            return "cancelled"
        await self.database.set_lifecycle(assessment_id, "cancelling")
        if self.active_id == assessment_id and self.active_task:
            self.active_task.cancel()
            try:
                await asyncio.shield(self.active_task)
            except BaseException:
                pass
        current = await self.database.get_assessment_row(assessment_id)
        return current["status"] if current else "cancelled"
