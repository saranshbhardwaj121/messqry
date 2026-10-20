import json
import selectors
import time
from uuid import UUID

import pytest

from messqry.runtime.transport import (
    MAX_PREDICTION_REQUEST_BYTES,
    MAX_PREDICTION_RESPONSE_BYTES,
    MAX_RESPONSE_HEADER_BYTES,
    RuntimeTransportError,
    _http_post,
    encode_prediction_payload,
    http_get_private,
    predict_from_runtime,
)

MODEL_ID = "ec0b4ef3-148d-4cb0-b7ad-36c5f2458cc3"


def response_body(**overrides):
    result = {
        "model_id": MODEL_ID,
        "environment": "production",
        "model_version": 4,
        "predictions": [0.91],
    }
    result.update(overrides)
    return json.dumps(result).encode()


class FakeSelector:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def register(self, sock, event):
        return None

    def select(self, timeout):
        time.sleep(min(0.04, timeout))
        return [(None, None)]


class FakeSocket:
    def __init__(self, response_parts):
        self.response_parts = iter(response_parts)
        self.sent = bytearray()
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.closed = True

    def setblocking(self, blocking):
        assert blocking is False

    def send(self, payload):
        self.sent.extend(payload)
        return len(payload)

    def recv(self, max_bytes):
        try:
            return next(self.response_parts)
        except StopIteration:
            return b""


def test_private_runtime_transport_posts_and_validates_prediction():
    observed = {}

    def exchange(ip, port, body, timeout):
        observed["destination"] = (ip, port)
        observed["body"] = body
        observed["timeout"] = timeout
        return 200, response_body()

    result = predict_from_runtime(
        "10.0.1.17",
        8000,
        {"inputs": [{"feature": 1}]},
        model_id=MODEL_ID,
        environment="production",
        model_version=4,
        exchange=exchange,
    )

    assert observed == {
        "destination": ("10.0.1.17", 8000),
        "body": b'{"inputs":[{"feature":1}]}',
        "timeout": 30,
    }
    assert result.model_id == UUID(MODEL_ID)
    assert result.predictions == [0.91]


@pytest.mark.parametrize("address", ["127.0.0.1", "169.254.1.2", "8.8.8.8", "::1"])
def test_runtime_transport_rejects_non_private_or_non_ipv4_addresses(address):
    with pytest.raises(ValueError, match="private IPv4"):
        predict_from_runtime(
            address,
            8000,
            {"inputs": [{"feature": 1}]},
            model_id=MODEL_ID,
            environment="production",
            model_version=4,
            exchange=lambda *_args: pytest.fail("network call must not run"),
        )


def test_runtime_transport_rejects_response_for_wrong_deployment():
    with pytest.raises(RuntimeTransportError, match="does not match"):
        predict_from_runtime(
            "10.0.1.17",
            8000,
            {"inputs": [{"feature": 1}]},
            model_id=MODEL_ID,
            environment="production",
            model_version=4,
            exchange=lambda *_args: (200, response_body(model_version=3)),
        )


def test_runtime_transport_rejects_redirect_response():
    calls = []
    with pytest.raises(RuntimeTransportError, match="unsuccessful response"):
        predict_from_runtime(
            "10.0.1.17",
            8000,
            {"inputs": [{"feature": 1}]},
            model_id=MODEL_ID,
            environment="production",
            model_version=4,
            exchange=lambda *args: calls.append(args) or (302, b""),
        )
    assert len(calls) == 1


def test_runtime_transport_rejects_oversized_request_before_network_call():
    with pytest.raises(ValueError, match="1 MiB runtime limit"):
        predict_from_runtime(
            "10.0.1.17",
            8000,
            {"inputs": [{"feature": "x" * (1024 * 1024)}]},
            model_id=MODEL_ID,
            environment="production",
            model_version=4,
            exchange=lambda *_args: pytest.fail("network call must not run"),
        )


def test_runtime_transport_marks_inference_timeout():
    def timeout(*_args):
        raise TimeoutError

    with pytest.raises(RuntimeTransportError, match="30-second limit") as error:
        predict_from_runtime(
            "10.0.1.17",
            8000,
            {"inputs": [{"feature": 1}]},
            model_id=MODEL_ID,
            environment="production",
            model_version=4,
            exchange=timeout,
        )
    assert error.value.timed_out is True


def test_runtime_transport_rejects_oversized_response():
    with pytest.raises(RuntimeTransportError, match="exceeds the 1 MiB limit"):
        predict_from_runtime(
            "10.0.1.17",
            8000,
            {"inputs": [{"feature": 1}]},
            model_id=MODEL_ID,
            environment="production",
            model_version=4,
            exchange=lambda *_args: (200, b" " * (MAX_PREDICTION_RESPONSE_BYTES + 1)),
        )


