"""Structured logging utilities for OnGarde.io

This module provides async-safe structured logging using structlog.
All logs include request_id for tracing and performance metrics.
"""

import logging
import sys
import time
from contextvars import ContextVar
from typing import Any, Optional

import structlog
from structlog.types import EventDict, Processor

# Context variable for request tracking
request_id_var: ContextVar[Optional[str]] = ContextVar("request_id", default=None)


def add_request_id(logger: logging.Logger, method_name: str, event_dict: EventDict) -> EventDict:
    """Add request_id to log context if available."""
    request_id = request_id_var.get()
    if request_id:
        event_dict["request_id"] = request_id
    return event_dict


def add_timestamp(logger: logging.Logger, method_name: str, event_dict: EventDict) -> EventDict:
    """Add ISO timestamp to log entries."""
    event_dict["timestamp"] = time.time()
    return event_dict


def configure_logging(
    log_level: str = "INFO",
    json_output: bool = True
) -> None:
    """Configure structured logging for the application.

    Args:
        log_level: Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        json_output: If True, output JSON format. If False, use console format.
    """
    processors: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        add_request_id,
        add_timestamp,
        structlog.processors.add_log_level,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    if json_output:
        # JSON output for production (Railway)
        processors.append(structlog.processors.JSONRenderer())
    else:
        # Pretty console output for development
        processors.extend([
            structlog.dev.ConsoleRenderer(colors=True),
        ])

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, log_level.upper())
        ),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str = "ongarde") -> structlog.stdlib.BoundLogger:
    """Get a configured logger instance.

    Args:
        name: Logger name (typically module name)

    Returns:
        Configured structlog logger
    """
    return structlog.get_logger(name)


class PerformanceLogger:
    """Context manager for tracking operation performance."""

    def __init__(self, operation: str, logger: Optional[structlog.stdlib.BoundLogger] = None):
        """Initialize performance logger.

        Args:
            operation: Name of the operation being timed
            logger: Logger instance to use (creates new if None)
        """
        self.operation = operation
        self.logger = logger or get_logger()
        self.start_time: float = 0
        self.end_time: float = 0

    def __enter__(self) -> "PerformanceLogger":
        """Start timing."""
        self.start_time = time.perf_counter()
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Stop timing and log performance."""
        self.end_time = time.perf_counter()
        duration_ms = (self.end_time - self.start_time) * 1000

        if exc_type is not None:
            self.logger.error(
                f"{self.operation} failed",
                operation=self.operation,
                duration_ms=duration_ms,
                error=str(exc_val),
            )
        else:
            # Warn if operation exceeded 50ms threshold
            log_method = self.logger.warning if duration_ms > 50 else self.logger.debug
            log_method(
                f"{self.operation} completed",
                operation=self.operation,
                duration_ms=duration_ms,
            )

    @property
    def duration_ms(self) -> float:
        """Get duration in milliseconds."""
        if self.end_time == 0:
            return (time.perf_counter() - self.start_time) * 1000
        return (self.end_time - self.start_time) * 1000


def set_request_id(request_id: str) -> None:
    """Set request ID in context for all subsequent logs.

    Args:
        request_id: Unique identifier for the request
    """
    request_id_var.set(request_id)


def clear_request_id() -> None:
    """Clear request ID from context."""
    request_id_var.set(None)


# Initialize logging with sensible defaults
# This will be reconfigured by main.py based on environment
configure_logging()
