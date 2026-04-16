from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


_BACKEND_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_DB = _BACKEND_ROOT / "data" / "factory.db"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "overseas-news-video-factory"
    debug: bool = False

    # SQLite（MVP）；完整方案可换 postgresql+asyncpg，见 .env.example
    database_url: str = f"sqlite+aiosqlite:///{_DEFAULT_DB.as_posix()}"

    # 预留：Redis / 队列（完整方案 Celery/RQ）
    redis_url: str | None = None

    # 脚本生成：未配置时使用本地 mock JSON
    openai_api_key: str | None = None
    openai_base_url: str | None = None
    script_model: str = "gpt-4o-mini"

    # TTS 预留（Azure / MiniMax / Edge 等）
    tts_provider: str = "stub"

    # 产物目录
    data_dir: Path = _BACKEND_ROOT / "data"
    assets_dir: Path = _BACKEND_ROOT / "data" / "assets"
    outputs_dir: Path = _BACKEND_ROOT / "data" / "outputs"

    # CORS（Vue 开发服）
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    # 新闻 RSS：逗号分隔 slug，见 us_news_sources_catalog.SOURCE_CATALOG；空=美国时政默认组合
    news_source_slugs: str = ""

    # 出网代理（用于 RSS 抓取）；示例：http://127.0.0.1:7890
    rss_proxy_url: str | None = None
    rss_fetch_timeout_seconds: float = 10.0
    rss_fetch_retry_count: int = 1
    job_stuck_recover_minutes: int = 8


settings = Settings()
