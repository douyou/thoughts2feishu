#!/usr/bin/env bash
# 兼容入口：请改用 scripts/run_import.sh + .env
# 本文件不再内置任何 App Secret。
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
exec "$ROOT/scripts/run_import.sh" "$@"
