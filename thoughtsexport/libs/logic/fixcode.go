package logic

import (
	"archive/zip"
	"bytes"
	"fmt"
	"html"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"thoughtsexport/libs/request"
)

var (
	preBlockRe      = regexp.MustCompile(`(?is)<pre[^>]*>([\s\S]*?)</pre>`)
	codeBlockLineRe = regexp.MustCompile(`(?is)<code[^>]*data-type="code-block"[^>]*>([\s\S]*?)</code>`)
	paragraphRe     = regexp.MustCompile(`(?s)<w:p>.*?</w:p>`)
	pPrRe           = regexp.MustCompile(`(?s)<w:pPr>.*?</w:pPr>`)
	wtContentRe     = regexp.MustCompile(`(?is)(<w:pPr>[\s\S]*?</w:pPr>)([\s\S]*)(</w:p>)`)
	firstWtRe       = regexp.MustCompile(`(?is)<w:t(?:\s+xml:space="preserve")?>([^<]*)</w:t>`)
)

func isSourceCodeParagraph(para string) bool {
	ppr := pPrRe.FindString(para)
	return ppr != "" && strings.Contains(ppr, `w:val="SourceCode"`)
}

func findSourceCodeParagraphs(doc string) []string {
	var out []string
	for _, para := range paragraphRe.FindAllString(doc, -1) {
		if isSourceCodeParagraph(para) {
			out = append(out, para)
		}
	}
	return out
}

func stripHTMLTags(s string) string {
	s = regexp.MustCompile(`(?s)<[^>]+>`).ReplaceAllString(s, "")
	return html.UnescapeString(s)
}

func parseCodeBlocksFromHTML(raw string) [][]string {
	var blocks [][]string
	for _, pre := range preBlockRe.FindAllStringSubmatch(raw, -1) {
		var lines []string
		for _, m := range codeBlockLineRe.FindAllStringSubmatch(pre[1], -1) {
			line := stripHTMLTags(m[1])
			lines = append(lines, line)
		}
		if len(lines) > 0 {
			blocks = append(blocks, lines)
		}
	}
	return blocks
}

type htmlExportClient interface {
	GetDownloadUrl(hash string, prefixPath string, fileType string) (*request.NodeDownload, error)
}

func downloadHTMLExport(req htmlExportClient, nodeID string) (string, error) {
	downloadInfo, err := req.GetDownloadUrl(nodeID, "", "html")
	if err != nil {
		return "", err
	}
	resp, err := http.Get(downloadInfo.DownURL)
	if err != nil {
		return "", err
	}
	defer resp.Body.Close()
	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return "", err
	}
	zr, err := zip.NewReader(bytes.NewReader(body), int64(len(body)))
	if err != nil {
		// 少数情况下直接返回 html
		return string(body), nil
	}
	for _, f := range zr.File {
		if strings.HasSuffix(strings.ToLower(f.Name), ".html") {
			rc, err := f.Open()
			if err != nil {
				return "", err
			}
			data, err := io.ReadAll(rc)
			rc.Close()
			if err != nil {
				return "", err
			}
			return string(data), nil
		}
	}
	return "", fmt.Errorf("html zip has no .html file")
}

func codeXmlEscapeText(s string) string {
	s = strings.ReplaceAll(s, "&", "&amp;")
	s = strings.ReplaceAll(s, "<", "&lt;")
	s = strings.ReplaceAll(s, ">", "&gt;")
	return s
}

func buildSourceCodeRuns(lines []string) string {
	if len(lines) == 0 {
		return ""
	}
	var b strings.Builder
	for i, line := range lines {
		if i > 0 {
			b.WriteString(`<w:r><w:rPr><w:rStyle w:val="VerbatimChar"/></w:rPr><w:br/></w:r>`)
		}
		b.WriteString(`<w:r><w:rPr><w:rStyle w:val="VerbatimChar"/></w:rPr><w:t xml:space="preserve">`)
		b.WriteString(codeXmlEscapeText(line))
		b.WriteString(`</w:t></w:r>`)
	}
	return b.String()
}

func replaceSourceCodeParagraph(para string, lines []string) string {
	if len(lines) <= 1 {
		return para
	}
	flat := strings.Join(lines, "")
	m := firstWtRe.FindStringSubmatch(para)
	if m == nil {
		return para
	}
	current := html.UnescapeString(m[1])
	compact := strings.ReplaceAll(strings.ReplaceAll(current, "\r", ""), "\n", "")
	compactFlat := strings.ReplaceAll(flat, "\r", "")
	if compact != compactFlat && compactFlat != "" {
		// 内容不一致时不强行替换
		return para
	}
	wm := wtContentRe.FindStringSubmatch(para)
	if wm == nil {
		return para
	}
	return "<w:p>" + wm[1] + buildSourceCodeRuns(lines) + wm[3]
}

