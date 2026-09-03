"""Minimal async client for the Agent37 hosting and per-instance APIs."""

from __future__ import annotations

import asyncio
import mimetypes
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

import httpx
from dotenv import load_dotenv

from .contracts import FileEntry, InstanceInfo, TemplateInfo, TurnResponse


KEY_PATTERN = re.compile(r"sk_live_[A-Za-z0-9._-]+")
RETRYABLE_CODES = {
    "no_capacity",
    "provisioning_failed",
    "try_again",
    "rate_limited",
    "container_unreachable",
    "upstream_unreachable",
    "instance_saturated",
    "host_mesh_not_ready",
    "wake_timeout",
    "wake_failed",
    "internal_error",
}


def redact_secrets(value: str, api_key: str | None = None) -> str:
    """Remove Agent37 credentials from text before it reaches logs or artifacts."""

    redacted = value.replace(api_key, "[REDACTED]") if api_key else value
    return KEY_PATTERN.sub("[REDACTED]", redacted)


class ConfigurationError(ValueError):
    pass


class Agent37Error(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int | None = None,
        response_id: str | None = None,
        api_key: str | None = None,
    ) -> None:
        self.code = code
        self.status_code = status_code
        self.response_id = response_id
        self.message = redact_secrets(message, api_key)
        super().__init__(f"{code}: {self.message}")


class BudgetExhaustedError(Agent37Error):
    pass


@dataclass(frozen=True)
class RuntimeSettings:
    api_key: str
    template: str
    budget_credit_micros: int

    def __repr__(self) -> str:
        return (
            "RuntimeSettings(api_key='[REDACTED]', "
            f"template={self.template!r}, budget_credit_micros={self.budget_credit_micros})"
        )


def validate_template_pin(template: str) -> str:
    if not re.fullmatch(r"agent37-hermes@[^@\s]+", template):
        raise ConfigurationError(
            "AGENT37_TEMPLATE must be a pinned full-browser template such as "
            "agent37-hermes@2026.07.02b"
        )
    return template


def load_settings(dotenv_path: Path | None = None) -> RuntimeSettings:
    """Load .env without overriding process-level values."""

    if dotenv_path is not None:
        load_dotenv(dotenv_path=dotenv_path, override=False)

    api_key = os.getenv("AGENT37_API_KEY", "").strip()
    if not api_key:
        raise ConfigurationError(
            "AGENT37_API_KEY is missing. Put it in project009/.env or set it in the "
            "process environment before running an assessment."
        )

    template = validate_template_pin(
        os.getenv("AGENT37_TEMPLATE", "agent37-hermes@2026.07.02b").strip()
    )
    raw_budget = os.getenv("AGENT37_BUDGET_CREDIT_MICROS", "1000000").strip()
    try:
        budget = int(raw_budget)
    except ValueError as exc:
        raise ConfigurationError("AGENT37_BUDGET_CREDIT_MICROS must be an integer") from exc
    if budget <= 0:
        raise ConfigurationError("AGENT37_BUDGET_CREDIT_MICROS must be greater than zero")
    return RuntimeSettings(api_key=api_key, template=template, budget_credit_micros=budget)


def _error_parts(response: httpx.Response) -> tuple[str, str, str | None]:
    try:
        body = response.json()
    except ValueError:
        return "http_error", response.text or f"HTTP {response.status_code}", None

    error = body.get("error") if isinstance(body, dict) else None
    if isinstance(error, str):
        return error, str(body.get("message", error)), None
    if isinstance(error, dict):
        return (
            str(error.get("code", "api_error")),
            str(error.get("message", f"HTTP {response.status_code}")),
            error.get("response_id"),
        )
    return "http_error", f"HTTP {response.status_code}", None


