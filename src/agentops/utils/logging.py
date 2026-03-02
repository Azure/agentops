"""Logging configuration for AgentOps CLI.

No side effects at import time — call setup_logging() explicitly from the
CLI callback before any command runs.
"""
from __future__ import annotations

import logging

_LOG_FORMAT = "%(levelname)s: %(message)s"
_LOG_FORMAT_VERBOSE = "%(asctime)s %(name)s %(levelname)s: %(message)s"


def setup_logging(verbose: bool = False) -> None:
    """Configure root logger.

    Args:
        verbose: When True, set level to DEBUG and include timestamps.
                 When False (default), set level to INFO.
    """
    level = logging.DEBUG if verbose else logging.INFO
    fmt = _LOG_FORMAT_VERBOSE if verbose else _LOG_FORMAT

    logging.basicConfig(
        level=level,
        format=fmt,
        force=True,  # safe to call multiple times (e.g. in tests)
    )

    # Silence noisy third-party loggers unless we are in DEBUG mode
    if not verbose:
        logging.getLogger("urllib3").setLevel(logging.WARNING)
        logging.getLogger("azure").setLevel(logging.WARNING)
        logging.getLogger("azure.identity").setLevel(logging.WARNING)
        logging.getLogger("azure.core").setLevel(logging.WARNING)
        logging.getLogger("azure.core.pipeline").setLevel(logging.WARNING)
        logging.getLogger("azure.core.pipeline.policies.http_logging_policy").setLevel(logging.WARNING)
        logging.getLogger("azure.ai.evaluation").setLevel(logging.WARNING)
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("openai").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """Return a module-level logger.

    Usage:
        log = get_logger(__name__)
        log.debug("...")
    """
    return logging.getLogger(name)
