package logic

import (
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"path"
	"path/filepath"
	"strings"
	"thoughtsexport/libs/request"
	"thoughtsexport/libs/utils"
)

var rootPath = ""

func ExportOne(url string, cookie string, fileType string) {
	ExportPaths(url, cookie, fileType, nil)
}

// ExportPaths 导出指定路径；paths 为空时导出全部节点。
func ExportPaths(url string, cookie string, fileType string, paths []string) {
	parts := strings.Split(url, "/")
	hashSpace := parts[4]
	baseURL := "https://" + parts[2]
	if strings.HasPrefix(url, "http://") {
		baseURL = "http://" + parts[2]
	}

	// 若 URL 指向 folders/{id}，仅导出该目录子树
	startHash := hashSpace
	for i := 0; i+1 < len(parts); i++ {
		if parts[i] == "folders" && parts[i+1] != "" {
			startHash = parts[i+1]
			break
		}
	}

	req := request.NewRequestWithBase(cookie, hashSpace, baseURL)

	workspace, err := req.GetWorkspace(hashSpace)
	if nil != err {
		panic(err)
	}

	// 如果没有导出权限，开启导出权限
	needCloseOutput := false
	if workspace.WorkspaceSecurity.DisableOutput {

		succeed, err := req.EnableOutput(hashSpace, true)
		if nil != err {
			panic(err)
		}

		if !succeed {
			panic("本文档无法下载，开启导出权限失败。请文档所有者在本工具登录后再尝试")
		}

		// 之前没有导出权限，现在临时开启，下载完成要关闭导出权限
		needCloseOutput = true
	}

	prefixPath := fmt.Sprintf("%s/%s", workspace.Organization.Name, workspace.Name)
	SetRootPath(GetCurrentDirectory() + "/" + prefixPath)
	fmt.Printf("所有文件将保存至 %s\n", GetRootPath())
	if startHash != hashSpace {
		fmt.Printf("仅导出目录子树: %s\n", startHash)
	}

	nodes, err := req.GetAllNodes(startHash, "")
	if nil != err {
		panic(err)
	}
	fmt.Printf("分析完成 %s\n", prefixPath)

	want := map[string]bool{}
	for _, p := range paths {
		want[p] = true
	}
	if len(want) > 0 {
		filtered := make([]*request.Node, 0, len(want))
		for _, node := range nodes {
			if want[node.Path] {
				filtered = append(filtered, node)
			}
		}
		nodes = filtered
		fmt.Printf("按路径过滤后待处理: %d\n", len(nodes))
		for _, n := range nodes {
			fmt.Printf("  - [%s] %s id=%s withChild=%v\n", n.Type, n.Path, n.ID, n.WithChild)
		}
	}

	total := len(nodes)
	counter := 0
	success, failed := 0, 0
	codeFixedFiles, codeFixedBlocks := 0, 0
	fixDocx := fileType == "all" || fileType == "docx"
	for _, node := range nodes {
		counter++
		fmt.Printf("当前进度 %d/%d [%.2f%%] 正在下载文档 %s \r", counter, total, float64(counter)*float64(100)/float64(total), node.Path)

		if node.Type == "folder" {
			// log.Println("我是空目录" + node.Path)
			CreateDir(GetRootPath() + node.Path + "/")
		} else if node.Type == "document" {
			// 下载 docx
			if fileType == "all" || fileType == "docx" {
				downloadInfo, err := req.GetDownloadUrl(node.ID, node.Path, "docx")
				if nil != err {
					failed++
					LogDownloadFailedInfo(node, err)
					continue
				}

				docxPath := GetRootPath() + downloadInfo.FullPath
				_, err = DownloadFile(downloadInfo.DownURL, docxPath)
				if nil != err {
					failed++
					LogDownloadFailedInfo(node, err)
					continue
				}
				if fixDocx {
					n, fixErr := FixCodeBlocksInDocx(req, node.ID, docxPath)
					if fixErr != nil {
						fmt.Printf("\n修正代码块失败 %s: %v\n", node.Path, fixErr)
					} else if n > 0 {
						codeFixedFiles++
						codeFixedBlocks += n
					}
				}
			}

			// 下载 html
			if fileType == "all" || fileType == "html" {
				downloadInfo, err := req.GetDownloadUrl(node.ID, node.Path, "html")
				if nil != err {
					failed++
					LogDownloadFailedInfo(node, err)
					continue
				}

				_, err = DownloadFile(downloadInfo.DownURL, GetRootPath()+downloadInfo.FullPath)
				if nil != err {
					failed++
					LogDownloadFailedInfo(node, err)
					continue
				}
			}
		} else {
			downloadInfo, err := req.GetDownloadUrlByDetail(node.ID, node.Path)
			if nil != err {
				failed++
				LogDownloadFailedInfo(node, err)
				continue
			}

			_, err = DownloadFile(downloadInfo.DownURL, GetRootPath()+downloadInfo.FullPath)
			if nil != err {
				failed++
				LogDownloadFailedInfo(node, err)
				continue
			}
		}

		success++
		fmt.Printf("[已完成] %s\n", node.Path)
	}

	fmt.Printf("所有文件已保存至 %s\n", GetRootPath())
	fmt.Printf("本次统计: 成功=%d 失败=%d\n", success, failed)

	if fileType == "all" || fileType == "docx" {
		fixedFiles, fixedLinks, fixErr := FixThoughtsLinksInTree(GetRootPath(), hashSpace, baseURL, cookie, nodes)
		if fixErr != nil {
			fmt.Printf("修正 Thoughts 内链失败: %v\n", fixErr)
		} else if fixedLinks > 0 {
			fmt.Printf("已修正 Thoughts 内链: %d 个文件, %d 处链接\n", fixedFiles, fixedLinks)
		}

		if codeFixedBlocks > 0 {
			fmt.Printf("已修正代码块换行: %d 个文件, %d 处代码块\n", codeFixedFiles, codeFixedBlocks)
		}

		if err := saveExportManifest(GetRootPath(), nodes); err != nil {
			fmt.Printf("写入 export_manifest.json 失败: %v\n", err)
		}
	}

	// 关闭导出权限
	if needCloseOutput {
		_, err := req.EnableOutput(hashSpace, false)
		if nil != err {
			panic(err)
		}
	}
}

