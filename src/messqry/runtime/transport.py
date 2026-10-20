"""Bounded JSON transport for private model runtime tasks."""

from __future__ import annotations

import ipaddress
import json
import selectors
import socket
import time
from typing import Callable

from pydantic import ValidationError

from messqry.api.schemas import PredictionResponse

MAX_PREDICTION_REQUEST_BYTES = 1024 * 1024
MAX_PREDICTION_RESPONSE_BYTES = 1024 * 1024
MAX_RESPONSE_HEADER_BYTES = 64 * 1024


class RuntimeTransportError(RuntimeError):
    """A private model runtime could not return a valid prediction response."""

    def __init__(self, message: str, *, timed_out: bool = False) -> None:
        super().__init__(message)
        self.timed_out = timed_out


def encode_prediction_payload(payload: dict) -> bytes:
    """Serialize as UTF-8 and enforce the wire-size limit before starting a task."""

    try:
        body = json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        raise ValueError("Prediction payload cannot be encoded as JSON") from exc
    if len(body) > MAX_PREDICTION_REQUEST_BYTES:
        raise ValueError("Prediction request exceeds the 1 MiB runtime limit")
    return body


def _wait_for(sock: socket.socket, event: int, deadline: float) -> None:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("prediction deadline exceeded")
    with selectors.DefaultSelector() as selector:
        selector.register(sock, event)
        if not selector.select(remaining):
            raise TimeoutError("prediction deadline exceeded")


def _recv(sock: socket.socket, deadline: float) -> bytes:
    while True:
        _wait_for(sock, selectors.EVENT_READ, deadline)
        try:
            return sock.recv(64 * 1024)
        except BlockingIOError:
            continue


def _read_exact(sock: socket.socket, buffer: bytearray, length: int, deadline: float) -> bytes:
    while len(buffer) < length:
        chunk = _recv(sock, deadline)
        if not chunk:
            raise RuntimeTransportError("Model runtime returned an incomplete response")
        buffer.extend(chunk)
    result = bytes(buffer[:length])
    del buffer[:length]
    return result


def _read_line(sock: socket.socket, buffer: bytearray, deadline: float) -> bytes:
    while b"\r\n" not in buffer:
        if len(buffer) > MAX_RESPONSE_HEADER_BYTES:
            raise RuntimeTransportError("Model runtime response headers are too large")
        chunk = _recv(sock, deadline)
        if not chunk:
            raise RuntimeTransportError("Model runtime returned an incomplete response")
        buffer.extend(chunk)
    end = buffer.index(b"\r\n")
    if end + 2 > MAX_RESPONSE_HEADER_BYTES:
        raise RuntimeTransportError("Model runtime response headers are too large")
    line = bytes(buffer[:end])
    del buffer[: end + 2]
    return line


def _read_http_response(sock: socket.socket, deadline: float) -> tuple[int, bytes]:
    buffer = bytearray()
    while b"\r\n\r\n" not in buffer:
        if len(buffer) > MAX_RESPONSE_HEADER_BYTES:
            raise RuntimeTransportError("Model runtime response headers are too large")
        chunk = _recv(sock, deadline)
        if not chunk:
            raise RuntimeTransportError("Model runtime returned an incomplete response")
        buffer.extend(chunk)
    header_end = buffer.index(b"\r\n\r\n")
    if header_end + 4 > MAX_RESPONSE_HEADER_BYTES:
        raise RuntimeTransportError("Model runtime response headers are too large")
    header_block = bytes(buffer[:header_end])
    del buffer[: header_end + 4]
    lines = header_block.split(b"\r\n")
    try:
        protocol, raw_status, _ = lines[0].decode("ascii").split(" ", 2)
        status = int(raw_status)
        if not protocol.startswith("HTTP/"):
            raise ValueError
        headers: dict[str, str] = {}
        for line in lines[1:]:
            name, separator, value = line.partition(b":")
            if not separator:
                raise ValueError
            headers[name.decode("ascii").strip().lower()] = value.decode("latin-1").strip()
    except (UnicodeDecodeError, ValueError) as exc:
        raise RuntimeTransportError("Model runtime returned malformed HTTP headers") from exc

    if "chunked" in headers.get("transfer-encoding", "").lower():
        body = bytearray()
        while True:
            size_line = _read_line(sock, buffer, deadline)
            try:
                size = int(size_line.split(b";", 1)[0].strip(), 16)
            except ValueError as exc:
                raise RuntimeTransportError(
                    "Model runtime returned malformed chunked data"
                ) from exc
            if size < 0 or size > MAX_PREDICTION_RESPONSE_BYTES - len(body):
                raise RuntimeTransportError("Model runtime response exceeds the 1 MiB limit")
            if size == 0:
                trailer_bytes = 0
                while True:
                    trailer_line = _read_line(sock, buffer, deadline)
                    trailer_bytes += len(trailer_line) + 2
                    if trailer_bytes > MAX_RESPONSE_HEADER_BYTES:
                        raise RuntimeTransportError("Model runtime response trailers are too large")
                    if not trailer_line:
                        break
                break
            body.extend(_read_exact(sock, buffer, size, deadline))
            if _read_exact(sock, buffer, 2, deadline) != b"\r\n":
                raise RuntimeTransportError("Model runtime returned malformed chunked data")
        return status, bytes(body)

    content_length = headers.get("content-length")
    if content_length is not None:
        try:
            length = int(content_length)
        except ValueError as exc:
            raise RuntimeTransportError("Model runtime returned malformed content length") from exc
        if length < 0 or length > MAX_PREDICTION_RESPONSE_BYTES:
            raise RuntimeTransportError("Model runtime response exceeds the 1 MiB limit")
        return status, _read_exact(sock, buffer, length, deadline)

    body = bytearray(buffer)
    if len(body) > MAX_PREDICTION_RESPONSE_BYTES:
        raise RuntimeTransportError("Model runtime response exceeds the 1 MiB limit")
    while True:
        chunk = _recv(sock, deadline)
        if not chunk:
            return status, bytes(body)
        body.extend(chunk)
        if len(body) > MAX_PREDICTION_RESPONSE_BYTES:
            raise RuntimeTransportError("Model runtime response exceeds the 1 MiB limit")


