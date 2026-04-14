from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class JobStatus(str, enum.Enum):
    """与《完整方案》任务编排层对齐，MVP 先实现子集流转。"""

    created = "created"
    fetching_news = "fetching_news"
    extracting_content = "extracting_content"
    scoring_candidate = "scoring_candidate"
    generating_script = "generating_script"
    collecting_assets = "collecting_assets"
    generating_audio = "generating_audio"
    building_timeline = "building_timeline"
    rendering_video = "rendering_video"
    ready_for_review = "ready_for_review"
    approved = "approved"
    failed = "failed"


class CandidateTier(str, enum.Enum):
    recommended = "可做"
    neutral = "一般"
    not_recommended = "不建议"


class NewsSource(Base):
    __tablename__ = "news_sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    rss_url: Mapped[str] = mapped_column(String(512), nullable=False)
    enabled: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    items: Mapped[list[NewsItem]] = relationship(back_populates="source")


class NewsItem(Base):
    __tablename__ = "news_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("news_sources.id"), nullable=False)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    url: Mapped[str] = mapped_column(String(1024), nullable=False, unique=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    summary_one_liner: Mapped[str | None] = mapped_column(String(1024))
    content_raw: Mapped[str | None] = mapped_column(Text)
    cleaned_content: Mapped[str | None] = mapped_column(Text)
    hero_image_url: Mapped[str | None] = mapped_column(String(2048))
    page_screenshot_path: Mapped[str | None] = mapped_column(String(1024))
    url_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    candidate_tier: Mapped[str] = mapped_column(String(32), default=CandidateTier.neutral.value)
    score_json: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # 各信源 RSS 每次抓取到该链接时刷新，用于「今日热点」排序（避免库里永远只按旧稿发布时间置顶）
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    source: Mapped[NewsSource] = relationship(back_populates="items")
    jobs: Mapped[list[VideoJob]] = relationship(back_populates="news_item")


class VideoJob(Base):
    __tablename__ = "video_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    news_item_id: Mapped[int] = mapped_column(ForeignKey("news_items.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(48), default=JobStatus.created.value)
    duration_sec: Mapped[int] = mapped_column(Integer, default=35)
    style_notes: Mapped[str | None] = mapped_column(String(512))
    error_message: Mapped[str | None] = mapped_column(Text)
    failed_stage: Mapped[str | None] = mapped_column(String(48), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    news_item: Mapped[NewsItem] = relationship(back_populates="jobs")
    scripts: Mapped[list[ScriptRecord]] = relationship(
        back_populates="job",
        order_by="ScriptRecord.version",
    )
    assets: Mapped[list[JobAsset]] = relationship(back_populates="job")
    audios: Mapped[list[AudioOutput]] = relationship(back_populates="job")
    videos: Mapped[list[VideoOutput]] = relationship(back_populates="job")
    subtitle_timelines: Mapped[list["SubtitleTimeline"]] = relationship(
        back_populates="job",
        order_by="SubtitleTimeline.id",
    )
    review_logs: Mapped[list["ReviewLog"]] = relationship(
        back_populates="job",
        order_by="ReviewLog.id",
    )


class ScriptRecord(Base):
    """对应文档 scripts 表；payload 为 JSON（hook/body/ending/titles 等）。"""

    __tablename__ = "scripts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("video_jobs.id"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    job: Mapped[VideoJob] = relationship(back_populates="scripts")


class JobAsset(Base):
    __tablename__ = "job_assets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("video_jobs.id"), nullable=False)
    asset_type: Mapped[str] = mapped_column(String(64), nullable=False)
    local_path: Mapped[str | None] = mapped_column(String(1024))
    remote_url: Mapped[str | None] = mapped_column(String(2048))
    meta_json: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    job: Mapped[VideoJob] = relationship(back_populates="assets")


class AudioOutput(Base):
    __tablename__ = "audio_outputs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("video_jobs.id"), nullable=False)
    file_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    duration_sec: Mapped[float | None] = mapped_column()
    meta_json: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    job: Mapped[VideoJob] = relationship(back_populates="audios")


class VideoOutput(Base):
    __tablename__ = "video_outputs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("video_jobs.id"), nullable=False)
    file_path: Mapped[str | None] = mapped_column(String(1024))
    preview_path: Mapped[str | None] = mapped_column(String(1024))
    meta_json: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    job: Mapped[VideoJob] = relationship(back_populates="videos")


class SubtitleTimeline(Base):
    """完整方案《数据设计》subtitle_timelines；MVP 与 JobAsset 字幕文件双写便于 Remotion 读盘。"""

    __tablename__ = "subtitle_timelines"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("video_jobs.id"), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), default="stub")
    timeline_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    job: Mapped[VideoJob] = relationship(back_populates="subtitle_timelines")


class ReviewLog(Base):
    """审核动作流水；MVP 仅占位，完整方案对接「通过 / 换图 / 重渲染」。"""

    __tablename__ = "review_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("video_jobs.id"), nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    detail: Mapped[str | None] = mapped_column(Text)
    meta_json: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    job: Mapped[VideoJob] = relationship(back_populates="review_logs")


class CommandLog(Base):
    __tablename__ = "command_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    raw_text: Mapped[str] = mapped_column(Text, nullable=False)
    parsed_json: Mapped[dict | None] = mapped_column(JSON)
    result_summary: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
