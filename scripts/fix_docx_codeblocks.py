#!/usr/bin/env python3
"""用 Thoughts HTML 导出恢复 docx 中代码块换行。

Thoughts 的 docx 导出会把多行 code-block 合并成一行；HTML 导出仍保留
<code data-type="code-block"> 逐行结构。本脚本按节点 ID 拉 HTML 并 patch docx。

用法:
  python3 scripts/fix_docx_codeblocks.py <导出根目录> <workspace_id>
  python3 scripts/fix_docx_codeblocks.py --doc <node_id> <path/to/file.docx>
"""

from __future__ import annotations

import argparse
import html
import io
import json
import os
import re
import sys
import time
import zipfile
from pathlib import Path

import requests

API = "https://thoughts.aliyun.com"
PRE_BLOCK_RE = re.compile(r"(?is)<pre[^>]*>([\s\S]*?)</pre>")
CODE_LINE_RE = re.compile(r'(?is)<code[^>]*data-type="code-block"[^>]*>([\s\S]*?)</code>')
PARAGRAPH_RE = re.compile(r"(?s)<w:p>.*?</w:p>")
PPR_RE = re.compile(r"(?s)<w:pPr>.*?</w:pPr>")
WT_CONTENT_RE = re.compile(r"(?is)(<w:pPr>[\s\S]*?</w:pPr>)([\s\S]*)(</w:p>)")
FIRST_WT_RE = re.compile(r'(?is)<w:t(?:\s+xml:space="preserve")?>([^<]*)</w:t>')
TAG_RE = re.compile(r"(?s)<[^>]+>")


def is_source_code_paragraph(para: str) -> bool:
    ppr = PPR_RE.search(para)
    return bool(ppr and 'w:val="SourceCode"' in ppr.group(0))


def find_source_code_paragraphs(doc: str) -> list[str]:
    return [p for p in PARAGRAPH_RE.findall(doc) if is_source_code_paragraph(p)]


def strip_html(s: str) -> str:
    return html.unescape(TAG_RE.sub("", s))


def parse_code_blocks(raw: str) -> list[list[str]]:
    blocks: list[list[str]] = []
    for pre in PRE_BLOCK_RE.findall(raw):
        lines = [strip_html(m) for m in CODE_LINE_RE.findall(pre)]
        if lines:
            blocks.append(lines)
    return blocks