func CreateDir(fullPath string) error {
	dirPath := path.Dir(fullPath)

	_, err := os.Stat(dirPath)
	if err == nil {
		return nil
	}

	if os.IsNotExist(err) {
		err = os.MkdirAll(dirPath, os.ModePerm)
		if err != nil {
			return err
		}

		// err = os.Chmod(dirPath, os.ModeDir)
		// if err != nil {
		// 	return err
		// }
	}

	return err
}

func DownloadFile(url string, filepath string) (int64, error) {
	if utils.FileExist(filepath) {
		return 0, nil
	}

	CreateDir(filepath)
	file, err := os.OpenFile(filepath, os.O_RDWR|os.O_CREATE|os.O_APPEND, 0644)
	if err != nil {
		return 0, err
	}
	defer file.Close()

	resp, err := http.Get(url)
	if err != nil {
		return 0, err
	}
	defer resp.Body.Close()

	n, err := io.Copy(file, resp.Body)

	return n, err
}

func LogFailedInfo(info string) {
	path := GetRootPath() + "/下载失败的文件清单.txt"
	utils.FileAppend(path, info)
}

func LogDownloadFailedInfo(node *request.Node, err error) {
	info := fmt.Sprintf("%s %s\n", node.Path, err.Error())
	LogFailedInfo(info)
	fmt.Println(info)
}

func SetRootPath(path string) {
	rootPath = path
}

func GetRootPath() string {
	return rootPath
}

func saveExportManifest(rootDir string, nodes []*request.Node) error {
	manifest := map[string]string{}
	for _, n := range nodes {
		if n == nil || n.Type != "document" || n.ID == "" {
			continue
		}
		rel := strings.TrimPrefix(n.Path, "/") + ".docx"
		manifest[rel] = n.ID
	}
	data, err := json.MarshalIndent(manifest, "", "  ")
	if err != nil {
		return err
	}
	return os.WriteFile(filepath.Join(rootDir, "export_manifest.json"), data, 0o644)
}
