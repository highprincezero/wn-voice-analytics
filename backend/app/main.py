import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.router import api_router
from app.db.init_db import init_database
from app.observability.tracing import setup_telemetry
from app.storage.blob import ensure_blob_container

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_database()
    ensure_blob_container()
    yield


def create_app() -> FastAPI:
    application = FastAPI(title="Voice Analytics", version="0.1.0", lifespan=lifespan)
    setup_telemetry(application)
    application.include_router(api_router)

    @application.get("/")
    def root() -> dict:
        return {"service": "voice-analytics", "docs": "/docs"}

    return application


app = create_app()
