#!/usr/bin/env python3
"""本地导出目录 -> 飞书知识库（保留目录树，可编辑 docx）。

配置全部通过环境变量注入，见仓库根目录 `.env.example` 与 `README.md`。
"""

from __future__ import annotations

import fcntl
import io
import json
import os
import sys
import tempfile
import time
import traceback
import zipfile
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import requests
from lxml import etree

_BASE = Path(__file__).resolve().parent

# 可通过环境变量切换目标（不同知识库 / 不同导出目录）
ROOT_DIR = Path(os.environ.get("FEISHU_ROOT_DIR", str(_BASE / "tmp_export")))
STATE_PATH = Path(os.environ.get("FEISHU_STATE_PATH", str(_BASE / "feishu_import_state.json")))
LOCK_PATH = Path(os.environ.get("FEISHU_LOCK_PATH", str(_BASE / "feishu_import.lock")))
LOG_PATH = Path(os.environ.get("FEISHU_LOG_PATH", str(_BASE / "feishu_import.log")))

PARENT_WIKI_TOKEN = os.environ.get("FEISHU_PARENT_WIKI_TOKEN", "").strip()
SPACE_ID = os.environ.get("FEISHU_SPACE_ID", "").strip()
# 可选：在父节点下再建中间目录；留空则直接挂在父节点下
CLEAN_ROOT_TITLE = os.environ.get("FEISHU_CLEAN_ROOT_TITLE", "")

APP_ID = os.environ.get("FEISHU_APP_ID", "")
APP_SECRET = os.environ.get("FEISHU_APP_SECRET", "")
USER_ACCESS_TOKEN = os.environ.get("FEISHU_USER_ACCESS_TOKEN", "").strip()

# L2-内部级（仅用户身份可写密级；应用身份通常无密级写权限）
# 以你们租户实际密级 ID 为准；不确定时可留空并在 .env 中配置
SECURE_LABEL_L2_ID = os.environ.get("FEISHU_SECURE_LABEL_L2_ID", "").strip()

IMPORT_SIZE_LIMIT = 18 * 1024 * 1024
API = "https://open.feishu.cn/open-apis"
# 仅用于日志中拼文档链接；可按租户设置 FEISHU_WIKI_BASE
WIKI_BASE = os.environ.get("FEISHU_WIKI_BASE", "https://<your-tenant>.feishu.cn").rstrip("/")


def wiki_url(token: str) -> str:
    return f"{WIKI_BASE}/wiki/{token}"


class Feishu:
    """导入用应用身份（tenant）；密级打标用用户身份（user）。"""

    def __init__(self, app_id: str = "", app_secret: str = "", user_access_token: str = ""):
        self.app_id = app_id
        self.app_secret = app_secret
        self.user_access_token = user_access_token
        self.token = ""
        self.token_expire_at = 0.0

    def ensure_token(self) -> str:
        # 长时导入优先应用身份，避免 user token 过期；无应用凭证时才退回用户身份
        if self.app_id and self.app_secret:
            if self.token and time.time() < self.token_expire_at - 60:
                return self.token
            r = requests.post(
                f"{API}/auth/v3/tenant_access_token/internal",
                json={"app_id": self.app_id, "app_secret": self.app_secret},
                timeout=30,
            )
            data = r.json()
            if data.get("code") != 0:
                raise RuntimeError(f"get token failed: {data}")
            self.token = data["tenant_access_token"]
            self.token_expire_at = time.time() + int(data.get("expire", 7000))
            return self.token
        if self.user_access_token:
            return self.user_access_token
        raise RuntimeError("缺少 FEISHU_APP_ID/SECRET 或 FEISHU_USER_ACCESS_TOKEN")

    def headers(self) -> Dict[str, str]:
        return {"Authorization": f"Bearer {self.ensure_token()}"}

    def user_headers(self) -> Dict[str, str]:
        if not self.user_access_token:
            raise RuntimeError("缺少 FEISHU_USER_ACCESS_TOKEN（密级打标需要用户身份）")
        return {"Authorization": f"Bearer {self.user_access_token}"}

    def _do_request(
        self,
        method: str,
        path: str,
        *,
        auth_headers: Dict[str, str],
        json_body: Any = None,
        data: Any = None,
        files: Any = None,
        params: Any = None,
        timeout: int = 180,
        retries: int = 6,
    ) -> Dict[str, Any]:
        url = path if path.startswith("http") else f"{API}{path}"
        last_err: Optional[Exception] = None
        for i in range(retries):
            try:
                kwargs: Dict[str, Any] = {
                    "headers": auth_headers,
                    "timeout": timeout,
                    "params": params,
                }
                if files is not None:
                    kwargs["files"] = files
                    kwargs["data"] = data
                elif json_body is not None:
                    kwargs["headers"] = {
                        **auth_headers,
                        "Content-Type": "application/json; charset=utf-8",
                    }
                    kwargs["json"] = json_body
                r = requests.request(method, url, **kwargs)
                body = r.json()
                code = body.get("code")
                if code == 0:
                    return body
                if code in (99991400, 131009, 1061045, 1069923) or r.status_code == 429:
                    time.sleep(min(2**i, 30))
                    continue
                return body
            except requests.RequestException as e:
                last_err = e
                time.sleep(min(2**i, 20))
        raise RuntimeError(f"request failed: {method} {url} err={last_err}")

    def request(
        self,
        method: str,
        path: str,
        *,
        json_body: Any = None,
        data: Any = None,
        files: Any = None,
        params: Any = None,
        timeout: int = 180,
        retries: int = 6,
    ) -> Dict[str, Any]:
        return self._do_request(
            method,
            path,
            auth_headers=self.headers(),
            json_body=json_body,
            data=data,
            files=files,
            params=params,
            timeout=timeout,
            retries=retries,
        )

    def user_request(
        self,
        method: str,
        path: str,
        *,
        json_body: Any = None,
        data: Any = None,
        files: Any = None,
        params: Any = None,
        timeout: int = 180,
        retries: int = 6,
    ) -> Dict[str, Any]:
        return self._do_request(
            method,
            path,
            auth_headers=self.user_headers(),
            json_body=json_body,
            data=data,
            files=files,
            params=params,
            timeout=timeout,
            retries=retries,
        )


