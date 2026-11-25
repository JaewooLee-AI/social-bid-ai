#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

MODE="${1:-webview}"

if [ "$MODE" = "build-ui" ]; then
  cd frontend && npm run build
  exit 0
fi

if [ "$MODE" = "dev" ]; then
  echo "[1/2] FastAPI Dev Server 시작 (http://127.0.0.1:8000)"
  .venv/bin/python -m app.main --dev-server --port 8000 &
  BACKEND_PID=$!
  trap 'kill $BACKEND_PID 2>/dev/null || true' EXIT
  echo "[2/2] Vite Dev Server 시작 (http://127.0.0.1:5173)"
  cd frontend && npm run dev
  exit 0
fi

if [ ! -f "frontend/ui_dist/index.html" ]; then
  echo "frontend/ui_dist가 없어 먼저 빌드합니다..."
  (cd frontend && npm run build)
fi

echo "PyWebview 데스크톱 앱을 실행합니다..."
exec .venv/bin/python -m app.main
