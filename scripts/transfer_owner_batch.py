#!/usr/bin/env python3
"""批量将知识库节点下的云文档所有者从机器人转给指定用户。

官方接口：
  POST /drive/v1/permissions/:token/members/transfer_owner?type=docx
  调用身份必须是当前所有者（导入后通常是应用机器人 → 用 tenant_access_token）。

用法示例：
  source .env
  python3 scripts/transfer_owner_batch.py \\
    --parent EBzqwxCvViqfb7ki4w9clTTYnwk \\
    --dry-run

  python3 scripts/transfer_owner_batch.py \\
    --parent EBzqwxCvViqfb7ki4w9clTTYnwk \\
    --parent <生态营销导入根节点>

环境变量：
  FEISHU_APP_ID / FEISHU_APP_SECRET   必填（机器人须为文档当前所有者）
  FEISHU_NEW_OWNER_OPEN_ID            新所有者 open_id（ou_xxx）
  或 FEISHU_USER_ACCESS_TOKEN         用其解析当前登录用户的 open_id
  FEISHU_SPACE_ID                     可选；不填则从 --parent 的 get_node 推断
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from typing import Any

import requests

API = "https://open.feishu.cn/open-apis"


def tenant_token(app_id: str, app_secret: str) -> str:
    r = requests.post(
        f"{API}/auth/v3/tenant_access_token/internal",
        json={"app_id": app_id, "app_secret": app_secret},
        timeout=30,
    ).json()
    if r.get("code") != 0:
        raise RuntimeError(f"tenant token failed: {r}")
    return r["tenant_access_token"]


def hdr(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def resolve_owner_open_id(user_token: str) -> str:
    r = requests.get(
        f"{API}/authen/v1/user_info",
        headers=hdr(user_token),
        timeout=30,
    ).json()
    if r.get("code") != 0:
        raise RuntimeError(f"user_info failed: {r}")
    oid = (r.get("data") or {}).get("open_id") or ""
    if not oid:
        raise RuntimeError(f"user_info missing open_id: {r}")
    return oid


def get_node(token: str, node_token: str) -> dict[str, Any]:
    r = requests.get(
        f"{API}/wiki/v2/spaces/get_node",
        headers=hdr(token),
        params={"token": node_token},
        timeout=60,
    ).json()
    if r.get("code") != 0:
        raise RuntimeError(f"get_node {node_token}: {r}")
    return r["data"]["node"]


def list_children(token: str, space_id: str, parent: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    page_token = None
    while True:
        params: dict[str, Any] = {"page_size": 50, "parent_node_token": parent}
        if page_token:
            params["page_token"] = page_token
        r = requests.get(
            f"{API}/wiki/v2/spaces/{space_id}/nodes",
            headers=hdr(token),
            params=params,
            timeout=60,
        ).json()
        if r.get("code") != 0:
            raise RuntimeError(f"list children of {parent}: {r}")
        data = r.get("data") or {}
        items.extend(data.get("items") or [])
        if not data.get("has_more"):
            break
        page_token = data.get("page_token")
    return items


def collect_descendants(
    token: str, space_id: str, roots: list[str], include_roots: bool
) -> list[dict[str, Any]]:
    """BFS 收集节点；默认不含 root 本身（避免误转「后端组」等父页面）。"""
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    queue = list(roots)

    if include_roots:
        for rt in roots:
            if rt in seen:
                continue
            seen.add(rt)
            out.append(get_node(token, rt))

    while queue:
        parent = queue.pop(0)
        for n in list_children(token, space_id, parent):
            nt = n.get("node_token") or ""
            if not nt or nt in seen:
                continue
            seen.add(nt)
            out.append(n)
            if n.get("has_child"):
                queue.append(nt)
    return out


def transfer_owner(
    token: str,
    obj_token: str,
    obj_type: str,
    owner_open_id: str,
    *,
    remove_old_owner: bool,
    old_owner_perm: str,
    need_notification: bool,
) -> dict[str, Any]:
    params = {
        "type": obj_type,
        "need_notification": str(need_notification).lower(),
        "remove_old_owner": str(remove_old_owner).lower(),
        "stay_put": "true",
    }
    if not remove_old_owner:
        params["old_owner_perm"] = old_owner_perm
    r = requests.post(
        f"{API}/drive/v1/permissions/{obj_token}/members/transfer_owner",
        headers={**hdr(token), "Content-Type": "application/json; charset=utf-8"},
        params=params,
        json={"member_type": "openid", "member_id": owner_open_id},
        timeout=60,
    ).json()
    return r


def main() -> None:
    ap = argparse.ArgumentParser(description="批量转移知识库文档所有者")
    ap.add_argument(
        "--parent",
        action="append",
        default=[],
        help="导入根节点 wiki token，可重复；默认读 FEISHU_PARENT_WIKI_TOKEN",
    )
    ap.add_argument(
        "--include-roots",
        action="store_true",
        help="连 --parent 自身一并转移（默认只转子孙）",
    )
    ap.add_argument(
        "--owner-open-id",
        default=os.environ.get("FEISHU_NEW_OWNER_OPEN_ID", "").strip(),
        help="新所有者 open_id（ou_xxx）",
    )
    ap.add_argument(
        "--remove-old-owner",
        action="store_true",
        help="转移后移除机器人权限（默认保留可管理，便于后续脚本）",
    )
    ap.add_argument(
        "--old-owner-perm",
        default="full_access",
        choices=["view", "edit", "full_access"],
        help="保留给原所有者的权限（默认 full_access）",
    )
    ap.add_argument(
        "--notify",
        action="store_true",
        help="通知新所有者（默认关闭，避免几百条轰炸）",
    )
    ap.add_argument("--dry-run", action="store_true", help="只列节点，不真正转移")
    ap.add_argument(
        "--sleep",
        type=float,
        default=0.65,
        help="每次转移间隔秒数（接口约 100 次/分钟）",
    )
    args = ap.parse_args()

    app_id = os.environ.get("FEISHU_APP_ID", "").strip()
    app_secret = os.environ.get("FEISHU_APP_SECRET", "").strip()
    if not app_id or not app_secret:
        sys.exit("需要 FEISHU_APP_ID / FEISHU_APP_SECRET")

    parents = list(args.parent)
    env_parent = os.environ.get("FEISHU_PARENT_WIKI_TOKEN", "").strip()
    if not parents and env_parent:
        parents = [env_parent]
    if not parents:
        sys.exit("请用 --parent 指定导入根节点，或设置 FEISHU_PARENT_WIKI_TOKEN")

    owner = args.owner_open_id
    user_tok = os.environ.get("FEISHU_USER_ACCESS_TOKEN", "").strip()
    if not owner and user_tok:
        owner = resolve_owner_open_id(user_tok)
        print(f"resolved owner open_id={owner}", flush=True)
    if not owner and not args.dry_run:
        sys.exit(
            "需要 FEISHU_NEW_OWNER_OPEN_ID / --owner-open-id，"
            "或提供 FEISHU_USER_ACCESS_TOKEN 自动解析"
        )

    tenant = tenant_token(app_id, app_secret)
    space_id = os.environ.get("FEISHU_SPACE_ID", "").strip()
    if not space_id:
        space_id = str(get_node(tenant, parents[0]).get("space_id") or "")
    if not space_id:
        sys.exit("无法解析 space_id，请设置 FEISHU_SPACE_ID")

    print(
        f"space_id={space_id} parents={parents} owner={owner or '(dry-run)'} "
        f"dry_run={args.dry_run} include_roots={args.include_roots}",
        flush=True,
    )
    nodes = collect_descendants(tenant, space_id, parents, args.include_roots)
    # 按 obj_token 去重（同一文档不应转两次）
    uniq: dict[str, dict[str, Any]] = {}
    for n in nodes:
        obj = n.get("obj_token") or ""
        if not obj:
            continue
        uniq[obj] = n
    targets = list(uniq.values())
    print(f"nodes_listed={len(nodes)} with_obj_token={len(targets)}", flush=True)

    if args.dry_run:
        for i, n in enumerate(targets[:30], 1):
            print(
                f"  [{i}] {n.get('title')} type={n.get('obj_type')} "
                f"obj={n.get('obj_token')} wiki={n.get('node_token')}",
                flush=True,
            )
        if len(targets) > 30:
            print(f"  ... and {len(targets) - 30} more", flush=True)
        print("dry-run done, 未做任何转移", flush=True)
        return

    ok = fail = skip = 0
    fails: list[tuple[str, str]] = []
    for i, n in enumerate(targets, 1):
        title = n.get("title") or ""
        obj = n.get("obj_token") or ""
        typ = n.get("obj_type") or "docx"
        wiki = n.get("node_token") or ""
        for attempt in range(1, 4):
            try:
                resp = transfer_owner(
                    tenant,
                    obj,
                    typ,
                    owner,
                    remove_old_owner=args.remove_old_owner,
                    old_owner_perm=args.old_owner_perm,
                    need_notification=args.notify,
                )
                code = resp.get("code")
                if code == 0:
                    ok += 1
                    print(f"OK [{i}/{len(targets)}] {title} {wiki}", flush=True)
                    break
                # 已是目标所有者等情况
                msg = str(resp.get("msg") or resp)
                if "already" in msg.lower() or code in (1063003,):
                    skip += 1
                    print(f"SKIP [{i}/{len(targets)}] {title} {resp}", flush=True)
                    break
                if attempt == 3:
                    fail += 1
                    fails.append((wiki or obj, msg[:200]))
                    print(f"FAIL [{i}/{len(targets)}] {title} {resp}", flush=True)
                else:
                    time.sleep(1.5 * attempt)
            except Exception as e:
                if attempt == 3:
                    fail += 1
                    fails.append((wiki or obj, str(e)[:200]))
                    print(f"FAIL [{i}/{len(targets)}] {title} {e}", flush=True)
                else:
                    time.sleep(1.5 * attempt)
        time.sleep(args.sleep)

    print(f"done ok={ok} skip={skip} fail={fail}", flush=True)
    for w, m in fails[:50]:
        print(f"  FAIL {w}: {m}", flush=True)
    if fail:
        sys.exit(1)


if __name__ == "__main__":
    main()
