#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

export PYTHONPATH=apps
export PORT=${PORT:-9001}

echo "Starting agent service on :$PORT"
exec uvicorn apps.agent.agent_service:app --reload --port "$PORT"


