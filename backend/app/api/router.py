from fastapi import APIRouter

from app.api.routes import clips, health, jobs, legal, publishing

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(jobs.router)
api_router.include_router(clips.router)
api_router.include_router(publishing.router)
api_router.include_router(legal.router)
