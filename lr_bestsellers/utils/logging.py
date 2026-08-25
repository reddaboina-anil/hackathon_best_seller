"""Structured logging configuration using structlog.

Call :func:`configure_logging` once at application startup (before any
``structlog.get_logger()`` calls) to set up shared processors and route
output to stdout as newline-delimited JSON.

Usage::

    from lr_bestsellers.utils.logging import configure_logging
    configure_logging(log_level="DEBUG")

    import structlog
    log = structlog.get_logger(__name__)
    log.info("app.start", version="0.1.0")
"""

from __future__ import annotations

import logging
import sys
from typing import Any

import structlog


def configure_logging(log_level: str = "INFO") -> None:
    """Configure structlog for structured JSON output to stdout.

    Sets up a shared processor chain used by both native structlog loggers and
    stdlib loggers (e.g. from third-party libraries).  Safe to call multiple
    times — subsequent calls reconfigure in-place.

    Processor chain:
    1. Merge context vars from ``structlog.contextvars`` (request-scoped state).
    2. Annotate with log level and logger name.
    3. Add an ISO-8601 timestamp.
    4. Render stack info for exceptions.
    5. Serialise to JSON via ``JSONRenderer``.

    Args:
        log_level: Standard Python log level name, e.g. ``"INFO"``, ``"DEBUG"``.
            Case-insensitive.

    Example:
        >>> configure_logging("DEBUG")
        >>> import structlog
        >>> log = structlog.get_logger("my.module")
        >>> log.info("ready", component="api")
    """
    shared_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
    ]

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.JSONRenderer(),
        ],
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    # Remove any handlers added by a previous call so we don't duplicate output.
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(log_level.upper())
