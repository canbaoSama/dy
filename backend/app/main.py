from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.database import SessionLocal, engine, init_db
from app.api.v1.router import router
from app.services.job_maintenance import recover_stuck_jobs


@asynccontextmanager
async def lifespan(_app: FastAPI):
    await init_db()
    async with SessionLocal() as session:
        await recover_stuck_jobs(
            session,
            stale_minutes=settings.job_stuck_recover_minutes,
            reason_prefix="服务重启时检测到任务卡住，已自动恢复为失败",
        )
    yield
    await engine.dispose()


app = FastAPI(title=settings.app_name, lifespan=lifespan)

origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins or ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)
