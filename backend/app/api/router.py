from fastapi import APIRouter

from app.api.routes import auth, chat, events, files, meta, prompts, summaries

# Top-level router: prefix="/api/v1" is prepended to every included route.
api_router = APIRouter(prefix="/api/v1")
# include_router mounts each feature router (e.g. /files -> /api/v1/files).
api_router.include_router(auth.router)
api_router.include_router(events.router)
api_router.include_router(files.router)
api_router.include_router(prompts.router)
api_router.include_router(summaries.router)
api_router.include_router(chat.router)
api_router.include_router(meta.router)
