from fastapi import APIRouter

from app.api.v1 import candidates, commands, health, ingest, jobs, sources

router = APIRouter(prefix="/api/v1")
for mod in (health, ingest, sources, candidates, jobs, commands):
    router.include_router(mod.router)
