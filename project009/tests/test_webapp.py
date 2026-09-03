from __future__ import annotations

import io
import zipfile
from pathlib import Path

import httpx
import pytest

from architect.contracts import AssessmentEvent
from architect.contracts import ResearchCorrection
from architect.run_store import FilesystemRunStore
from webapp.app import create_app
from webapp.coordinator import CompositeWebProgressSink, safe_event
from webapp.interactions import ActionConflict, FrontendAssessmentInteraction


def xlsx_bytes() -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types />")
        archive.writestr("xl/workbook.xml", "<workbook />")
    return output.getvalue()


@pytest.mark.asyncio
async def test_run_store_serializes_nested_models(tmp_path: Path) -> None:
    store = FilesystemRunStore(tmp_path / "run")
    await store.write_json(
        "parsed/research_corrections.json",
        [ResearchCorrection(target="summary", corrected_value="User correction")],
    )
    text = (tmp_path / "run" / "parsed" / "research_corrections.json").read_text()
    assert "user_provided" in text


@pytest.mark.asyncio
async def test_customer_requirements_are_persisted_in_the_snapshot(tmp_path: Path) -> None:
    app = create_app(database_path=tmp_path / "requirements.sqlite3")
    async with app.router.lifespan_context(app):
        requirements = {
            "company_name": "Northstar Health",
            "website": "https://northstar.example/",
            "participant_role": "Operations Director",
            "industry": "Healthcare operations",
            "workflow_challenge": "Triage incoming cases without repeated manual review.",
            "desired_outcome": "Reduce triage time while preserving human review.",
            "proof_goal": "Verify case priority and SLA assignment.",
            "proof_fields": ["case ID", "priority", "SLA"],
        }
        await app.state.database.create_assessment(
            "customer-assessment", "customer-123", "Northstar Health", 1_000_000,
            requirements,
        )
        snapshot = await app.state.database.snapshot("customer-assessment")
        assert snapshot is not None
        assert snapshot["company_name"] == "Northstar Health"
        assert snapshot["requirements"] == requirements


@pytest.mark.asyncio
async def test_snapshot_and_uploads_are_browser_safe(tmp_path: Path) -> None:
    app = create_app(database_path=tmp_path / "web.sqlite3")
    async with app.router.lifespan_context(app):
        database = app.state.database
        await database.create_assessment("assessment-1", "meridian", "Meridian", 1_000_000)
        await database.create_action(
            "documents-action",
            "assessment-1",
            "proof_workspace",
            "proof",
            {"requirements": {"formats": ["pdf", "xlsx", "png"]}},
        )
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            files = [
                ("quote.pdf", "application/pdf", b"%PDF-1.4\n%%EOF"),
                ("quote.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", xlsx_bytes()),
                ("quote.png", "image/png", b"\x89PNG\r\n\x1a\nfixture"),
            ]
            for name, mime, content in files:
                response = await client.post(
                    "/api/assessments/assessment-1/documents",
                    files={"file": (name, content, mime)},
                )
                assert response.status_code == 201, response.text
                assert "stored_path" not in response.json()

            duplicate = await client.post(
                "/api/assessments/assessment-1/documents",
                files={"file": ("again.pdf", b"%PDF-1.4\n%%EOF", "application/pdf")},
            )
            assert duplicate.status_code == 409
            snapshot = (await client.get("/api/assessments/assessment-1")).json()
            serialized = str(snapshot).lower()
            assert "stored_path" not in serialized
            assert "instance_id" not in serialized
            assert "cleanup_error" not in snapshot
            assert {document["format"] for document in snapshot["documents"]} == {
                "pdf", "xlsx", "png"
            }
            document_ids = [item["document_id"] for item in snapshot["documents"]]
            await database.create_assessment(
                "assessment-foreign", "customer-foreign", "Other", 1_000_000
            )
            await database.create_action(
                "foreign-proof-action", "assessment-foreign", "proof_workspace", "proof",
                {"messages_used": 0, "message_limit": 3},
            )
            foreign = FrontendAssessmentInteraction(database, "assessment-foreign")
            with pytest.raises(ValueError, match="owned PDF"):
                await foreign.submit(
                    "foreign-proof-action",
                    {"kind": "run", "document_ids": document_ids},
                )
            interaction = FrontendAssessmentInteraction(database, "assessment-1")
            assert await interaction.submit(
                "documents-action", {"kind": "run", "document_ids": document_ids}
            ) == "accepted"
            locked = await client.delete(
                f"/api/assessments/assessment-1/documents/{document_ids[0]}"
            )
            assert locked.status_code == 404


@pytest.mark.asyncio
async def test_actions_are_idempotent_and_sse_replays_once(tmp_path: Path) -> None:
    app = create_app(database_path=tmp_path / "events.sqlite3")
    async with app.router.lifespan_context(app):
        database = app.state.database
        await database.create_assessment("assessment-2", "meridian", "Meridian", 1_000_000)
        await database.create_action(
            "research-action", "assessment-2", "confirm_research", "research",
            {"research": {"facts": []}},
        )
        payload = {"accepted": True, "corrections": []}
        assert (await database.accept_action("assessment-2", "research-action", payload))[0] == "accepted"
        assert (await database.accept_action("assessment-2", "research-action", payload))[0] == "duplicate"
        assert (await database.accept_action("assessment-2", "research-action", {"accepted": False}))[0] == "conflict"
        await database.set_lifecycle("assessment-2", "completed", stage="results", terminal=True)
        terminal_sequence = await database.append_event(
            "assessment-2", "terminal", {"status": "completed", "stage": "results"}
        )
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/assessments/assessment-2/events?after=0")
            assert response.status_code == 200
            assert response.text.count("event: terminal") == 1
            assert f"id: {terminal_sequence}" in response.text


