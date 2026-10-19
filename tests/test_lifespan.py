import sys

import pytest
from fastapi import FastAPI

from messqry import main


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="The managed Windows Python event loop hangs; this test runs in Linux CI.",
)
@pytest.mark.anyio
async def test_application_lifespan_disposes_database_engine_on_shutdown(monkeypatch):
    disposed = False

    def mark_disposed():
        nonlocal disposed
        disposed = True

    monkeypatch.setattr(main, "dispose_engine", mark_disposed)

    async with main.lifespan(FastAPI()):
        assert disposed is False

    assert disposed is True
