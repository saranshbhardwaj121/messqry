"""HTTP routes for the initial API surface."""

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()


class HealthResponse(BaseModel):
    """Liveness response for the API process."""

    status: str


@router.get("/health", response_model=HealthResponse, tags=["operations"])
def health_check() -> HealthResponse:
    """Report that the API process is accepting requests."""

    return HealthResponse(status="ok")
