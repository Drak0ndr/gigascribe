#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
if [[ ! -f .venv/bin/activate ]]; then
  echo 'Сначала установите зависимости по инструкции в README.md'
  exit 1
fi
source .venv/bin/activate
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
