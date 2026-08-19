"""Resolve local or Azure Storage evaluation datasets into stable snapshots."""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, FrozenSet, Literal

from agentops.core.dataset_source import DatasetReference, parse_dataset_reference
from agentops.utils.azure_credentials import get_shared_credential, is_credential_error

MAX_DATASET_BYTES = 100 * 1024 * 1024
_SDK_RETRY_ATTEMPTS = 3
_READ_CHUNK_BYTES = 4 * 1024 * 1024

DatasetSourceStatus = Literal[
    "ready",
    "malformed",
    "not_found",
    "authentication_failed",
    "authorization_failed",
    "connectivity_failed",
    "service_unavailable",
    "source_changed",
    "oversized",
    "invalid_content",
    "unknown",
]


class DatasetSourceError(RuntimeError):
    """A source-safe dataset resolution failure with a stable category."""

    def __init__(
        self,
        category: DatasetSourceStatus,
        source: str,
        message: str,
    ) -> None:
        super().__init__(message)
        self.category = category
        self.source = source


@dataclass
class DatasetSnapshot:
    """A local reader path plus source provenance and cleanup responsibility."""

    local_path: Path
    source: DatasetReference
    display_name: str
    size_bytes: int
    etag: str | None = None
    last_modified: Any | None = None
    temporary: bool = False
    columns: FrozenSet[str] = frozenset()
    _cleaned: bool = field(default=False, init=False, repr=False)

    @property
    def provenance(self) -> str:
        if self.source.source_uri:
            return self.source.source_uri
        return str(self.local_path)

    def cleanup(self) -> None:
        if self._cleaned or not self.temporary:
            return
        self._cleaned = True
        try:
            self.local_path.unlink()
        except FileNotFoundError:
            pass

    def __enter__(self) -> "DatasetSnapshot":
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self.cleanup()


@dataclass(frozen=True)
class DatasetSourceDiagnosis:
    """Bounded readiness result shared by eval analysis and runtime diagnostics."""

    status: DatasetSourceStatus
    source: str
    columns: FrozenSet[str]
    message: str
    access_checked: bool


def resolve_dataset_source(
    value: str | Path,
    *,
    config_dir: Path | None = None,
    snapshot_dir: Path | None = None,
    validate_content: bool = True,
) -> DatasetSnapshot:
    """Resolve one scalar to a local file, downloading remote sources once."""

    try:
        reference = parse_dataset_reference(value)
    except ValueError as exc:
        raise DatasetSourceError(
            "malformed",
            "<invalid Azure Storage dataset>",
            (
                "malformed: invalid Azure Storage dataset reference. Use "
                "https://<account>.blob.core.windows.net/<container>/<object>.jsonl "
                "or https://<account>.dfs.core.windows.net/<filesystem>/<file>.jsonl."
            ),
        ) from exc

    if not reference.is_remote:
        return _resolve_local(
            reference,
            config_dir=config_dir,
            validate_content=validate_content,
        )

    source = reference.source_uri or "<Azure Storage dataset>"
    destination: Path | None = None
    try:
        client = _create_storage_client(reference)
        before = _client_get_properties(client, reference)
        declared_size = _property_size(before)
        if declared_size is not None and declared_size > MAX_DATASET_BYTES:
            raise _source_error("oversized", source, reference)

        etag = _property_value(before, "etag")
        last_modified = _property_value(before, "last_modified")
        destination = _new_snapshot_path(snapshot_dir or _default_snapshot_dir(config_dir))
        bytes_written = _download_to_path(
            client,
            reference,
            destination,
            etag=str(etag) if etag is not None else None,
        )

        after = _client_get_properties(client, reference)
        after_etag = _property_value(after, "etag")
        after_size = _property_size(after)
        if (
            (etag is not None and after_etag is not None and str(etag) != str(after_etag))
            or (after_size is not None and after_size != bytes_written)
            or (declared_size is not None and declared_size != bytes_written)
        ):
            raise _source_error("source_changed", source, reference)

        columns = (
            _validate_jsonl(destination, source=source) if validate_content else frozenset()
        )
        return DatasetSnapshot(
            local_path=destination,
            source=reference,
            display_name=Path(reference.object_path or "dataset.jsonl").name,
            size_bytes=bytes_written,
            etag=str(etag) if etag is not None else None,
            last_modified=last_modified,
            temporary=True,
            columns=columns,
        )
    except DatasetSourceError:
        if destination is not None:
            _remove_partial(destination)
        raise
    except Exception as exc:
        if destination is not None:
            _remove_partial(destination)
        raise _map_storage_error(exc, source=source, reference=reference) from exc


