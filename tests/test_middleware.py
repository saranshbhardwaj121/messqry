import json

from fastapi import FastAPI, File, UploadFile
from fastapi.responses import JSONResponse

from messqry.api.middleware import ArtifactUploadBodyLimitMiddleware


def run_without_event_loop(coroutine):
    """Run these fully in-memory ASGI coroutines without Windows loop startup."""
    try:
        coroutine.send(None)
    except StopIteration:
        return
    raise AssertionError("The in-memory ASGI test unexpectedly suspended")


def test_artifact_body_limit_rejects_oversized_content_length_before_route():
    route_called = False
    received = False
    sent = []

    async def app(scope, receive, send):
        nonlocal route_called
        route_called = True

    async def receive():
        nonlocal received
        received = True
        return {"type": "http.request", "body": b"too large", "more_body": False}

    async def send(message):
        sent.append(message)

    middleware = ArtifactUploadBodyLimitMiddleware(app, max_body_bytes=8)
    run_without_event_loop(
        middleware(
            {
                "type": "http",
                "method": "POST",
                "path": "/api/v1/models/model-id/versions/artifact",
                "headers": [(b"content-length", b"9")],
            },
            receive,
            send,
        )
    )

    assert not route_called
    assert not received
    assert sent[0]["status"] == 413
    assert json.loads(sent[1]["body"]) == {
        "error": {
            "code": "request_too_large",
            "message": "Artifact upload exceeds the maximum request size",
        }
    }


def test_artifact_body_limit_counts_streamed_chunks_without_content_length():
    chunks = iter((b"1234", b"5678", b"9"))
    delivered_to_parser = []
    sent = []

    async def receive():
        return {"type": "http.request", "body": next(chunks), "more_body": True}

    async def app(scope, limited_receive, send):
        while True:
            message = await limited_receive()
            delivered_to_parser.append(message["body"])

    async def send(message):
        sent.append(message)

    middleware = ArtifactUploadBodyLimitMiddleware(app, max_body_bytes=8)
    run_without_event_loop(
        middleware(
            {
                "type": "http",
                "method": "POST",
                "path": "/api/v1/models/model-id/versions/artifact",
                "headers": [(b"content-type", b"multipart/form-data; boundary=test")],
            },
            receive,
            send,
        )
    )

    assert delivered_to_parser == [b"1234", b"5678"]
    assert sent[0]["status"] == 413


def test_fastapi_multipart_parser_preserves_streamed_413_status():
    app = FastAPI()

    @app.post("/api/v1/models/{model_id}/versions/artifact")
    async def upload_artifact(file: UploadFile = File(...)):
        return JSONResponse({"filename": file.filename})

    body = (
        b"--boundary\r\n"
        b'Content-Disposition: form-data; name="file"; filename="model.zip"\r\n'
        b"Content-Type: application/zip\r\n\r\n"
        b"payload larger than configured limit\r\n"
        b"--boundary--\r\n"
    )
    sent = []
    delivered = False

    async def receive():
        nonlocal delivered
        if delivered:
            return {"type": "http.request", "body": b"", "more_body": False}
        delivered = True
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message):
        sent.append(message)

    middleware = ArtifactUploadBodyLimitMiddleware(app, max_body_bytes=8)
    run_without_event_loop(
        middleware(
            {
                "type": "http",
                "asgi": {"version": "3.0"},
                "http_version": "1.1",
                "method": "POST",
                "scheme": "http",
                "path": "/api/v1/models/model-id/versions/artifact",
                "raw_path": b"/api/v1/models/model-id/versions/artifact",
                "query_string": b"",
                "headers": [(b"content-type", b"multipart/form-data; boundary=boundary")],
                "server": ("test", 80),
                "client": ("test", 1234),
                "root_path": "",
            },
            receive,
            send,
        )
    )

    assert sent[0]["status"] == 413
    assert json.loads(sent[1]["body"])["error"]["code"] == "request_too_large"


def test_artifact_body_limit_does_not_restrict_other_routes():
    forwarded = False

    async def app(scope, receive, send):
        nonlocal forwarded
        forwarded = True

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        pass

    middleware = ArtifactUploadBodyLimitMiddleware(app, max_body_bytes=1)
    run_without_event_loop(
        middleware(
            {
                "type": "http",
                "method": "POST",
                "path": "/api/v1/models",
                "headers": [(b"content-length", b"999")],
            },
            receive,
            send,
        )
    )
    assert forwarded


def test_prediction_body_limit_rejects_oversized_content_length_before_route():
    route_called = False
    received = False
    sent = []

    async def app(scope, receive, send):
        nonlocal route_called
        route_called = True

    async def receive():
        nonlocal received
        received = True
        return {"type": "http.request", "body": b"too large", "more_body": False}

    async def send(message):
        sent.append(message)

    middleware = ArtifactUploadBodyLimitMiddleware(
        app, max_body_bytes=100, prediction_max_body_bytes=8
    )
    run_without_event_loop(
        middleware(
            {
                "type": "http",
                "method": "POST",
                "path": "/api/v1/models/model-id/deployments/staging/predict",
                "headers": [(b"content-length", b"9")],
            },
            receive,
            send,
        )
    )

    assert not route_called
    assert not received
    assert sent[0]["status"] == 413
    assert json.loads(sent[1]["body"]) == {
        "error": {
            "code": "request_too_large",
            "message": "Prediction request exceeds the maximum request size",
        }
    }


def test_prediction_body_limit_counts_chunks_without_content_length():
    chunks = iter((b"1234", b"5678", b"9"))
    delivered_to_parser = []
    sent = []

    async def receive():
        return {"type": "http.request", "body": next(chunks), "more_body": True}

    async def app(scope, limited_receive, send):
        while True:
            message = await limited_receive()
            delivered_to_parser.append(message["body"])

    async def send(message):
        sent.append(message)

    middleware = ArtifactUploadBodyLimitMiddleware(
        app, max_body_bytes=100, prediction_max_body_bytes=8
    )
    run_without_event_loop(
        middleware(
            {
                "type": "http",
                "method": "POST",
                "path": "/api/v1/models/model-id/deployments/staging/predict",
                "headers": [(b"content-type", b"application/json")],
            },
            receive,
            send,
        )
    )

    assert delivered_to_parser == [b"1234", b"5678"]
    assert sent[0]["status"] == 413