def test_unicode_payload_is_forwarded_as_utf8_without_expansion():
    payload = {"inputs": [{"text": "漢" * 200_000}]}
    raw_body = encode_prediction_payload(payload)

    assert len(raw_body) < MAX_PREDICTION_REQUEST_BYTES
    assert "漢".encode() in raw_body
    assert b"\\u6f22" not in raw_body


def test_http_transport_does_not_follow_redirects(monkeypatch):
    monkeypatch.setattr(selectors, "DefaultSelector", FakeSelector)
    client = FakeSocket(
        [
            b"HTTP/1.1 302 Found\r\n"
            b"Location: http://169.254.169.254/latest/meta-data/\r\n"
            b"Content-Length: 0\r\nConnection: close\r\n\r\n"
        ]
    )
    calls = []
    response_status, body = _http_post(
        "10.0.0.10",
        8000,
        b"{}",
        2,
        connector=lambda address, timeout: calls.append((address, timeout)) or client,
    )

    assert len(calls) == 1
    assert calls[0][0] == ("10.0.0.10", 8000)
    assert 0 < calls[0][1] <= 2
    assert response_status == 302
    assert body == b""
    assert b"169.254.169.254" not in client.sent


def test_http_transport_enforces_one_total_deadline_while_response_trickles(monkeypatch):
    monkeypatch.setattr(selectors, "DefaultSelector", FakeSelector)
    response_parts = [
        b"HTTP/1.1 200 OK\r\nContent-Length: 32\r\n\r\n",
        *[b"x"] * 32,
    ]
    client = FakeSocket(response_parts)
    started = time.monotonic()
    with pytest.raises(TimeoutError):
        _http_post("10.0.0.10", 8000, b"{}", 0.15, connector=lambda *_args, **_kwargs: client)
    assert time.monotonic() - started < 0.8


def test_private_health_get_rejects_redirect_without_following_it(monkeypatch):
    monkeypatch.setattr(selectors, "DefaultSelector", FakeSelector)
    client = FakeSocket(
        [
            b"HTTP/1.1 302 Found\r\n"
            b"Location: http://169.254.169.254/latest/meta-data/\r\n"
            b"Content-Length: 0\r\nConnection: close\r\n\r\n"
        ]
    )
    calls = []
    status, body = http_get_private(
        "10.0.0.10",
        8000,
        "/health/ready",
        1,
        connector=lambda address, timeout: calls.append((address, timeout)) or client,
    )
    assert status == 302
    assert body == b""
    assert len(calls) == 1
    assert b"169.254.169.254" not in client.sent


def test_health_get_enforces_one_total_deadline_while_response_trickles(monkeypatch):
    monkeypatch.setattr(selectors, "DefaultSelector", FakeSelector)
    response_parts = [
        b"HTTP/1.1 200 OK\r\nContent-Length: 4096\r\n\r\n",
        *[b"x"] * 4096,
    ]
    client = FakeSocket(response_parts)
    started = time.monotonic()
    with pytest.raises(TimeoutError):
        http_get_private(
            "10.0.0.10",
            8000,
            "/health/ready",
            0.15,
            connector=lambda *_args, **_kwargs: client,
        )
    assert time.monotonic() - started < 0.8


def test_http_transport_rejects_oversized_headers_even_when_terminator_is_in_same_read(
    monkeypatch,
):
    monkeypatch.setattr(selectors, "DefaultSelector", FakeSelector)
    client = FakeSocket(
        [b"HTTP/1.1 200 OK\r\nX-Large: " + b"a" * MAX_RESPONSE_HEADER_BYTES + b"\r\n\r\n"]
    )
    with pytest.raises(RuntimeTransportError, match="headers are too large"):
        _http_post("10.0.0.10", 8000, b"{}", 1, connector=lambda *_a, **_k: client)


def test_http_transport_counts_trailer_bytes_not_trailer_lines(monkeypatch):
    monkeypatch.setattr(selectors, "DefaultSelector", FakeSelector)
    client = FakeSocket(
        [
            b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n"
            b"0\r\n" + b"x" * MAX_RESPONSE_HEADER_BYTES + b"\r\n\r\n"
        ]
    )
    with pytest.raises(RuntimeTransportError, match="too large"):
        _http_post("10.0.0.10", 8000, b"{}", 1, connector=lambda *_a, **_k: client)
