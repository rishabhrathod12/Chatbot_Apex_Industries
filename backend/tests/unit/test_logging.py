import json
import logging

from app.core.logging import REDACTED, JsonFormatter, redact_text, request_id_ctx


def _format(message: str, **extra: object) -> dict[str, object]:
    record = logging.makeLogRecord({"msg": message, "levelname": "INFO", "name": "t", **extra})
    return json.loads(JsonFormatter().format(record))  # type: ignore[no-any-return]


def test_openai_key_redacted_in_message() -> None:
    assert "sk-abcdefgh12345" not in redact_text("using key sk-abcdefgh12345 now")


def test_key_value_pairs_redacted() -> None:
    out = redact_text("password=hunter2 and Authorization: Bearer abc.def.ghi")
    assert "hunter2" not in out
    assert "abc.def.ghi" not in out


def test_sensitive_extra_fields_redacted() -> None:
    data = _format("hello", api_key="sk-realsecret999", status=200)
    assert data["api_key"] == REDACTED
    assert data["status"] == 200


def test_request_id_included() -> None:
    token = request_id_ctx.set("req-123")
    try:
        assert _format("hi")["request_id"] == "req-123"
    finally:
        request_id_ctx.reset(token)
