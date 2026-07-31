package logic

import (
	"archive/zip"
	"bytes"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"thoughtsexport/libs/request"
)

const thoughtsPlaceholder = "thoughts 文档"

var (
	docLinkRe = regexp.MustCompile(`(?i)(?:undefined|https?://[^/]+)?/workspaces/([a-f0-9]+)/docs/([a-f0-9]+)`)
	relTargetRe = regexp.MustCompile(`Id="(rId\d+)" Target="([^"]+)"`)
	hyperlinkBlockRe = regexp.MustCompile(`(?s)<w:hyperlink r:id="(rId\d+)">(.*?)</w:hyperlink>`)
	wtTextRe = regexp.MustCompile(`(?s)(<w:t(?:\s+xml:space="preserve")?>)([^<]*)(</w:t>)`)
)

func buildTitleMap(nodes []*request.Node, workspaceID string) map[string]string {
	out := make(map[string]string, len(nodes))
	for _, n := range nodes {
		if n == nil || n.ID == "" {
			continue
		}
		title := strings.TrimSpace(n.Title)
		if title != "" {
			out[workspaceID+":"+n.ID] = title
			out[n.ID] = title
		}
	}
	return out
}

func needsFixLinkText(text, title, target string) bool {
	if title == "" {
		return false
	}
	text = strings.TrimSpace(text)
	if text == thoughtsPlaceholder {
		return true
	}
	if strings.Contains(text, "thoughts.aliyun.com") || strings.Contains(text, "undefined/workspaces") {
		return true
	}
	if strings.Contains(text, "阿里云登录") {
		return true
	}
	if text != title {
		return true
	}
	_ = target
	return false
}

func xmlEscapeText(s string) string {
	s = strings.ReplaceAll(s, "&", "&amp;")
	s = strings.ReplaceAll(s, "<", "&lt;")
	s = strings.ReplaceAll(s, ">", "&gt;")
	return s
}

func buildThoughtsURL(baseURL, workspaceID, docID string) string {
	baseURL = strings.TrimRight(baseURL, "/")
	return fmt.Sprintf("%s/workspaces/%s/docs/%s", baseURL, workspaceID, docID)
}

func resolveDocTitle(wsID, docID string, titleByID map[string]string, cookie, baseURL string, cache map[string]string) string {
	key := wsID + ":" + docID
	if t := titleByID[key]; t != "" {
		return t
	}
	if t := titleByID[docID]; t != "" {
		return t
	}
	if t := cache[key]; t != "" {
		return t
	}
	if cookie != "" && wsID != "" {
		if t, err := request.GetNodeTitle(cookie, wsID, baseURL, docID); err == nil && t != "" {
			cache[key] = t
			return t
		}
	}
	cache[key] = docID
	return docID
}

func fixDocxFile(path, workspaceID, baseURL, cookie string, titleByID map[string]string, cache map[string]string) (int, error) {
	raw, err := os.ReadFile(path)
	if err != nil {
		return 0, err
	}
	zr, err := zip.NewReader(bytes.NewReader(raw), int64(len(raw)))
	if err != nil {
		return 0, err
	}

	var docXML, relsXML []byte
	relsPath := "word/_rels/document.xml.rels"
	for _, f := range zr.File {
		switch f.Name {
		case "word/document.xml":
			docXML, err = readZipFile(f)
			if err != nil {
				return 0, err
			}
		case relsPath:
			relsXML, err = readZipFile(f)
			if err != nil {
				return 0, err
			}
		}
	}
	if len(docXML) == 0 {
		return 0, nil
	}
	doc := string(docXML)
	rels := string(relsXML)

	relMap := map[string]string{}
	for _, m := range relTargetRe.FindAllStringSubmatch(rels, -1) {
		relMap[m[1]] = m[2]
	}

	changes := 0
	newRels := rels
	for rid, target := range relMap {
		decoded := strings.ReplaceAll(target, "&amp;", "&")
		match := docLinkRe.FindStringSubmatch(decoded)
		if match == nil {
			continue
		}
		fixed := buildThoughtsURL(baseURL, match[1], match[2])
		escaped := strings.ReplaceAll(fixed, "&", "&amp;")
		if target != escaped {
			newRels = strings.Replace(newRels, `Id="`+rid+`" Target="`+target+`"`, `Id="`+rid+`" Target="`+escaped+`"`, 1)
			changes++
		}
	}

	newDoc := doc
	for _, block := range hyperlinkBlockRe.FindAllStringSubmatch(doc, -1) {
		rid := block[1]
		inner := block[0]
		target := strings.ReplaceAll(relMap[rid], "&amp;", "&")
		match := docLinkRe.FindStringSubmatch(target)
		if match == nil {
			continue
		}
		title := resolveDocTitle(match[1], match[2], titleByID, cookie, baseURL, cache)
		text := extractHyperlinkText(inner)
		if !needsFixLinkText(text, title, target) {
			continue
		}
		fixedInner := replaceFirstWtText(inner, xmlEscapeText(title))
		if fixedInner == inner {
			continue
		}
		newDoc = strings.Replace(newDoc, inner, fixedInner, 1)
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
		switch f.Name {
		case "word/document.xml":
			data = []byte(newDoc)
		case relsPath:
			data = []byte(newRels)
		}
		h := &zip.FileHeader{
			Name:   f.Name,
			Method: f.Method,
		}
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
	if err := os.WriteFile(path, buf.Bytes(), 0o644); err != nil {
		return 0, err
	}
	return changes, nil
}

func extractHyperlinkText(inner string) string {
	m := wtTextRe.FindStringSubmatch(inner)
	if m == nil {
		return ""
	}
	return m[2]
}

func replaceFirstWtText(inner, newText string) string {
	loc := wtTextRe.FindStringSubmatchIndex(inner)
	if loc == nil {
		return inner
	}
	return inner[:loc[4]] + newText + inner[loc[5]:]
}

func readZipFile(f *zip.File) ([]byte, error) {
	rc, err := f.Open()
	if err != nil {
		return nil, err
	}
	defer rc.Close()
	return io.ReadAll(rc)
}

// FixThoughtsLinksInTree 修正导出 docx 中的 Thoughts 内链文本与 URL。
func FixThoughtsLinksInTree(rootDir, workspaceID, baseURL, cookie string, nodes []*request.Node) (int, int, error) {
	titleByID := buildTitleMap(nodes, workspaceID)
	cache := map[string]string{}
	fixedFiles := 0
	fixedLinks := 0

	err := filepath.Walk(rootDir, func(path string, info os.FileInfo, err error) error {
		if err != nil {
			return err
		}
		if info.IsDir() || !strings.HasSuffix(strings.ToLower(info.Name()), ".docx") {
			return nil
		}
		n, fixErr := fixDocxFile(path, workspaceID, baseURL, cookie, titleByID, cache)
		if fixErr != nil {
			return fixErr
		}
		if n > 0 {
			fixedFiles++
			fixedLinks += n
		}
		return nil
	})
	return fixedFiles, fixedLinks, err
}
