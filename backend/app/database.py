from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import settings


def _migrate_sqlite_video_jobs(sync_conn) -> None:
    """SQLite 已有库不会自动加列；与 create_all 同事务补列。"""
    if sync_conn.dialect.name != "sqlite":
        return
    r = sync_conn.exec_driver_sql("PRAGMA table_info(video_jobs)")
    cols = [row[1] for row in r.fetchall()]
    if cols and "failed_stage" not in cols:
        sync_conn.exec_driver_sql("ALTER TABLE video_jobs ADD COLUMN failed_stage VARCHAR(48)")


def _migrate_sqlite_news_items_last_seen(sync_conn) -> None:
    if sync_conn.dialect.name != "sqlite":
        return
    r = sync_conn.exec_driver_sql("PRAGMA table_info(news_items)")
    cols = [row[1] for row in r.fetchall()]
    if cols and "last_seen_at" not in cols:
        sync_conn.exec_driver_sql("ALTER TABLE news_items ADD COLUMN last_seen_at DATETIME")


class Base(DeclarativeBase):
    pass


engine = create_async_engine(
    settings.database_url,
    echo=settings.debug,
)

SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with SessionLocal() as session:
        yield session


async def init_db() -> None:
    from app import models  # noqa: F401 — 注册模型

    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.assets_dir.mkdir(parents=True, exist_ok=True)
    settings.outputs_dir.mkdir(parents=True, exist_ok=True)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_migrate_sqlite_video_jobs)
        await conn.run_sync(_migrate_sqlite_news_items_last_seen)
