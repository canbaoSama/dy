#!/usr/bin/env bash
# 本地双进程说明（MVP）：生产内核 FastAPI + 运营入口 Vite。
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
echo "项目根: $ROOT"
echo ""
echo "终端 A — 后端:"
echo "  cd \"$ROOT/backend\" && ./run.sh"
echo ""
echo "终端 B — 前端:"
echo "  cd \"$ROOT\" && npm install && npm run dev"
echo ""
