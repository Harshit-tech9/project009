"""Shared conversation, persistence, and strict-JSON parsing behavior."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from ..client import Agent37Client
from ..contracts import TurnResponse, Usage
from ..run_store import FilesystemRunStore, RunStore


ModelT = TypeVar("ModelT", bound=BaseModel)


class StageValidationError(RuntimeError):
    pass


class Conversation:
    def __init__(
        self, client: Agent37Client, instance_url: str, artifact_dir: Path | RunStore
    ) -> None:
        self.client = client
        self.instance_url = instance_url
        self.run_store = (
            artifact_dir
            if isinstance(artifact_dir, RunStore)
            else FilesystemRunStore(artifact_dir)
        )
        self.session_id: str | None = None
        self.responses: list[TurnResponse] = []

    async def send(
        self,
        prompt: str,
        *,
        label: str,
        files: list[str] | None = None,
    ) -> TurnResponse:
        response = await self.client.send_turn(
            self.instance_url,
            prompt,
            session_id=self.session_id,
            files=files,
        )
        if self.session_id is not None and response.session_id != self.session_id:
            raise StageValidationError("Agent37 changed session_id during the assessment")
        self.session_id = response.session_id
        self.responses.append(response)
        turn_number = len(self.responses)
        safe_label = re.sub(r"[^a-z0-9_-]+", "-", label.lower()).strip("-")
        prefix = Path("raw") / f"{turn_number:02d}-{safe_label}"
        await self.run_store.write_text(prefix.with_suffix(".txt"), response.output_text)
        await self.run_store.write_json(prefix.with_suffix(".response.json"), response)
        return response

    def usage_since(self, response_index: int) -> Usage | None:
        usages = [response.usage for response in self.responses[response_index:] if response.usage]
        if not usages:
            return None
        known_costs = [usage.cost_usd for usage in usages if usage.cost_usd is not None]
        return Usage(
            input_tokens=sum(usage.input_tokens for usage in usages),
            output_tokens=sum(usage.output_tokens for usage in usages),
            cost_usd=sum(known_costs) if len(known_costs) == len(usages) else None,
        )


def _load_json_object(output: str) -> dict[str, object]:
    candidate = output.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*\n?(.*?)\n?```", candidate, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        candidate = fenced.group(1).strip()
    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise StageValidationError(f"invalid JSON: {exc.msg} at character {exc.pos}") from exc
    if not isinstance(parsed, dict):
        raise StageValidationError("stage output must be one JSON object")
    return parsed


def parse_model(output: str, model: type[ModelT]) -> ModelT:
    try:
        return model.model_validate(_load_json_object(output))
    except ValidationError as exc:
        raise StageValidationError(str(exc)) from exc


async def request_model(
    conversation: Conversation,
    prompt: str,
    model: type[ModelT],
    *,
    label: str,
    files: list[str] | None = None,
) -> ModelT:
    response = await conversation.send(prompt, label=label, files=files)
    try:
        return parse_model(response.output_text, model)
    except StageValidationError as first_error:
        correction = (
            "Your previous response did not match the required JSON contract. "
            "Return the corrected JSON object only, with no explanation or markdown.\n\n"
            f"Validation error:\n{first_error}\n\n"
            f"Required JSON Schema:\n{json.dumps(model.model_json_schema(), ensure_ascii=False)}"
        )
        retry = await conversation.send(correction, label=f"{label}-correction")
        try:
            return parse_model(retry.output_text, model)
        except StageValidationError as second_error:
            raise StageValidationError(
                f"{label} returned invalid output twice: {second_error}"
            ) from second_error
