#!/usr/bin/env bash
set -u -o pipefail
cd "$(dirname "$0")/.."
LOG=live_check.log
: > "$LOG"
say() { echo "$@" | tee -a "$LOG"; }

say "== 1. модель доступна?"
if ! python3 - <<'PY' 2>&1 | tee -a "$LOG"
from agent.cli import load_dotenv, make_model
from pathlib import Path
import argparse
load_dotenv(Path(".env"))
model, info = make_model(argparse.Namespace(scripted=None))
print("модель:", info)
response = model.complete([{"role": "user", "content": "Ответь одним словом: работает?"}], [])
print("ответ:", response.content, "usage:", response.usage)
PY
then
  say "ОШИБКА: модель не отвечает, проверь .env"
  exit 1
fi
say ""

say "== 2. один настоящий run: скилл + MCP + запись файла"
START=$(date +%s)
python3 -m agent run "Выгрузи из трекера задач все открытые задачи: сохрани их ID JSON-массивом в файл open-tasks.json." \
  --mode auto --no-plan 2>&1 | tee -a "$LOG"
say "время run: $(( $(date +%s) - START )) с"
RUN_ID=$(grep -o 'run_id: [0-9a-f-]*' "$LOG" | tail -1 | cut -d' ' -f2)
say "RUN_ID=$RUN_ID"
say "--- open-tasks.json:"
cat workspace/open-tasks.json 2>&1 | tee -a "$LOG"
say ""
if [ -n "$RUN_ID" ]; then
  python3 -m agent rollback "$RUN_ID" 2>&1 | tee -a "$LOG"
fi

say "== 3. пробный eval trial (c1, A и B по одному)"
START=$(date +%s)
python3 -m agent eval run evals/suite.json --out evals/results/smoke --only c1_open_tasks_mcp --trials 1 2>&1 | tee -a "$LOG"
say "время eval: $(( $(date +%s) - START )) с"
say "== готово"
