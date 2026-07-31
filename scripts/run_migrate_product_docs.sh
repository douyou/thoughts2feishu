#!/usr/bin/env bash
# 产品文档迁移：Thoughts 6916f2c1 → 飞书「产品文档」
#
# 用法:
#   1. cp scripts/migrate_product_docs.env.example scripts/migrate_product_docs.env
#   2. 填写 FEISHU_APP_SECRET；确保 .thoughts_cookie 有效
#   3. ./scripts/run_migrate_product_docs.sh export   # 仅导出
#   4. ./scripts/run_migrate_product_docs.sh import   # 仅导入
#   5. ./scripts/run_migrate_product_docs.sh all      # 导出 + 导入
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

ENV_FILE="${MIGRATE_ENV:-$ROOT/scripts/migrate_product_docs.env}"
if [ ! -f "$ENV_FILE" ]; then
  echo "缺少 $ENV_FILE，请先: cp scripts/migrate_product_docs.env.example scripts/migrate_product_docs.env" >&2
  exit 1
fi
set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

if [ -z "${THOUGHTS_COOKIE:-}" ] && [ -f .thoughts_cookie ]; then
  THOUGHTS_COOKIE="$(tr -d '\n' < .thoughts_cookie)"
  export THOUGHTS_COOKIE
fi

MODE="${1:-all}"
URL="${THOUGHTS_WORKSPACE_URL:-https://thoughts.aliyun.com/workspaces/6916f2c189ba36001b1009ee/overview}"
EXPORT_BIN="${THOUGHTS_EXPORT_BIN:-$ROOT/thoughtsexport/bin/export_with_cookie}"

case "$MODE" in
  export)
    if [ -z "${THOUGHTS_COOKIE:-}" ]; then
      echo "缺少 THOUGHTS_COOKIE 或 .thoughts_cookie" >&2
      exit 1
    fi
    mkdir -p "$(dirname "$FEISHU_ROOT_DIR")"
    echo "$(date '+%F %T') export start url=$URL"
    (
      cd "$(dirname "$EXPORT_BIN")"
      exec caffeinate -i ./export_with_cookie "$URL" docx
    )
    echo "$(date '+%F %T') export done; 请确认 FEISHU_ROOT_DIR=$FEISHU_ROOT_DIR"
    ;;
  import)
    echo "$(date '+%F %T') import start parent=$FEISHU_PARENT_WIKI_TOKEN"
    exec caffeinate -i python3 -u "$ROOT/feishu_import_v2.py" --skip-probe "${@:2}"
    ;;
  all)
    "$0" export
    "$0" import "${@:2}"
    ;;
  *)
    echo "用法: $0 {export|import|all} [import 参数…]" >&2
    exit 1
    ;;
esac
