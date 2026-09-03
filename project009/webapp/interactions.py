"""Durable browser-backed implementation of the core interaction protocol."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from uuid import uuid4

from architect.contracts import (
    AgentBlueprintResult,
    CompanyResearch,
    DocumentInput,
    InterviewProgress,
    OpportunitySet,
    ProofWorkspaceAction,
    ResearchCorrection,
    ResearchReview,
)
from architect.pipeline import AssessmentCancelled

from .database import Database


class ActionConflict(RuntimeError):
    pass


MAX_PROOF_MESSAGES = 3


class FrontendAssessmentInteraction:
    def __init__(
        self,
        database: Database,
        assessment_id: str,
        *,
        timeout_seconds: float = 900,
    ) -> None:
        self.database = database
        self.assessment_id = assessment_id
        self.timeout_seconds = timeout_seconds
        self._waiters: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._interview_messages: list[dict[str, str]] = []

    async def _wait(
        self, action_type: str, stage: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        action_id = uuid4().hex
        loop = asyncio.get_running_loop()
        future: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._waiters[action_id] = future
        await self.database.create_action(
            action_id, self.assessment_id, action_type, stage, payload
        )
        try:
            return await asyncio.wait_for(future, timeout=self.timeout_seconds)
        except asyncio.TimeoutError as exc:
            await self.database.expire_action(self.assessment_id, action_id)
            raise AssessmentCancelled("interaction_timeout") from exc
        finally:
            self._waiters.pop(action_id, None)

    async def submit(self, action_id: str, response: dict[str, Any]) -> str:
        pending = await self.database.pending_action(self.assessment_id)
        if pending is None or pending["action_id"] != action_id:
            status, _ = await self.database.accept_action(
                self.assessment_id, action_id, response
            )
            if status == "duplicate":
                return status
            raise ActionConflict("action is stale or does not match the current interaction")
        validated = self._validate_response(pending, response)
        status, accepted = await self.database.accept_action(
            self.assessment_id, action_id, validated
        )
        if status in {"missing", "conflict"}:
            raise ActionConflict("action is stale or has already been answered differently")
        if (
            status == "accepted"
            and pending["type"] == "proof_workspace"
            and validated.get("kind") == "message"
        ):
            await self.database.add_proof_message(
                self.assessment_id,
                uuid4().hex,
                "participant",
                validated["message"],
            )
        if status == "accepted" and pending["type"] == "confirm_research":
            await self.database.project_result(
                self.assessment_id,
                "research_corrections",
                validated.get("corrections", []),
            )
        if status == "accepted" and pending["type"] == "answer_interview":
            question = str(pending["payload"].get("question", "")).strip()
            if question:
                self._interview_messages.extend(
                    [
                        {"role": "agent", "content": question},
                        {"role": "participant", "content": validated["answer"]},
                    ]
                )
                await self.database.project_result(
                    self.assessment_id,
                    "interview_draft",
                    {
                        "question_count": pending["payload"].get("progress", {}).get(
                            "question_number", 0
                        ),
                        "confirmed_topics": pending["payload"].get("progress", {}).get(
                            "confirmed_topics", []
                        ),
                        "messages": self._interview_messages,
                    },
                )
        if status == "accepted" and pending["type"] == "select_opportunity":
            projected = dict(pending["payload"]["opportunities"])
            projected["selected_id"] = validated["selected_id"]
            await self.database.project_result(
                self.assessment_id, "opportunities", projected
            )
        waiter = self._waiters.get(action_id)
        if status == "accepted" and waiter is not None and not waiter.done():
            waiter.set_result(accepted or validated)
        return status

    def _validate_response(
        self, pending: dict[str, Any], response: dict[str, Any]
    ) -> dict[str, Any]:
        action_type = pending["type"]
        if action_type == "confirm_research":
            accepted = response.get("accepted")
            if not isinstance(accepted, bool):
                raise ValueError("accepted must be true or false")
            corrections = [
                ResearchCorrection.model_validate(item).model_dump(mode="json")
                for item in response.get("corrections", [])
            ]
            if not accepted and not corrections:
                raise ValueError("a rejected research summary requires corrections")
            known_labels = {
                fact["label"] for fact in pending["payload"]["research"].get("facts", [])
            }
            for correction in corrections:
                if correction["target"] == "fact" and correction["fact_label"] not in known_labels:
                    raise ValueError("fact correction must target an existing research fact")
            return {"accepted": accepted, "corrections": corrections}
        if action_type == "answer_interview":
            answer = str(response.get("answer", "")).strip()
            if not answer or len(answer) > 4000:
                raise ValueError("answer must contain between 1 and 4000 characters")
            return {"answer": answer}
        if action_type == "select_opportunity":
            selected_id = response.get("selected_id")
            if selected_id not in {
                item["id"] for item in pending["payload"]["opportunities"]["opportunities"]
            }:
                raise ValueError("selected_id is not one of the generated opportunities")
            return {"selected_id": selected_id}
        if action_type == "confirm_blueprint":
            if response != {"accepted": True}:
                raise ValueError("blueprint confirmation requires accepted=true")
            return response
        if action_type == "proof_workspace":
            kind = response.get("kind")
            if kind == "message":
                if set(response) != {"kind", "message"}:
                    raise ValueError("a proof message accepts only kind and message")
                message = str(response.get("message", "")).strip()
                if not message or len(message) > 2000:
                    raise ValueError("proof message must contain between 1 and 2000 characters")
                if int(pending["payload"].get("messages_used", 0)) >= MAX_PROOF_MESSAGES:
                    raise ValueError("the three-message proof conversation limit has been reached")
                return {"kind": "message", "message": message}
            if kind == "run":
                if set(response) != {"kind", "document_ids"}:
                    raise ValueError("running proof accepts only kind and document_ids")
                ids = response.get("document_ids")
                if not isinstance(ids, list) or len(ids) != 3 or len(set(ids)) != 3:
                    raise ValueError("exactly three unique document_ids are required")
                return {"kind": "run", "document_ids": [str(item) for item in ids]}
            if kind == "skip" and set(response) == {"kind"}:
                return {"kind": "skip"}
            raise ValueError("proof workspace kind must be message, run, or skip")
        raise ValueError("unsupported action type")

    async def confirm_research(self, research: CompanyResearch) -> ResearchReview:
        await self.database.project_result(
            self.assessment_id, "research", research.model_dump(mode="json")
        )
        response = await self._wait(
            "confirm_research",
            "research",
            {"research": research.model_dump(mode="json")},
        )
        return ResearchReview(
            research=research,
            corrections=[ResearchCorrection.model_validate(item) for item in response["corrections"]],
        )

    async def answer_interview(self, question: str, progress: InterviewProgress) -> str:
        response = await self._wait(
            "answer_interview",
            "interview",
            {"question": question, "progress": progress.model_dump(mode="json")},
        )
        pair = [
            {"role": "agent", "content": question},
            {"role": "participant", "content": response["answer"]},
        ]
        if self._interview_messages[-2:] != pair:
            self._interview_messages.extend(pair)
            await self.database.project_result(
                self.assessment_id,
                "interview_draft",
                {
                    "question_count": progress.question_number,
                    "confirmed_topics": progress.confirmed_topics,
                    "messages": self._interview_messages,
                },
            )
        return response["answer"]

    async def select_opportunity(
        self, opportunities: OpportunitySet, recommended_id: str
    ) -> str:
        await self.database.project_result(
            self.assessment_id,
            "opportunities",
            opportunities.model_dump(mode="json"),
        )
        response = await self._wait(
            "select_opportunity",
            "opportunities",
            {
                "opportunities": opportunities.model_dump(mode="json"),
                "recommended_id": recommended_id,
            },
        )
        return response["selected_id"]

    async def confirm_blueprint(self, blueprint: AgentBlueprintResult) -> None:
        await self._wait(
            "confirm_blueprint",
            "blueprint",
            {"blueprint": blueprint.model_dump(mode="json")},
        )

    async def next_proof_action(
        self, blueprint: AgentBlueprintResult
    ) -> ProofWorkspaceAction:
        messages = await self.database.list_proof_messages(self.assessment_id)
        messages_used = sum(message["role"] == "participant" for message in messages)
        response = await self._wait(
            "proof_workspace",
            "proof",
            {
                "requirements": {
                    "formats": ["pdf", "xlsx", "png"],
                    "count": 3,
                    "max_bytes_each": 10_000_000,
                },
                "blueprint": {
                    "title": blueprint.title,
                    "opportunity_id": blueprint.opportunity_id,
                },
                "messages_used": messages_used,
                "message_limit": MAX_PROOF_MESSAGES,
            },
        )
        if response["kind"] == "message":
            return ProofWorkspaceAction(kind="message", message=response["message"])
        if response["kind"] == "skip":
            return ProofWorkspaceAction(kind="skip")
        documents = await self.database.list_documents(self.assessment_id)
        selected = [item for item in documents if item["document_id"] in response["document_ids"]]
        if len(selected) != 3 or {item["format"] for item in selected} != {"pdf", "xlsx", "png"}:
            raise ValueError("selected documents must contain one PDF, XLSX, and PNG")
        await self.database.mark_documents_consumed(response["document_ids"])
        return ProofWorkspaceAction(
            kind="run",
            documents=[
                DocumentInput(
                    name=item["original_name"],
                    format=item["format"],
                    local_path=Path(item["stored_path"]),
                )
                for item in selected
            ],
        )

    async def record_proof_reply(self, content: str) -> None:
        safe_content = content.strip()[:4000]
        if not safe_content:
            safe_content = "I could not prepare a useful response. Please rephrase your proof question."
        message = await self.database.add_proof_message(
            self.assessment_id, uuid4().hex, "agent", safe_content
        )
        await self.database.append_event(
            self.assessment_id,
            "assessment_event",
            {
                "event": "proof_chat.reply",
                "stage": "proof",
                "status": "completed",
                "properties": {"message_sequence": message["sequence"]},
            },
        )
