# 海外新闻视频工厂 · MVP（预留完整方案）

依据 [MVP 方案](https://raw.githubusercontent.com/canbaoSama/Architect_Reading/cursor/-bc-0dbc0e19-cd94-4011-a204-319afb7995e0-7fcd/docs/AI%E5%B7%A5%E5%85%B7/%E6%B5%B7%E5%A4%96%E6%96%B0%E9%97%BB%E8%A7%86%E9%A2%91%E5%B7%A5%E5%8E%82-MVP%E6%96%B9%E6%A1%88.md) 与 [完整方案](https://raw.githubusercontent.com/canbaoSama/Architect_Reading/cursor/-bc-0dbc0e19-cd94-4011-a204-319afb7995e0-7fcd/docs/AI%E5%B7%A5%E5%85%B7/%E6%B5%B7%E5%A4%96%E6%96%B0%E9%97%BB%E8%A7%86%E9%A2%91%E5%B7%A5%E5%8E%82-%E5%AE%8C%E6%95%B4%E6%96%B9%E6%A1%88.md) 搭建：**先做 MVP 闭环**，数据模型与状态机按完整方案命名，便于后续接 PostgreSQL、Redis 队列、多 Worker、QClaw 前台。

## 架构分层（避免把「运营入口」当成「生产内核」）

| 层级 | 职责 | 本仓库实现 |
|------|------|------------|
| 自然语言运营层 | 解析「今天候选」「做第 N 条」等命令，展示状态 | **Vue 聊天页**（HTTP）；与文档中的 Telegram/飞书 Bot **同级**，只是传输协议不同 |
| 任务编排与状态层 | 任务状态、落库、管线调度 | **FastAPI** + SQLAlchemy；路由位于 `backend/app/api/v1/`，领域逻辑在 `backend/app/services/` |
| 生产步骤 | 抓取、抽取、脚本、素材、TTS、字幕、渲染 | `pipeline.py` 串行 MVP；完整方案可拆为 `backend/workers/` 下独立进程 |

同一套 **`/api/v1/*`** 可被 Web、Bot、QClaw 连接器调用；不要在业务逻辑里写死「只有 Web」。

## 目录结构

| 路径 | 说明 |
|------|------|
| `backend/app/api/v1/` | HTTP 路由（health / ingest / candidates / jobs / commands） |
| `backend/app/services/` | 采集、抽取、脚本、素材、TTS/字幕占位、渲染占位 |
| `backend/workers/` | 预留：worker-news / worker-render 等（当前为空占位） |
| `src/` | Vue 运营入口（`components/`、`constants/`、`pages/` 等） |
| `remotion-template/` | 35s 竖屏模板占位 |
| `scripts/dev.sh` | 打印本地双终端启动说明 |
| `docker-compose.yml` | PostgreSQL + Redis（完整方案；MVP 默认 SQLite 可不启） |

## 已实现（MVP）

- 表：`news_sources`、`news_items`、`video_jobs`、`scripts`、`job_assets`、`audio_outputs`、`video_outputs`、`command_logs`，以及完整方案预留的 **`subtitle_timelines`**、**`review_logs`**
- RSS：美国时政默认组合（`us_news_sources_catalog.py`）；环境变量 `NEWS_SOURCE_SLUGS` 逗号分隔 slug 自选；`GET /api/v1/sources/catalog` 列出全部可选源
- `POST /api/v1/ingest/trigger`；`GET /api/v1/candidates`
- `GET /api/v1/jobs/{id}`；**`GET /api/v1/jobs/{id}/detail`**（脚本版本、音频/视频产物、字幕表 id，供运营面板与后续 QClaw 拉齐上下文）
- 聊天命令：`POST /api/v1/commands`（`command_parser.py`）
- 管线：`pipeline.py`，状态含 `scoring_candidate`，字幕写入 **表 + JobAsset 文件** 双写
- 脚本：未配置 `OPENAI_API_KEY` 时 mock；配置后走 OpenAI 兼容接口

## 未实现（按完整方案后续接）

- Playwright 截图、真实 TTS、Whisper 对齐、Remotion 导出 mp4、对象存储签名 URL
- Celery/RQ、`backend/workers/*` 实装、QClaw 连接器服务

## 本地运行

查看一键说明：

```bash
./scripts/dev.sh
```

**终端 A — 后端**

```bash
cd backend
./run.sh
```

**终端 B — 前端**

```bash
npm install
npm run dev
```

浏览器打开 Vite 地址；先 **「抓取新闻」**，再 **「今天候选」** → **「做第1条」** → **「开始渲染」**。有当前任务时，页面内 **任务面板** 会轮询 `GET /api/v1/jobs/{id}/detail`。

## API 摘要

- `GET /api/v1/health`
- `GET /api/v1/sources/catalog`
- `POST /api/v1/ingest/trigger`
- `GET /api/v1/candidates`
- `POST /api/v1/jobs/from-candidate?index=1`
- `POST /api/v1/jobs/{id}/pipeline`
- `GET /api/v1/jobs/{id}`
- **`GET /api/v1/jobs/{id}/detail`**
- `POST /api/v1/commands` body: `{ "message": "...", "active_job_id": null }`

## Remotion（可选）

```bash
cd remotion-template
npm install
npm run preview
```

正式导出需本机 Node + 后续把 `backend/data/outputs/job_*` 的脚本与素材接到 Composition props。