def diagnose_dataset_source(
    value: str | Path,
    *,
    config_dir: Path | None = None,
    snapshot_dir: Path | None = None,
) -> DatasetSourceDiagnosis:
    """Inspect one source using the same validation and resolver as eval runs."""

    try:
        reference = parse_dataset_reference(value)
        access_checked = reference.is_remote
    except ValueError:
        reference = None
        access_checked = False

    try:
        with resolve_dataset_source(
            value,
            config_dir=config_dir,
            snapshot_dir=snapshot_dir,
            validate_content=True,
        ) as snapshot:
            return DatasetSourceDiagnosis(
                status="ready",
                source=snapshot.provenance,
                columns=snapshot.columns,
                message=f"Dataset is ready: {snapshot.provenance}.",
                access_checked=access_checked,
            )
    except DatasetSourceError as exc:
        return DatasetSourceDiagnosis(
            status=exc.category,
            source=exc.source,
            columns=frozenset(),
            message=str(exc),
            access_checked=access_checked,
        )
    except FileNotFoundError as exc:
        source = str(value)
        return DatasetSourceDiagnosis(
            status="not_found",
            source=source,
            columns=frozenset(),
            message=str(exc),
            access_checked=access_checked,
        )
    except Exception:
        source = (
            reference.source_uri
            if reference is not None and reference.source_uri
            else str(value)
        )
        return DatasetSourceDiagnosis(
            status="unknown",
            source=source,
            columns=frozenset(),
            message=f"unknown: dataset readiness could not be determined for {source}.",
            access_checked=access_checked,
        )


def _resolve_local(
    reference: DatasetReference,
    *,
    config_dir: Path | None,
    validate_content: bool,
) -> DatasetSnapshot:
    candidate = reference.local_path or Path(reference.original)
    if not candidate.is_absolute():
        candidate = (config_dir or Path.cwd()) / candidate
    resolved = candidate.resolve()
    if not resolved.is_file():
        raise FileNotFoundError(
            f"not_found: dataset file was not found: {resolved}. Verify the path."
        )
    try:
        size = resolved.stat().st_size
        columns = (
            _validate_jsonl(resolved, source=str(resolved))
            if validate_content
            else frozenset()
        )
    except DatasetSourceError:
        raise
    except OSError as exc:
        raise DatasetSourceError(
            "unknown",
            str(resolved),
            f"unknown: dataset could not be read: {resolved}.",
        ) from exc
    return DatasetSnapshot(
        local_path=resolved,
        source=reference,
        display_name=resolved.name,
        size_bytes=size,
        temporary=False,
        columns=columns,
    )


def _create_storage_client(reference: DatasetReference) -> Any:
    credential = get_shared_credential()
    if reference.kind == "blob":
        from azure.storage.blob import BlobClient

        assert reference.source_uri is not None
        return BlobClient.from_blob_url(
            reference.source_uri,
            credential=credential,
            retry_total=_SDK_RETRY_ATTEMPTS,
            retry_connect=_SDK_RETRY_ATTEMPTS,
            retry_read=_SDK_RETRY_ATTEMPTS,
            retry_status=_SDK_RETRY_ATTEMPTS,
        )

    from azure.storage.filedatalake import DataLakeFileClient

    assert reference.container_or_filesystem is not None
    assert reference.object_path is not None
    return DataLakeFileClient(
        account_url=f"https://{reference.account}.dfs.core.windows.net",
        file_system_name=reference.container_or_filesystem,
        file_path=reference.object_path,
        credential=credential,
        retry_total=_SDK_RETRY_ATTEMPTS,
        retry_connect=_SDK_RETRY_ATTEMPTS,
        retry_read=_SDK_RETRY_ATTEMPTS,
        retry_status=_SDK_RETRY_ATTEMPTS,
    )


def _client_get_properties(client: Any, reference: DatasetReference) -> Any:
    generic = getattr(client, "get_properties", None)
    if callable(generic):
        return generic()
    if reference.kind == "blob":
        return client.get_blob_properties()
    return client.get_file_properties()


def _client_download(client: Any, reference: DatasetReference, **kwargs: Any) -> Any:
    generic = getattr(client, "download", None)
    if callable(generic):
        return generic(**kwargs)
    if reference.kind == "blob":
        return client.download_blob(**kwargs)
    return client.download_file(**kwargs)


def _download_to_path(
    client: Any,
    reference: DatasetReference,
    destination: Path,
    *,
    etag: str | None,
) -> int:
    kwargs: dict[str, Any] = {"max_concurrency": 1}
    if etag:
        from azure.core import MatchConditions

        kwargs.update(etag=etag, match_condition=MatchConditions.IfNotModified)
    downloader = _client_download(client, reference, **kwargs)
    total = 0
    with destination.open("wb", buffering=0) as handle:
        for chunk in downloader.chunks():
            payload = bytes(chunk)
            total += len(payload)
            if total > MAX_DATASET_BYTES:
                raise _source_error(
                    "oversized",
                    reference.source_uri or "<Azure Storage dataset>",
                    reference,
                )
            handle.write(payload)
    return total


