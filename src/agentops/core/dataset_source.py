"""Pure classification and validation for evaluation dataset references."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from urllib.parse import unquote, urlsplit

DatasetSourceKind = Literal["local", "blob", "adls"]

_ACCOUNT_HOST = re.compile(
    r"(?P<account>[a-z0-9]{3,24})\."
    r"(?P<endpoint>blob|dfs)\.core\.windows\.net"
)
_CONTAINER = re.compile(r"[a-z0-9](?:[a-z0-9-]{1,61})[a-z0-9]")
_URI_PREFIX = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*:")
_WINDOWS_DRIVE_PATH = re.compile(r"^[a-zA-Z]:[\\/]")
_WILDCARD_CHARACTERS = frozenset("*?[]{}")


@dataclass(frozen=True, slots=True)
class DatasetReference:
    """A validated local path or canonical Azure Storage object URL."""

    kind: DatasetSourceKind
    original: str
    source_uri: str | None = None
    account: str | None = None
    container_or_filesystem: str | None = None
    object_path: str | None = None

    @property
    def local_path(self) -> Path | None:
        """Return the unchanged local path representation, if this is local."""

        if self.kind != "local":
            return None
        return Path(self.original)

    @property
    def is_remote(self) -> bool:
        """Whether this reference requires Azure Storage resolution."""

        return self.kind != "local"


def _invalid(reason: str) -> ValueError:
    return ValueError(
        "Invalid Azure Storage dataset reference: "
        f"{reason}. Use "
        "https://<account>.blob.core.windows.net/<container>/<object>.jsonl "
        "or https://<account>.dfs.core.windows.net/<filesystem>/<file>.jsonl."
    )


def _looks_like_uri(value: str) -> bool:
    if _WINDOWS_DRIVE_PATH.match(value):
        return False
    return bool(_URI_PREFIX.match(value))


def _validate_root(root: str, *, kind: DatasetSourceKind) -> None:
    if kind == "blob" and root == "$root":
        return
    if not _CONTAINER.fullmatch(root) or "--" in root:
        label = "container" if kind == "blob" else "filesystem"
        raise _invalid(f"the {label} name is invalid")


def _validate_object_path(raw_path: str) -> str:
    try:
        object_path = unquote(raw_path, errors="strict")
    except UnicodeError as exc:
        raise _invalid("the object path contains invalid escaping") from exc

    if (
        not object_path
        or raw_path.endswith("/")
        or "\\" in object_path
        or any(character in object_path for character in _WILDCARD_CHARACTERS)
        or any(ord(character) < 32 for character in object_path)
    ):
        raise _invalid("the URL must identify one object without wildcards")

    parts = object_path.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise _invalid("the object path contains an empty or relative segment")
    if not object_path.lower().endswith(".jsonl"):
        raise _invalid("the object must be a JSONL file")
    return object_path


def parse_dataset_reference(value: str | Path) -> DatasetReference:
    """Classify one dataset scalar without performing filesystem or network I/O."""

    original = str(value)
    if not _looks_like_uri(original):
        return DatasetReference(kind="local", original=original)

    try:
        parsed = urlsplit(original)
        username = parsed.username
        password = parsed.password
        port = parsed.port
    except ValueError as exc:
        raise _invalid("the URL authority is malformed") from exc

    if parsed.scheme != "https":
        raise _invalid("only HTTPS Blob and DFS URLs are supported")
    if username is not None or password is not None:
        raise _invalid("embedded user information is not supported")
    if parsed.query or "?" in original:
        raise _invalid("query strings and SAS tokens are not supported")
    if parsed.fragment or "#" in original:
        raise _invalid("fragments are not supported")
    if port is not None:
        raise _invalid("custom ports are not supported")

    host_match = _ACCOUNT_HOST.fullmatch(parsed.netloc)
    if host_match is None:
        raise _invalid("the host is not a canonical public Azure Storage endpoint")

    endpoint = host_match.group("endpoint")
    kind: DatasetSourceKind = "blob" if endpoint == "blob" else "adls"
    if not parsed.path.startswith("/") or parsed.path.startswith("//"):
        raise _invalid("the URL path is malformed")

    root_and_object = parsed.path[1:].split("/", 1)
    if len(root_and_object) != 2 or not all(root_and_object):
        raise _invalid("both a container or filesystem and an object path are required")
    root, raw_object_path = root_and_object
    if unquote(root) != root:
        raise _invalid("the container or filesystem name must be canonical")
    _validate_root(root, kind=kind)
    object_path = _validate_object_path(raw_object_path)

    return DatasetReference(
        kind=kind,
        original=original,
        source_uri=original,
        account=host_match.group("account"),
        container_or_filesystem=root,
        object_path=object_path,
    )


def classify_dataset_reference(value: str | Path) -> DatasetReference:
    """Compatibility-friendly alias for :func:`parse_dataset_reference`."""

    return parse_dataset_reference(value)


def is_remote_dataset_reference(value: str | Path) -> bool:
    """Validate and report whether a dataset scalar is an Azure object URL."""

    return parse_dataset_reference(value).is_remote


__all__ = [
    "DatasetReference",
    "DatasetSourceKind",
    "classify_dataset_reference",
    "is_remote_dataset_reference",
    "parse_dataset_reference",
]