@pytest.mark.asyncio
async def test_blueprint_checkpoint_and_proof_workspace_are_durable(tmp_path: Path) -> None:
    app = create_app(database_path=tmp_path / "proof-workspace.sqlite3")
    async with app.router.lifespan_context(app):
        database = app.state.database
        interaction = FrontendAssessmentInteraction(database, "assessment-3")
        await database.create_assessment(
            "assessment-3", "customer-3", "Northstar", 1_000_000
        )

        await database.create_action(
            "interview-action", "assessment-3", "answer_interview", "interview",
            {
                "question": "Who reviews an exception?",
                "progress": {"question_number": 1, "confirmed_topics": ["owner"]},
            },
        )
        assert await interaction.submit(
            "interview-action", {"answer": "The operations manager."}
        ) == "accepted"
        draft = (await database.snapshot("assessment-3"))["results"]["interview_draft"]
        assert [item["content"] for item in draft["messages"]] == [
            "Who reviews an exception?", "The operations manager."
        ]

        await database.create_action(
            "blueprint-action", "assessment-3", "confirm_blueprint", "blueprint",
            {"blueprint": {"title": "Case triage agent"}},
        )
        waiting = await database.snapshot("assessment-3")
        assert waiting["activity"] == "reviewing_blueprint"
        assert waiting["pending_action"]["type"] == "confirm_blueprint"
        assert await interaction.submit(
            "blueprint-action", {"accepted": True}
        ) == "accepted"
        accepted = await database.snapshot("assessment-3")
        assert accepted["activity"] == "preparing_proof"

        await database.create_action(
            "proof-message-action", "assessment-3", "proof_workspace", "proof",
            {"messages_used": 0, "message_limit": 3},
        )
        message = {"kind": "message", "message": "Which fields will be checked?"}
        assert await interaction.submit("proof-message-action", message) == "accepted"
        assert await interaction.submit("proof-message-action", message) == "duplicate"
        snapshot = await database.snapshot("assessment-3")
        assert snapshot["activity"] == "answering_proof_message"
        assert [item["content"] for item in snapshot["proof_messages"]] == [
            "Which fields will be checked?"
        ]

        await database.create_action(
            "proof-decision-action", "assessment-3", "proof_workspace", "proof",
            {"messages_used": 1, "message_limit": 3},
        )
        assert await interaction.submit(
            "proof-decision-action", {"kind": "skip"}
        ) == "accepted"
        skipped = await database.snapshot("assessment-3")
        assert skipped["activity"] == "preparing_results"
        with pytest.raises(ActionConflict):
            await interaction.submit(
                "proof-decision-action",
                {"kind": "run", "document_ids": ["pdf", "xlsx", "png"]},
            )


@pytest.mark.asyncio
async def test_long_operation_activity_keeps_completed_stage_visible(tmp_path: Path) -> None:
    app = create_app(database_path=tmp_path / "activity.sqlite3")
    async with app.router.lifespan_context(app):
        database = app.state.database
        await database.create_assessment(
            "assessment-4", "customer-4", "Northstar", 1_000_000
        )
        await database.set_lifecycle(
            "assessment-4", "running", stage="research", activity="reviewing_research"
        )
        sink = CompositeWebProgressSink(
            database, "assessment-4", tmp_path / "activity-events.jsonl"
        )
        event = AssessmentEvent.model_validate(
            {
                "event_id": "interview-started",
                "sequence": 7,
                "event": "interview_started",
                "stage": "interview",
                "status": "in_progress",
                "timestamp": "2026-09-03T00:00:00Z",
                "assessment_id": "assessment-4",
                "company_key": "customer-4",
                "instance_id": "private-instance",
                "session_id": "private-session",
            }
        )
        await sink.emit(event)
        snapshot = await database.snapshot("assessment-4")
        assert snapshot["current_stage"] == "research"
        assert snapshot["activity"] == "preparing_question"
        assert "private-instance" not in str(await database.fetch_events("assessment-4", 0))


def test_safe_event_removes_runtime_identifiers() -> None:
    event = AssessmentEvent.model_validate(
        {
            "event_id": "event-1",
            "sequence": 2,
            "event": "document_processed",
            "stage": "proof",
            "status": "completed",
            "timestamp": "2026-09-03T00:00:00Z",
            "assessment_id": "assessment-1",
            "company_key": "meridian",
            "instance_id": "private-instance",
            "session_id": "private-session",
            "properties": {
                "document_name": "quote.pdf",
                "remote_path": "/home/user/private/quote.pdf",
                "instance_id": "private-instance",
                "cleanup_error": "internal cleanup trace",
            },
        }
    )
    payload = safe_event(event)
    assert "remote_path" not in payload["properties"]
    assert "instance_id" not in payload["properties"]
    assert "cleanup_error" not in payload["properties"]
    assert "private-instance" not in str(payload)
    assert "private-session" not in str(payload)
