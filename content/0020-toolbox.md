---
title: 打開黑盒子的工具箱
slug: toolbox
part: p0
number: "0.2"
order: 20
summary: 逃逸分析、組合語言輸出、GODEBUG、delve、pprof 與 trace —— 讓你自己驗證本書所有說法的六件工具。
updated: "1.26"
---

## 先確認環境

本書所有輸出都以 Go 1.26 為準。先確認你手上的版本：

```bash
go version
```

```text
go version go1.26.0 windows/amd64
```

如果你的版本不同，大部分內容仍然適用，但涉及 map 內部結構（Go 1.24 換成 Swiss Table）、計時器（Go 1.23 重寫）、迴圈變數（Go 1.22 改語意）、函式呼叫慣例（Go 1.17 改成暫存器傳參）的章節會有差異，這些地方本書都會標注版本框。

!!! warning "go.mod 的 go 指令行會影響語意"
    Go 有「語言版本閘門」機制：某些行為改變只在 `go.mod` 宣告足夠新的版本時才生效。例如迴圈變數的 per-iteration 語意需要 `go 1.22+`、新版計時器語意需要 `go 1.23+`。所以「我用 Go 1.26 編譯」跟「我的模組宣告 go 1.26」是兩件事，跑實驗時要留意。

---

## 工具一：`-gcflags=-m` 看逃逸分析

這是最常用、CP 值最高的一個。它告訴你編譯器把哪些變數放在**堆積（heap）**、哪些留在**堆疊（stack）**，以及哪些函式被內聯。

```go
package main

import "fmt"

type point struct{ x, y int }

func stackAlloc() point {
	p := point{1, 2} // 值回傳，不逃逸
	return p
}

func heapAlloc() *point {
	p := point{3, 4} // 位址被回傳，逃逸到堆積
	return &p
}

func main() {
	a := stackAlloc()
	b := heapAlloc()
	fmt.Println(a, b)
}
```

```bash
go build -gcflags="-m" ./main.go
```

```text
./main.go:7:6: can inline stackAlloc
./main.go:12:6: can inline heapAlloc
./main.go:18:16: inlining call to stackAlloc
./main.go:19:16: inlining call to heapAlloc
./main.go:13:2: moved to heap: p
./main.go:20:13: ... argument does not escape
```

重點讀兩種訊息：

- `moved to heap: p` —— 這個變數逃逸了，會產生一次堆積配置，之後要靠 GC 回收。
- `can inline` / `inlining call to` —— 函式被內聯，呼叫開銷消失。

加第二個 `-m` 會給出**理由**，這在調校時特別有用：

```bash
go build -gcflags="-m -m" ./main.go
```

要看完整訊息、不被內聯干擾，可以把內聯關掉：

```bash
go build -gcflags="-m -l" ./main.go
```

!!! tip "常見的逃逸原因"
    1. 回傳區域變數的位址。
    2. 傳給 `interface{}` / `any` 參數（例如 `fmt.Println` 的引數）—— 這是最常被忽略的一個。
    3. 送進 channel 的指標。
    4. 編譯期無法確定大小的配置，例如 `make([]int, n)` 且 `n` 是變數且可能很大。
    5. 被閉包捕捉且閉包本身逃逸。

---

## 工具二：`-gcflags=-S` 看產生的組合語言

當你想確認「這段語法糖到底被改寫成什麼」，直接看組合語言最沒有爭議。

```go
package main

func add(a, b int) int { return a + b }

func main() {
	_ = add(1, 2)
}
```

```bash
go build -gcflags="-S -l" ./main.go
```

輸出會像這樣（節錄，`amd64`）：

```text
main.add STEXT nosplit size=4 args=0x10 locals=0x0 funcid=0x0
	0x0000 00000 (main.go:3)	TEXT	main.add(SB), NOSPLIT|ABIInternal, $0-16
	0x0000 00000 (main.go:3)	ADDQ	BX, AX
	0x0003 00003 (main.go:3)	RET
```

有三個細節值得注意：

- `ABIInternal` —— 這是 Go 1.17 之後的**暫存器呼叫慣例**。兩個 `int` 參數分別放在 `AX` 與 `BX`，不再走堆疊。詳見 [函式呼叫與呼叫慣例](calling-convention.html)。
- `NOSPLIT` —— 這個函式不需要堆疊成長檢查，因為它不呼叫別的函式也不吃堆疊空間。
- `$0-16` —— 區域變數 0 位元組、參數加回傳值共 16 位元組。

Go 的組合語言是一種**虛擬組合語言**（Plan 9 風格），跟你在 Intel 手冊上看到的語法不同：運算元順序是「來源在前、目的在後」，暫存器名稱前沒有 `%`。看不懂沒關係，本書只在必要時引用幾行。