def log(msg: str) -> None:
    # 只打 stdout；无人值守时由 shell 重定向到日志文件，避免再写 LOG_PATH 造成双份
    print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}", flush=True)


def load_state() -> Dict[str, Any]:
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    return {"root_token": "", "nodes": {}, "done_files": {}, "failed": {}}


def save_state(state: Dict[str, Any]) -> None:
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def create_wiki_docx(fs: Feishu, parent: str, title: str) -> Tuple[str, str]:
    resp = fs.request(
        "POST",
        f"/wiki/v2/spaces/{SPACE_ID}/nodes",
        json_body={
            "obj_type": "docx",
            "parent_node_token": parent,
            "node_type": "origin",
            "title": (title or "未命名")[:1000],
        },
    )
    if resp.get("code") != 0:
        raise RuntimeError(f"create wiki node failed: {title} {resp}")
    node = resp["data"]["node"]
    return node["node_token"], node.get("obj_token", "")


def move_to_wiki(fs: Feishu, parent: str, obj_type: str, obj_token: str) -> str:
    resp = fs.request(
        "POST",
        f"/wiki/v2/spaces/{SPACE_ID}/nodes/move_docs_to_wiki",
        json_body={
            "parent_wiki_token": parent,
            "obj_type": obj_type,
            "obj_token": obj_token,
        },
    )
    if resp.get("code") != 0:
        raise RuntimeError(f"move_docs_to_wiki failed: {resp}")
    data = resp.get("data") or {}
    if data.get("wiki_token"):
        return data["wiki_token"]
    task_id = data.get("task_id")
    if not task_id:
        raise RuntimeError(f"move no wiki_token: {resp}")
    for _ in range(90):
        time.sleep(2)
        tr = fs.request("GET", f"/wiki/v2/tasks/{task_id}", params={"task_type": "move"})
        if tr.get("code") != 0:
            continue
        tdata = tr.get("data") or {}
        wiki_token = tdata.get("wiki_token")
        if not wiki_token:
            move_result = (tdata.get("task") or {}).get("move_result") or []
            if move_result:
                wiki_token = ((move_result[0] or {}).get("node") or {}).get("node_token")
        if wiki_token:
            return wiki_token
    raise RuntimeError(f"move task timeout: {task_id}")


def get_wiki_node(fs: Feishu, wiki_token: str) -> Dict[str, Any]:
    resp = fs.request("GET", "/wiki/v2/spaces/get_node", params={"token": wiki_token})
    if resp.get("code") != 0:
        raise RuntimeError(f"get_node failed: {wiki_token} {resp}")
    return resp["data"]["node"]