def _new_snapshot_path(directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    for _ in range(10):
        path = directory / f"agentops-dataset-{uuid.uuid4().hex}.jsonl"
        try:
            descriptor = os.open(
                path,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                0o600,
            )
        except FileExistsError:
            continue
        os.close(descriptor)
        return path
    raise RuntimeError("could not allocate a private dataset snapshot")


def _default_snapshot_dir(config_dir: Path | None) -> Path:
    return (config_dir or Path.cwd()) / ".agentops" / ".resolved"


def _remove_partial(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def _property_value(properties: Any, name: str) -> Any:
    if isinstance(properties, dict):
        return properties.get(name)
    return getattr(properties, name, None)


def _property_size(properties: Any) -> int | None:
    value = _property_value(properties, "size")
    if value is None:
        value = _property_value(properties, "content_length")
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _validate_jsonl(path: Path, *, source: str) -> FrozenSet[str]:
    columns: set[str] = set()
    rows = 0
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                stripped = line.strip()
                if not stripped:
                    continue
                rows += 1
                try:
                    row = json.loads(stripped)
                except json.JSONDecodeError as exc:
                    raise DatasetSourceError(
                        "invalid_content",
                        source,
                        (
                            f"invalid_content: {source} has invalid JSONL on line "
                            f"{line_number}. Each non-blank line must be a JSON object."
                        ),
                    ) from exc
                if not isinstance(row, dict):
                    raise DatasetSourceError(
                        "invalid_content",
                        source,
                        (
                            f"invalid_content: {source} line {line_number} is not a "
                            "JSON object."
                        ),
                    )
                columns.update(str(key) for key in row)
    except UnicodeError as exc:
        raise DatasetSourceError(
            "invalid_content",
            source,
            f"invalid_content: {source} must be UTF-8 JSONL.",
        ) from exc
    except OSError as exc:
        raise DatasetSourceError(
            "unknown",
            source,
            f"unknown: dataset could not be read: {source}.",
        ) from exc
    if rows == 0:
        raise DatasetSourceError(
            "invalid_content",
            source,
            f"invalid_content: {source} contains no JSONL rows.",
        )
    return frozenset(columns)


def _source_error(
    category: DatasetSourceStatus,
    source: str,
    reference: DatasetReference,
) -> DatasetSourceError:
    guidance = {
        "oversized": (
            "The object exceeds the 100 MiB limit; reduce or partition the dataset."
        ),
        "source_changed": (
            "The object changed while its snapshot was downloading; retry after "
            "source writes complete."
        ),
    }.get(category, "Dataset resolution failed.")
    return DatasetSourceError(category, source, f"{category}: {source}. {guidance}")


def _map_storage_error(
    exc: Exception,
    *,
    source: str,
    reference: DatasetReference,
) -> DatasetSourceError:
    status = getattr(exc, "status_code", None)
    code = str(getattr(exc, "error_code", "") or "").lower()
    type_name = type(exc).__name__.lower()

    if is_credential_error(exc) or status == 401 or "authentication" in code:
        return DatasetSourceError(
            "authentication_failed",
            source,
            (
                f"authentication_failed: no usable Azure identity could read {source}. "
                "Run `az login` locally or verify the workload, managed, or service "
                "principal identity configuration."
            ),
        )
    if status == 403 or "authorization" in code or "permission" in code:
        acl = " and review ADLS path ACLs" if reference.kind == "adls" else ""
        return DatasetSourceError(
            "authorization_failed",
            source,
            (
                f"authorization_failed: the current Azure identity cannot read {source}. "
                f"Assign Storage Blob Data Reader at the narrowest practical scope{acl}."
            ),
        )
    if status == 404 or code in {
        "blobnotfound",
        "containernotfound",
        "filesystemnotfound",
        "pathnotfound",
    }:
        return DatasetSourceError(
            "not_found",
            source,
            f"not_found: {source} was not found; verify the object path.",
        )
    if status in {409, 412} or code in {"conditionnotmet", "targetconditionnotmet"}:
        return _source_error("source_changed", source, reference)
    if (
        "servicerequesterror" in type_name
        or "serviceresponseerror" in type_name
        or "service_request" in code
        or "servicerequest" in code
        or "network" in code
        or "connection" in code
    ):
        return DatasetSourceError(
            "connectivity_failed",
            source,
            (
                f"connectivity_failed: could not reach {source}. Check runner network, "
                "DNS, firewall, and private endpoint connectivity."
            ),
        )
    if status in {408, 429, 500, 502, 503, 504}:
        return DatasetSourceError(
            "service_unavailable",
            source,
            (
                f"service_unavailable: Azure Storage retries were exhausted for "
                f"{source}. Retry later and inspect Azure service health."
            ),
        )
    return DatasetSourceError(
        "unknown",
        source,
        f"unknown: Azure Storage could not resolve {source}.",
    )


__all__ = [
    "DatasetSnapshot",
    "DatasetSourceDiagnosis",
    "DatasetSourceError",
    "DatasetSourceStatus",
    "MAX_DATASET_BYTES",
    "diagnose_dataset_source",
    "resolve_dataset_source",
]
