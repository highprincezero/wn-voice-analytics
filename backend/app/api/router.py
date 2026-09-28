from fastapi import APIRouter

from app.api.routes import auth, files, meta, prompts, summaries

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(auth.router)
api_router.include_router(files.router)
api_router.include_router(prompts.router)
api_router.include_router(summaries.router)
api_router.include_router(meta.router)
