#!/usr/bin/env bash
# 已废弃：分批 + 多进程续跑容易导致飞书同名重复。
# 请使用: ./scripts/run_import.sh --skip-probe
echo "请改用 ./scripts/run_import.sh（单进程）。旧的分批续跑方式已废弃（易产生重复文档）。" >&2
exit 1