func fixDocxCodeBlocksWithHTML(docxPath string, htmlContent string) (int, error) {
	blocks := parseCodeBlocksFromHTML(htmlContent)
	if len(blocks) == 0 {
		return 0, nil
	}

	raw, err := os.ReadFile(docxPath)
	if err != nil {
		return 0, err
	}
	zr, err := zip.NewReader(bytes.NewReader(raw), int64(len(raw)))
	if err != nil {
		return 0, err
	}

	var docXML []byte
	for _, f := range zr.File {
		if f.Name == "word/document.xml" {
			docXML, err = readZipFile(f)
			if err != nil {
				return 0, err
			}
			break
		}
	}
	if len(docXML) == 0 {
		return 0, nil
	}

	doc := string(docXML)
	paras := findSourceCodeParagraphs(doc)
	if len(paras) == 0 {
		return 0, nil
	}

	changes := 0
	newDoc := doc
	limit := len(paras)
	if len(blocks) < limit {
		limit = len(blocks)
	}
	for i := 0; i < limit; i++ {
		if len(blocks[i]) <= 1 {
			continue
		}
		fixed := replaceSourceCodeParagraph(paras[i], blocks[i])
		if fixed == paras[i] {
			continue
		}
		newDoc = strings.Replace(newDoc, paras[i], fixed, 1)
		changes++
	}
	if changes == 0 {
		return 0, nil
	}

	var buf bytes.Buffer
	zw := zip.NewWriter(&buf)
	for _, f := range zr.File {
		data, err := readZipFile(f)
		if err != nil {
			return 0, err
		}
		if f.Name == "word/document.xml" {
			data = []byte(newDoc)
		}
		h := &zip.FileHeader{Name: f.Name, Method: f.Method}
		h.SetModTime(f.Modified)
		w, err := zw.CreateHeader(h)
		if err != nil {
			return 0, err
		}
		if _, err := w.Write(data); err != nil {
			return 0, err
		}
	}
	if err := zw.Close(); err != nil {
		return 0, err
	}
	if err := os.WriteFile(docxPath, buf.Bytes(), 0o644); err != nil {
		return 0, err
	}
	return changes, nil
}

// FixCodeBlocksInDocx 用同文档 HTML 导出恢复 SourceCode 段落换行。
func FixCodeBlocksInDocx(req htmlExportClient, nodeID, docxPath string) (int, error) {
	htmlContent, err := downloadHTMLExport(req, nodeID)
	if err != nil {
		return 0, err
	}
	return fixDocxCodeBlocksWithHTML(docxPath, htmlContent)
}

// FixCodeBlocksInTree 遍历已导出 docx，按节点 ID 修正代码块换行。
func FixCodeBlocksInTree(rootDir string, req htmlExportClient, nodes []*request.Node) (int, int, error) {
	pathToID := map[string]string{}
	for _, n := range nodes {
		if n == nil || n.Type != "document" || n.ID == "" {
			continue
		}
		pathToID[n.Path] = n.ID
	}

	fixedFiles := 0
	fixedBlocks := 0
	err := filepath.Walk(rootDir, func(path string, info os.FileInfo, err error) error {
		if err != nil {
			return err
		}
		if info.IsDir() || !strings.HasSuffix(strings.ToLower(info.Name()), ".docx") {
			return nil
		}
		rel, err := filepath.Rel(rootDir, path)
		if err != nil {
			return err
		}
		id := pathToID[relDocPath(rel)]
		if id == "" {
			return nil
		}
		n, fixErr := FixCodeBlocksInDocx(req, id, path)
		if fixErr != nil {
			fmt.Printf("修正代码块失败 %s: %v\n", rel, fixErr)
			return nil
		}
		if n > 0 {
			fixedFiles++
			fixedBlocks += n
		}
		return nil
	})
	return fixedFiles, fixedBlocks, err
}

func relDocPath(rel string) string {
	rel = filepath.ToSlash(rel)
	rel = strings.TrimPrefix(rel, "/")
	if strings.HasSuffix(strings.ToLower(rel), ".docx") {
		return "/" + rel[:len(rel)-5]
	}
	return "/" + rel
}
