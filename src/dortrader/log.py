"""Structured logging with secret redaction."""

import logging
import re
from collections.abc import Mapping, MutableMapping
from typing import Any

import structlog

_SECRET_KEY = re.compile(r"(key|secret|token|password|authorization|database_url)", re.IGNORECASE)
_SECRET_VALUE = re.compile(r"(postgres(?:ql)?://[^:\s]+:)[^@\s]+(@)", re.IGNORECASE)


def _redact_value(key: str, value: Any) -> Any:
    if _SECRET_KEY.search(key):
        return "***"
    if isinstance(value, Mapping):
        return {k: _redact_value(str(k), v) for k, v in value.items()}
    if isinstance(value, str):
        return _SECRET_VALUE.sub(r"\1***\2", value)
    return value


def redact(
    _logger: Any, _method: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    for key in list(event_dict):
        if key != "event":
            event_dict[key] = _redact_value(key, event_dict[key])
    event_dict["event"] = _redact_value("event", event_dict["event"])
    return event_dict


def configure_logging(level: str = "INFO", json_logs: bool = True) -> None:
    logging.basicConfig(format="%(message)s", level=level, force=True)
    for noisy in ("httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)  # one line per request is just noise
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            redact,
            structlog.processors.JSONRenderer()
            if json_logs
            else structlog.dev.ConsoleRenderer(colors=False),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.getLevelName(level)),
        # No file argument: sys.stdout is resolved per logger, so captured streams keep working.
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=False,
    )


def get_logger(name: str) -> structlog.typing.FilteringBoundLogger:
    logger: structlog.typing.FilteringBoundLogger = structlog.get_logger(name)
    return logger
