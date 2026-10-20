"""Non-executing validation for uploaded MLflow pyfunc ZIP packages."""

from __future__ import annotations

import hashlib
import math
import re
import stat
import zipfile
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, BinaryIO

import yaml

_MAX_MLMODEL_BYTES = 1024 * 1024
_PATH_FIELDS = {
    "data",
    "model_data",
    "python_model",
    "model_path",
    "code",
    "conda_env",
    "python_env",
}


class PackageValidationError(ValueError):
    """Raised when an uploaded archive does not match the supported package contract."""


@dataclass(frozen=True)
class PackageLimits:
    """Resource bounds applied before any archive content is accepted."""

    max_archive_bytes: int = 512 * 1024 * 1024
    max_expanded_bytes: int = 2 * 1024 * 1024 * 1024
    max_files: int = 10_000
    max_compression_ratio: float = 200.0


@dataclass(frozen=True)
class ValidatedPackage:
    """Integrity and safe, non-executable metadata for one accepted package."""

    package_format: str
    sha256: str
    size_bytes: int
    manifest: dict[str, Any]


def _safe_member_name(name: str, *, is_directory: bool) -> str:
    if not name or "\\" in name or "\x00" in name or name.startswith("/"):
        raise PackageValidationError("Archive contains an unsafe path")
    normalized = name[:-1] if is_directory and name.endswith("/") else name
    parts = normalized.split("/")
    if any(
        part in {"", ".", ".."}
        or ":" in part
        or part.endswith((".", " "))
        or part.split(".")[0].upper() in {"CON", "PRN", "AUX", "NUL"}
        or re.fullmatch(r"(?:COM|LPT)[1-9]", part.split(".")[0].upper())
        for part in parts
    ):
        raise PackageValidationError("Archive contains an unsafe path")
    return PurePosixPath(normalized).as_posix()


def _hash_archive(archive: BinaryIO, limits: PackageLimits) -> tuple[str, int]:
    try:
        if not archive.seekable():
            raise PackageValidationError("Package upload must be seekable for validation")
        archive.seek(0)
        digest = hashlib.sha256()
        total = 0
        while chunk := archive.read(1024 * 1024):
            total += len(chunk)
            if total > limits.max_archive_bytes:
                raise PackageValidationError("Package exceeds the maximum upload size")
            digest.update(chunk)
        if total == 0:
            raise PackageValidationError("Package upload is empty")
        archive.seek(0)
        return digest.hexdigest(), total
    except (OSError, AttributeError) as exc:
        raise PackageValidationError("Package upload could not be read") from exc


def _read_mlmodel(archive: zipfile.ZipFile, info: zipfile.ZipInfo) -> dict[str, Any]:
    if info.file_size > _MAX_MLMODEL_BYTES:
        raise PackageValidationError("MLmodel metadata exceeds the maximum allowed size")
    try:
        with archive.open(info) as stream:
            raw = stream.read(_MAX_MLMODEL_BYTES + 1)
        if len(raw) > _MAX_MLMODEL_BYTES:
            raise PackageValidationError("MLmodel metadata exceeds the maximum allowed size")
        metadata = yaml.safe_load(raw)
    except (
        UnicodeDecodeError,
        yaml.YAMLError,
        zipfile.BadZipFile,
        RuntimeError,
        RecursionError,
    ) as exc:
        raise PackageValidationError("MLmodel metadata is invalid") from exc
    if not isinstance(metadata, dict):
        raise PackageValidationError("MLmodel metadata must be a YAML mapping")
    return metadata


def _referenced_paths(python_function: dict[str, Any]) -> list[str]:
    paths: list[str] = []
    for name in _PATH_FIELDS:
        if name not in python_function:
            continue
        value = python_function.get(name)
        if isinstance(value, str):
            paths.append(value)
        elif isinstance(value, list) and all(isinstance(item, str) for item in value):
            paths.extend(value)
        else:
            raise PackageValidationError(f"MLmodel {name} path must be a string or string list")
    environments = python_function.get("env")
    if "env" in python_function and not isinstance(environments, dict):
        raise PackageValidationError("MLmodel environment paths must be a mapping")
    if isinstance(environments, dict):
        for key in ("conda", "virtualenv"):
            if key not in environments:
                continue
            value = environments.get(key)
            if isinstance(value, str):
                paths.append(value)
            else:
                raise PackageValidationError(f"MLmodel environment {key} path must be a string")
    if "artifacts" in python_function:
        artifacts = python_function["artifacts"]
        if not isinstance(artifacts, dict):
            raise PackageValidationError("MLmodel artifacts must be a mapping")
        for name, artifact in artifacts.items():
            if not isinstance(name, str) or not name.strip() or not isinstance(artifact, dict):
                raise PackageValidationError("MLmodel artifact entries must be named mappings")
            path = artifact.get("path")
            uri = artifact.get("uri")
            if not isinstance(path, str) or not path:
                raise PackageValidationError("MLmodel artifact path must be a string")
            if uri is not None and not isinstance(uri, str):
                raise PackageValidationError("MLmodel artifact URI must be a string")
            paths.append(path)
    return paths


