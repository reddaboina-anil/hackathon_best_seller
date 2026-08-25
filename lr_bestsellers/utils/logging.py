"""Structured logging configuration using structlog.

Call :func:`configure_logging` once at application startup (before any
``structlog.get_logger()`` calls) to set up shared processors and route
output to stdout as newline-delimited JSON.  When ``log_file`` is provided
a :class:`~logging.handlers.RotatingFileHandler` is added that writes the
same JSON stream to disk.

Usage::

    from lr_bestsellers.utils.logging import configure_logging
    configure_logging(log_level="DEBUG")

    # With file rotation (10 MiB per file, keep 5 backups):
    configure_logging(log_level="INFO", log_file="logs/lr_bestsellers.log")

    import structlog
    log = structlog.get_logger(__name__)
    log.info("app.start", version="0.1.0")
"""

from __future__ import annotations

import logging
import logging.handlers
import sys
from pathlib import Path
from typing import Any

import structlog


def configure_logging(
    log_level: str = "INFO",
    log_file: str | None = None,
    log_max_bytes: int = 10 * 1024 * 1024,
    log_backup_count: int = 5,
) -> None:
    """Configure structlog for structured JSON output to stdout and optionally a file.

    Sets up a shared processor chain used by both native structlog loggers and
    stdlib loggers (e.g. from third-party libraries).  Safe to call multiple
    times — subsequent calls reconfigure in-place.

    Processor chain:
    1. Merge context vars from ``structlog.contextvars`` (request-scoped state).
    2. Annotate with log level and logger name.
    3. Add an ISO-8601 timestamp.
    4. Render stack info for exceptions.
    5. Serialise to JSON via ``JSONRenderer``.

    When ``log_file`` is provided a ``RotatingFileHandler`` is added that
    writes the identical JSON stream to ``log_file``.  The parent directory
    is created automatically if it does not exist.

    Args:
        log_level: Standard Python log level name, e.g. ``"INFO"``, ``"DEBUG"``.
            Case-insensitive.
        log_file: Optional path to a rotating JSON log file. ``None`` disables
            file logging (stdout only).
        log_max_bytes: Maximum size of a single log file before rotation.
            Default 10 MiB. Ignored when ``log_file`` is ``None``.
        log_backup_count: Number of rotated backup files to keep. Default 5.
            Ignored when ``log_file`` is ``None``.

    Example:
        >>> configure_logging("DEBUG")
        >>> configure_logging("INFO", log_file="logs/app.log", log_max_bytes=5_000_000)
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

    # Always write to stdout.
    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    # Remove any handlers added by a previous call so we don't duplicate output.
    root_logger.handlers.clear()
    root_logger.addHandler(stdout_handler)

    # Optionally write the same JSON stream to a rotating file.
    if log_file:
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            log_file,
            maxBytes=log_max_bytes,
            backupCount=log_backup_count,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        root_logger.addHandler(file_handler)

    root_logger.setLevel(log_level.upper())
