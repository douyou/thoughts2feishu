#!/usr/bin/env python3
"""批量将知识库节点设为 L2 密级。

用户身份来源（按优先级）：
  1. FEISHU_USER_ACCESS_TOKEN / .feishu_user_token
  2. 本机 lark-cli 已登录用户（docs:secure_label:write_only）

默认读取 FEISHU_STATE_PATH 中的 nodes + done_files 去重后的 wiki_token。
也可传入 --tokens-file 每行一个 node_token。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import requests

# 允许从 scripts/ 子目录导入仓库根模块
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from feishu_secure_label import (  # noqa: E402
    load_user_access_token,
    resolve_l2_label_id,
    resolve_user_auth_mode,
    set_secure_label_l2,
)

API = "https://open.feishu.cn/open-apis"


def tenant_token(app_id: str, app_secret: str) -> str:
    r = requests.post(
        f"{API}/auth/v3/tenant_access_token/internal",
        json={"app_id": app_id, "app_secret": app_secret},
        timeout=30,
    ).json()
    if r.get("code") != 0:
        raise RuntimeError(r)
    return r["tenant_access_token"]


def collect_tokens(state_path: Path) -> list[str]:
    s = json.loads(state_path.read_text(encoding="utf-8"))
    tokens: set[str] = set()
    for n in (s.get("nodes") or {}).values():
        t = (n or {}).get("wiki_token")
        if t:
            tokens.add(t)
    for t in (s.get("done_files") or {}).values():
        if t:
            tokens.add(t)
    root = s.get("root_token")
    if root:
        tokens.add(root)
    return sorted(tokens)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--state",
        default=os.environ.get("FEISHU_STATE_PATH", "feishu_import_state.json"),
    )
    ap.add_argument("--tokens-file", default="")
    ap.add_argument(
        "--wiki",
        action="append",
        default=[],
        help="单个 wiki node_token，可重复指定",
    )
    args = ap.parse_args()

    app_id = os.environ.get("FEISHU_APP_ID", "")
    app_secret = os.environ.get("FEISHU_APP_SECRET", "")
    if not app_id or not app_secret:
        sys.exit("需要 FEISHU_APP_ID / FEISHU_APP_SECRET")

    user = load_user_access_token()
    auth_mode = resolve_user_auth_mode(user)
    if auth_mode == "none":
        sys.exit(
            "需要用户身份：FEISHU_USER_ACCESS_TOKEN / .feishu_user_token，"
            "或 lark-cli 用户登录（docs:secure_label:write_only）"
        )

    try:
        l2_id = resolve_l2_label_id()
    except RuntimeError as e:
        sys.exit(str(e))

    if args.tokens_file:
        tokens = [
            ln.strip()
            for ln in Path(args.tokens_file).read_text(encoding="utf-8").splitlines()
            if ln.strip() and not ln.startswith("#")
        ]
    elif args.wiki:
        tokens = args.wiki
    else:
        tokens = collect_tokens(Path(args.state))

    print(f"to_label={len(tokens)} auth={auth_mode} l2={l2_id}", flush=True)
    tenant = tenant_token(app_id, app_secret)
    ok = fail = 0
    fails: list[tuple[str, str]] = []
    for i, wiki in enumerate(tokens, 1):
        for attempt in range(1, 4):
            try:
                set_secure_label_l2(
                    tenant,
                    wiki,
                    l2_id=l2_id,
                    user_token=user,
                    auth_mode=auth_mode,
                )
                print(f"OK {wiki}", flush=True)
                ok += 1
                break
            except Exception as e:
                if attempt == 3:
                    fail += 1
                    fails.append((wiki, str(e)[:200]))
                    print(f"FAIL {wiki} {e}", flush=True)
                else:
                    time.sleep(attempt * 2)
        if i % 50 == 0:
            print(f"progress {i}/{len(tokens)} ok={ok} fail={fail}", flush=True)
        time.sleep(0.08)
    print(f"DONE ok={ok} fail={fail}")
    for w, e in fails[:20]:
        print("FAIL", w, e)
    if fail:
        sys.exit(1)


if __name__ == "__main__":
    main()
