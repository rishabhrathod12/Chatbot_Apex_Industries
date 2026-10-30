import logging
from collections.abc import Sequence
from typing import Any, Literal, Protocol, TypedDict

import openai

from app.core.config import Settings

logger = logging.getLogger("app.llm")


class ChatMessage(TypedDict):
    role: Literal["system", "user", "assistant"]
    content: str


class LlmError(Exception):
    """The language model could not be used. `reason` is a short code, never message content."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class ChatModel(Protocol):
    def complete(self, messages: Sequence[ChatMessage]) -> str: ...


class OpenAIChatModel:
    def __init__(
        self,
        api_key: str,
        model: str,
        temperature: float | None = 0.0,
        max_tokens: int = 600,
        client: Any = None,
    ) -> None:
        self._model = model
        self._temperature = temperature
        self._max_tokens = max_tokens
        # The SDK retries rate limits, 5xx and connection errors with backoff.
        self._client = client or openai.OpenAI(api_key=api_key, max_retries=3, timeout=60.0)

    def complete(self, messages: Sequence[ChatMessage]) -> str:
        kwargs: dict[str, Any] = {
            "model": self._model,
            "messages": list(messages),
            "max_completion_tokens": self._max_tokens,
        }
        if self._temperature is not None:
            kwargs["temperature"] = self._temperature
        try:
            response = self._client.chat.completions.create(**kwargs)
        except openai.OpenAIError as exc:
            # Log only the error type. Messages can echo request details.
            logger.error("chat request failed", extra={"error_type": type(exc).__name__})
            raise LlmError("llm_request_failed") from exc
        if not response.choices:
            raise LlmError("llm_empty_response")
        return str(response.choices[0].message.content or "")


def get_chat_model(settings: Settings) -> ChatModel:
    key = settings.openai_api_key.get_secret_value() if settings.openai_api_key else ""
    if not key:
        raise LlmError("llm_not_configured")
    return OpenAIChatModel(
        api_key=key,
        model=settings.chat_model,
        temperature=settings.chat_temperature,
        max_tokens=settings.chat_max_tokens,
    )
