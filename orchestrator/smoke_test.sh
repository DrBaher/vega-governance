#!/usr/bin/env bash
# Smoke test: scaffold a temp VEGA deployment and verify the structure.
#
# Usage: ./smoke_test.sh
#
# Honors $PYTHON env var; falls back to `python3` then `python`.

set -euo pipefail

PYTHON="${PYTHON:-}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
if [ -z "$PYTHON" ]; then
  # Prefer a .venv next to the script so deps from requirements.txt are available.
  if [ -x "$SCRIPT_DIR/.venv/bin/python" ]; then
    PYTHON="$SCRIPT_DIR/.venv/bin/python"
  elif command -v python3 >/dev/null 2>&1; then
    PYTHON=python3
  elif command -v python >/dev/null 2>&1; then
    PYTHON=python
  else
    echo "[smoke] ✗ Neither python3 nor python found in PATH. Set PYTHON env var." >&2
    exit 1
  fi
fi

TMPDIR="$(mktemp -d)"
echo "[smoke] using temp dir: $TMPDIR (python: $PYTHON)"

# Copy this orchestrator into a fake project tree
PROJECT="$TMPDIR/vega"
mkdir -p "$PROJECT/orchestrator"
cp -R "$(dirname "$0")"/* "$PROJECT/orchestrator/"

# Minimal config — point everything at the temp project
cat > "$PROJECT/orchestrator/config.py" <<EOF
from pathlib import Path
PROJECT_NAME = "smoke"
DEPLOY_DATE = "2026-01-01"
BASE_DIR = Path("$PROJECT")
AGENTS_DIR = str(BASE_DIR / "agents")
UNIVERSAL_DIR = str(BASE_DIR / "universal")
SCOPE_DIR = str(BASE_DIR / "scope")
FRAMEWORK_DIR = str(BASE_DIR / "framework")
ARTIFACTS_DIR = str(BASE_DIR / "artifacts" / "archive")
CYCLES_DIR = str(BASE_DIR / "cycles" / "active")
OP_BACKLOG_DIR = str(BASE_DIR / "op_backlog")
STATE_DIR = str(BASE_DIR / "state")
TEST_MODELS_FULL_DIR = str(BASE_DIR / "test_models" / "full")
TEST_MODELS_BUILD_DIR = str(BASE_DIR / "test_models" / "build")
AGENTS = ["SG", "SA", "SE", "TG", "TA", "TE", "BR", "BTA", "SYS"]
AGENT_MODEL = "claude-sonnet-4-6"
AGENT_MODEL_OVERRIDES = {}
EXTENDED_THINKING_ENABLED = True
THINKING_BUDGET_TOKENS = 1000
PROMPT_CACHING_ENABLED = True
MAX_TOKENS = 1000
POLL_INTERVAL = 10
SYS_SCHEDULE = "daily"
SYS_DAILY_TIME = "02:00"
SYS_EXECUTION_THRESHOLD = 20
CYCLE_CONTEXT_WARNING = 0.4
WIKI_REPLACE_THRESHOLD_DEFAULT = 10
WIKI_REPLACE_THRESHOLDS = {}
CORTEX_ENABLED = False
CORTEX_SCAN_THRESHOLD = 15
EXT_BUILD_ENABLED = True
ANTHROPIC_API_KEY = ""
TELEGRAM_BOT_TOKEN = ""
TELEGRAM_OP_CHAT_ID = ""
EOF

cd "$PROJECT/orchestrator"

echo "[smoke] running --init-only…"
"$PYTHON" main.py --init-only

echo "[smoke] expected directories:"
for d in agents universal scope test_models artifacts/archive cycles/active op_backlog/pending state; do
  if [ -d "$PROJECT/$d" ]; then
    echo "  ✓ $d"
  else
    echo "  ✗ MISSING $d"
    exit 1
  fi
done

echo "[smoke] running pytest…"
"$PYTHON" -m pytest -x -q || exit 1

echo "[smoke] ✓ all checks passed"
echo "[smoke] cleaning up $TMPDIR"
rm -rf "$TMPDIR"
