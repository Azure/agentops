"""Logging configuration for AgentOps CLI.

No side effects at import time - call setup_logging() explicitly from the
CLI callback before any command runs.
"""

from __future__ import annotations

import logging

_LOG_FORMAT = "%(levelname)s: %(message)s"
_LOG_FORMAT_VERBOSE = "%(asctime)s %(name)s %(levelname)s: %(message)s"

# Loggers that must never reach the console below WARNING in non-verbose
# runs. Setting the level on the logger is not enough: any third-party
# import can call ``logging.basicConfig(force=True)`` or reset the level
# afterwards, which is how ``INFO: HTTP Request: ...`` lines leak over the
# Doctor spinner. The filter below is installed on the loggers themselves,
# which nothing in the stdlib can undo, and on the root handler as a
# second line of defence.
_NOISY_LOGGERS = (
    "urllib3",
    "azure",
    "httpx",
    "httpcore",
    "openai",
)


class _NoisyThirdPartyFilter(logging.Filter):
    """Drop sub-WARNING records emitted by known chatty dependencies."""

    def filter(self, record: logging.LogRecord) -> bool:
        if record.levelno >= logging.WARNING:
            return True
        name = record.name
        return not any(
            name == noisy or name.startswith(noisy + ".")
            for noisy in _NOISY_LOGGERS
        )


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
        # azure.identity emits WARNING when individual credential sources
        # in DefaultAzureCredential fail (e.g. the Azure CLI is locked or
        # times out). Those failures are usually transient and the chain
        # still succeeds via another source, so we hide them at the user
        # level. They are still surfaced if the run fails outright.
        logging.getLogger("azure.identity").setLevel(logging.ERROR)
        logging.getLogger("azure.core").setLevel(logging.WARNING)
        logging.getLogger("azure.core.pipeline").setLevel(logging.WARNING)
        logging.getLogger("azure.core.pipeline.policies.http_logging_policy").setLevel(
            logging.WARNING
        )
        logging.getLogger("azure.ai.evaluation").setLevel(logging.CRITICAL)
        logging.getLogger("azure.ai.evaluation._legacy").setLevel(logging.CRITICAL)
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("openai").setLevel(logging.WARNING)

        # Belt and braces. Levels can be reset by any later import and
        # ``basicConfig(force=True)`` replaces the root handler outright,
        # so neither a level nor a root-handler filter is enough on its
        # own. ``_quiet`` also gives each chatty logger a private
        # WARNING-level handler and turns propagation off, which means a
        # sub-WARNING record has nowhere to go even if the level and the
        # filter are both undone behind our back.
        for name in _NOISY_LOGGERS:
            _quiet(name)
        for handler in logging.getLogger().handlers:
            _install_filter(handler)
    else:
        # ``--verbose`` must show everything, including anything muted by a
        # previous non-verbose call.
        for name in _NOISY_LOGGERS:
            _unquiet(name)


def _quiet(name: str) -> None:
    """Make ``name`` structurally incapable of printing below WARNING."""
    logger = logging.getLogger(name)
    logger.setLevel(logging.WARNING)
    _install_filter(logger)
    if not any(getattr(h, "_agentops_quiet", False) for h in logger.handlers):
        handler = logging.StreamHandler()
        handler.setLevel(logging.WARNING)
        handler.setFormatter(logging.Formatter(_LOG_FORMAT))
        handler.addFilter(_NoisyThirdPartyFilter())
        handler._agentops_quiet = True  # type: ignore[attr-defined]
        logger.addHandler(handler)
    logger.propagate = False


def _unquiet(name: str) -> None:
    """Undo ``_quiet`` so verbose runs show every record again."""
    logger = logging.getLogger(name)
    logger.handlers[:] = [
        h for h in logger.handlers if not getattr(h, "_agentops_quiet", False)
    ]
    logger.filters[:] = [
        f for f in logger.filters if not isinstance(f, _NoisyThirdPartyFilter)
    ]
    logger.setLevel(logging.NOTSET)
    logger.propagate = True


def _install_filter(target: logging.Logger | logging.Handler) -> None:
    if not any(
        isinstance(existing, _NoisyThirdPartyFilter) for existing in target.filters
    ):
        target.addFilter(_NoisyThirdPartyFilter())


def get_logger(name: str) -> logging.Logger:
    """Return a module-level logger.

    Usage:
        log = get_logger(__name__)
        log.debug("...")
    """
    return logging.getLogger(name)