def _http_request(
    private_ip: str,
    port: int,
    method: str,
    path: str,
    body: bytes,
    timeout_seconds: float,
    *,
    connector: Callable[..., socket.socket] = socket.create_connection,
) -> tuple[int, bytes]:
    """Send one bounded HTTP request; parse responses without redirect handling."""

    deadline = time.monotonic() + timeout_seconds
    try:
        with connector((private_ip, port), timeout=max(0.001, deadline - time.monotonic())) as sock:
            if time.monotonic() >= deadline:
                raise TimeoutError("prediction deadline exceeded")
            sock.setblocking(False)
            request = (
                f"{method} {path} HTTP/1.1\r\n"
                f"Host: {private_ip}:{port}\r\n"
                "Content-Type: application/json\r\n"
                "Accept: application/json\r\n"
                f"Content-Length: {len(body)}\r\n"
                "Connection: close\r\n\r\n"
            ).encode("ascii") + body
            sent = 0
            while sent < len(request):
                _wait_for(sock, selectors.EVENT_WRITE, deadline)
                try:
                    sent += sock.send(request[sent:])
                except BlockingIOError:
                    continue
            return _read_http_response(sock, deadline)
    except TimeoutError:
        raise
    except OSError as exc:
        raise RuntimeTransportError("Model runtime is unavailable") from exc


def _http_post(
    private_ip: str,
    port: int,
    body: bytes,
    timeout_seconds: float,
    *,
    connector: Callable[..., socket.socket] = socket.create_connection,
) -> tuple[int, bytes]:
    return _http_request(
        private_ip, port, "POST", "/predict", body, timeout_seconds, connector=connector
    )


def http_get_private(
    private_ip: str,
    port: int,
    path: str,
    timeout_seconds: float,
    *,
    connector: Callable[..., socket.socket] = socket.create_connection,
) -> tuple[int, bytes]:
    """Fetch one private endpoint without redirects and within one total deadline."""

    try:
        address = ipaddress.ip_address(private_ip)
    except ValueError as exc:
        raise ValueError("Runtime address must be a private IPv4 address") from exc
    if (
        address.version != 4
        or not address.is_private
        or address.is_loopback
        or address.is_link_local
    ):
        raise ValueError("Runtime address must be a private IPv4 address")
    if not path.startswith("/") or "\r" in path or "\n" in path:
        raise ValueError("Runtime health path is invalid")
    return _http_request(str(address), port, "GET", path, b"", timeout_seconds, connector=connector)


def predict_from_runtime(
    private_ip: str,
    port: int,
    payload: dict,
    *,
    model_id: str,
    environment: str,
    model_version: int,
    timeout_seconds: float = 30,
    exchange: Callable[[str, int, bytes, float], tuple[int, bytes]] | None = None,
) -> PredictionResponse:
    """Send one bounded prediction request and verify runtime identity and output shape."""

    try:
        address = ipaddress.ip_address(private_ip)
    except ValueError as exc:
        raise ValueError("Model runtime address must be a private IPv4 address") from exc
    if (
        address.version != 4
        or not address.is_private
        or address.is_loopback
        or address.is_link_local
    ):
        raise ValueError("Model runtime address must be a private IPv4 address")
    body = encode_prediction_payload(payload)
    sender = exchange or _http_post
    try:
        status, raw_response = sender(str(address), port, body, timeout_seconds)
    except TimeoutError as exc:
        raise RuntimeTransportError(
            f"Model inference exceeded its {timeout_seconds:g}-second limit", timed_out=True
        ) from exc
    except RuntimeTransportError:
        raise
    except OSError as exc:
        raise RuntimeTransportError("Model runtime is unavailable") from exc
    if status != 200:
        raise RuntimeTransportError("Model runtime returned an unsuccessful response")
    if len(raw_response) > MAX_PREDICTION_RESPONSE_BYTES:
        raise RuntimeTransportError("Model runtime response exceeds the 1 MiB limit")
    try:
        response = PredictionResponse.model_validate_json(raw_response)
    except (ValidationError, ValueError) as exc:
        raise RuntimeTransportError("Model runtime returned a malformed response") from exc
    if (
        str(response.model_id) != model_id
        or response.environment != environment
        or response.model_version != model_version
        or len(response.predictions) != len(payload.get("inputs", []))
    ):
        raise RuntimeTransportError("Model runtime response does not match the selected deployment")
    return response
