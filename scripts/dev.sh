#!/bin/bash
cd "$(dirname "$0")/.."
source .venv/bin/activate
echo "Starting LifeOS Voice Server at http://localhost:8000"
LIFEOS_LOG_LEVEL=DEBUG python -m uvicorn server.main:app --host 0.0.0.0 --port 8000 --reload --log-level debug

