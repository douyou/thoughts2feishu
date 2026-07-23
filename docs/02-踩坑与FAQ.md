# 踩坑与 FAQ

## 1. 飞书出现大量同名重复文档

**原因**：两个导入进程同时跑（例如 `nohup` 后台 + 前台续跑），都读到「未完成」的 state，各自 `move_docs_to_wiki` 一次。飞书允许同名节点并存。

**处理**：

- 只用 `./scripts/run_import.sh` 单进程；脚本带 flock。
- 不要并行开第二份导入。
- 已产生的重复：挪到「待删」目录后人工删除，再按 state 去重续跑或清 state 重导。

## 2. 正文设计文档顶部多了「子页面列表 / 子目录」

**原因**：旧逻辑把「同名目录 + 同名 docx」合成的正文父页也当成空目录，插入了 `block_type=51`。

**现状**：`feishu_import_v2.py` 仅对空目录壳插子页面列表；`dir+docx-editable` 只打密级、不插组件。

## 3. 表格导入后显示「内容已删除」

**原因**：Thoughts 导出的部分 docx 缺少 `tblGrid`，飞书解析异常。

**处理**：导入前 `repair_docx_tables()` 会自动补网格（主程序已内置）。

## 4. 路径被拆成多级（如 `SPU/SKU`）

**原因**：标题中的 `/` 被当成路径分隔符。

**处理**：导出工具将 `/`、`\` 替换为全角 `／`、`＼`（`thoughtsexport` 侧已修）。

## 5. 云效部分文档 `export failed`

部分节点无法转 docx（常见于带 `subDocumentCount` 的特殊文档）。需网页手动导出后放入对应本地路径，再跑导入。

## 6. user_access_token 中途失效

长时导入请 **不要依赖** user token 做主鉴权；导入用 App ID/Secret。导入结束后再刷新 token 跑 `set_l2_batch.py`。

## 7. 目标知识库中途消失 / not found

应用对某个 `space_id` 突然不可见时：

1. 检查机器人是否仍是该库管理员。
2. `GET /wiki/v2/spaces` 是否还能列出该空间。
3. 若无法恢复，可改挂到仍有权限的知识库父节点，更新 `.env` 后清 state 重导（或从断点续跑前先确认父节点 `get_node` 成功）。

## 8. 分批 `--limit` 续跑

可以，但必须 **同一时间只有一个进程**，且共用同一 `FEISHU_STATE_PATH`。不要用已废弃的 `run_import_batches.sh` 多开。
