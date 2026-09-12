"""Unified model client abstraction.

Business code never imports a provider SDK. It uses ``ModelClient`` and receives
either validated Pydantic output or plain text.
"""

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel

from ..errors import ModelClientError
from ..infrastructure.settings import Settings
from ..observability.trace import TraceRecorder

T = TypeVar("T", bound=BaseModel)


@dataclass
class ModelResult:
    """The raw completion result with usage metadata."""

    content: str
    model: str
    latency_ms: int
    token_input: int | None = None
    token_output: int | None = None


class ModelClient(ABC):
    """Interface for optional LLM enhancement."""

    def __init__(self, trace: TraceRecorder | None = None):
        self.trace = trace

    async def generate_structured(
        self,
        *,
        system: str,
        user: str,
        output_model: type[T],
        task_id: str | None = None,
        step: str = "llm",
        component: str = "ModelClient",
    ) -> T:
        """Generate JSON and validate it into ``output_model``."""

        started = time.perf_counter()
        try:
            result = await self._generate(
                system=system,
                user=user,
                json_mode=True,
                output_model=output_model,
            )
            self._record(
                task_id,
                step,
                component,
                "generate_structured",
                "SUCCESS",
                started,
                result,
            )
            return output_model.model_validate_json(result.content)
        except Exception as exc:
            self._record_error(task_id, step, component, "generate_structured", started, exc)
            if isinstance(exc, ModelClientError):
                raise
            raise ModelClientError(f"Structured model output is invalid: {exc}") from exc

    async def generate_text(
        self,
        *,
        system: str,
        user: str,
        task_id: str | None = None,
        step: str = "llm",
        component: str = "ModelClient",
    ) -> str:
        """Generate free-form text."""

        started = time.perf_counter()
        try:
            result = await self._generate(system=system, user=user, json_mode=False)
            self._record(task_id, step, component, "generate_text", "SUCCESS", started, result)
            return result.content
        except Exception as exc:
            self._record_error(task_id, step, component, "generate_text", started, exc)
            raise

    @abstractmethod
    async def _generate(
        self,
        *,
        system: str,
        user: str,
        json_mode: bool,
        output_model: type[BaseModel] | None = None,
    ) -> ModelResult:
        """Provider-specific completion."""

    def _record(self, task_id, step, component, action, status, started, result: ModelResult) -> None:
        if self.trace is None or task_id is None:
            return
        self.trace.record(
            task_id=task_id,
            step=step,
            component=component,
            action=action,
            status=status,
            duration_ms=int((time.perf_counter() - started) * 1000),
            model=result.model,
            token_input=result.token_input,
            token_output=result.token_output,
        )

    def _record_error(self, task_id, step, component, action, started, exc: Exception) -> None:
        if self.trace is None or task_id is None:
            return
        self.trace.record(
            task_id=task_id,
            step=step,
            component=component,
            action=action,
            status="ERROR",
            duration_ms=int((time.perf_counter() - started) * 1000),
            error=str(exc),
        )


class OpenAICompatibleModelClient(ModelClient):
    """OpenAI-compatible chat-completions client."""

    def __init__(self, settings: Settings, trace: TraceRecorder | None = None):
        super().__init__(trace)
        self._settings = settings

    async def _generate(
        self,
        *,
        system: str,
        user: str,
        json_mode: bool,
        output_model: type[BaseModel] | None = None,
    ) -> ModelResult:
        if not self._settings.llm_api_key:
            raise ModelClientError("LLM_API_KEY is not configured")
        url = self._settings.llm_base_url.rstrip("/") + "/chat/completions"
        payload: dict[str, Any] = {
            "model": self._settings.llm_model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        started = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=self._settings.llm_timeout_seconds) as client:
                response = await client.post(
                    url,
                    headers={"Authorization": f"Bearer {self._settings.llm_api_key}"},
                    json=payload,
                )
                response.raise_for_status()
                data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise ModelClientError(f"Model request failed: {exc}") from exc

        choices = data.get("choices") or []
        if not choices:
            raise ModelClientError("Model returned no choices")
        content = choices[0].get("message", {}).get("content", "")
        if isinstance(content, list):
            content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
        usage = data.get("usage") or {}
        return ModelResult(
            content=str(content),
            model=self._settings.llm_model,
            latency_ms=int((time.perf_counter() - started) * 1000),
            token_input=usage.get("prompt_tokens"),
            token_output=usage.get("completion_tokens"),
        )


class MockModelClient(ModelClient):
    """Deterministic model client for tests and offline operation."""

    def __init__(
        self,
        *,
        structured_responses: dict[str, Any] | None = None,
        text_response: str = "",
        unavailable: bool = False,
        trace: TraceRecorder | None = None,
    ):
        super().__init__(trace)
        self.structured_responses = structured_responses or {}
        self.text_response = text_response
        self.unavailable = unavailable

    async def _generate(
        self,
        *,
        system: str,
        user: str,
        json_mode: bool,
        output_model: type[BaseModel] | None = None,
    ) -> ModelResult:
        if self.unavailable:
            raise ModelClientError("Mock model is unavailable")
        if json_mode:
            if output_model is None:
                raise ModelClientError("Mock structured response requires an output model")
            value = self.structured_responses.get(output_model.__name__)
            if value is None:
                raise ModelClientError(
                    f"No mock structured response for {output_model.__name__}"
                )
            content = value.model_dump_json() if isinstance(value, BaseModel) else str(value)
            if not isinstance(value, BaseModel):
                import json

                content = json.dumps(value)
            return ModelResult(
                content=content,
                model="mock",
                latency_ms=1,
                token_input=0,
                token_output=0,
            )
        return ModelResult(
            content=self.text_response,
            model="mock",
            latency_ms=1,
            token_input=0,
            token_output=0,
        )
