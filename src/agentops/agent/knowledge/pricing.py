"""Cached loader for the packaged Observe list-price reference."""

from __future__ import annotations

import logging
from functools import lru_cache
from importlib import resources

from agentops.core.observe_pricing import PriceReferenceLoadResult, load_price_reference

log = logging.getLogger(__name__)
_PRICING_PACKAGE = "agentops.agent.observe.pricing"
_REFERENCE_NAME = "list-prices.json"


@lru_cache(maxsize=1)
def load_packaged_price_reference() -> PriceReferenceLoadResult:
    """Read and validate the packaged reference once, degrading without raising."""
    try:
        raw = (
            resources.files(_PRICING_PACKAGE)
            .joinpath(_REFERENCE_NAME)
            .read_text(encoding="utf-8")
        )
    except FileNotFoundError:
        log.warning("Packaged Observe list-price reference not found")
        return load_price_reference(None)
    except OSError as exc:
        log.warning("Packaged Observe list-price reference could not be read: %s", exc)
        return PriceReferenceLoadResult(
            state="invalid",
            error_code="price_reference_unreadable",
            message="The packaged list-price reference could not be read.",
        )
    result = load_price_reference(raw)
    if result.state != "valid":
        log.warning("Packaged Observe list-price reference is invalid")
    return result