def ensure_sub_page_list(fs: Feishu, wiki_token: str) -> None:
    """在目录页插入「子页面列表」(block_type=51)，已有则跳过。"""
    node = get_wiki_node(fs, wiki_token)
    obj_token = node.get("obj_token") or ""
    if node.get("obj_type") != "docx" or not obj_token:
        return
    items: list = []
    page_token = None
    while True:
        params: Dict[str, Any] = {"page_size": 500}
        if page_token:
            params["page_token"] = page_token
        resp = fs.request("GET", f"/docx/v1/documents/{obj_token}/blocks", params=params)
        if resp.get("code") != 0:
            raise RuntimeError(f"list blocks failed: {resp}")
        data = resp.get("data") or {}
        items.extend(data.get("items") or [])
        if not data.get("has_more"):
            break
        page_token = data.get("page_token")
    for b in items:
        if b.get("block_type") == 51:
            return
        if b.get("block_type") == 42:
            return
    resp = fs.request(
        "POST",
        f"/docx/v1/documents/{obj_token}/blocks/{obj_token}/children",
        params={"document_revision_id": -1},
        json_body={
            "index": 0,
            "children": [
                {
                    "block_type": 51,
                    "sub_page_list": {"wiki_token": wiki_token},
                }
            ],
        },
    )
    if resp.get("code") != 0:
        raise RuntimeError(f"insert sub_page_list failed: {resp}")
    log(f"sub_page_list ok: {wiki_token}")


def set_secure_label_l2(fs: Feishu, wiki_token: str) -> None:
    """用户身份将文档密级设为 L2-内部级。无 user token 时静默跳过。"""
    if not fs.user_access_token:
        return
    if not SECURE_LABEL_L2_ID:
        raise RuntimeError("缺少 FEISHU_SECURE_LABEL_L2_ID")
    # get_node 用应用身份即可；打标必须用用户身份
    node = get_wiki_node(fs, wiki_token)
    obj_token = node.get("obj_token") or ""
    obj_type = node.get("obj_type") or "docx"
    if not obj_token:
        raise RuntimeError(f"no obj_token for {wiki_token}")
    resp = fs.user_request(
        "PATCH",
        f"/drive/v2/files/{obj_token}/secure_label",
        params={"type": obj_type},
        json_body={"id": SECURE_LABEL_L2_ID},
    )
    if resp.get("code") != 0:
        raise RuntimeError(f"set L2 failed: {wiki_token} {resp}")
    log(f"L2 ok: {wiki_token}")


def finalize_wiki_node(
    fs: Feishu, wiki_token: str, *, add_sub_page_list: bool = False
) -> None:
    """可选插入「子页面列表」；所有导入节点打 L2。失败只告警，不中断导入。

    注意：子页面列表只应加在「空目录壳」页面上。
    同名正文作为目录父节点（dir+docx-editable）时不要加，否则正文里会出现子目录组件。
    """
    if add_sub_page_list:
        try:
            ensure_sub_page_list(fs, wiki_token)
        except Exception as e:
            log(f"[WARN] sub_page_list {wiki_token}: {e}")
    try:
        set_secure_label_l2(fs, wiki_token)
    except Exception as e:
        log(f"[WARN] L2 {wiki_token}: {e}")


def upload_import_media(fs: Feishu, file_path: Path) -> str:
    size = file_path.stat().st_size
    ext = file_path.suffix.lstrip(".").lower()
    extra = json.dumps({"obj_type": "docx", "file_extension": ext}, ensure_ascii=False)
    with file_path.open("rb") as f:
        resp = fs.request(
            "POST",
            "/drive/v1/medias/upload_all",
            data={
                "file_name": file_path.name,
                "parent_type": "ccm_import_open",
                "size": str(size),
                "extra": extra,
            },
            files={"file": (file_path.name, f)},
            timeout=300,
        )
    if resp.get("code") != 0:
        raise RuntimeError(f"upload media failed: {resp}")
    return resp["data"]["file_token"]


def create_import_task(fs: Feishu, file_token: str, title: str, ext: str) -> str:
    resp = fs.request(
        "POST",
        "/drive/v1/import_tasks",
        json_body={
            "file_extension": ext,
            "file_token": file_token,
            "type": "docx",
            "file_name": title,
            "point": {"mount_type": 1, "mount_key": ""},
        },
    )
    if resp.get("code") != 0:
        raise RuntimeError(f"create import task failed: {resp}")
    return resp["data"]["ticket"]