def validate_mlflow_pyfunc_package(
    archive_file: BinaryIO,
    *,
    limits: PackageLimits = PackageLimits(),
) -> ValidatedPackage:
    """Validate archive paths, bounds, checksums, and MLflow flavor without loading code."""

    digest, archive_size = _hash_archive(archive_file, limits)
    try:
        with zipfile.ZipFile(archive_file) as archive:
            infos = archive.infolist()
            if not infos or len(infos) > limits.max_files:
                raise PackageValidationError("Package has no files or exceeds the file-count limit")

            seen_paths: set[str] = set()
            casefolded_paths: set[str] = set()
            files: set[str] = set()
            entry_types: dict[str, bool] = {}
            total_uncompressed = 0
            mlmodel_info: zipfile.ZipInfo | None = None
            for info in infos:
                path = _safe_member_name(info.filename, is_directory=info.is_dir())
                folded = path.casefold()
                if path in seen_paths or folded in casefolded_paths:
                    raise PackageValidationError("Archive contains duplicate normalized paths")
                seen_paths.add(path)
                casefolded_paths.add(folded)
                entry_types[folded] = info.is_dir()

                mode = info.external_attr >> 16
                kind = stat.S_IFMT(mode)
                expected_kind = stat.S_IFDIR if info.is_dir() else stat.S_IFREG
                if kind not in {0, expected_kind}:
                    raise PackageValidationError("Archive contains a non-regular filesystem entry")
                if info.flag_bits & 0x1:
                    raise PackageValidationError("Encrypted ZIP entries are not supported")
                if info.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}:
                    raise PackageValidationError("ZIP compression method is not supported")
                if info.is_dir():
                    continue

                total_uncompressed += info.file_size
                if total_uncompressed > limits.max_expanded_bytes:
                    raise PackageValidationError("Package exceeds the maximum expanded size")
                if info.file_size and not info.compress_size:
                    raise PackageValidationError("Archive entry has an invalid compression size")
                if info.compress_size:
                    ratio = info.file_size / info.compress_size
                    if not math.isfinite(ratio) or ratio > limits.max_compression_ratio:
                        raise PackageValidationError(
                            "Archive entry exceeds the compression-ratio limit"
                        )
                files.add(path)
                if path == "MLmodel":
                    mlmodel_info = info

            # Reject file/directory conflicts even when an explicit directory entry is absent
            # or appears after its child. Compare case-insensitively for Windows extraction.
            file_paths = {
                folded_path for folded_path, is_directory in entry_types.items() if not is_directory
            }
            for folded_path in entry_types:
                parts = folded_path.split("/")
                if any("/".join(parts[:index]) in file_paths for index in range(1, len(parts))):
                    raise PackageValidationError("Archive contains a file/directory path conflict")

            if mlmodel_info is None:
                raise PackageValidationError("Package must contain a root MLmodel file")

            metadata = _read_mlmodel(archive, mlmodel_info)
            flavors = metadata.get("flavors")
            if not isinstance(flavors, dict):
                raise PackageValidationError("MLmodel metadata has no flavor mapping")
            python_function = flavors.get("python_function")
            if not isinstance(python_function, dict):
                raise PackageValidationError("Model must declare the python_function flavor")
            loader_module = python_function.get("loader_module")
            if not isinstance(loader_module, str) or not loader_module.strip():
                raise PackageValidationError("python_function flavor must declare loader_module")
            mlflow_version = metadata.get("mlflow_version")
            if mlflow_version is not None and not isinstance(mlflow_version, str):
                raise PackageValidationError("MLmodel mlflow_version must be a string")
            if not all(isinstance(flavor, str) and flavor for flavor in flavors):
                raise PackageValidationError("MLmodel flavor names must be non-empty strings")

            for raw_path in _referenced_paths(python_function):
                try:
                    normalized_path = _safe_member_name(
                        raw_path,
                        is_directory=raw_path.endswith("/"),
                    )
                except PackageValidationError as exc:
                    raise PackageValidationError(
                        "MLmodel metadata contains an unsafe path"
                    ) from exc
                exists = normalized_path in files or any(
                    entry.startswith(normalized_path.rstrip("/") + "/") for entry in files
                )
                if not exists:
                    raise PackageValidationError(
                        "MLmodel metadata references a missing package path"
                    )

            # Read every entry in bounded chunks so the ZIP reader verifies each CRC without
            # materializing the expanded package in memory.
            for info in infos:
                if info.is_dir():
                    continue
                with archive.open(info) as stream:
                    while stream.read(1024 * 1024):
                        pass

            manifest = {
                "mlflow_version": mlflow_version,
                "flavors": sorted(flavors),
                "file_count": len(files),
                "expanded_size_bytes": total_uncompressed,
            }
            archive_file.seek(0)
            return ValidatedPackage(
                package_format="mlflow_pyfunc",
                sha256=digest,
                size_bytes=archive_size,
                manifest=manifest,
            )
    except PackageValidationError:
        raise
    except (zipfile.BadZipFile, OSError, RuntimeError) as exc:
        raise PackageValidationError("Package is not a valid supported ZIP archive") from exc
