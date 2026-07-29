"""Centralised logging setup.

Three things were wrong with what this replaces.

**The request id never reached the logs.** ``RequestIDMiddleware`` has always
generated an ``X-Request-ID`` and returned it to the caller, but no log line
carried it. A user reporting "request abc123 failed" gave you an identifier
that appeared nowhere in the logs, so correlation meant guessing from
timestamps.

**Two divergent configurations.** ``main.py`` and ``worker.py`` each called
``logging.basicConfig`` with a different format, so the API and the worker —
which ship in the same image and write to the same place — produced lines that
could not be parsed by one rule.

**Nothing was machine-readable.** Plain text is right at a terminal and wrong in
CloudWatch, where every query becomes a regex. ``LOG_FORMAT`` now selects, and
production defaults to JSON.

The request id travels in a :class:`~contextvars.ContextVar` rather than being
threaded through call signatures. Under asyncio a ContextVar is per-task, so
concurrent requests cannot see each other's value, and a service function five
frames deep gets correlation without taking a parameter it does not otherwise
need.
"""

import json
import logging
import sys
from contextvars import ContextVar

# "-" rather than None: it renders predictably in both formatters, and log
# lines from startup or the worker legitimately have no request.
request_id_var: ContextVar[str] = ContextVar("request_id", default="-")

# Attributes LogRecord always carries. Anything outside this set was passed by
# the caller via `extra=` and is worth emitting as a structured field.
_STANDARD_ATTRS = frozenset(
    logging.LogRecord("", 0, "", 0, "", (), None).__dict__
) | {"asctime", "message", "taskName", "request_id", "service"}


class RequestIdFilter(logging.Filter):
    """Stamp every record with the current request id."""

    def __init__(self, service: str) -> None:
        super().__init__()
        self.service = service

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        record.service = self.service
        return True


class JsonFormatter(logging.Formatter):
    """One JSON object per line, for log aggregators."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": getattr(record, "request_id", "-"),
            "service": getattr(record, "service", "-"),
        }

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        # Anything the caller attached via extra=, so structured fields do not
        # have to be interpolated into the message and re-parsed later.
        for key, value in record.__dict__.items():
            if key not in _STANDARD_ATTRS:
                payload[key] = value

        # default=str so a stray datetime or UUID degrades to a string rather
        # than raising inside the logging call and losing the record entirely.
        return json.dumps(payload, default=str)


def configure_logging(service: str = "api") -> None:
    """Install handlers on the root logger. Safe to call more than once."""
    from app.config import get_settings

    settings = get_settings()
    level = getattr(logging, settings.log_level.upper(), logging.INFO)

    if settings.log_format == "json":
        formatter: logging.Formatter = JsonFormatter()
    else:
        formatter = logging.Formatter(
            "%(asctime)s  %(levelname)-8s  [%(request_id)s]  %(name)s  %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)
    handler.addFilter(RequestIdFilter(service))

    root = logging.getLogger()
    # Replace rather than append: uvicorn and a previous call both install
    # handlers, and without this every line is emitted once per handler.
    for existing in root.handlers[:]:
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(level)

    # uvicorn's loggers propagate to root by default only if they have no
    # handlers of their own. They do, so they would otherwise bypass the
    # formatter and lose the request id.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers.clear()
        uvicorn_logger.propagate = True


def set_request_id(request_id: str):
    """Bind a request id to the current context. Returns the reset token."""
    return request_id_var.set(request_id)


def reset_request_id(token) -> None:
    request_id_var.reset(token)