def wait_import(fs: Feishu, ticket: str) -> str:
    for _ in range(240):
        time.sleep(2)
        resp = fs.request("GET", f"/drive/v1/import_tasks/{ticket}")
        if resp.get("code") != 0:
            continue
        result = (resp.get("data") or {}).get("result") or {}
        status = result.get("job_status")
        if status == 0:
            token = result.get("token")
            if not token:
                raise RuntimeError(f"import ok but no token: {resp}")
            return token
        if status in (3, 100, 101, 102, 103, 104, 105, 106, 107, 108):
            raise RuntimeError(f"import failed: {resp}")
    raise RuntimeError(f"import timeout: {ticket}")


W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_W = {"w": W_NS}


def _w_tag(tag: str) -> str:
    return f"{{{W_NS}}}{tag}"


def _row_col_count(tr: etree._Element) -> int:
    total = 0
    for tc in tr.findall(_w_tag("tc")):
        gs = tc.find(f'{_w_tag("tcPr")}/{_w_tag("gridSpan")}')
        if gs is not None:
            try:
                total += int(gs.get(_w_tag("val")) or 1)
            except (TypeError, ValueError):
                total += 1
        else:
            total += 1
    return total


def repair_docx_tables(src: Path, dst: Path) -> int:
    """补全 Thoughts 导出 Word 中缺失的 w:tblGrid，避免飞书导入后 column_size=0。

    云效 Thoughts 导出的大量表格缺少 tblGrid/gridCol，飞书 Import 会把表格建成
    column_size=0，UI 显示「表格内容已经被删除」。根据各行单元格数补全列网格后即可正常显示。
    """
    fixed = 0
    with zipfile.ZipFile(src, "r") as zin:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zout:
            for item in zin.infolist():
                data = zin.read(item.filename)
                if item.filename == "word/document.xml":
                    root = etree.fromstring(data)
                    for tbl in root.xpath("//w:tbl", namespaces=_W):
                        grid = tbl.find(_w_tag("tblGrid"))
                        cols = grid.findall(_w_tag("gridCol")) if grid is not None else []
                        if cols:
                            continue
                        n = 0
                        for tr in tbl.findall(_w_tag("tr")):
                            n = max(n, _row_col_count(tr))
                        if n <= 0:
                            continue
                        if grid is not None:
                            tbl.remove(grid)
                        grid = etree.Element(_w_tag("tblGrid"))
                        col_w = max(500, 9000 // n)
                        for _ in range(n):
                            gc = etree.SubElement(grid, _w_tag("gridCol"))
                            gc.set(_w_tag("w"), str(col_w))
                        tbl_pr = tbl.find(_w_tag("tblPr"))
                        if tbl_pr is not None:
                            tbl.insert(list(tbl).index(tbl_pr) + 1, grid)
                            tbl_w = tbl_pr.find(_w_tag("tblW"))
                            if tbl_w is not None:
                                ww = tbl_w.get(_w_tag("w"))
                                wt = tbl_w.get(_w_tag("type"))
                                if ww in ("0", "0.0", None) or (
                                    wt == "pct" and float(ww or 0) == 0
                                ):
                                    tbl_w.set(_w_tag("type"), "pct")
                                    tbl_w.set(_w_tag("w"), "5000")
                        else:
                            tbl.insert(0, grid)
                        fixed += 1
                    data = etree.tostring(
                        root, xml_declaration=True, encoding="UTF-8", standalone=True
                    )
                zout.writestr(item, data)
        dst.write_bytes(buf.getvalue())
    return fixed


def import_docx_as_wiki(fs: Feishu, parent: str, file_path: Path, title: str) -> str:
    """Word -> 可编辑飞书文档，并挂到知识库 parent 下。"""
    if file_path.stat().st_size > IMPORT_SIZE_LIMIT:
        raise RuntimeError(f"file too large for import API (>18MB): {file_path}")
    ext = file_path.suffix.lstrip(".").lower()
    if ext != "docx":
        raise RuntimeError(f"only docx supported for editable import: {file_path}")

    upload_path = file_path
    tmp_path: Optional[Path] = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as tmp:
            tmp_path = Path(tmp.name)
        fixed = repair_docx_tables(file_path, tmp_path)
        if fixed:
            log(f"repair tables: {fixed} in {file_path.name}")
            upload_path = tmp_path
        file_token = upload_import_media(fs, upload_path)
        ticket = create_import_task(fs, file_token, title, ext)
        obj_token = wait_import(fs, ticket)
        return move_to_wiki(fs, parent, "docx", obj_token)
    finally:
        if tmp_path is not None and tmp_path.exists():
            tmp_path.unlink(missing_ok=True)


def upload_file_as_wiki(fs: Feishu, parent: str, file_path: Path, title: str) -> str:
    size = file_path.stat().st_size
    with file_path.open("rb") as f:
        if size <= 20 * 1024 * 1024:
            resp = fs.request(
                "POST",
                "/drive/v1/files/upload_all",
                data={
                    "file_name": f"{title}{file_path.suffix}",
                    "parent_type": "explorer",
                    "parent_node": "",
                    "size": str(size),
                },
                files={"file": (file_path.name, f)},
                timeout=300,
            )
            if resp.get("code") != 0:
                raise RuntimeError(f"upload_all failed: {resp}")
            file_token = resp["data"]["file_token"]
        else:
            prep = fs.request(
                "POST",
                "/drive/v1/files/upload_prepare",
                json_body={
                    "file_name": f"{title}{file_path.suffix}",
                    "parent_type": "explorer",
                    "parent_node": "",
                    "size": size,
                },
            )
            if prep.get("code") != 0:
                raise RuntimeError(f"upload_prepare failed: {prep}")
            upload_id = prep["data"]["upload_id"]
            block_size = int(prep["data"]["block_size"])
            seq = 0
            while True:
                chunk = f.read(block_size)
                if not chunk:
                    break
                part = fs.request(
                    "POST",
                    "/drive/v1/files/upload_part",
                    data={"upload_id": upload_id, "seq": str(seq), "size": str(len(chunk))},
                    files={"file": (f"part{seq}", chunk)},
                    timeout=300,
                )
                if part.get("code") != 0:
                    raise RuntimeError(f"upload_part failed: {part}")
                seq += 1
            finish = fs.request(
                "POST",
                "/drive/v1/files/upload_finish",
                json_body={"upload_id": upload_id, "block_num": seq},
            )
            if finish.get("code") != 0:
                raise RuntimeError(f"upload_finish failed: {finish}")
            file_token = finish["data"]["file_token"]
    return move_to_wiki(fs, parent, "file", file_token)


def ensure_root(fs: Feishu, state: Dict[str, Any]) -> str:
    if state.get("root_token"):
        return state["root_token"]
    if CLEAN_ROOT_TITLE:
        token, _ = create_wiki_docx(fs, PARENT_WIKI_TOKEN, CLEAN_ROOT_TITLE)
        state["root_token"] = token
        state["nodes"]["."] = {"wiki_token": token, "title": CLEAN_ROOT_TITLE, "kind": "root"}
        save_state(state)
        finalize_wiki_node(fs, token, add_sub_page_list=True)
        log(f"创建干净根目录: {wiki_url(token)} ({CLEAN_ROOT_TITLE})")
        return token
    state["root_token"] = PARENT_WIKI_TOKEN
    state["nodes"]["."] = {"wiki_token": PARENT_WIKI_TOKEN, "title": "parent", "kind": "root"}
    save_state(state)
    log("直接导入到父节点下（不建中间目录）")
    return PARENT_WIKI_TOKEN


def ensure_dir(
    fs: Feishu, state: Dict[str, Any], rel: str, parent_token: str, title: str
) -> str:
    key = rel.replace("\\", "/")
    if key in state["nodes"]:
        return state["nodes"][key]["wiki_token"]

    sibling_docx = ROOT_DIR / f"{key}.docx"
    sibling_pdf = ROOT_DIR / f"{key}.pdf"

    # 飞书侧已有同名节点：直接复用，避免并发/重跑再建一份
    try:
        existing = list_wiki_child_titles(fs, parent_token)
        if title in existing:
            wiki_token = existing[title]
            kind = "dir+docx-editable" if sibling_docx.exists() else "dir"
            state["nodes"][key] = {"wiki_token": wiki_token, "title": title, "kind": kind}
            if sibling_docx.exists():
                state["done_files"][str(sibling_docx.relative_to(ROOT_DIR))] = wiki_token
            save_state(state)
            log(f"[SKIP-dup-dir] {key} already exists -> {wiki_token}")
            return wiki_token
    except Exception as e:
        log(f"[WARN] dir dup-check failed {key}: {e}")

    # 有同名正文：导入为可编辑文档，并作为目录父节点（不加子页面列表）
    if sibling_docx.exists() and sibling_docx.is_file():
        fkey = str(sibling_docx.relative_to(ROOT_DIR))
        try:
            if sibling_docx.stat().st_size <= IMPORT_SIZE_LIMIT:
                wiki_token = import_docx_as_wiki(fs, parent_token, sibling_docx, title)
                kind = "dir+docx-editable"
            else:
                wiki_token, _ = create_wiki_docx(fs, parent_token, title)
                file_wiki = upload_file_as_wiki(fs, wiki_token, sibling_docx, f"{title}（附件）")
                state["done_files"][fkey] = file_wiki
                finalize_wiki_node(fs, file_wiki, add_sub_page_list=False)
                kind = "dir+docx-file"
            if kind == "dir+docx-editable":
                state["done_files"][fkey] = wiki_token
            state["nodes"][key] = {"wiki_token": wiki_token, "title": title, "kind": kind}
            save_state(state)
            finalize_wiki_node(fs, wiki_token, add_sub_page_list=False)
            log(f"[OK-dir] {key} -> {wiki_url(wiki_token)}")
            return wiki_token
        except Exception as e:
            log(f"[WARN] 目录正文导入失败，改建空目录: {key} err={e}")

    if sibling_pdf.exists() and sibling_pdf.is_file() and not sibling_docx.exists():
        wiki_token, _ = create_wiki_docx(fs, parent_token, title)
        fkey = str(sibling_pdf.relative_to(ROOT_DIR))
        try:
            file_wiki = upload_file_as_wiki(fs, wiki_token, sibling_pdf, title)
            state["done_files"][fkey] = file_wiki
            state["nodes"][key] = {"wiki_token": wiki_token, "title": title, "kind": "dir+pdf"}
            save_state(state)
            finalize_wiki_node(fs, wiki_token, add_sub_page_list=True)
            finalize_wiki_node(fs, file_wiki, add_sub_page_list=False)
            log(f"[OK-dir-pdf] {key} -> {wiki_url(wiki_token)}")
            return wiki_token
        except Exception as e:
            log(f"[WARN] 目录pdf失败: {key} err={e}")

    # 纯空目录壳：才插入子页面列表
    wiki_token, _ = create_wiki_docx(fs, parent_token, title)
    state["nodes"][key] = {"wiki_token": wiki_token, "title": title, "kind": "dir"}
    save_state(state)
    finalize_wiki_node(fs, wiki_token, add_sub_page_list=True)
    return wiki_token


def list_wiki_child_titles(fs: Feishu, parent_token: str) -> Dict[str, str]:
    """parent 下已有子节点 title -> node_token，用于防重复导入。"""
    out: Dict[str, str] = {}
    page_token = None
    while True:
        params: Dict[str, Any] = {"page_size": 50, "parent_node_token": parent_token}
        if page_token:
            params["page_token"] = page_token
        resp = fs.request("GET", f"/wiki/v2/spaces/{SPACE_ID}/nodes", params=params)
        if resp.get("code") != 0:
            raise RuntimeError(f"list children failed: {resp}")
        data = resp.get("data") or {}
        for n in data.get("items") or []:
            title = n.get("title") or ""
            if title and title not in out:
                out[title] = n.get("node_token") or ""
        if not data.get("has_more"):
            break
        page_token = data.get("page_token")
    return out


def import_one_file(fs: Feishu, state: Dict[str, Any], file_path: Path, parent_token: str) -> None:
    rel = str(file_path.relative_to(ROOT_DIR))
    if rel in state["done_files"]:
        return
    # 同名目录正文已在 ensure_dir 处理
    if (file_path.parent / file_path.stem).is_dir():
        return

    title = file_path.stem
    # 飞书侧已有同名子节点则跳过，避免进程并发/中断重跑造成重复
    try:
        existing = list_wiki_child_titles(fs, parent_token)
        if title in existing or f"{title}{file_path.suffix}" in existing:
            wiki_token = existing.get(title) or existing.get(f"{title}{file_path.suffix}")
            state["done_files"][rel] = wiki_token
            save_state(state)
            log(f"[SKIP-dup] {rel} already exists -> {wiki_token}")
            return
    except Exception as e:
        log(f"[WARN] dup-check failed {rel}: {e}")

    try:
        suffix = file_path.suffix.lower()
        if suffix == ".docx" and file_path.stat().st_size <= IMPORT_SIZE_LIMIT:
            wiki_token = import_docx_as_wiki(fs, parent_token, file_path, title)
        else:
            wiki_token = upload_file_as_wiki(fs, parent_token, file_path, title)
        state["done_files"][rel] = wiki_token
        state["failed"].pop(rel, None)
        save_state(state)
        finalize_wiki_node(fs, wiki_token, add_sub_page_list=False)
        log(f"[OK] {rel} -> {wiki_url(wiki_token)}")
    except Exception as e:
        state["failed"][rel] = str(e)
        save_state(state)
        log(f"[FAIL] {rel}: {e}")


def probe_import_permission(fs: Feishu) -> None:
    """启动前探测 import 权限，失败则直接退出避免再导入一堆空壳。"""
    samples = sorted(
        [
            p
            for p in ROOT_DIR.rglob("*.docx")
            if p.is_file()
            and p.stat().st_size < 80_000
            and not (p.parent / p.stem).is_dir()
        ],
        key=lambda p: p.stat().st_size,
    )
    if not samples:
        raise RuntimeError("找不到用于权限探测的小 docx")
    sample = samples[0]
    log(f"探测 import 权限: {sample.name}")
    try:
        # 仅做到 create import task 即可
        file_token = upload_import_media(fs, sample)
        ticket = create_import_task(fs, file_token, "权限探测-可删", "docx")
        obj_token = wait_import(fs, ticket)
        # 探测成功后挂到父节点下，便于人工删除
        wiki = move_to_wiki(fs, PARENT_WIKI_TOKEN, "docx", obj_token)
        log(f"import 权限正常，探测文档: {wiki_url(wiki)}")
    except Exception as e:
        raise RuntimeError(
            "docs:document:import 仍不可用。请确认已开通【应用身份】权限并发布版本，"
            "权限项至少包含 docs:document:import / drive:drive / wiki:wiki。"
            f"\n详细错误: {e}"
        )


def ensure_path_token(fs: Feishu, state: Dict[str, Any], root_token: str, rel: str) -> str:
    """确保 rel 对应目录节点存在（含祖先），返回 wiki_token。"""
    if rel in (".", ""):
        return root_token
    key = rel.replace("\\", "/")
    if key in state["nodes"]:
        return state["nodes"][key]["wiki_token"]
    parts = Path(key).parts
    parent_rel = "." if len(parts) == 1 else str(Path(*parts[:-1]))
    parent_token = ensure_path_token(fs, state, root_token, parent_rel)
    return ensure_dir(fs, state, key, parent_token, parts[-1])


def walk_and_import(fs: Feishu, state: Dict[str, Any], limit: Optional[int] = None) -> None:
    root_token = ensure_root(fs, state)

    # 目录阶段不受 limit 限制，避免只建一半目录就停
    dirs = sorted(
        [p for p in ROOT_DIR.rglob("*") if p.is_dir()],
        key=lambda p: (len(p.relative_to(ROOT_DIR).parts), str(p)),
    )
    pending_dirs = [d for d in dirs if str(d.relative_to(ROOT_DIR)) not in state["nodes"]]
    log(f"目录节点待处理: {len(pending_dirs)}/{len(dirs)}")
    for i, d in enumerate(pending_dirs, 1):
        rel = str(d.relative_to(ROOT_DIR))
        ensure_path_token(fs, state, root_token, rel)
        if i % 5 == 0 or i == len(pending_dirs):
            log(f"目录进度 {i}/{len(pending_dirs)}")
        time.sleep(0.12)

    files = sorted(
        [p for p in ROOT_DIR.rglob("*") if p.is_file() and p.suffix.lower() in {".docx", ".pdf"}],
        key=lambda p: str(p),
    )
    pending_files = [
        f
        for f in files
        if str(f.relative_to(ROOT_DIR)) not in state["done_files"]
        and not (f.parent / f.stem).is_dir()
    ]
    log(f"叶子文件待导入: {len(pending_files)}/{len(files)}")
    done_delta = 0
    for f in pending_files:
        if limit is not None and done_delta >= limit:
            log(f"达到 limit={limit}，停止")
            return
        parent_rel = "." if f.parent == ROOT_DIR else str(f.parent.relative_to(ROOT_DIR))
        parent_token = ensure_path_token(fs, state, root_token, parent_rel)
        before = len(state["done_files"])
        import_one_file(fs, state, f, parent_token)
        if len(state["done_files"]) > before:
            done_delta += 1
        time.sleep(0.25)


def acquire_import_lock() -> Any:
    """单实例锁：禁止 nohup + 前台续跑同时写同一知识库。"""
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    fh = open(LOCK_PATH, "a+", encoding="utf-8")
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        fh.seek(0)
        holder = (fh.read() or "").strip() or "unknown"
        fh.close()
        raise SystemExit(
            f"另一导入进程仍在运行（lock={LOCK_PATH} holder={holder}）。"
            "请先停掉旧进程，再启动；禁止并发导入。"
        )
    fh.seek(0)
    fh.truncate()
    fh.write(f"pid={os.getpid()} started={time.strftime('%F %T')}\n")
    fh.flush()
    return fh


def main() -> None:
    if not USER_ACCESS_TOKEN and (not APP_ID or not APP_SECRET):
        print("请设置 FEISHU_USER_ACCESS_TOKEN 或 FEISHU_APP_ID/SECRET", file=sys.stderr)
        sys.exit(1)
    if not PARENT_WIKI_TOKEN or not SPACE_ID:
        print(
            "请设置 FEISHU_PARENT_WIKI_TOKEN 与 FEISHU_SPACE_ID（见 .env.example）",
            file=sys.stderr,
        )
        sys.exit(1)
    if not ROOT_DIR.exists():
        print(f"源目录不存在: {ROOT_DIR}", file=sys.stderr)
        sys.exit(1)

    lock_fh = acquire_import_lock()

    limit = None
    fresh = False
    skip_probe = False
    reimport_keys: list[str] = []
    for arg in sys.argv[1:]:
        if arg.startswith("--limit="):
            limit = int(arg.split("=", 1)[1])
        elif arg == "--fresh":
            fresh = True
        elif arg == "--skip-probe":
            skip_probe = True
        elif arg.startswith("--reimport="):
            reimport_keys.append(arg.split("=", 1)[1].strip())
        elif arg == "--reimport-docx":
            reimport_keys.append("*docx*")
        elif arg == "--direct-under-backend":
            # 不建「云效资料库」中间层
            global CLEAN_ROOT_TITLE
            CLEAN_ROOT_TITLE = ""

    if fresh and STATE_PATH.exists():
        STATE_PATH.unlink()
        log("已清空 v2 状态，准备全新导入")

    fs = Feishu(APP_ID, APP_SECRET, USER_ACCESS_TOKEN)
    if APP_ID and APP_SECRET:
        auth = "tenant_access_token(+user for L2)" if USER_ACCESS_TOKEN else "tenant_access_token"
    else:
        auth = "user_access_token"
    if not USER_ACCESS_TOKEN:
        log(
            "[WARN] 未设置 FEISHU_USER_ACCESS_TOKEN：将无法打 L2 密级"
            "（docs:secure_label 仅用户身份可用）"
        )
    probe = fs.request(
        "GET", "/wiki/v2/spaces/get_node", params={"token": PARENT_WIKI_TOKEN}
    )
    if probe.get("code") != 0:
        raise RuntimeError(f"无法访问目标父节点: {probe}")
    log(f"鉴权成功 auth={auth} node={probe['data']['node']['title']} L2={SECURE_LABEL_L2_ID}")

    if not skip_probe:
        probe_import_permission(fs)

    state = load_state()
    if reimport_keys:
        removed = 0
        keys = list(state.get("done_files", {}).keys())
        for k in keys:
            hit = False
            if "*docx*" in reimport_keys and k.lower().endswith(".docx"):
                hit = True
            elif k in reimport_keys or any(
                k.endswith(x) or k == x for x in reimport_keys if x != "*docx*"
            ):
                hit = True
            if not hit:
                continue
            old = state["done_files"].pop(k, None)
            state.get("failed", {}).pop(k, None)
            removed += 1
            if old:
                # 知识库节点无公开删除 API：改名标记，便于人工清理
                title = Path(k).stem
                rename = fs.request(
                    "POST",
                    f"/wiki/v2/spaces/{SPACE_ID}/nodes/{old}/update_title",
                    json_body={"title": f"{title}（表格损坏-待删）"},
                )
                log(
                    f"reimport 旧节点改名 {k} -> {old} "
                    f"code={rename.get('code')} msg={rename.get('msg')}"
                )
        save_state(state)
        log(f"已标记重导 {removed} 个文件（旧节点已改名为「表格损坏-待删」）")

    log(
        f"开始干净导入 parent={PARENT_WIKI_TOKEN} clean_root={CLEAN_ROOT_TITLE!r} "
        f"limit={limit} done={len(state.get('done_files', {}))} root={ROOT_DIR}"
    )
    try:
        walk_and_import(fs, state, limit=limit)
    except Exception:
        log("异常中断:\n" + traceback.format_exc())
        raise
    log(
        f"结束: done={len(state.get('done_files', {}))} failed={len(state.get('failed', {}))} "
        f"root={wiki_url(state.get('root_token') or '')}"
    )
    if state.get("failed"):
        log("失败列表:")
        for k, v in state["failed"].items():
            log(f"  - {k}: {v}")


if __name__ == "__main__":
    main()
