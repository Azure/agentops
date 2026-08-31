"""Pure validation for the packaged Observe list-price reference."""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    ValidationError,
    field_validator,
    model_validator,
)

TokenClass = Literal["input", "output", "cache_read", "cache_write", "reasoning"]
PRICE_REFERENCE_STALE_DAYS = 90
MAX_PRICE_REFERENCE_BYTES = 128 * 1024


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PriceEntry(_StrictModel):
    model: str = Field(min_length=1)
    token_class: TokenClass
    unit_price: Decimal = Field(gt=0)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    per_tokens: StrictInt = Field(default=1, gt=0)

    @field_validator("unit_price", mode="before")
    @classmethod
    def _decimal_only(cls, value: Any) -> Any:
        if not isinstance(value, str):
            raise ValueError("unit_price must be a decimal string, never a JSON number")
        return value


class PriceReference(_StrictModel):
    version: str = Field(min_length=1)
    effective_date: date
    source: str = Field(min_length=1)
    entries: list[PriceEntry] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique_prices(self) -> "PriceReference":
        keys = [(entry.model, entry.token_class) for entry in self.entries]
        if len(keys) != len(set(keys)):
            raise ValueError("duplicate model and token_class price entry")
        return self

    def age_days(self, as_of: date) -> int:
        return max((as_of - self.effective_date).days, 0)

    def is_stale(self, as_of: date) -> bool:
        return self.age_days(as_of) > PRICE_REFERENCE_STALE_DAYS

    def prices_by_model(self) -> dict[str, dict[TokenClass, PriceEntry]]:
        prices: dict[str, dict[TokenClass, PriceEntry]] = {}
        for entry in self.entries:
            prices.setdefault(entry.model, {})[entry.token_class] = entry
        return prices


class PriceReferenceLoadResult(_StrictModel):
    state: Literal["valid", "absent", "invalid"]
    reference: PriceReference | None = None
    error_code: str | None = None
    message: str | None = None

    @model_validator(mode="after")
    def _state_shape(self) -> "PriceReferenceLoadResult":
        if self.state == "valid" and self.reference is None:
            raise ValueError("valid price reference requires reference")
        if self.state != "valid" and self.reference is not None:
            raise ValueError("unavailable price reference must omit reference")
        if self.state == "invalid" and not self.message:
            raise ValueError("invalid price reference requires a safe message")
        return self


def load_price_reference(raw: str | None) -> PriceReferenceLoadResult:
    """Validate an in-memory JSON reference without performing any I/O."""
    if raw is None:
        return PriceReferenceLoadResult(
            state="absent",
            error_code="price_reference_absent",
            message="The packaged list-price reference is unavailable.",
        )
    if not isinstance(raw, str):
        return PriceReferenceLoadResult(
            state="invalid",
            error_code="price_reference_invalid_type",
            message="The packaged list-price reference is not UTF-8 JSON text.",
        )
    try:
        encoded = raw.encode("utf-8")
    except UnicodeEncodeError:
        return PriceReferenceLoadResult(
            state="invalid",
            error_code="price_reference_invalid_encoding",
            message="The packaged list-price reference is not valid UTF-8.",
        )
    if len(encoded) > MAX_PRICE_REFERENCE_BYTES:
        return PriceReferenceLoadResult(
            state="invalid",
            error_code="price_reference_too_large",
            message="The packaged list-price reference exceeds 128 KiB.",
        )
    try:
        payload = json.loads(raw, parse_float=Decimal)
        reference = PriceReference.model_validate(payload)
    except (json.JSONDecodeError, RecursionError, ValidationError):
        return PriceReferenceLoadResult(
            state="invalid",
            error_code="price_reference_invalid",
            message="The packaged list-price reference is invalid.",
        )
    return PriceReferenceLoadResult(state="valid", reference=reference)


parse_price_reference = load_price_reference
