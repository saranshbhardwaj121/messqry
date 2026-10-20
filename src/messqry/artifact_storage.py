"""Private artifact storage backends for registered model packages."""

from __future__ import annotations

import base64
import hashlib
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Protocol

from messqry.config import Settings

_SHA256_PATTERN = re.compile(r"^[a-f0-9]{64}$")


@dataclass(frozen=True)
class StoredArtifact:
    """Location returned after an artifact has been persisted."""

    uri: str
    version_id: str | None = None


class ArtifactStore(Protocol):
    """Minimal interface for private, immutable artifact objects."""

    def put(
        self,
        key: str,
        source: BinaryIO,
        *,
        sha256: str,
        size_bytes: int,
    ) -> StoredArtifact: ...

    def open(self, key: str, *, version_id: str | None = None) -> BinaryIO: ...

    def delete(self, key: str, *, version_id: str | None = None) -> None: ...


def _validate_key(key: str) -> str:
    """Accept only normalized relative POSIX object keys."""

    if (
        not key
        or key.startswith("/")
        or "\\" in key
        or "\x00" in key
        or any(part in {"", ".", ".."} for part in key.split("/"))
    ):
        raise ValueError("Artifact key must be a normalized relative path")
    return PurePosixPath(key).as_posix()


def _validate_integrity_metadata(sha256: str, size_bytes: int) -> None:
    if not _SHA256_PATTERN.fullmatch(sha256):
        raise ValueError("Artifact SHA-256 must be 64 lowercase hexadecimal characters")
    if size_bytes < 1:
        raise ValueError("Artifact size must be positive")


class LocalArtifactStore:
    """Filesystem backend intended for local development and tests only."""

    def __init__(self, root: Path | str):
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        normalized = _validate_key(key)
        destination = (self.root / Path(*PurePosixPath(normalized).parts)).resolve()
        if self.root not in destination.parents:
            raise ValueError("Artifact key resolves outside the local storage root")
        return destination

    def uri_for_key(self, key: str) -> str:
        return self._path(key).as_uri()

    def put(
        self,
        key: str,
        source: BinaryIO,
        *,
        sha256: str,
        size_bytes: int,
    ) -> StoredArtifact:
        _validate_integrity_metadata(sha256, size_bytes)
        destination = self._path(key)
        destination.parent.mkdir(parents=True, exist_ok=True)
        created = False
        completed = False
        try:
            with destination.open("xb") as target:
                created = True
                digest = hashlib.sha256()
                total_size = 0
                while chunk := source.read(1024 * 1024):
                    total_size += len(chunk)
                    if total_size > size_bytes:
                        raise ValueError("Artifact size does not match the declared size")
                    digest.update(chunk)
                    target.write(chunk)
            if total_size != size_bytes:
                raise ValueError("Artifact size does not match the declared size")
            if digest.hexdigest() != sha256:
                raise ValueError("Artifact SHA-256 does not match the declared checksum")
            completed = True
        finally:
            if created and not completed:
                destination.unlink()
        return StoredArtifact(uri=destination.as_uri())

    def open(self, key: str, *, version_id: str | None = None) -> BinaryIO:
        if version_id is not None:
            raise ValueError("Local artifact storage does not support object version IDs")
        return self._path(key).open("rb")

    def delete(self, key: str, *, version_id: str | None = None) -> None:
        if version_id is not None:
            raise ValueError("Local artifact storage does not support object version IDs")
        self._path(key).unlink(missing_ok=True)


class S3ArtifactStore:
    """S3 backend using the AWS SDK credential chain and private object access."""

    def __init__(self, bucket: str, *, prefix: str = "messqry", client=None):
        if not bucket or not bucket.strip():
            raise ValueError("An S3 bucket is required")
        self.bucket = bucket.strip()
        self.prefix = _validate_key(prefix.strip("/")) if prefix.strip("/") else ""
        if client is None:
            try:
                import boto3
            except ImportError as exc:
                raise RuntimeError('Install the "aws" extra to use S3 artifact storage') from exc
            client = boto3.client("s3")
        self.client = client

    def _key(self, key: str) -> str:
        normalized = _validate_key(key)
        return f"{self.prefix}/{normalized}" if self.prefix else normalized

    def uri_for_key(self, key: str) -> str:
        return f"s3://{self.bucket}/{self._key(key)}"

    def put(
        self,
        key: str,
        source: BinaryIO,
        *,
        sha256: str,
        size_bytes: int,
    ) -> StoredArtifact:
        _validate_integrity_metadata(sha256, size_bytes)
        full_key = self._key(key)
        response = self.client.put_object(
            Bucket=self.bucket,
            Key=full_key,
            Body=source,
            ContentLength=size_bytes,
            ContentType="application/zip",
            Metadata={"sha256": sha256},
            ChecksumSHA256=base64.b64encode(bytes.fromhex(sha256)).decode("ascii"),
            ServerSideEncryption="AES256",
            IfNoneMatch="*",
        )
        version_id = response.get("VersionId")
        if version_id in (None, "null"):
            version_id = None
        return StoredArtifact(uri=f"s3://{self.bucket}/{full_key}", version_id=version_id)

    def open(self, key: str, *, version_id: str | None = None) -> BinaryIO:
        request = {"Bucket": self.bucket, "Key": self._key(key)}
        if version_id is not None:
            request["VersionId"] = version_id
        return self.client.get_object(**request)["Body"]

    def delete(self, key: str, *, version_id: str | None = None) -> None:
        request = {"Bucket": self.bucket, "Key": self._key(key)}
        if version_id is not None:
            request["VersionId"] = version_id
        self.client.delete_object(**request)


def create_artifact_store(settings: Settings, *, s3_client=None) -> ArtifactStore:
    """Build the configured local or S3 backend."""

    if settings.artifact_storage == "local":
        return LocalArtifactStore(settings.artifact_local_root)
    if settings.artifact_s3_bucket is None:
        raise ValueError("MESSQRY_ARTIFACT_S3_BUCKET is required for S3 storage")
    return S3ArtifactStore(
        settings.artifact_s3_bucket,
        prefix=settings.artifact_s3_prefix,
        client=s3_client,
    )
