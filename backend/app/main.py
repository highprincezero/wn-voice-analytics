import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.router import api_router
from app.db.init_db import init_database
from app.observability.tracing import setup_telemetry
from app.storage.blob import ensure_blob_container
from app.telemetry import init_telemetry

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")


# Lifespan handler: code before `yield` runs once at startup, code after it at shutdown.
# @asynccontextmanager turns this async generator into the context manager FastAPI needs.
@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_telemetry()
    init_database()
    ensure_blob_container()
    yield


# App factory: builds and configures the FastAPI instance.
def create_app() -> FastAPI:
    # FastAPI(...): the ASGI app; lifespan= hooks in the startup/shutdown function above.
    application = FastAPI(title="Audio Analytics", version="0.1.0", lifespan=lifespan)
    # Optional OpenTelemetry tracing of every request (enabled via settings).
    setup_telemetry(application)
    # Mounts all API routes (prefixed /api/v1) from api/router.py.
    application.include_router(api_router)

    @application.get("/")
    def root() -> dict:
        return {"service": "voice-analytics", "docs": "/docs"}

    return application


# Module-level `app` is what uvicorn loads (app.main:app).
app = create_app()
