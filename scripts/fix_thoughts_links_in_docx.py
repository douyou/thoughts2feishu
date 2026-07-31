#!/usr/bin/env python3
"""修复导出 docx 中 Thoughts 内链：错误 URL / 占位文本 / 登录页标题 / 裸链。

与 thoughtsexport/libs/logic/fixdocx.go 规则保持一致，可单独对已有导出目录执行。
"""

from __future__ import annotations

import re
import sys
import time
import zipfile
from io import BytesIO
from pathlib import Path

import requests

DOC_LINK_RE = re.compile(
    r"(?:undefined|https?://[^/]+)?/workspaces/([a-f0-9]+)/docs/([a-f0-9]+)",
    re.I,
)
REL_TARGET_RE = re.compile(r'Id="(rId\d+)" Target="([^"]+)"')
HYPERLINK_BLOCK_RE = re.compile(
    r'<w:hyperlink r:id="(rId\d+)">(?:(?!</w:hyperlink>).)*?</w:hyperlink>',
    re.DOTALL,
)
WT_TEXT_RE = re.compile(r"(<w:t(?:\s+xml:space=\"preserve\")?>)([^<]*)(</w:t>)")
THOUGHTS_PLACEHOLDER = "thoughts 文档"


def xml_escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def needs_fix(text: str, title: str) -> bool:
    text = text.strip()
    if not title:
        return False
    if text == THOUGHTS_PLACEHOLDER:
        return True
    if "thoughts.aliyun.com" in text or "undefined/workspaces" in text:
        return True
    if "阿里云登录" in text:
        return True
    return text != title


def load_cookie() -> str:
    for p in (Path(__file__).resolve().parent.parent / ".thoughts_cookie", Path(".thoughts_cookie")):
        if p.is_file():
            return p.read_text(encoding="utf-8").strip()
    raise SystemExit("缺少 .thoughts_cookie")


def fetch_title(session: requests.Session, ws: str, base: str, doc_id: str, cache: dict[str, str]) -> str:
    key = f"{ws}:{doc_id}"
    if key in cache:
        return cache[key]
    url = f"{base}/api/workspaces/{ws}/nodes/{doc_id}?pageSize=1000"
    for attempt in range(4):
        try:
            r = session.get(url, timeout=30)
            if r.status_code == 200:
                title = (r.json().get("title") or "").strip()
                if title:
                    cache[key] = title
                    return title
            break
        except Exception:
            time.sleep(1.5 * (attempt + 1))
    cache[key] = doc_id
    return doc_id


def fix_docx(path: Path, ws: str, base: str, session: requests.Session, cache: dict[str, str]) -> list[str]:
    changes: list[str] = []
    with zipfile.ZipFile(path, "r") as zin:
        doc = zin.read("word/document.xml").decode("utf-8")
        rels_path = "word/_rels/document.xml.rels"
        rels = zin.read(rels_path).decode("utf-8")
        rel_map = {m.group(1): m.group(2) for m in REL_TARGET_RE.finditer(rels)}
        new_rels, new_doc = rels, doc

        for rid, target in rel_map.items():
            decoded = target.replace("&amp;", "&")
            m = DOC_LINK_RE.search(decoded)
            if not m:
                continue
            fixed = f"{base}/workspaces/{m.group(1)}/docs/{m.group(2)}"
            escaped = fixed.replace("&", "&amp;")
            if target != escaped:
                new_rels = new_rels.replace(f'Id="{rid}" Target="{target}"', f'Id="{rid}" Target="{escaped}"', 1)
                changes.append(f"  url {rid}: {decoded} -> {fixed}")

        for block in HYPERLINK_BLOCK_RE.finditer(doc):
            rid = block.group(1)
            inner = block.group(0)
            target = rel_map.get(rid, "").replace("&amp;", "&")
            m = DOC_LINK_RE.search(target)
            if not m:
                continue
            title = fetch_title(session, m.group(1), base, m.group(2), cache)
            tm = WT_TEXT_RE.search(inner)
            if not tm:
                continue
            old_text = tm.group(2)
            if not needs_fix(old_text, title):
                continue
            new_inner = inner[: tm.start(2)] + xml_escape(title) + inner[tm.end(2) :]
            new_doc = new_doc.replace(inner, new_inner, 1)
            changes.append(f"  text {rid}: {old_text!r} -> {title!r}")

        if not changes:
            return changes

        buf = BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zout:
            for item in zin.infolist():
                data = zin.read(item.filename)
                if item.filename == "word/document.xml":
                    data = new_doc.encode("utf-8")
                elif item.filename == rels_path:
                    data = new_rels.encode("utf-8")
                zout.writestr(item, data)
        path.write_bytes(buf.getvalue())
    return changes


def main() -> None:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
    if not root.is_dir():
        sys.exit(f"目录不存在: {root}")

    cookie = load_cookie()
    base = "https://thoughts.aliyun.com"
    ws = ""
    # 从任意 docx 内链推断 workspace；找不到则要求传参
    for p in root.rglob("*.docx"):
        with zipfile.ZipFile(p) as z:
            rels = z.read("word/_rels/document.xml.rels").decode("utf-8", errors="replace")
        m = DOC_LINK_RE.search(rels.replace("&amp;", "&"))
        if m:
            ws = m.group(1)
            break
    if len(sys.argv) > 2:
        ws = sys.argv[2]
    if not ws:
        sys.exit("无法推断 workspace_id，用法: fix_thoughts_links_in_docx.py <dir> [workspace_id]")

    session = requests.Session()
    session.headers.update(
        {
            "Cookie": cookie,
            "Accept": "application/json",
            "Referer": f"{base}/workspaces/{ws}/overview",
            "User-Agent": "Mozilla/5.0",
        }
    )
    cache: dict[str, str] = {}
    fixed_files = 0
    total = 0
    print(f"scan_root={root} workspace={ws}")
    for p in sorted(root.rglob("*.docx")):
        changes = fix_docx(p, ws, base, session, cache)
        if changes:
            fixed_files += 1
            total += len(changes)
            print(f"\n[fix] {p.relative_to(root)}")
            for c in changes:
                print(c)
    print(f"\ndone fixed_files={fixed_files} changes={total}")


if __name__ == "__main__":
    main()
