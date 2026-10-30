import json
import logging
import re
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

request_id_ctx: ContextVar[str] = ContextVar("request_id", default="-")

REDACTED = "[REDACTED]"
_SENSITIVE_KEYS = ("password", "secret", "token", "api_key", "authorization", "cookie")
_KEY_VALUE = re.compile(
    r"(?i)\b(password|secret|token|api[_-]?key|authorization)\b(\s*[=:]\s*)(?:bearer\s+)?[^\s,;&]+"
)
_OPENAI_KEY = re.compile(r"sk-[A-Za-z0-9_-]{8,}")
_RESERVED = set(logging.makeLogRecord({}).__dict__) | {"message", "asctime"}


def redact_text(text: str) -> str:
    text = _OPENAI_KEY.sub(REDACTED, text)
    return _KEY_VALUE.sub(lambda m: f"{m.group(1)}{m.group(2)}{REDACTED}", text)


def _redact_value(key: str, value: Any) -> Any:
    if any(s in key.lower() for s in _SENSITIVE_KEYS):
        return REDACTED
    if isinstance(value, str):
        return redact_text(value)
    return value


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": redact_text(record.getMessage()),
            "request_id": request_id_ctx.get(),
        }
        for key, value in record.__dict__.items():
            if key not in _RESERVED and key not in payload:
                payload[key] = _redact_value(key, value)
        if record.exc_info:
            payload["exception"] = redact_text(self.formatException(record.exc_info))
        return json.dumps(payload, default=str)


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)