class Agent37Client:
    """Thin, secret-safe wrapper around only the endpoints used by Phase 0."""

    def __init__(
        self,
        api_key: str,
        *,
        hosting_base_url: str = "https://api.agent37.com/v1",
        http_client: httpx.AsyncClient | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        max_attempts: int = 3,
    ) -> None:
        if not api_key.strip():
            raise ConfigurationError("Agent37 API key cannot be empty")
        self._api_key = api_key.strip()
        self._hosting_base_url = hosting_base_url.rstrip("/")
        self._client = http_client or httpx.AsyncClient(
            timeout=httpx.Timeout(connect=20, read=300, write=65, pool=20)
        )
        self._owns_client = http_client is None
        self._sleep = sleep
        self._max_attempts = max_attempts

    def __repr__(self) -> str:
        return f"Agent37Client(api_key='[REDACTED]', hosting_base_url={self._hosting_base_url!r})"

    async def __aenter__(self) -> "Agent37Client":
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    def _headers(self, plane: str, *, content_type: str | None = "application/json") -> dict[str, str]:
        if plane == "hosting":
            headers = {"Authorization": f"Bearer {self._api_key}"}
        else:
            headers = {"X-Agent37-Key": self._api_key}
        if content_type:
            headers["Content-Type"] = content_type
        return headers

    async def _request_json(
        self,
        method: str,
        url: str,
        *,
        plane: str,
        json_body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        content: bytes | None = None,
        content_type: str | None = "application/json",
        safe_to_retry: bool = True,
        allow_missing: bool = False,
    ) -> dict[str, Any]:
        for attempt in range(1, self._max_attempts + 1):
            try:
                response = await self._client.request(
                    method,
                    url,
                    headers=self._headers(plane, content_type=content_type),
                    json=json_body,
                    params=params,
                    content=content,
                )
            except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
                if safe_to_retry and attempt < self._max_attempts:
                    await self._sleep(2 ** (attempt - 1))
                    continue
                raise Agent37Error(
                    "network_error", str(exc), api_key=self._api_key
                ) from exc
            except (httpx.ReadTimeout, httpx.WriteTimeout) as exc:
                raise Agent37Error(
                    "ambiguous_transport",
                    "The request timed out after transmission may have started; it was not replayed.",
                    api_key=self._api_key,
                ) from exc

            if response.is_success:
                try:
                    body = response.json()
                except ValueError as exc:
                    raise Agent37Error(
                        "invalid_response",
                        "Agent37 returned a non-JSON response",
                        status_code=response.status_code,
                        api_key=self._api_key,
                    ) from exc
                if not isinstance(body, dict):
                    raise Agent37Error(
                        "invalid_response",
                        "Agent37 returned an unexpected JSON shape",
                        status_code=response.status_code,
                        api_key=self._api_key,
                    )
                return body

            code, message, response_id = _error_parts(response)
            if allow_missing and response.status_code == 404 and code in {"not_found", "http_error"}:
                return {"deleted": True}
            retryable = safe_to_retry and (
                code in RETRYABLE_CODES or response.status_code in {408, 429, 500, 502, 503, 504}
            )
            if retryable and attempt < self._max_attempts:
                await self._sleep(2 ** (attempt - 1))
                continue
            raise Agent37Error(
                code,
                message,
                status_code=response.status_code,
                response_id=response_id,
                api_key=self._api_key,
            )

        raise AssertionError("retry loop exited unexpectedly")

    async def create_instance(
        self,
        template: str,
        budget_credit_micros: int,
        metadata: dict[str, str],
    ) -> InstanceInfo:
        validate_template_pin(template)
        body = await self._request_json(
            "POST",
            f"{self._hosting_base_url}/instances",
            plane="hosting",
            json_body={
                "template": template,
                "name": f"project009-{metadata.get('company_key', 'assessment')}",
                "user": metadata.get("company_key", "project009"),
                "metadata": metadata,
                "budget": {"credit_micros": budget_credit_micros},
            },
            safe_to_retry=True,
        )
        instance = InstanceInfo.model_validate(body)
        if instance.template != template:
            raise Agent37Error(
                "template_mismatch",
                f"Requested {template!r}, received {instance.template!r}",
                api_key=self._api_key,
            )
        return instance

    async def verify_template(self, template: str) -> TemplateInfo:
        """Confirm the immutable full-browser Hermes pin exists before provisioning."""

        validate_template_pin(template)
        body = await self._request_json(
            "GET",
            f"{self._hosting_base_url}/templates/{template}",
            plane="hosting",
            content_type=None,
            safe_to_retry=True,
        )
        template_info = TemplateInfo.model_validate(body)
        if "hermes" not in template_info.agents:
            raise Agent37Error(
                "invalid_template",
                f"Template {template!r} does not declare the Hermes agent",
                api_key=self._api_key,
            )
        return template_info

    async def find_instance_by_metadata(
        self, key: str, value: str
    ) -> InstanceInfo | None:
        """Recover a create whose response timed out, without replaying the create."""

        body = await self._request_json(
            "GET",
            f"{self._hosting_base_url}/instances",
            plane="hosting",
            content_type=None,
            safe_to_retry=True,
        )
        raw_instances = body.get("data")
        if not isinstance(raw_instances, list):
            raise Agent37Error(
                "invalid_response",
                "Agent37 returned an unexpected instance-list shape",
                api_key=self._api_key,
            )
        matches = [
            InstanceInfo.model_validate(item)
            for item in raw_instances
            if isinstance(item, dict)
            and isinstance(item.get("metadata"), dict)
            and item["metadata"].get(key) == value
        ]
        if len(matches) > 1:
            raise Agent37Error(
                "ambiguous_recovery",
                f"Multiple instances matched metadata {key!r}; refusing to choose one",
                api_key=self._api_key,
            )
        return matches[0] if matches else None

    async def wait_until_healthy(
        self,
        instance_url: str,
        *,
        timeout_seconds: float = 120,
        poll_interval_seconds: float = 2,
    ) -> None:
        deadline = time.monotonic() + timeout_seconds
        last_error = "gateway not ready"
        while time.monotonic() < deadline:
            try:
                body = await self._request_json(
                    "GET",
                    f"{instance_url.rstrip('/')}/v1/health",
                    plane="instance",
                    content_type=None,
                    safe_to_retry=False,
                )
                if body.get("healthy") is True:
                    return
                last_error = "health response did not report healthy=true"
            except Agent37Error as exc:
                last_error = str(exc)
            await self._sleep(poll_interval_seconds)
        raise Agent37Error(
            "health_timeout", last_error, api_key=self._api_key
        )

    async def send_turn(
        self,
        instance_url: str,
        input: str,
        *,
        session_id: str | None = None,
        files: list[str] | None = None,
    ) -> TurnResponse:
        payload: dict[str, Any] = {"input": input, "stream": False}
        if session_id:
            payload["session_id"] = session_id
        if files:
            payload["files"] = files

        for attempt in range(1, self._max_attempts + 1):
            body = await self._request_json(
                "POST",
                f"{instance_url.rstrip('/')}/v1/responses",
                plane="instance",
                json_body=payload,
                safe_to_retry=False,
            )
            response = TurnResponse.model_validate(body)
            if response.status not in {"failed", "cancelled"}:
                return response
            error = response.error or None
            code = error.code if error else "agent_error"
            message = error.message if error else f"Turn ended with status {response.status}"
            if code == "quota_exhausted":
                raise BudgetExhaustedError(
                    "budget_exhausted",
                    message,
                    response_id=response.id,
                    api_key=self._api_key,
                )
            if code == "rate_limited" and attempt < self._max_attempts:
                await self._sleep(2 ** (attempt - 1))
                continue
            raise Agent37Error(
                code,
                message,
                response_id=response.id,
                api_key=self._api_key,
            )
        raise AssertionError("turn retry loop exited unexpectedly")

    async def upload_file(
        self,
        instance_url: str,
        local_path: Path,
        remote_path: str,
    ) -> FileEntry:
        content_type = mimetypes.guess_type(local_path.name)[0] or "application/octet-stream"
        body = await self._request_json(
            "PUT",
            f"{instance_url.rstrip('/')}/v1/files/content",
            plane="instance",
            params={"path": remote_path, "overwrite": "true"},
            content=local_path.read_bytes(),
            content_type=content_type,
            safe_to_retry=True,
        )
        return FileEntry.model_validate(body)

    async def delete_instance(self, instance_id: str) -> None:
        await self._request_json(
            "DELETE",
            f"{self._hosting_base_url}/instances/{instance_id}",
            plane="hosting",
            content_type=None,
            safe_to_retry=True,
            allow_missing=True,
        )
