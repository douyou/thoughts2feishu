package main

import (
	"fmt"
	"os"
	"strings"
	"thoughtsexport/libs/logic"
)

// 用法:
//   THOUGHTS_COOKIE='...' go run ./cmd/export_with_cookie [url] [docx|html|all]
func main() {
	cookie := strings.TrimSpace(os.Getenv("THOUGHTS_COOKIE"))
	if cookie == "" {
		fmt.Fprintln(os.Stderr, "请设置环境变量 THOUGHTS_COOKIE")
		os.Exit(1)
	}
	targetURL := ""
	fileType := "docx"
	if len(os.Args) > 1 && os.Args[1] != "" {
		targetURL = os.Args[1]
	}
	if targetURL == "" {
		fmt.Fprintln(os.Stderr, "用法: export_with_cookie <Thoughts文件夹URL> [docx|html|all]")
		fmt.Fprintln(os.Stderr, "示例: export_with_cookie 'https://thoughts.aliyun.com/workspaces/<wsId>/folders/<folderId>' docx")
		os.Exit(1)
	}
	if len(os.Args) > 2 && os.Args[2] != "" {
		fileType = os.Args[2]
	}
	fmt.Println("开始导出…")
	logic.ExportOne(targetURL, cookie, fileType)
	fmt.Println("导出流程结束")
}
