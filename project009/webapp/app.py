"""FastAPI composition root for the local Phase 1 application."""

from __future__ import annotations

import asyncio
import hashlib
import json
import mimetypes
import zipfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator
from uuid import uuid4

from fastapi import FastAPI, File, Header, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, ConfigDict

from architect.client import ConfigurationError
from architect.contracts import AssessmentRequirements

from .coordinator import AssessmentCoordinator
from .database import Database
from .interactions import ActionConflict


ROOT = Path(__file__).resolve().parents[1]
RUNTIME_ROOT = ROOT / "runtime"
FRONTEND_DIST = ROOT.parent / "blaislogic_frontend" / "dist"
MAX_FILE_BYTES = 10_000_000
TERMINAL_STATUSES = {"completed", "failed", "cancelled"}


class StrictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ActionRequest(StrictRequest):
    kind: str | None = None
    message: str | None = None
    accepted: bool | None = None
    corrections: list[dict[str, Any]] | None = None
    answer: str | None = None
    selected_id: str | None = None
    document_ids: list[str] | None = None

    def compact(self) -> dict[str, Any]:
        return self.model_dump(exclude_none=True)


def public_document(document: dict[str, Any]) -> dict[str, Any]:
    return {
        "document_id": document["document_id"],
        "name": document["original_name"],
        "format": document["format"],
        "mime_type": document["mime_type"],
        "size_bytes": document["size_bytes"],
        "sha256": document["sha256"],
    }


def detect_format(path: Path) -> str | None:
    with path.open("rb") as handle:
        prefix = handle.read(16)
    if prefix.startswith(b"%PDF-"):
        return "pdf"
    if prefix.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if prefix.startswith(b"PK\x03\x04"):
        try:
            with zipfile.ZipFile(path) as archive:
                names = set(archive.namelist())
                if "[Content_Types].xml" in names and any(
                    name.startswith("xl/") for name in names
                ):
                    return "xlsx"
        except zipfile.BadZipFile:
            return None
    return None


