"""Artifact persistence boundaries shared by the CLI and web application."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel

from .client import redact_secrets


def _safe_value(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return _safe_value(value.model_dump(mode="json"))
    if isinstance(value, str):
        return redact_secrets(value)
    if isinstance(value, dict):
        return {str(key): _safe_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_safe_value(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    return value


@runtime_checkable
class RunStore(Protocol):
    @property
    def root(self) -> Path: ...

    async def write_json(
        self, relative_path: str | Path, value: BaseModel | dict[str, Any] | list[Any]
    ) -> None: ...

    async def write_text(self, relative_path: str | Path, value: str) -> None: ...

    def resolve(self, relative_path: str | Path) -> Path: ...


class FilesystemRunStore(RunStore):
    def __init__(self, root: Path) -> None:
        self._root = root
        self._root.mkdir(parents=True, exist_ok=True)

    @property
    def root(self) -> Path:
        return self._root

    def resolve(self, relative_path: str | Path) -> Path:
        candidate = (self._root / relative_path).resolve()
        root = self._root.resolve()
        if candidate != root and root not in candidate.parents:
            raise ValueError("artifact path escapes the run directory")
        return candidate

    async def write_json(
        self, relative_path: str | Path, value: BaseModel | dict[str, Any] | list[Any]
    ) -> None:
        path = self.resolve(relative_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = value.model_dump(mode="json") if isinstance(value, BaseModel) else value
        path.write_text(
            json.dumps(_safe_value(payload), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    async def write_text(self, relative_path: str | Path, value: str) -> None:
        path = self.resolve(relative_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(redact_secrets(value), encoding="utf-8")
