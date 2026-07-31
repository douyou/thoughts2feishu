#!/usr/bin/env bash
# 通用迁移：Thoughts → 飞书知识库
#
# 用法:
#   cp scripts/migrate_xxx.env.example scripts/migrate_xxx.env  # 首次
#   ./scripts/run_migrate.sh scripts/migrate_xxx.env export
#   ./scripts/run_migrate.sh scripts/migrate_xxx.env import
#   ./scripts/run_migrate.sh scripts/migrate_xxx.env all
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [ $# -lt 1 ]; then
  echo "用法: $0 <env-file> export|import|all [import 参数…]" >&2
  exit 1
fi
ENV_FILE="$1"
MODE="${2:-all}"
shift 2 || true

if [ ! -f "$ENV_FILE" ]; then
  echo "缺少 $ENV_FILE" >&2
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

URL="${THOUGHTS_EXPORT_URL:-${THOUGHTS_WORKSPACE_URL:-}}"
EXPORT_BIN="${THOUGHTS_EXPORT_BIN:-$ROOT/thoughtsexport/bin/export_with_cookie}"

abs_path() {
  local p="$1"
  if [[ "$p" = /* ]]; then
    echo "$p"
  else
    echo "$ROOT/$p"
  fi
}

sync_export_to_root_dir() {
  local staging dest name item
  if [ -z "${EXPORT_STAGING_DIR:-}" ] || [ -z "${FEISHU_ROOT_DIR:-}" ]; then
    return 0
  fi
  staging="$(abs_path "$EXPORT_STAGING_DIR")"
  dest="$(abs_path "$FEISHU_ROOT_DIR")"
  if [ ! -d "$staging" ]; then
    echo "staging 不存在: $staging" >&2
    return 1
  fi
  mkdir -p "$dest"
  echo "$(date '+%F %T') sync -> $dest"
  shopt -s nullglob
  for item in "$staging"/*; do
    name="$(basename "$item")"
    [ "$name" = "多式联运" ] && continue
    if [ -d "$item" ]; then
      rsync -a --delete "$item/" "$dest/$name/"
      rm -rf "$item"
    elif [ -f "$item" ]; then
      cp -f "$item" "$dest/$name"
      rm -f "$item"
    fi
  done
  shopt -u nullglob
}

case "$MODE" in
  export)
    if [ -z "${THOUGHTS_COOKIE:-}" ]; then
      echo "缺少 THOUGHTS_COOKIE 或 .thoughts_cookie" >&2
      exit 1
    fi
    if [ -z "$URL" ]; then
      echo "请在 env 中设置 THOUGHTS_EXPORT_URL 或 THOUGHTS_WORKSPACE_URL" >&2
      exit 1
    fi
    echo "$(date '+%F %T') export start url=$URL"
    cd "$(dirname "$EXPORT_BIN")"
    caffeinate -i ./export_with_cookie "$URL" docx
    cd "$ROOT"
    sync_export_to_root_dir
    echo "$(date '+%F %T') export done root=${FEISHU_ROOT_DIR:-}"
    ;;
  import)
    if [ ! -d "$(abs_path "${FEISHU_ROOT_DIR:-}")" ]; then
      echo "FEISHU_ROOT_DIR 不存在: ${FEISHU_ROOT_DIR:-空}" >&2
      exit 1
    fi
    export FEISHU_ROOT_DIR="$(abs_path "$FEISHU_ROOT_DIR")"
    echo "$(date '+%F %T') import start parent=$FEISHU_PARENT_WIKI_TOKEN root=$FEISHU_ROOT_DIR"
    exec caffeinate -i python3 -u "$ROOT/feishu_import_v2.py" --skip-probe "$@"
    ;;
  all)
    "$0" "$ENV_FILE" export
    "$0" "$ENV_FILE" import "$@"
    ;;
  *)
    echo "用法: $0 <env-file> {export|import|all} [import 参数…]" >&2
    exit 1
    ;;
esac
