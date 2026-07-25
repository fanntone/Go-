# Go 底層原理筆記

一份用**台灣用語**寫的 Go 語言設計與實作筆記，涵蓋編譯器、資料結構、語言核心、並行、記憶體管理與標準函式庫。內容全部對照 **Go 1.26** 重新校對。

本地端靜態網站，零外部相依（不連 CDN、不需要網路）。

## 快速開始

```bash
pip install -r requirements.txt
python build.py --serve
```

瀏覽器打開 <http://localhost:8080>。

只要產生檔案不啟伺服器：

```bash
python build.py
```

輸出在 `site/`。

> 直接用 `file://` 開 `site/index.html` 也能看，但**全站搜尋會失效**（`fetch` 讀不到 `search-index.json`）。要用搜尋就跑 `--serve`。

## 專案結構

```
go-internals-tw/
├── build.py              靜態網站產生器
├── verify_examples.py    抽出並驗證所有 Go 範例
├── check_links.py        檢查站內連結與錨點
├── requirements.txt
├── content/
│   ├── _book.yml         書名、Part 定義、首頁文案
│   └── NNNN-*.md         章節內容（檔名數字決定排序）
├── theme/
│   ├── base.html         HTML 樣板
│   ├── style.css         版型與亮／暗主題
│   └── app.js            主題切換、搜尋、程式碼複製、目錄高亮
└── site/                 產生結果（可安全刪除，重跑 build.py 即可）
```

## 內容大綱

| Part | 主題 | 章節數 |
| --- | --- | --- |
| 0 | 開始之前 — 分層心智模型、用語約定、工具箱 | 2 |
| 1 | 編譯器 — 流程、詞法語法、型別檢查、SSA、逃逸分析、連結 | 6 |
| 2 | 資料結構 — array、slice、map（Swiss Table）、string | 4 |
| 3 | 語言核心 — 呼叫慣例、interface、reflect | 3 |
| 4 | 關鍵字 — for/range、select、defer、panic、make/new | 5 |
| 5 | 並行 — context、sync、timer、channel、GMP、netpoller、sysmon | 7 |
| 6 | 記憶體管理 — 配置器、GC、堆疊 | 3 |
| 7 | 標準函式庫與進階 — json、http、database/sql、cgo、程式碼生成 | 5 |

共 **35 章**。

## 撰寫慣例

### 用語

全書使用台灣術語：執行緒（thread）、行程（process）、記憶體、指標、佇列、排程器、快取、搶佔。

`goroutine`、`channel`、`slice`、`map`、`panic`、`defer`、`runtime` 等沒有共識譯名的詞一律保留原文。完整對照表在 [`content/0010-layers.md`](content/0010-layers.md)。

「堆疊（stack）」與「堆積（heap）」一律加註英文，避免與中國大陸用法的「堆／棧」混淆。

### 版本標注

凡是近年改過的行為，都用版本框標出「舊版是這樣、現在是這樣」：

```markdown
!!! version "Go 1.24：map 換成 Swiss Table"
    ...
```

其他提示框類型：`note`、`tip`、`warning`、`danger`。

### 圖表

用手寫的行內 SVG，套用 `theme/style.css` 裡定義的 `.d-box` / `.d-t` 等 class，這樣圖表會自動跟隨亮／暗主題。不使用外部繪圖函式庫。

```markdown
<figure class="diagram"><svg viewBox="0 0 700 300" role="img" aria-label="...">
  ...
</svg><figcaption><b>標題。</b>說明文字。</figcaption></figure>
```

SVG 內部**不能有空行**，否則 Markdown 會把它拆開。

### 跨章連結

用產生後的 slug，例如：

```markdown
見 [GMP 排程器](scheduler.html#工作竊取)
```

錨點是由標題文字產生的（保留中文）。改標題會讓既有錨點失效，所以改完記得跑 `check_links.py`。

## 驗證

### 程式碼範例

```bash
python verify_examples.py
```

抽出所有以 `package main` 開頭的 ```go 區塊，逐一送 `go vet`。會自動跳過需要外部套件、cgo、`go:embed` 的範例。

目前狀態：**178 通過 / 8 跳過 / 0 失敗**（Go 1.26.5）。

### 站內連結

```bash
python check_links.py
```

檢查每個 `href` 指到的頁面與錨點是否存在。

目前狀態：**2558 個連結全部通過**。

### 建議的完整流程

```bash
python build.py && python check_links.py && python verify_examples.py
```

## 新增章節

1. 在 `content/` 建立 `NNNN-slug.md`，數字決定排序。
2. 寫 frontmatter：

```yaml
---
title: 章節標題
slug: url-slug
part: p5          # 對應 _book.yml 的 parts[].id
number: "5.8"     # 顯示用編號
order: 580        # 排序（與檔名數字一致即可）
summary: 一句話說明，會出現在首頁與搜尋結果。
updated: "1.26"   # 對照的 Go 版本
---
```

3. 跑 `python build.py`。側邊欄、首頁目錄、上下頁導覽、搜尋索引都會自動更新。

## 關於內容來源

本站主題大綱參考了公開的 Go 原始碼閱讀路線，但**所有內容為原創撰寫**，非任何既有著作的翻譯或改寫。技術細節以 Go 官方文件、release notes 與 `$(go env GOROOT)/src/` 原始碼為準。
