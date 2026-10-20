from hashlib import sha256
from io import BytesIO
from warnings import catch_warnings, simplefilter
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile, ZipInfo

import pytest

from messqry.package_validation import (
    PackageLimits,
    PackageValidationError,
    validate_mlflow_pyfunc_package,
)

MLMODEL = b"""\
mlflow_version: 3.0.0
flavors:
  python_function:
    loader_module: mlflow.sklearn
    data: data/model.pkl
    env:
      virtualenv: python_env.yaml
"""


def package(entries=None, *, compression=ZIP_DEFLATED) -> bytes:
    files = entries or {
        "MLmodel": MLMODEL,
        "data/model.pkl": b"opaque model bytes",
        "python_env.yaml": b"python: 3.11",
    }
    output = BytesIO()
    with ZipFile(output, "w", compression=compression) as archive:
        for name, contents in files.items():
            if isinstance(name, ZipInfo):
                archive.writestr(name, contents)
            else:
                archive.writestr(name, contents)
    return output.getvalue()


def validate(payload: bytes, limits: PackageLimits = PackageLimits()):
    return validate_mlflow_pyfunc_package(BytesIO(payload), limits=limits)


def test_valid_pyfunc_package_returns_digest_and_safe_manifest():
    payload = package()
    stream = BytesIO(payload)

    validated = validate_mlflow_pyfunc_package(stream)

    assert validated.package_format == "mlflow_pyfunc"
    assert validated.sha256 == sha256(payload).hexdigest()
    assert validated.size_bytes == len(payload)
    assert validated.manifest == {
        "mlflow_version": "3.0.0",
        "flavors": ["python_function"],
        "file_count": 3,
        "expanded_size_bytes": sum(map(len, [MLMODEL, b"opaque model bytes", b"python: 3.11"])),
    }
    assert stream.tell() == 0


@pytest.mark.parametrize(
    "entries, message",
    [
        ({"data/model.pkl": b"data"}, "root MLmodel"),
        ({"MLmodel": b"not: [valid: yaml"}, "MLmodel metadata is invalid"),
        (
            {"MLmodel": b"flavors:\n  sklearn: {}\n"},
            "python_function flavor",
        ),
        (
            {
                "MLmodel": b"flavors:\n  python_function:\n    loader_module: mlflow.sklearn\n    data: missing.bin\n"
            },
            "references a missing package path",
        ),
        (
            {
                "MLmodel": b"flavors:\n  python_function:\n    loader_module: mlflow.sklearn\n    data: ../outside.bin\n",
                "data/model.pkl": b"data",
            },
            "unsafe path",
        ),
        (
            {
                "MLmodel": b"flavors:\n  python_function:\n    loader_module: mlflow.sklearn\n    data: C:/outside.bin\n",
            },
            "unsafe path",
        ),
        (
            {
                "MLmodel": b"flavors:\n  python_function:\n    loader_module: mlflow.sklearn\n    data: [model.bin, 7]\n",
            },
            "path must be a string or string list",
        ),
        (
            {
                "MLmodel": b"flavors:\n  python_function:\n    loader_module: mlflow.sklearn\n    env: []\n",
            },
            "environment paths must be a mapping",
        ),
    ],
)
def test_rejects_invalid_or_incomplete_mlflow_packages(entries, message):
    with pytest.raises(PackageValidationError, match=message):
        validate(package(entries))


@pytest.mark.parametrize("name", ["../outside", "/absolute", "folder\\\\file", "C:/drive/file"])
def test_rejects_unsafe_archive_member_paths(name):
    backslash_path = "folder" + chr(92) + "file"
    stored_name = "folderXfile" if name == backslash_path else name
    payload = package(
        {
            "MLmodel": MLMODEL,
            "data/model.pkl": b"opaque model bytes",
            "python_env.yaml": b"python: 3.11",
            stored_name: b"invalid",
        }
    )
    if name == backslash_path:
        # zipfile normalizes backslashes on Windows, so tamper with the ZIP name
        # bytes to represent a malicious cross-platform archive entry.
        payload = payload.replace(b"folderXfile", b"folder" + bytes([92]) + b"file")
    with pytest.raises(PackageValidationError, match="unsafe path"):
        validate(payload)


