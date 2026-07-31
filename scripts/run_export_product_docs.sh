#!/usr/bin/env bash
# 产品文档导出（仅导出，带日志）
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
ENV_FILE="$ROOT/scripts/migrate_product_docs.env"
LOG="$ROOT/product_docs_export.log"
FAIL_LIST="$ROOT/tmp_export_product_docs/北京万联易达科技有限公司/生态营销文档库/下载失败的文件清单.txt"

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

if [ -z "${THOUGHTS_COOKIE:-}" ] && [ -f .thoughts_cookie ]; then
  THOUGHTS_COOKIE="$(tr -d '\n' < .thoughts_cookie)"
  export THOUGHTS_COOKIE
fi

URL="${THOUGHTS_WORKSPACE_URL:-https://thoughts.aliyun.com/workspaces/6916f2c189ba36001b1009ee/overview}"
EXPORT_BIN="${THOUGHTS_EXPORT_BIN:-$ROOT/thoughtsexport/bin/export_with_cookie}"

exec >>"$LOG" 2>&1
echo "$(date '+%F %T') === EXPORT START ==="
echo "url=$URL"
echo "cookie_len=${#THOUGHTS_COOKIE}"

if [ -z "${THOUGHTS_COOKIE:-}" ]; then
  echo "ERROR: missing THOUGHTS_COOKIE"
  exit 1
fi

cd "$(dirname "$EXPORT_BIN")"
caffeinate -i ./export_with_cookie "$URL" docx
EC=$?
echo "$(date '+%F %T') === EXPORT END exit=$EC ==="

# stats
EXPORT_ROOT="$ROOT/tmp_export_product_docs"
if [ -d "$EXPORT_ROOT" ]; then
  DOCX=$(find "$EXPORT_ROOT" -name '*.docx' 2>/dev/null | wc -l | tr -d ' ')
  SIZE=$(du -sh "$EXPORT_ROOT" 2>/dev/null | awk '{print $1}')
  echo "docx_count=$DOCX size=$SIZE"
fi
if [ -f "$FAIL_LIST" ]; then
  FAILS=$(grep -c '获取下载链接失败\|下载失败' "$FAIL_LIST" 2>/dev/null || echo 0)
  echo "fail_list_lines=$FAILS"
  echo "--- fail list ---"
  cat "$FAIL_LIST"
fi
echo "$EC" > "$ROOT/product_docs_export.exit"
