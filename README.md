# 云效 Thoughts → 飞书知识库迁移手册

把阿里云云效 Thoughts（`thoughts.aliyun.com`）中的目录树导出为 Word，再导入飞书知识库，保留目录结构，正文尽量可编辑。

配置与密钥请放在本地 `.env`（参考 `.env.example`），不要提交 Cookie / token / App Secret。第三方导出工具致谢见 `NOTICE`。

---

## 目录结构

```text
docs-migrate/   # 或你的仓库根目录名
├── README.md
├── NOTICE                    # 第三方致谢
├── docs/
│   ├── 01-飞书权限清单.md
│   ├── 02-踩坑与FAQ.md
│   └── 03-迁移记录摘要.md
├── thoughtsexport/           # 第三方导出工具（源自 marknown/thoughtsexport）
│   ├── cmd/export_with_cookie/
│   ├── libs/
│   └── bin/                  # go build 后生成，勿提交二进制
├── feishu_import_v2.py
├── scripts/
├── .env.example
├── .gitignore
└── requirements.txt
```

### 第三方导出工具来源

本目录下的 `thoughtsexport/` 基于开源项目二次整合（含 Cookie 导出、路径中 `/` 处理等改动），**原作者仓库**：

- https://github.com/marknown/thoughtsexport

请在二次分发时保留对上游项目的致谢与许可证要求（以该仓库为准）。

配套用法：编译后使用 `thoughtsexport/bin/export_with_cookie`。

---

## 整体流程

```text
① 创建飞书自建应用并开通权限、发布版本
② 把应用机器人加为「目标知识库」管理员
③ 准备凭证：App ID/Secret、（可选）user_access_token、Thoughts Cookie
④ 用 thoughtsexport 从云效导出 docx 到本地目录
⑤ 配置 .env 后执行 feishu_import_v2.py 导入
⑥ （可选）批量打 L2 密级
```

---

## 一、创建飞书自建应用（机器人）

