"""Compatibility re-exports for the shared Azure credential helpers."""

from agentops.utils.azure_credentials import (
    format_source_error,
    get_shared_credential,
    is_credential_error,
    log_source_error,
    reset_shared_credentials,
    summarise_credential_error,
)

__all__ = [
    "format_source_error",
    "get_shared_credential",
    "is_credential_error",
    "log_source_error",
    "reset_shared_credentials",
    "summarise_credential_error",
]