def test_rejects_symlinks_and_duplicate_normalized_entries():
    symlink = ZipInfo("data/link")
    symlink.create_system = 3
    symlink.external_attr = 0o120777 << 16
    with pytest.raises(PackageValidationError, match="non-regular filesystem entry"):
        validate(package({"MLmodel": MLMODEL, symlink: b"target"}))

    output = BytesIO()
    with catch_warnings():
        simplefilter("ignore", UserWarning)
        with ZipFile(output, "w") as archive:
            archive.writestr("MLmodel", MLMODEL)
            archive.writestr("data/model.pkl", b"first")
            archive.writestr("data/model.pkl", b"second")
    with pytest.raises(PackageValidationError, match="duplicate normalized paths"):
        validate(output.getvalue())


@pytest.mark.parametrize("parent", ["data", "DATA"])
def test_rejects_file_as_parent_of_another_archive_entry(parent):
    with pytest.raises(PackageValidationError, match="file/directory path conflict"):
        validate(
            package(
                {
                    "MLmodel": MLMODEL,
                    parent: b"not a directory",
                    "data/model.pkl": b"model bytes",
                    "python_env.yaml": b"python: 3.11",
                }
            )
        )


def test_validates_custom_artifact_paths_and_references():
    metadata = b"""\
flavors:
  python_function:
    loader_module: mlflow.sklearn
    artifacts:
      lookup:
        path: artifacts/lookup.db
        uri: runs:/123/lookup.db
"""
    valid = package({"MLmodel": metadata, "artifacts/lookup.db": b"lookup"})
    assert validate(valid).package_format == "mlflow_pyfunc"

    missing = package({"MLmodel": metadata})
    with pytest.raises(PackageValidationError, match="references a missing package path"):
        validate(missing)


@pytest.mark.parametrize(
    "artifact_entry",
    [
        b"path: ../outside\n        uri: runs:/123/outside",
        b"path: /outside\n        uri: runs:/123/outside",
        b"path: 7\n        uri: runs:/123/outside",
        b"path: artifacts/file\n        uri: 7",
    ],
)
def test_rejects_unsafe_or_malformed_custom_artifact_paths(artifact_entry):
    metadata = (
        b"flavors:\n  python_function:\n    loader_module: mlflow.sklearn\n    artifacts:\n      lookup:\n        "
        + artifact_entry
        + b"\n"
    )
    with pytest.raises(PackageValidationError, match="MLmodel"):
        validate(package({"MLmodel": metadata, "artifacts/file": b"artifact"}))


def test_enforces_archive_and_expansion_limits():
    payload = package()
    with pytest.raises(PackageValidationError, match="maximum upload size"):
        validate(payload, PackageLimits(max_archive_bytes=10))

    stored = package(
        {
            "MLmodel": b"flavors:\n  python_function:\n    loader_module: mlflow.sklearn\n",
            "large.bin": b"x" * 1024,
        },
        compression=ZIP_STORED,
    )
    with pytest.raises(PackageValidationError, match="maximum expanded size"):
        validate(stored, PackageLimits(max_expanded_bytes=100))

    with pytest.raises(PackageValidationError, match="file-count limit"):
        validate(payload, PackageLimits(max_files=2))

    compressed = package(
        {
            "MLmodel": b"flavors:\n  python_function:\n    loader_module: mlflow.sklearn\n",
            "large.bin": b"x" * 16_000,
        }
    )
    with pytest.raises(PackageValidationError, match="compression-ratio limit"):
        validate(compressed, PackageLimits(max_compression_ratio=10))


def test_rejects_invalid_zip_data_and_unsupported_compression():
    with pytest.raises(PackageValidationError, match="valid supported ZIP"):
        validate(b"not a zip")

    bzip_package = package(compression=12)
    with pytest.raises(PackageValidationError, match="compression method is not supported"):
        validate(bzip_package)
