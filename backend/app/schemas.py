from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class CandidateItem(BaseModel):
    index: int
    title: str
    title_zh: str | None = None
    source: str
    score_10: float | None = None
    published_at: datetime | None
    summary: str | None
    summary_zh: str | None = None
    tier: str
    url: str


class JobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    news_item_id: int
    status: str
    duration_sec: int
    style_notes: str | None
    error_message: str | None
    failed_stage: str | None = None
    created_at: datetime
    updated_at: datetime | None = None


class VideoOutputBrief(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    file_path: str | None
    preview_path: str | None
    meta_json: dict[str, Any] | None = None


class AudioOutputBrief(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    file_path: str
    duration_sec: float | None
    meta_json: dict[str, Any] | None = None


class JobDetailOut(BaseModel):
    """运营面板 / 未来 QClaw 连接器：单请求拉齐任务与产物元数据。"""

    job: JobOut
    latest_script: dict[str, Any] | None = None
    latest_script_version: int | None = None
    audios: list[AudioOutputBrief] = []
    videos: list[VideoOutputBrief] = []
    subtitle_timeline_id: int | None = None


class CommandRequest(BaseModel):
    message: str
    active_job_id: int | None = None


class CommandResponse(BaseModel):
    reply: str
    active_job_id: int | None = None
    candidates: list[CandidateItem] | None = None
    job: JobOut | None = None