如果只想看某一個函式，可以搭配 `-S` 與文字搜尋，或改用更精準的方式：

```bash
go tool compile -S main.go
```

---

## 工具三：`GODEBUG` 觀察 runtime

`GODEBUG` 是一組用逗號分隔的環境變數開關，可以讓 runtime 把內部狀態印出來。這是觀察 GC 與排程器最直接的方法。

### 看 GC

```bash
GODEBUG=gctrace=1 go run main.go
```

```text
gc 1 @0.021s 0%: 0.015+0.42+0.003 ms clock, 0.12+0.11/0.31/0.28+0.026 ms cpu, 4->4->1 MB, 5 MB goal, 0 MB stacks, 0 MB globals, 8 P
```

拆解這一行：

| 欄位 | 意義 |
| --- | --- |
| `gc 1` | 第 1 次 GC |
| `@0.021s` | 程式啟動後 0.021 秒 |
| `0%` | GC 至今佔用的 CPU 比例 |
| `0.015+0.42+0.003 ms clock` | STW 掃描準備 + 並行標記 + STW 標記終止的耗時 |
| `4->4->1 MB` | GC 開始時堆積大小 → 結束時 → 存活大小 |
| `5 MB goal` | 下次觸發 GC 的目標堆積大小 |
| `8 P` | 使用了 8 個 P |

**最該盯的是第三段的 STW 時間**。健康的 Go 服務，這兩段 STW 應該都在數十微秒到一兩毫秒之間。

### 看排程器

```bash
GODEBUG=schedtrace=1000 go run main.go
```

每秒印一行排程器摘要：

```text
SCHED 1004ms: gomaxprocs=8 idleprocs=7 threads=6 spinningthreads=0 idlethreads=3 runqueue=0 [0 0 0 0 0 0 0 0]
```

`runqueue` 是全域執行佇列長度，後面中括號裡是**每個 P 的本地佇列**長度。如果你看到某幾個 P 排了一堆、其他是 0，代表工作竊取沒有發揮作用，通常是 goroutine 在互相阻塞。

加上 `scheddetail=1` 會逐一列出每個 G、M、P 的狀態，資訊量很大，適合小程式的深度除錯。

### 其他好用的開關

| 開關 | 用途 |
| --- | --- |
| `gctrace=1` | 每次 GC 印一行摘要 |
| `schedtrace=N` | 每 N 毫秒印排程器狀態 |
| `scheddetail=1` | 搭配上一項，印出每個 G/M/P 明細 |
| `allocfreetrace=1` | 追蹤每一次配置與釋放（極慢，只用於小程式） |
| `inittrace=1` | 印出每個套件 `init` 的耗時與配置量，抓啟動慢的元兇很好用 |
| `madvdontneed=1` | Linux 上改用 `MADV_DONTNEED` 歸還記憶體，讓 RSS 下降得比較快 |
| `invalidptr=0` | 關掉無效指標檢查（除錯 cgo 時偶爾需要） |
| `asyncpreemptoff=1` | 關閉非同步搶佔，用來驗證某個 bug 是否與搶佔有關 |

!!! note "GODEBUG 也是相容性機制"
    除了除錯開關，Go 還用 `GODEBUG` 承載**行為相容性**。當 Go 改變某個既有行為時，通常會加一個 `GODEBUG` 讓你切回舊行為，例如 `asynctimerchan=1` 可以切回 Go 1.23 之前的計時器語意。完整清單在 `go doc runtime`，或線上的 GODEBUG History 文件。

---

## 工具四：delve 逐步除錯

`delve`（指令是 `dlv`）是 Go 專用的除錯器，比 gdb 更懂 goroutine。

```bash
go install github.com/go-delve/delve/cmd/dlv@latest
```

最有價值的不是設中斷點，而是這幾個**看 runtime 狀態**的指令：

```text
(dlv) goroutines          列出所有 goroutine 及其狀態
(dlv) goroutine 7         切換到第 7 個 goroutine
(dlv) bt                  印出目前 goroutine 的呼叫堆疊
(dlv) print myVar         印出變數（會展開 struct）
(dlv) print -x mySlice    以十六進位顯示
(dlv) regs                看暫存器（配合暫存器 ABI 很有用）
```

想看 runtime 的內部結構（例如 `g` 或 `hmap`），要先關掉最佳化，否則變數會被暫存器化而看不到：

```bash
dlv debug --build-flags="-gcflags='all=-N -l'" ./main.go
```

---

## 工具五：pprof 找熱點

`pprof` 收集的是**取樣式的統計剖析**，用來回答「時間花在哪裡」「記憶體被誰吃掉」。

最簡單的用法是在測試裡開：

