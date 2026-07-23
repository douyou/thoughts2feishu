#!/usr/bin/env bash
# 飞书导入：单进程，禁止并发
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck disable=SC1091
source "$ROOT/scripts/_load_env.sh"

ARGS=("$@")
if [ ${#ARGS[@]} -eq 0 ]; then
  ARGS=(--skip-probe)
fi

echo "$(date '+%F %T') import start root=${FEISHU_ROOT_DIR} parent=${FEISHU_PARENT_WIKI_TOKEN}"
exec caffeinate -i python3 -u "$ROOT/feishu_import_v2.py" "${ARGS[@]}"