def create_app(
    *,
    database_path: Path | None = None,
    interaction_timeout_seconds: float = 900,
) -> FastAPI:
    database = Database(database_path or (RUNTIME_ROOT / "phase1.sqlite3"))
    coordinator = AssessmentCoordinator(
        ROOT, database, interaction_timeout_seconds=interaction_timeout_seconds
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        await coordinator.start()
        yield
        await coordinator.stop()

    app = FastAPI(
        title="BlaiseLogic Agentic AI Architect",
        version="1.0.0-phase1",
        lifespan=lifespan,
    )
    app.state.database = database
    app.state.coordinator = coordinator

    @app.post("/api/assessments", status_code=202)
    async def create_assessment(body: AssessmentRequirements) -> dict[str, Any]:
        try:
            snapshot = await coordinator.create(body)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except ConfigurationError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        assessment_id = snapshot["assessment_id"]
        return {
            "assessment_id": assessment_id,
            "status": snapshot["status"],
            "current_stage": snapshot["current_stage"],
            "created_at": snapshot["created_at"],
            "snapshot_url": f"/api/assessments/{assessment_id}",
            "events_url": f"/api/assessments/{assessment_id}/events",
        }

    @app.get("/api/assessments/{assessment_id}")
    async def get_assessment(assessment_id: str) -> dict[str, Any]:
        snapshot = await database.snapshot(assessment_id)
        if snapshot is None:
            raise HTTPException(status_code=404, detail="assessment not found")
        return snapshot

    @app.get("/api/assessments/{assessment_id}/events")
    async def assessment_events(
        assessment_id: str,
        request: Request,
        after: int = 0,
        last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    ) -> StreamingResponse:
        if await database.get_assessment_row(assessment_id) is None:
            raise HTTPException(status_code=404, detail="assessment not found")
        cursor = after
        if last_event_id:
            try:
                cursor = max(cursor, int(last_event_id))
            except ValueError:
                raise HTTPException(status_code=400, detail="Last-Event-ID must be an integer")

        async def stream() -> AsyncIterator[str]:
            nonlocal cursor
            idle_ticks = 0
            while True:
                if await request.is_disconnected():
                    return
                events = await database.fetch_events(assessment_id, cursor)
                if events:
                    idle_ticks = 0
                    for event in events:
                        cursor = event["sequence"]
                        data = json.dumps(event["payload"], ensure_ascii=False, separators=(",", ":"))
                        yield f"id: {cursor}\nevent: {event['event_type']}\ndata: {data}\n\n"
                        if event["event_type"] == "terminal":
                            return
                else:
                    idle_ticks += 1
                    if idle_ticks >= 30:
                        idle_ticks = 0
                        yield ": heartbeat\n\n"
                await asyncio.sleep(0.5)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    @app.post("/api/assessments/{assessment_id}/actions/{action_id}")
    async def submit_action(
        assessment_id: str, action_id: str, body: ActionRequest
    ) -> dict[str, Any]:
        try:
            status = await coordinator.submit_action(
                assessment_id, action_id, body.compact()
            )
            return {"status": status, "action_id": action_id}
        except ActionConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/api/assessments/{assessment_id}/documents", status_code=201)
    async def upload_document(
        assessment_id: str, file: UploadFile = File(...)
    ) -> dict[str, Any]:
        if await database.get_assessment_row(assessment_id) is None:
            raise HTTPException(status_code=404, detail="assessment not found")
        pending = await database.pending_action(assessment_id)
        if pending is None or pending["type"] != "proof_workspace":
            raise HTTPException(status_code=409, detail="assessment is not waiting for documents")
        original_name = Path(file.filename or "").name
        extension = Path(original_name).suffix.lower().lstrip(".")
        if extension not in {"pdf", "xlsx", "png"}:
            raise HTTPException(status_code=415, detail="only PDF, XLSX, and PNG are accepted")
        allowed_mimes = {
            "pdf": {"application/pdf"},
            "xlsx": {"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"},
            "png": {"image/png"},
        }
        declared_mime = (file.content_type or mimetypes.guess_type(original_name)[0] or "").lower()
        if declared_mime not in allowed_mimes[extension]:
            raise HTTPException(status_code=415, detail="declared MIME type does not match the extension")
        existing = await database.list_documents(assessment_id)
        if extension in {item["format"] for item in existing}:
            raise HTTPException(status_code=409, detail=f"a {extension.upper()} document is already uploaded")
        upload_dir = RUNTIME_ROOT / "uploads" / assessment_id
        upload_dir.mkdir(parents=True, exist_ok=True)
        document_id = uuid4().hex
        stored_path = upload_dir / f"{document_id}.{extension}"
        digest = hashlib.sha256()
        size = 0
        try:
            with stored_path.open("wb") as handle:
                while chunk := await file.read(1024 * 1024):
                    size += len(chunk)
                    if size > MAX_FILE_BYTES:
                        raise HTTPException(status_code=413, detail="file exceeds the 10 MB limit")
                    digest.update(chunk)
                    handle.write(chunk)
            detected = detect_format(stored_path)
            if detected != extension:
                raise HTTPException(status_code=415, detail="file content does not match its format")
            record = {
                "document_id": document_id,
                "assessment_id": assessment_id,
                "original_name": original_name,
                "stored_path": str(stored_path.resolve()),
                "format": extension,
                "mime_type": declared_mime,
                "size_bytes": size,
                "sha256": digest.hexdigest(),
            }
            await database.add_document(record)
            return public_document(record)
        except BaseException:
            if stored_path.exists():
                stored_path.unlink()
            raise
        finally:
            await file.close()

    @app.delete("/api/assessments/{assessment_id}/documents/{document_id}", status_code=204)
    async def delete_document(assessment_id: str, document_id: str) -> None:
        record = await database.delete_document(assessment_id, document_id)
        if record is None:
            raise HTTPException(status_code=404, detail="removable document not found")
        path = Path(record["stored_path"])
        if path.is_file() and RUNTIME_ROOT.resolve() in path.resolve().parents:
            path.unlink()

    @app.post("/api/assessments/{assessment_id}/cancel")
    async def cancel_assessment(assessment_id: str) -> dict[str, str]:
        try:
            status = await coordinator.cancel(assessment_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="assessment not found") from exc
        return {"status": status}

    @app.get("/{path:path}", include_in_schema=False)
    async def serve_spa(path: str) -> Response:
        requested = (FRONTEND_DIST / path).resolve()
        if requested.is_file() and FRONTEND_DIST.resolve() in requested.parents:
            return FileResponse(requested)
        index = FRONTEND_DIST / "index.html"
        if index.is_file():
            return FileResponse(index)
        return JSONResponse(
            {"detail": "frontend build not found; run the Vite development server"},
            status_code=404,
        )

    return app


app = create_app()
