#!/usr/bin/env bash
# 从仓库根目录加载 .env（不打印密钥）
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [ ! -f .env ]; then
  echo "缺少 .env，请先: cp .env.example .env 并填写凭证" >&2
  exit 1
fi

set -a
# shellcheck disable=SC1091
source .env
set +a

# 兼容：token 也可放在独立文件（勿提交）
if [ -z "${FEISHU_USER_ACCESS_TOKEN:-}" ] && [ -f .feishu_user_token ]; then
  FEISHU_USER_ACCESS_TOKEN="$(tr -d '\n' < .feishu_user_token)"
  export FEISHU_USER_ACCESS_TOKEN
fi
if [ -z "${THOUGHTS_COOKIE:-}" ] && [ -f .thoughts_cookie ]; then
  THOUGHTS_COOKIE="$(tr -d '\n' < .thoughts_cookie)"
  export THOUGHTS_COOKIE
fi