1. 打开 [飞书开放平台](https://open.feishu.cn/)，登录企业账号。
2. **创建企业自建应用**，记下：
   - `App ID`（形如 `cli_xxx`）
   - `App Secret`（只在创建/重置时可见，保存到本地 `.env`，不要发群）
3. 进入应用 → **权限管理**，按 [docs/01-飞书权限清单.md](docs/01-飞书权限清单.md) 开通权限。
   - 注意区分 **应用身份（tenant）** 与 **用户身份（user）**。
   - 导入走 **应用身份**；打文档密级 L2 只能走 **用户身份**。
4. **创建版本并发布**（权限不发布则 OpenAPI 仍不可用）。
5. 可选：在「应用能力 → 机器人」确认机器人已启用，记下机器人名称便于在知识库里搜索。

获取机器人 open_id（排障用）：

```bash
# 先拿到 tenant_access_token，再调用
curl -s -H "Authorization: Bearer $TENANT_TOKEN" \
  https://open.feishu.cn/open-apis/bot/v3/info
```

---

## 二、把机器人加入知识库管理员

导入目标必须是机器人有「可管理」权限的知识库节点。

1. 打开目标知识库（或目标父页面）。
2. 右上角 **… / 更多** → **设置** → **成员设置**（或「知识库成员」）。
3. **添加管理员**，搜索你的应用机器人名称，设为 **管理员**（仅「可阅读」不够）。
4. 验证：用应用身份调用 `GET /wiki/v2/spaces/get_node?token=<父节点token>`，应返回 `code=0`。

父节点 token 与 space_id：

- 浏览器打开父页面，URL 形如：`https://xxx.feishu.cn/wiki/<NODE_TOKEN>`
- `NODE_TOKEN` 即 `FEISHU_PARENT_WIKI_TOKEN`
- `FEISHU_SPACE_ID` 用上面 `get_node` 返回的 `data.node.space_id`

> 若中途机器人对知识库「突然 not found」，几乎都是成员权限被改掉或知识库被删/迁走。此时应用侧 `GET /wiki/v2/spaces` 往往看不到该空间。

---

## 三、准备凭证

### 3.1 应用凭证（导入用）

写入 `.env`（由 `.env.example` 复制）：

```bash
cp .env.example .env
# 编辑 .env：FEISHU_APP_ID / FEISHU_APP_SECRET / 父节点 / space_id / 导出目录
```

### 3.2 user_access_token（仅打 L2 需要）

应用身份 **无法** 开通 `docs:secure_label:write_only` 时，必须用人的 token：

1. 开放平台 → 你的应用 → **安全设置 / 免登** 等相关能力（按当前控制台指引配置重定向 URL）。
2. 走 [获取 user_access_token](https://open.feishu.cn/document/server-docs/authentication-management/access-token/get-user-access-token) 流程（OAuth 授权码 → user_access_token）。
3. 把 token 写入 `.env` 的 `FEISHU_USER_ACCESS_TOKEN=`，或本地文件 `.feishu_user_token`（已在 `.gitignore`）。
4. **会过期**。长任务导入可先不传 user token；导入完成后再刷新 token，执行 `scripts/set_l2_batch.py`。

自检：

```bash
curl -s -H "Authorization: Bearer $FEISHU_USER_ACCESS_TOKEN" \
  https://open.feishu.cn/open-apis/authen/v1/user_info
```

### 3.3 云效 Thoughts Cookie

导出工具需要登录态 Cookie（至少含会话相关字段，如 `TEAMBITION_SESSIONID`）。

常见做法：

1. 浏览器登录 [thoughts.aliyun.com](https://thoughts.aliyun.com) 并打开目标资料库。
2. 从开发者工具 Application → Cookies 复制，或用已有脚本从 Chrome 远程调试端口导出。
3. 写入 `.env` 的 `THOUGHTS_COOKIE=`，或文件 `.thoughts_cookie`（已在 `.gitignore`）。

**不要**把 Cookie 发到群里或写进文档正文。

---

## 四、从云效导出到本地

导出能力来自第三方工具 [marknown/thoughtsexport](https://github.com/marknown/thoughtsexport)（已放入本目录 `thoughtsexport/`）。

### 4.1 编译导出工具

```bash
cd thoughtsexport
# 需要本机安装 Go
go build -o bin/export_with_cookie ./cmd/export_with_cookie
# 可选：带 UI 的主程序
# go build -o bin/thoughts_export_macos_arm64 .
```

### 4.2 导出某个目录子树

URL 使用 **folders** 链接（只导出该目录及其子树）：

```text
https://thoughts.aliyun.com/workspaces/<workspaceId>/folders/<folderId>
```

```bash
cd /path/to/文档迁移
./scripts/run_export_thoughts.sh \
  'https://thoughts.aliyun.com/workspaces/<wsId>/folders/<folderId>' \
  docx
```

默认文件落在 `thoughtsexport/bin/<组织名>/<工作空间名>/` 下。导出结束后，把该目录路径填进 `.env`：

```bash
FEISHU_ROOT_DIR=/绝对路径/到/导出根目录
```

也可把导出结果复制到本仓库 `tmp_export/`（`tmp_*/` 已忽略）。

### 4.3 导出失败文件

工具会在导出根目录写 `下载失败的文件清单.txt`。部分文档云效侧 `export failed`（常见于含子文档的特殊节点），需人工在网页导出 PDF/Word 后放到对应路径再导入。

标题里的 `/` 会被替换成全角 `／`，避免落盘路径被拆坏。

---

## 五、导入飞书

### 5.1 依赖

```bash
pip3 install -r requirements.txt
```

### 5.2 配置检查清单

| 变量 | 说明 |
|------|------|
| `FEISHU_APP_ID` / `FEISHU_APP_SECRET` | 应用凭证 |
| `FEISHU_PARENT_WIKI_TOKEN` | 目标父节点 |
| `FEISHU_SPACE_ID` | 目标知识空间 |
| `FEISHU_ROOT_DIR` | 本地导出根目录 |
| `FEISHU_STATE_PATH` 等 | 建议每个任务单独文件名，避免互相覆盖 |
| `FEISHU_USER_ACCESS_TOKEN` | 可选；导入阶段可空，仅影响 L2 |

### 5.3 执行（必须单进程）

```bash
./scripts/run_import.sh --skip-probe
```

常用参数：

| 参数 | 含义 |
|------|------|
| `--skip-probe` | 跳过启动时的小文件权限探测 |
| `--limit=N` | 只再导入 N 个叶子文件（目录仍会尽量建齐） |
| `--fresh` | 清空本地 state 后重来（不会自动删飞书已有节点） |

脚本内带 **文件锁**，禁止两个导入进程同时跑（否则易产生同名重复页）。

### 5.4 导入行为摘要

- `.docx`（小于约 18MB）：走飞书「导入任务」→ 可编辑新版文档，再挂到知识库。
- 过大或非 docx：按文件节点上传挂载。
- 同名「目录 + 同名 docx」：正文导入为父节点（`dir+docx-editable`），**不再**往正文里插「子页面列表」。
- 纯空目录壳：创建空文档并插入「子页面列表」组件，便于导航。
- 导入前按「父节点下标题」做去重，降低重跑重复。

---

## 六、批量打 L2 密级

```bash
# 确保 .env 里 FEISHU_USER_ACCESS_TOKEN 有效
set -a && source .env && set +a
python3 scripts/set_l2_batch.py --state "$FEISHU_STATE_PATH"
```

密级 ID 默认读 `FEISHU_SECURE_LABEL_L2_ID`（示例为内部级，以你们租户配置为准）。

---

## 七、清理与重导

飞书 Wiki **没有**稳定的「删除节点」OpenAPI。实务做法：

1. 建一个「【待删-…】」目录。
2. 用 `POST /wiki/v2/spaces/:space_id/nodes/:node_token/move` 把旧树挪进去。
3. 清空本地 state，再重新导入。
4. 待删目录由人工在飞书客户端删除。

---

## 相关官方文档

- [知识库概述](https://open.feishu.cn/document/server-docs/docs/wiki-v2/wiki-overview)
- [创建知识空间节点](https://open.feishu.cn/document/server-docs/docs/wiki-v2/space-node/create)
- [导入文件概述](https://open.feishu.cn/document/server-docs/docs/drive-v1/import_task/import-user-guide)
- [移动知识空间节点](https://open.feishu.cn/document/server-docs/docs/wiki-v2/space-node/move)
- [获取 tenant_access_token](https://open.feishu.cn/document/server-docs/authentication-management/access-token/tenant-access-token-internal)
