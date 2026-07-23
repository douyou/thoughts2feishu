#!/usr/bin/env bash
# 兼容入口：请改用 scripts/run_export_thoughts.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
URL="${1:-}"
if [ -z "$URL" ]; then
  echo "用法: $0 '<Thoughts folders URL>' [docx]" >&2
  exit 1
fi
exec "$ROOT/scripts/run_export_thoughts.sh" "$URL" "${2:-docx}"
