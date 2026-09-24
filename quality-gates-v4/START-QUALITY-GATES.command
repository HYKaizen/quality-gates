#!/bin/zsh
set -e
cd "$(dirname "$0")"

if curl -fsS http://127.0.0.1:8040/ >/dev/null 2>&1; then
  open http://127.0.0.1:8040/
  exit 0
fi

PYTHONDONTWRITEBYTECODE=1 python3 -B server.py &
quality_gates_pid=$!
sleep 1
open http://127.0.0.1:8040/
wait "$quality_gates_pid"