def xml_escape(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def build_runs(lines: list[str]) -> str:
    parts: list[str] = []
    for i, line in enumerate(lines):
        if i:
            parts.append(
                '<w:r><w:rPr><w:rStyle w:val="VerbatimChar"/></w:rPr><w:br/></w:r>'
            )
        parts.append(
            '<w:r><w:rPr><w:rStyle w:val="VerbatimChar"/></w:rPr>'
            f'<w:t xml:space="preserve">{xml_escape(line)}</w:t></w:r>'
        )
    return "".join(parts)


def replace_para(para: str, lines: list[str]) -> str:
    if len(lines) <= 1:
        return para
    flat = "".join(lines)
    m = FIRST_WT_RE.search(para)
    if not m:
        return para
    current = html.unescape(m.group(1)).replace("\r", "").replace("\n", "")
    if current != flat.replace("\r", "") and flat:
        return para
    wm = WT_CONTENT_RE.search(para)
    if not wm:
        return para
    return "<w:p>" + wm.group(1) + build_runs(lines) + wm.group(3)


def fix_docx_with_html(docx_path: Path, html_content: str) -> int:
    blocks = parse_code_blocks(html_content)
    if not blocks:
        return 0
    raw = docx_path.read_bytes()
    zr = zipfile.ZipFile(io.BytesIO(raw))
    doc = zr.read("word/document.xml").decode("utf-8")
    paras = find_source_code_paragraphs(doc)
    if not paras:
        return 0
    changes = 0
    new_doc = doc
    for i in range(min(len(paras), len(blocks))):
        if len(blocks[i]) <= 1:
            continue
        fixed = replace_para(paras[i], blocks[i])
        if fixed == paras[i]:
            continue
        new_doc = new_doc.replace(paras[i], fixed, 1)
        changes += 1
    if not changes:
        return 0
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zw:
        for info in zr.infolist():
            data = zr.read(info.filename)
            if info.filename == "word/document.xml":
                data = new_doc.encode("utf-8")
            zw.writestr(info, data)
    docx_path.write_bytes(buf.getvalue())
    return changes


def fetch_html(session: requests.Session, node_id: str) -> str:
    ts = int(time.time() * 1000)
    r = session.get(f"{API}/convert/api/nodes/{node_id}/export:html?pageSize=1000&_={ts}", timeout=30)
    r.raise_for_status()
    eid = r.json()["id"]
    for _ in range(30):
        time.sleep(2)
        pr = session.get(
            f"{API}/convert/api/exportDocx:polling?pageSize=1000&id={eid}&_={int(time.time()*1000)}",
            timeout=30,
        ).json()
        if pr.get("convertProcess") == 1:
            url = pr["message"]["downloadUrl"]
            break
        if pr.get("convertProcess") == -1:
            raise RuntimeError(pr)
    else:
        raise RuntimeError("html export timeout")
    content = session.get(url, timeout=120).content
    if content[:2] == b"PK":
        z = zipfile.ZipFile(io.BytesIO(content))
        name = next(n for n in z.namelist() if n.lower().endswith(".html"))
        return z.read(name).decode("utf-8", errors="replace")
    return content.decode("utf-8", errors="replace")


def list_nodes(session: requests.Session, ws: str, folder: str = "") -> list[dict]:
    nodes: list[dict] = []
    stack = [folder]
    while stack:
        parent = stack.pop()
        token = ""
        while True:
            params = {"pageSize": 1000, "parentId": parent}
            if token:
                params["pageToken"] = token
            r = session.get(f"{API}/api/workspaces/{ws}/nodes", params=params, timeout=60).json()
            batch = r.get("result") or []
            for n in batch:
                nodes.append(n)
                if n.get("type") == "folder":
                    stack.append(n["_id"])
            token = r.get("nextPageToken") or ""
            if not token:
                break
    return nodes


def walk_paths(root: Path, parent_title: str, node: dict, out: dict[str, str]) -> None:
    title = (node.get("title") or "").replace("/", "／")
    path = f"{parent_title}/{title}" if parent_title else f"/{title}"
    if node.get("type") == "document":
        rel = path.lstrip("/") + ".docx"
        out[rel] = node["_id"]
    # children resolved separately in list_nodes flat walk - rebuild tree below


def build_path_index(nodes: list[dict]) -> dict[str, str]:
    by_id = {n["_id"]: n for n in nodes}
    out: dict[str, str] = {}

    def path_for(nid: str) -> str:
        parts: list[str] = []
        cur = by_id.get(nid)
        while cur:
            parts.append((cur.get("title") or "").replace("/", "／"))
            pid = cur.get("_parentId") or ""
            cur = by_id.get(pid)
        parts.reverse()
        return "/" + "/".join(parts)

    for n in nodes:
        if n.get("type") != "document":
            continue
        p = path_for(n["_id"])
        rel = p.lstrip("/") + ".docx"
        out[rel] = n["_id"]
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("root", nargs="?", default="")
    ap.add_argument("workspace", nargs="?", default="")
    ap.add_argument("--doc", nargs=2, metavar=("NODE_ID", "DOCX"))
    ap.add_argument("--folder", default="", help="仅处理某 folder 子树（node id）")
    args = ap.parse_args()

    cookie = os.environ.get("THOUGHTS_COOKIE", "").strip()
    if not cookie and Path(".thoughts_cookie").is_file():
        cookie = Path(".thoughts_cookie").read_text(encoding="utf-8").strip()
    if not cookie:
        sys.exit("需要 THOUGHTS_COOKIE 或 .thoughts_cookie")

    session = requests.Session()
    session.headers.update({"Cookie": cookie, "X-Requested-With": "XMLHttpRequest"})

    if args.doc:
        node_id, docx = args.doc
        n = fix_docx_with_html(Path(docx), fetch_html(session, node_id))
        print(f"fixed_blocks={n} file={docx}")
        return

    if not args.root or not args.workspace:
        ap.print_help()
        sys.exit(1)

    root = Path(args.root)
    index: dict[str, str] = {}
    manifest = root / "export_manifest.json"
    if manifest.is_file():
        index = json.loads(manifest.read_text(encoding="utf-8"))
    else:
        nodes = list_nodes(session, args.workspace, args.folder)
        index = build_path_index(nodes)
    ok = fail = 0
    for rel, nid in sorted(index.items()):
        docx = root / rel
        if not docx.is_file():
            continue
        try:
            html_content = fetch_html(session, nid)
            n = fix_docx_with_html(docx, html_content)
            if n:
                ok += 1
                print(f"[fix] {rel} blocks={n}")
        except Exception as e:
            fail += 1
            print(f"[fail] {rel} {e}", file=sys.stderr)
        time.sleep(0.15)
    print(f"done fixed_files={ok} failed={fail}")


if __name__ == "__main__":
    main()
