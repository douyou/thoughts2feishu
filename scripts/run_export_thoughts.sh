#!/usr/bin/env bash
# 云效 Thoughts 导出到本地目录（依赖 thoughtsexport 工具）
#
# 用法:
#   ./scripts/run_export_thoughts.sh '<folders URL>' [docx|html|all]
#
# 示例:
#   ./scripts/run_export_thoughts.sh \
#     'https://thoughts.aliyun.com/workspaces/<wsId>/folders/<folderId>' \
#     docx
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck disable=SC1091
source "$ROOT/scripts/_load_env.sh"

URL="${1:?请传入 Thoughts folders/docs URL}"
FILE_TYPE="${2:-docx}"

THOUGHTS_EXPORT_BIN="${THOUGHTS_EXPORT_BIN:-$ROOT/thoughtsexport/bin/export_with_cookie}"
if [ ! -x "$THOUGHTS_EXPORT_BIN" ]; then
  echo "找不到导出工具: $THOUGHTS_EXPORT_BIN" >&2
  echo "请先: (cd thoughtsexport && go build -o bin/export_with_cookie ./cmd/export_with_cookie)" >&2
  echo "或设置 THOUGHTS_EXPORT_BIN" >&2
  exit 1
fi

if [ -z "${THOUGHTS_COOKIE:-}" ]; then
  echo "缺少 THOUGHTS_COOKIE（写入 .env 或 .thoughts_cookie）" >&2
  exit 1
fi

EXPORT_DIR="$(cd "$(dirname "$THOUGHTS_EXPORT_BIN")" && pwd)"
LOG="$ROOT/thoughts_export.log"
echo "$(date '+%F %T') export start url=$URL type=$FILE_TYPE" | tee -a "$LOG"
(
  cd "$EXPORT_DIR"
  exec caffeinate -i ./export_with_cookie "$URL" "$FILE_TYPE"
) 2>&1 | tee -a "$LOG"
echo "$(date '+%F %T') export finished；请把导出目录配置到 FEISHU_ROOT_DIR 后再导入" | tee -a "$LOG"
