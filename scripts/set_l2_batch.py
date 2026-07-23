#!/usr/bin/env python3
"""批量将知识库节点设为 L2 密级（需 FEISHU_USER_ACCESS_TOKEN）。

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

API = "https://open.feishu.cn/open-apis"
L2 = os.environ.get("FEISHU_SECURE_LABEL_L2_ID", "").strip()


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


def set_l2(tenant: str, user: str, wiki: str) -> None:
    th = {"Authorization": f"Bearer {tenant}"}
    uh = {"Authorization": f"Bearer {user}"}
    gn = requests.get(
        f"{API}/wiki/v2/spaces/get_node",
        headers=th,
        params={"token": wiki},
        timeout=60,
    ).json()
    if gn.get("code") != 0:
        raise RuntimeError(f"get_node {gn}")
    node = gn["data"]["node"]
    obj = node.get("obj_token") or ""
    typ = node.get("obj_type") or "docx"
    if not obj:
        raise RuntimeError("no obj_token")
    resp = requests.patch(
        f"{API}/drive/v2/files/{obj}/secure_label",
        headers=uh,
        params={"type": typ},
        json={"id": L2},
        timeout=60,
    ).json()
    if resp.get("code") != 0:
        raise RuntimeError(resp)
    print(f"OK {wiki} {node.get('title')}", flush=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--state",
        default=os.environ.get("FEISHU_STATE_PATH", "feishu_import_state.json"),
    )
    ap.add_argument("--tokens-file", default="")
    args = ap.parse_args()

    app_id = os.environ.get("FEISHU_APP_ID", "")
    app_secret = os.environ.get("FEISHU_APP_SECRET", "")
    user = os.environ.get("FEISHU_USER_ACCESS_TOKEN", "").strip()
    if not app_id or not app_secret:
        sys.exit("需要 FEISHU_APP_ID / FEISHU_APP_SECRET")
    if not user:
        sys.exit("需要 FEISHU_USER_ACCESS_TOKEN（密级只能用户身份写入）")
    if not L2:
        sys.exit("需要 FEISHU_SECURE_LABEL_L2_ID")

    if args.tokens_file:
        tokens = [
            ln.strip()
            for ln in Path(args.tokens_file).read_text(encoding="utf-8").splitlines()
            if ln.strip() and not ln.startswith("#")
        ]
    else:
        tokens = collect_tokens(Path(args.state))

    print(f"to_label={len(tokens)}")
    tenant = tenant_token(app_id, app_secret)
    ok = fail = 0
    fails: list[tuple[str, str]] = []
    for i, wiki in enumerate(tokens, 1):
        for attempt in range(1, 4):
            try:
                set_l2(tenant, user, wiki)
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


if __name__ == "__main__":
    main()
