"""Request guards that must run before FastAPI parses request bodies."""

from collections.abc import Awaitable, Callable
from typing import Any

from starlette.responses import JSONResponse

ASGIMessage = dict[str, Any]
Receive = Callable[[], Awaitable[ASGIMessage]]
Send = Callable[[ASGIMessage], Awaitable[None]]


class _RequestBodyTooLarge(Exception):
    pass


class ArtifactUploadBodyLimitMiddleware:
    """Bound artifact and prediction bodies before FastAPI parses them."""

    def __init__(
        self, app, *, max_body_bytes: int, prediction_max_body_bytes: int = 1024 * 1024
    ) -> None:
        self.app = app
        self.max_body_bytes = max_body_bytes
        self.prediction_max_body_bytes = prediction_max_body_bytes

    async def __call__(self, scope: dict[str, Any], receive: Receive, send: Send) -> None:
        limit = self._body_limit(scope)
        if limit is None:
            await self.app(scope, receive, send)
            return

        max_body_bytes, body_kind = limit
        if self._content_length_exceeds_limit(scope, max_body_bytes):
            await self._too_large_response(scope, receive, send, body_kind)
            return

        received_bytes = 0
        body_too_large = False

        async def limited_receive() -> ASGIMessage:
            nonlocal received_bytes, body_too_large
            message = await receive()
            if message["type"] == "http.request":
                received_bytes += len(message.get("body", b""))
                if received_bytes > max_body_bytes:
                    body_too_large = True
                    raise _RequestBodyTooLarge
            return message

        async def guarded_send(message: ASGIMessage) -> None:
            # FastAPI may turn a body-read exception into its own 400 response.
            # Suppress that response so this outer guard can send the canonical 413.
            if not body_too_large:
                await send(message)

        try:
            await self.app(scope, limited_receive, guarded_send)
        except _RequestBodyTooLarge:
            pass
        if body_too_large:
            await self._too_large_response(scope, receive, send, body_kind)

    def _body_limit(self, scope: dict[str, Any]) -> tuple[int, str] | None:
        if scope.get("type") != "http" or scope.get("method") != "POST":
            return None
        path = scope.get("path", "")
        if path.startswith("/api/v1/models/") and path.endswith("/versions/artifact"):
            return self.max_body_bytes, "Artifact upload"
        parts = path.strip("/").split("/")
        if (
            len(parts) == 7
            and parts[:3] == ["api", "v1", "models"]
            and parts[4] == "deployments"
            and parts[6] == "predict"
        ):
            return self.prediction_max_body_bytes, "Prediction request"
        return None

    @staticmethod
    def _content_length_exceeds_limit(scope: dict[str, Any], max_body_bytes: int) -> bool:
        for key, value in scope.get("headers", []):
            if key.lower() == b"content-length":
                try:
                    return int(value) > max_body_bytes
                except ValueError:
                    return False
        return False

    @staticmethod
    async def _too_large_response(
        scope: dict[str, Any], receive: Receive, send: Send, body_kind: str
    ) -> None:
        response = JSONResponse(
            status_code=413,
            content={
                "error": {
                    "code": "request_too_large",
                    "message": f"{body_kind} exceeds the maximum request size",
                }
            },
        )
        await response(scope, receive, send)
