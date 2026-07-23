# Thoughts 文档导出工具（第三方）

本目录基于开源项目二次整合：

**上游仓库：** https://github.com/marknown/thoughtsexport

用于将阿里云 Thoughts / 所思知识库导出为 docx、html，供飞书等系统导入。本地改动可能包括 Cookie 导出入口、路径分隔符处理等。

## 编译

需要本机安装 Go：

```bash
cd thoughtsexport
go build -o bin/export_with_cookie ./cmd/export_with_cookie
```

## 推荐用法（从仓库根目录）

```bash
./scripts/run_export_thoughts.sh \
  'https://thoughts.aliyun.com/workspaces/<wsId>/folders/<folderId>' \
  docx
```

需设置环境变量 `THOUGHTS_COOKIE`（或仓库根目录 `.thoughts_cookie`，且勿提交）。

## 说明

1. 主程序 UI 导出可能需要安装 Chrome。
2. 下载目录默认为：`二进制所在目录/<组织名>/<知识库名>/`。
3. 文档标题中的 `/`、`\` 会替换为全角字符，避免落盘路径被拆坏。
4. 请遵守上游仓库的许可与致谢要求（见仓库根目录 `NOTICE`）。
