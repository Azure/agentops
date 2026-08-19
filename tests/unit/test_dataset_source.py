"""Tests for pure evaluation dataset-reference parsing."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from agentops.core.dataset_source import DatasetReference, parse_dataset_reference
from agentops.services import dataset_source
from agentops.services.dataset_source import DatasetSourceError, resolve_dataset_source


@pytest.mark.parametrize(
    "value",
    [
        ".agentops/data/smoke.jsonl",
        Path("datasets/regression.jsonl"),
        r"C:\datasets\smoke.jsonl",
    ],
)
def test_local_dataset_references_preserve_path_behavior(value: str | Path) -> None:
    reference = parse_dataset_reference(value)

    assert reference.kind == "local"
    assert reference.original == str(value)
    assert reference.source_uri is None
    assert reference.local_path == Path(value)


@pytest.mark.parametrize(
    ("uri", "kind", "account", "root", "object_path"),
    [
        (
            "https://examplestorage.blob.core.windows.net/evals/smoke.jsonl",
            "blob",
            "examplestorage",
            "evals",
            "smoke.jsonl",
        ),
        (
            "https://examplestorage.dfs.core.windows.net/evals/regression/smoke.jsonl",
            "adls",
            "examplestorage",
            "evals",
            "regression/smoke.jsonl",
        ),
    ],
)
def test_canonical_azure_storage_urls_are_parsed_without_rewriting(
    uri: str,
    kind: str,
    account: str,
    root: str,
    object_path: str,
) -> None:
    reference = parse_dataset_reference(uri)

    assert isinstance(reference, DatasetReference)
    assert reference.kind == kind
    assert reference.original == uri
    assert reference.source_uri == uri
    assert reference.account == account
    assert reference.container_or_filesystem == root
    assert reference.object_path == object_path
    assert reference.local_path is None


@pytest.mark.parametrize(
    "value",
    [
        "http://examplestorage.blob.core.windows.net/evals/smoke.jsonl",
        "abfs://evals@examplestorage.dfs.core.windows.net/smoke.jsonl",
        "abfss://evals@examplestorage.dfs.core.windows.net/smoke.jsonl",
        "ftp://examplestorage.blob.core.windows.net/evals/smoke.jsonl",
        "https://example.com/evals/smoke.jsonl",
        "https://blob.core.windows.net/evals/smoke.jsonl",
        "https://ab.blob.core.windows.net/evals/smoke.jsonl",
        "https://invalid-account.blob.core.windows.net/evals/smoke.jsonl",
        "https://EXAMPLESTORAGE.blob.core.windows.net/evals/smoke.jsonl",
        "https://examplestorage.blob.core.windows.net:443/evals/smoke.jsonl",
        "https://examplestorage.blob.core.windows.net",
        "https://examplestorage.blob.core.windows.net/",
        "https://examplestorage.blob.core.windows.net/evals",
        "https://examplestorage.blob.core.windows.net/evals/",
        "https://examplestorage.blob.core.windows.net/Invalid/smoke.jsonl",
        "https://examplestorage.blob.core.windows.net/bad--root/smoke.jsonl",
        "https://examplestorage.blob.core.windows.net/evals/*.jsonl",
        "https://examplestorage.blob.core.windows.net/evals/smoke.csv",
        "https://examplestorage.blob.core.windows.net/evals/../smoke.jsonl",
        "https://examplestorage.dfs.core.windows.net/evals/regression/",
        "https://user@example.blob.core.windows.net/evals/smoke.jsonl",
        "https://examplestorage.blob.core.windows.net/evals/smoke.jsonl?sv=secret",
        "https://examplestorage.blob.core.windows.net/evals/smoke.jsonl?",
        "https://examplestorage.blob.core.windows.net/evals/smoke.jsonl#row",
        "https://examplestorage.blob.core.windows.net/evals/smoke.jsonl#",
    ],
)
def test_remote_dataset_rejects_unsupported_or_non_object_urls(value: str) -> None:
    with pytest.raises(ValueError, match="Azure Storage dataset"):
        parse_dataset_reference(value)


class _Downloader:
    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks

    def chunks(self):
        yield from self._chunks


class _StorageClient:
    def __init__(
        self,
        chunks: list[bytes],
        *,
        size: int | None = None,
        etags: tuple[str, str] = ('"etag-1"', '"etag-1"'),
        failure: Exception | None = None,
    ) -> None:
        self.chunks = chunks
        self.size = sum(len(chunk) for chunk in chunks) if size is None else size
        self.etags = etags
        self.failure = failure
        self.properties_calls = 0
        self.download_kwargs: dict[str, Any] = {}

    def get_properties(self):
        self.properties_calls += 1
        etag = self.etags[min(self.properties_calls - 1, 1)]
        return SimpleNamespace(
            size=self.size,
            etag=etag,
            last_modified="2026-08-19T12:00:00Z",
        )

    def download(self, **kwargs):
        self.download_kwargs = kwargs
        if self.failure is not None:
            raise self.failure
        return _Downloader(self.chunks)


@pytest.mark.parametrize(
    "uri",
    [
        "https://examplestorage.blob.core.windows.net/evals/smoke.jsonl",
        "https://examplestorage.dfs.core.windows.net/evals/regression/smoke.jsonl",
    ],
)
def test_remote_resolver_streams_one_bounded_snapshot_and_cleans_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    uri: str,
) -> None:
    client = _StorageClient(
        [
            b'{"input":"hello","expected":"hi"}\n',
            b'{"input":"bye","expected":"goodbye"}\n',
        ]
    )
    created: list[DatasetReference] = []

    def create(reference: DatasetReference):
        created.append(reference)
        return client

    monkeypatch.setattr(dataset_source, "_create_storage_client", create)

    with resolve_dataset_source(uri, snapshot_dir=tmp_path) as snapshot:
        materialized = snapshot.local_path
        assert materialized.read_bytes() == b"".join(client.chunks)
        assert snapshot.temporary is True
        assert snapshot.source.source_uri == uri
        assert snapshot.etag == '"etag-1"'
        assert snapshot.size_bytes == sum(map(len, client.chunks))
        assert snapshot.display_name == "smoke.jsonl"
        assert client.properties_calls == 2
        assert client.download_kwargs["etag"] == '"etag-1"'
        assert created == [parse_dataset_reference(uri)]

    assert not materialized.exists()


@pytest.mark.parametrize(
    ("uri", "module_name", "client_name"),
    [
        (
            "https://examplestorage.blob.core.windows.net/evals/smoke.jsonl",
            "azure.storage.blob",
            "BlobClient",
        ),
        (
            "https://examplestorage.dfs.core.windows.net/evals/smoke.jsonl",
            "azure.storage.filedatalake",
            "DataLakeFileClient",
        ),
    ],
)
def test_storage_clients_use_shared_identity_and_bounded_sdk_retries(
    monkeypatch: pytest.MonkeyPatch,
    uri: str,
    module_name: str,
    client_name: str,
) -> None:
    import importlib

    credential = object()
    captured: dict[str, Any] = {}
    module = importlib.import_module(module_name)

    if client_name == "BlobClient":
        def create(blob_url, **kwargs):
            captured.update(url=blob_url, **kwargs)
            return object()

        monkeypatch.setattr(module.BlobClient, "from_blob_url", create)
    else:
        def create(**kwargs):
            captured.update(**kwargs)
            return object()

        monkeypatch.setattr(module, "DataLakeFileClient", create)
    monkeypatch.setattr(dataset_source, "get_shared_credential", lambda: credential)

    dataset_source._create_storage_client(parse_dataset_reference(uri))

    assert captured["credential"] is credential
    assert captured["retry_total"] == 3
    assert captured["retry_connect"] == 3
    assert captured["retry_read"] == 3
    assert captured["retry_status"] == 3


def test_local_resolver_is_passthrough_and_never_deletes_source(tmp_path: Path) -> None:
    source = tmp_path / "local.jsonl"
    source.write_text('{"input":"hello"}\n', encoding="utf-8")

    with resolve_dataset_source(source, config_dir=tmp_path) as snapshot:
        assert snapshot.local_path == source.resolve()
        assert snapshot.provenance == str(source.resolve())
        assert snapshot.temporary is False

    assert source.exists()


def test_local_resolver_preserves_missing_file_exception(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="dataset file was not found"):
        resolve_dataset_source(tmp_path / "missing.jsonl", config_dir=tmp_path)


def test_remote_resolver_rejects_size_from_metadata_before_download(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _StorageClient([], size=dataset_source.MAX_DATASET_BYTES + 1)
    monkeypatch.setattr(dataset_source, "_create_storage_client", lambda _ref: client)

    with pytest.raises(DatasetSourceError) as excinfo:
        with resolve_dataset_source(
            "https://examplestorage.blob.core.windows.net/evals/large.jsonl",
            snapshot_dir=tmp_path,
        ):
            pass

    assert excinfo.value.category == "oversized"
    assert not list(tmp_path.glob("agentops-dataset-*.jsonl"))


def test_remote_resolver_rejects_size_crossing_limit_during_chunking(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _StorageClient(
        [b"x" * dataset_source.MAX_DATASET_BYTES, b"x"],
        size=dataset_source.MAX_DATASET_BYTES,
    )
    monkeypatch.setattr(dataset_source, "_create_storage_client", lambda _ref: client)

    with pytest.raises(DatasetSourceError) as excinfo:
        with resolve_dataset_source(
            "https://examplestorage.blob.core.windows.net/evals/large.jsonl",
            snapshot_dir=tmp_path,
            validate_content=False,
        ):
            pass

    assert excinfo.value.category == "oversized"
    assert not list(tmp_path.glob("agentops-dataset-*.jsonl"))


def test_remote_resolver_rejects_changed_etag_and_removes_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _StorageClient(
        [b'{"input":"hello"}\n'],
        etags=('"etag-1"', '"etag-2"'),
    )
    monkeypatch.setattr(dataset_source, "_create_storage_client", lambda _ref: client)

    with pytest.raises(DatasetSourceError) as excinfo:
        with resolve_dataset_source(
            "https://examplestorage.dfs.core.windows.net/evals/changed.jsonl",
            snapshot_dir=tmp_path,
        ):
            pass

    assert excinfo.value.category == "source_changed"
    assert not list(tmp_path.glob("agentops-dataset-*.jsonl"))


@pytest.mark.parametrize(
    "value",
    [
        "https://user:secret@examplestorage.blob.core.windows.net/evals/a.jsonl",
        "https://examplestorage.blob.core.windows.net/evals/a.jsonl?sig=secret",
        "https://examplestorage.blob.core.windows.net/evals/a.jsonl#fragment",
        "https://example.test/evals/a.jsonl",
    ],
)
def test_identity_only_validation_rejects_credential_bearing_urls_before_client(
    monkeypatch: pytest.MonkeyPatch,
    value: str,
) -> None:
    def create(_ref):
        pytest.fail("storage client must not be created")

    monkeypatch.setattr(dataset_source, "_create_storage_client", create)

    with pytest.raises(DatasetSourceError) as excinfo:
        with resolve_dataset_source(value):
            pass

    assert excinfo.value.category == "malformed"
    assert "secret" not in str(excinfo.value)


class _StorageFailure(Exception):
    def __init__(self, status_code: int | None = None, error_code: str | None = None):
        super().__init__("sensitive authorization and credential-chain details")
        self.status_code = status_code
        self.error_code = error_code


@pytest.mark.parametrize(
    ("failure", "category", "guidance"),
    [
        (_StorageFailure(401), "authentication_failed", "az login"),
        (_StorageFailure(403), "authorization_failed", "Storage Blob Data Reader"),
        (_StorageFailure(404), "not_found", "verify the object path"),
        (_StorageFailure(412), "source_changed", "writes complete"),
        (_StorageFailure(503), "service_unavailable", "service health"),
        (_StorageFailure(error_code="ServiceRequestError"), "connectivity_failed", "network"),
    ],
)
def test_remote_resolver_maps_failures_to_safe_actionable_categories(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: Exception,
    category: str,
    guidance: str,
) -> None:
    monkeypatch.setattr(
        dataset_source,
        "_create_storage_client",
        lambda _ref: _StorageClient([], failure=failure),
    )

    with pytest.raises(DatasetSourceError) as excinfo:
        with resolve_dataset_source(
            "https://examplestorage.dfs.core.windows.net/evals/failure.jsonl",
            snapshot_dir=tmp_path,
        ):
            pass

    assert excinfo.value.category == category
    assert guidance.lower() in str(excinfo.value).lower()
    assert "sensitive authorization" not in str(excinfo.value)


def test_remote_resolver_classifies_invalid_jsonl_content(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _StorageClient([b'{"input":"valid"}\nnot-json\n'])
    monkeypatch.setattr(dataset_source, "_create_storage_client", lambda _ref: client)

    with pytest.raises(DatasetSourceError) as excinfo:
        with resolve_dataset_source(
            "https://examplestorage.blob.core.windows.net/evals/invalid.jsonl",
            snapshot_dir=tmp_path,
        ):
            pass

    assert excinfo.value.category == "invalid_content"
    assert not list(tmp_path.glob("agentops-dataset-*.jsonl"))
