from hashlib import sha256
from io import BytesIO

import pytest

from messqry.artifact_storage import (
    LocalArtifactStore,
    S3ArtifactStore,
    create_artifact_store,
)
from messqry.config import Settings


def digest(payload: bytes) -> str:
    return sha256(payload).hexdigest()


def test_local_artifact_store_round_trips_without_overwriting(tmp_path):
    payload = b"test model package"
    store = LocalArtifactStore(tmp_path / "artifacts")

    stored = store.put(
        "models/model-id/versions/1/package.zip",
        BytesIO(payload),
        sha256=digest(payload),
        size_bytes=len(payload),
    )

    with store.open("models/model-id/versions/1/package.zip") as artifact:
        assert artifact.read() == payload
    assert stored.uri.startswith("file:")

    with pytest.raises(FileExistsError):
        store.put(
            "models/model-id/versions/1/package.zip",
            BytesIO(payload),
            sha256=digest(payload),
            size_bytes=len(payload),
        )


@pytest.mark.parametrize("key", ["../escape.zip", "/absolute.zip", "a\\b.zip", "a//b.zip"])
def test_local_artifact_store_rejects_unsafe_keys(tmp_path, key):
    store = LocalArtifactStore(tmp_path / "artifacts")
    with pytest.raises(ValueError, match="normalized relative path"):
        store.open(key)


def test_local_artifact_store_rejects_mismatched_size_without_leaving_partial_file(tmp_path):
    store = LocalArtifactStore(tmp_path / "artifacts")
    with pytest.raises(ValueError, match="does not match"):
        store.put(
            "models/model-id/package.zip",
            BytesIO(b"payload"),
            sha256=digest(b"payload"),
            size_bytes=99,
        )
    assert not list((tmp_path / "artifacts").rglob("*.zip"))


def test_local_artifact_store_rejects_same_size_checksum_mismatch_and_cleans_up(tmp_path):
    store = LocalArtifactStore(tmp_path / "artifacts")
    with pytest.raises(ValueError, match="SHA-256 does not match"):
        store.put(
            "models/model-id/package.zip",
            BytesIO(b"changed"),
            sha256=digest(b"payload"),
            size_bytes=len(b"changed"),
        )
    assert not list((tmp_path / "artifacts").rglob("*.zip"))


class FakeS3Client:
    def __init__(self):
        self.put_request = None
        self.get_request = None
        self.delete_request = None
        self.response = {"VersionId": "s3-version-123"}
        self.body = BytesIO(b"model archive")

    def put_object(self, **kwargs):
        self.put_request = kwargs
        return self.response

    def get_object(self, **kwargs):
        self.get_request = kwargs
        return {"Body": self.body}

    def delete_object(self, **kwargs):
        self.delete_request = kwargs


def test_s3_artifact_store_uses_private_immutable_checksummed_uploads():
    payload = b"model archive"
    client = FakeS3Client()
    store = S3ArtifactStore("messqry-private", prefix="registry", client=client)

    stored = store.put(
        "models/model-id/versions/1/package.zip",
        BytesIO(payload),
        sha256=digest(payload),
        size_bytes=len(payload),
    )

    assert stored.uri == "s3://messqry-private/registry/models/model-id/versions/1/package.zip"
    assert stored.version_id == "s3-version-123"
    assert client.put_request["ServerSideEncryption"] == "AES256"
    assert client.put_request["IfNoneMatch"] == "*"
    assert client.put_request["Metadata"] == {"sha256": digest(payload)}
    assert client.put_request["ContentLength"] == len(payload)

    stream = store.open("models/model-id/versions/1/package.zip", version_id="s3-version-123")
    assert stream.read() == payload
    assert client.get_request["VersionId"] == "s3-version-123"

    store.delete("models/model-id/versions/1/package.zip", version_id="s3-version-123")
    assert client.delete_request["VersionId"] == "s3-version-123"
    assert client.delete_request["Bucket"] == "messqry-private"


def test_storage_factory_uses_local_root_and_s3_settings(tmp_path):
    local = create_artifact_store(Settings(artifact_local_root=tmp_path / "local"))
    assert isinstance(local, LocalArtifactStore)
    assert (tmp_path / "local").is_dir()

    client = FakeS3Client()
    s3 = create_artifact_store(
        Settings(artifact_storage="s3", artifact_s3_bucket="messqry-test"), s3_client=client
    )
    assert isinstance(s3, S3ArtifactStore)
    assert s3.bucket == "messqry-test"


def test_artifact_store_validates_integrity_metadata(tmp_path):
    store = LocalArtifactStore(tmp_path / "artifacts")
    with pytest.raises(ValueError, match="64 lowercase hexadecimal"):
        store.put("package.zip", BytesIO(b"x"), sha256="invalid", size_bytes=1)
    with pytest.raises(ValueError, match="must be positive"):
        store.put("package.zip", BytesIO(b"x"), sha256=digest(b"x"), size_bytes=0)