```bash
go test -bench=. -cpuprofile=cpu.out -memprofile=mem.out
go tool pprof -http=:8081 cpu.out
```

長時間執行的服務則掛上 HTTP 端點：

```go
package main

import (
	"log"
	"net/http"
	_ "net/http/pprof" // 匿名匯入，會自動註冊 /debug/pprof/* 路由
)

func main() {
	go func() {
		log.Println(http.ListenAndServe("localhost:6060", nil))
	}()

	select {} // 你的服務主體
}
```

!!! danger "不要把 pprof 端點暴露到公網"
    `net/http/pprof` 會註冊到 `http.DefaultServeMux`。如果你的對外服務剛好也用 `DefaultServeMux`，這些端點就跟著上線了，任何人都能拉走你的堆積快照與完整呼叫堆疊。務必綁在 `localhost`，或掛在另一個只有內網能連的 mux 上。

常用的剖析種類：

| 種類 | 回答什麼問題 |
| --- | --- |
| `profile` | CPU 時間花在哪些函式（預設取樣 30 秒） |
| `heap` | 目前存活的物件由誰配置 |
| `allocs` | 從啟動到現在的累計配置（找 GC 壓力來源） |
| `goroutine` | 現在有幾個 goroutine、卡在哪裡（抓洩漏） |
| `mutex` | 鎖競爭熱點（需先 `runtime.SetMutexProfileFraction`） |
| `block` | goroutine 阻塞在哪（需先 `runtime.SetBlockProfileRate`） |

!!! version "Go 1.26：goroutineleak 剖析"
    Go 1.26 在 `runtime/pprof` 加入了實驗性的 `goroutineleak` 剖析，會嘗試找出**永遠不可能被喚醒**的 goroutine（例如卡在沒有其他人持有的 channel 上）。傳統做法是抓兩次 `goroutine` 剖析比對差異，這個新剖析可以直接指出候選者。

---

## 工具六：trace 看時間軸

`pprof` 告訴你「總共花了多少」，`trace` 告訴你「什麼時候發生的」。要診斷延遲毛刺（latency spike）、GC 停頓、goroutine 被卡住，只有 trace 能給答案。

```go
package main

import (
	"os"
	"runtime/trace"
)

func main() {
	f, _ := os.Create("trace.out")
	defer f.Close()

	trace.Start(f)
	defer trace.Stop()

	work()
}

func work() {
	ch := make(chan int)
	for i := 0; i < 4; i++ {
		go func(n int) { ch <- n * n }(i)
	}
	for i := 0; i < 4; i++ {
		<-ch
	}
}
```

```bash
go tool trace trace.out
```

瀏覽器會開起來，最有用的兩個檢視是：

- **Goroutine analysis** —— 每個 goroutine 花在「執行／等排程／等 GC／等同步／等系統呼叫」的時間分佈。如果「等同步」佔大宗，代表你的鎖或 channel 設計有問題。
- **Scheduler latency profile** —— 從「可執行」到「真的開始跑」之間的延遲。這個數字大，通常是 `GOMAXPROCS` 不足或有 goroutine 長時間不讓出 CPU。

---

## 讀 runtime 原始碼

本書引用 runtime 原始碼時會標注相對路徑，例如 `runtime/proc.go`。完整路徑在：

```bash
go env GOROOT
```

幾個閱讀時的提醒：

1. **`//go:` 指示詞很重要。** `//go:nosplit`（不做堆疊成長檢查）、`//go:linkname`（跨套件連結符號）、`//go:systemstack`（必須在系統堆疊上執行）這些註解決定了函式的執行條件，不是普通註解。
2. **有些函式沒有函式本體。** 例如 `runtime/stubs.go` 裡宣告了一堆只有簽章的函式，它們的實作在 `.s` 組合語言檔裡。
3. **`_` 開頭的常數多半來自 C 時代。** Go runtime 最早是從 C 翻譯過來的，命名風格留了下來。
4. **`internal/runtime/` 是新趨勢。** 近年 runtime 持續把子系統拆到 `internal/runtime/` 底下，例如 Go 1.24 的新 map 實作在 `internal/runtime/maps/`、atomic 原語在 `internal/runtime/atomic/`。找不到東西時記得往這裡翻。

!!! version "Go 1.23 起：`go:linkname` 被鎖緊了"
    以前有些函式庫會用 `//go:linkname` 直接連到 runtime 的私有函式（例如自己實作 `nanotime`）。Go 1.23 開始，連結器預設**禁止**指向 runtime 內部符號的「拉取式（pull-only）」linkname，除非該符號明確標記允許。如果你升級後看到 `//go:linkname must refer to declared function or variable` 之類的錯誤，通常就是相依套件用了這招。

---

工具備齊了。下一部分開始，我們從一份 `.go` 檔進入編譯器。
