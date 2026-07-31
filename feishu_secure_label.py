"""飞书文档密级（L2）设置：支持 user_access_token 或本机 lark-cli 用户身份。

优先级：
  1. FEISHU_USER_ACCESS_TOKEN 环境变量
  2. 仓库根目录 .feishu_user_token
  3. lark-cli 已登录的用户身份（docs:secure_label:write_only）

L2 标签 ID 优先级：
  1. FEISHU_SECURE_LABEL_L2_ID
  2. 按 FEISHU_SECURE_LABEL_L2_NAME（默认 L2-内部级）经 lark-cli 查询
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Literal, Optional

import requests

API = "https://open.feishu.cn/open-apis"
_BASE = Path(__file__).resolve().parent
DEFAULT_L2_NAME = "L2-内部级"
LARK_CLI = os.environ.get("LARK_CLI", "lark-cli")


def load_user_access_token(base: Optional[Path] = None) -> str:
    tok = os.environ.get("FEISHU_USER_ACCESS_TOKEN", "").strip()
    if tok:
        return tok
    token_path = (base or _BASE) / ".feishu_user_token"
    if token_path.is_file():
        return token_path.read_text(encoding="utf-8").strip()
    return ""


def lark_cli_available() -> bool:
    return shutil.which(LARK_CLI) is not None


def lark_cli_user_ready() -> bool:
    if not lark_cli_available():
        return False
    try:
        proc = subprocess.run(
            [LARK_CLI, "auth", "status", "--json"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if proc.returncode != 0:
            return False
        data = json.loads(proc.stdout)
        user = (data.get("identities") or {}).get("user") or {}
        if not user.get("available"):
            return False
        status = str(user.get("tokenStatus") or "")
        return status in {"valid", "ready", "needs_refresh"}
    except Exception:
        return False


def resolve_user_auth_mode(user_token: str = "") -> Literal["token", "lark-cli", "none"]:
    if user_token:
        return "token"
    if lark_cli_user_ready():
        return "lark-cli"
    return "none"


def _lark_cli_json(args: list[str]) -> dict:
    proc = subprocess.run(
        [LARK_CLI, *args],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if proc.returncode != 0 and not proc.stdout.strip():
        raise RuntimeError(proc.stderr.strip() or f"lark-cli failed: {args}")
    raw = proc.stdout.strip()
    if not raw:
        raise RuntimeError(f"lark-cli empty output: {args}")
    return json.loads(raw)


def resolve_l2_label_id(
    label_id: str = "",
    label_name: str = "",
) -> str:
    label_id = (label_id or os.environ.get("FEISHU_SECURE_LABEL_L2_ID", "")).strip()
    if label_id:
        return label_id
    label_name = (
        label_name
        or os.environ.get("FEISHU_SECURE_LABEL_L2_NAME", DEFAULT_L2_NAME)
    ).strip()
    if not lark_cli_user_ready():
        raise RuntimeError(
            "缺少 FEISHU_SECURE_LABEL_L2_ID，且 lark-cli 用户未登录，无法查询密级列表"
        )
    data = _lark_cli_json(["drive", "+secure-label-list", "--as", "user", "--json"])
    items = (data.get("data") or {}).get("items") or data.get("items") or []
    if not items and data.get("ok"):
        # 部分版本 jq 过滤后结构不同，走 openapi
        data = _lark_cli_json(
            [
                "api",
                "GET",
                "/open-apis/drive/v2/my_secure_labels",
                "--as",
                "user",
                "--json",
            ]
        )
        items = (data.get("data") or {}).get("items") or []
    for item in items:
        name = (item.get("name") or "").strip()
        if name == label_name or label_name in name:
            lid = str(item.get("id") or "").strip()
            if lid:
                return lid
    names = [item.get("name") for item in items]
    raise RuntimeError(f"未找到密级 {label_name!r}，可用: {names}")


def get_wiki_obj(tenant_token: str, wiki_token: str) -> tuple[str, str, str]:
    """返回 (obj_token, obj_type, title)。"""
    resp = requests.get(
        f"{API}/wiki/v2/spaces/get_node",
        headers={"Authorization": f"Bearer {tenant_token}"},
        params={"token": wiki_token},
        timeout=60,
    ).json()
    if resp.get("code") != 0:
        raise RuntimeError(f"get_node failed: {resp}")
    node = resp["data"]["node"]
    obj = node.get("obj_token") or ""
    typ = node.get("obj_type") or "docx"
    title = node.get("title") or wiki_token
    if not obj:
        raise RuntimeError(f"no obj_token for {wiki_token}")
    return obj, typ, title


def _set_via_token(user_token: str, obj_token: str, obj_type: str, l2_id: str) -> None:
    resp = requests.patch(
        f"{API}/drive/v2/files/{obj_token}/secure_label",
        headers={"Authorization": f"Bearer {user_token}"},
        params={"type": obj_type},
        json={"id": l2_id},
        timeout=60,
    ).json()
    if resp.get("code") != 0:
        raise RuntimeError(resp)


def _set_via_lark_cli(obj_token: str, obj_type: str, l2_id: str) -> None:
    data = _lark_cli_json(
        [
            "drive",
            "+secure-label-update",
            "--as",
            "user",
            "--token",
            obj_token,
            "--label-id",
            l2_id,
            "--type",
            obj_type,
        ]
    )
    if not data.get("ok"):
        raise RuntimeError(data.get("error") or data)


def set_secure_label_l2(
    tenant_token: str,
    wiki_token: str,
    *,
    l2_id: str,
    user_token: str = "",
    auth_mode: Optional[Literal["token", "lark-cli", "none"]] = None,
) -> str:
    """为 wiki 节点对应文档设置 L2 密级。返回实际使用的 auth_mode。"""
    mode = auth_mode or resolve_user_auth_mode(user_token)
    if mode == "none":
        raise RuntimeError(
            "无法设置密级：请配置 FEISHU_USER_ACCESS_TOKEN / .feishu_user_token，"
            "或执行 lark-cli auth login --scope docs:secure_label:write_only"
        )
    obj, typ, _title = get_wiki_obj(tenant_token, wiki_token)
    if mode == "token":
        _set_via_token(user_token, obj, typ, l2_id)
    else:
        _set_via_lark_cli(obj, typ, l2_id)
    return mode
