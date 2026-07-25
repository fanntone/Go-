---
title: 分層心智模型與用語約定
slug: layers
part: p0
number: "0.1"
order: 10
summary: 在鑽進原始碼之前，先把「編譯期／連結期／執行期」這三層分清楚，並約定全書使用的台灣術語。
updated: "1.26"
---

## 為什麼要先分層

初學 Go 的人問「slice 為什麼會擴容」，得到的答案常常混在一起：有人講 `growslice` 這個 runtime 函式，有人講編譯器如何把 `append` 改寫成呼叫，有人講記憶體配置器怎麼挑 size class。三個答案都對，但它們屬於**三個不同的階段**。

把階段分清楚，之後每讀到一個機制，你都可以先問自己一句：「這件事是誰做的、什麼時候做的？」答案幾乎總是落在下面三格之一。

<figure class="diagram"><svg viewBox="0 0 720 300" role="img" aria-label="Go 程式從原始碼到執行的三個階段"><rect class="d-box-a" x="10" y="40" width="215" height="130" rx="8"/><text class="d-t-a" x="26" y="64">編譯期 compile time</text><text class="d-t-s" x="26" y="88">go build / go tool compile</text><text class="d-t-m" x="26" y="112">.go → AST → IR → SSA → .o</text><text class="d-t-s" x="26" y="134">型別檢查、逃逸分析、內聯</text><text class="d-t-s" x="26" y="152">語法糖改寫成 runtime 呼叫</text><rect class="d-box" x="252" y="40" width="185" height="130" rx="8"/><text class="d-t-b" x="268" y="64">連結期 link time</text><text class="d-t-s" x="268" y="88">go tool link</text><text class="d-t-m" x="268" y="112">.o + runtime.a → 執行檔</text><text class="d-t-s" x="268" y="134">符號解析、去除死碼</text><text class="d-t-s" x="268" y="152">寫入型別中繼資料</text><rect class="d-box-o" x="464" y="40" width="246" height="130" rx="8"/><text class="d-t-b" x="480" y="64">執行期 run time</text><text class="d-t-s" x="480" y="88">你的程式 + Go runtime</text><text class="d-t-m" x="480" y="112">排程器、GC、記憶體配置器</text><text class="d-t-s" x="480" y="134">goroutine 建立與切換</text><text class="d-t-s" x="480" y="152">堆疊成長、系統呼叫代理</text><path class="d-line-a" d="M225 105 L248 105" marker-end="url(#ar1)"/><path class="d-line-a" d="M437 105 L460 105" marker-end="url(#ar1)"/><defs><marker id="ar1" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto"><path d="M0 0 L10 5 L0 10 z" fill="currentColor" style="fill:var(--accent)"/></marker></defs><line class="d-dash" x1="10" y1="205" x2="710" y2="205"/><text class="d-t-s" x="10" y="235">以 append 為例：</text><text class="d-t-s" x="10" y="256">① 編譯期把 s = append(s, x) 改寫成「檢查容量 → 不夠就呼叫 runtime.growslice」的指令序列；</text><text class="d-t-s" x="10" y="276">② 連結期把 growslice 的位址填進去；③ 執行期 growslice 真的去跟記憶體配置器要一塊新記憶體。</text></svg><figcaption><b>三個階段。</b>絕大多數「Go 為什麼這樣運作」的問題，都可以先定位到其中一格。編譯器做的是<b>靜態改寫</b>，runtime 做的是<b>動態決策</b>；兩者的分工線，就是理解 Go 的主軸。</figcaption></figure>

### 一句話分工

| 階段 | 誰在做 | 典型工作 | 你可以怎麼觀察 |
| --- | --- | --- | --- |
| 編譯期 | `cmd/compile` | 型別檢查、把語法糖展開、決定變數配置在堆疊還是堆積 | `go build -gcflags="-m"`、`-S` |
| 連結期 | `cmd/link` | 合併目標檔、去除沒用到的符號、產生型別中繼資料 | `go tool nm`、`-ldflags="-w"` |
| 執行期 | `runtime` 套件 | 排程 goroutine、配置與回收記憶體、代理系統呼叫 | `GODEBUG`、`pprof`、`trace` |

Go 的一個重要設計取向是：**盡量把工作往編譯期推**。沒有虛擬機、沒有 JIT、沒有執行期的位元組碼解譯。`runtime` 只是一個被靜態連結進你程式的普通函式庫，它跟你的程式碼跑在同一顆執行檔裡，用同一套呼叫慣例。

!!! note "runtime 不是虛擬機"
    很多從 Java 或 Python 過來的人，會把 Go 的 `runtime` 想像成 JVM 那樣的虛擬機。它不是。`runtime` 是一包用 Go 和組合語言寫成的程式碼，被連結器塞進你的執行檔。你寫的 `main` 並不是程式進入點，真正的進入點是 runtime 的組合語言啟動程式碼，它把排程器與記憶體系統準備好之後，才把 `main.main` 當成第一個 goroutine 跑起來。

---

## 全書用語約定

這份筆記用台灣的技術用語。原始文獻大量使用中國大陸的譯法，有些字面相同但意思差很多（最經典的是「堆」），有些在台灣根本不通行。以下是全書一致採用的對照表。

### 一定要注意的四組詞

| 大陸常見用法 | 本書用語 | 說明 |
| --- | --- | --- |
| 协程 | **goroutine** | 不譯。它既不是 coroutine（沒有對稱的 yield 語意），也不是 green thread（有搶佔）。硬要翻成「協程」反而誤導。 |
| 线程 | **執行緒**（thread） | 作業系統排程的單位。Go 裡對應 GMP 的 **M**。 |
| 进程 | **行程**（process） | 作業系統的資源容器。 |
| 技术栈 | **技術組合**／**技術堆疊** | 這是最容易誤讀的一個。中文語境的「栈」是 stack，但「技术栈」指的是 tech stack（一整套工具與框架），跟記憶體的 stack 沒有關係。本書談記憶體時一律用「堆疊」，談工具組合時用「技術組合」。 |

「堆」與「栈」在大陸用法裡分別是 heap 與 stack，但在台灣「堆疊」慣指 stack。為了避免歧義，本書**一律加註英文**：

- **堆疊（stack）** —— 後進先出的那塊記憶體，每個 goroutine 各有一份，函式回傳就自動釋放。
- **堆積（heap）** —— GC 管理的共用記憶體區，逃逸的物件會配置在這裡。

### 完整對照表

| 大陸用語 | 本書用語 | 英文 |
| --- | --- | --- |
| 内存 | 記憶體 | memory |
| 指针 | 指標 | pointer |
| 数组 | 陣列 | array |
| 切片 | slice（切片） | slice |
| 哈希表／散列表 | 雜湊表 | hash table |
| 键值对 | 鍵值對 | key-value pair |
| 队列 | 佇列 | queue |
| 链表 | 鏈結串列 | linked list |
| 接口 | 介面 | interface |
| 函数 | 函式 | function |
| 变量 | 變數 | variable |
| 对象 | 物件 | object |
| 实现 | 實作 | implementation |
| 默认 | 預設 | default |
| 缓存 | 快取 | cache |
| 缓冲区 | 緩衝區 | buffer |
| 调度器 | 排程器 | scheduler |
| 抢占 | 搶佔 | preemption |
| 阻塞 | 阻塞 | blocking |
| 并发／并行 | 並行／平行 | concurrency / parallelism |
| 自旋 | 自旋 | spinning |
| 信号 | 訊號 | signal |
| 字节 | 位元組 | byte |
| 位 | 位元 | bit |
| 溢出 | 溢位 | overflow |
| 全局 | 全域 | global |
| 局部 | 區域 | local |
| 递归 | 遞迴 | recursion |
| 遍历 | 走訪／迭代 | iterate |
| 拷贝 | 複製 | copy |
| 序列化 | 序列化 | serialization |
| 优化 | 最佳化 | optimization |
| 内联 | 內聯 | inlining |
| 逃逸分析 | 逃逸分析 | escape analysis |
| 垃圾回收 | 垃圾回收 | garbage collection |
| 屏障 | 屏障 | barrier |
| 断言 | 斷言 | assertion |
| 元数据 | 中繼資料 | metadata |
| 位图 | 點陣圖／位元圖 | bitmap |
| 句柄 | handle | handle |

### 不翻譯的詞

以下詞彙在台灣的工程現場幾乎都直接講英文，本書保留原文：

`goroutine`、`channel`、`slice`、`map`、`struct`、`interface`（提到 Go 的型別時）、`panic`、`recover`、`defer`、`GC`、`runtime`、`compiler`（討論 `cmd/compile` 這個具體程式時）、`mutex`、`spinlock`、`cache line`、`false sharing`、`GMP`、`P`／`M`／`G`。

---

## 並行不等於平行

這兩個詞在中文裡常被混用，但在 Go 的語境下必須分開。

- **並行（concurrency）**：程式的**結構**。它被拆成多個可以獨立推進的工作。就算只有一顆 CPU，只要這些工作可以交錯執行，就是並行。
- **平行（parallelism）**：程式的**執行方式**。多個工作在同一瞬間真的同時在跑，需要多顆 CPU 核心。

Go 提供的是並行的**語言結構**（goroutine 與 channel），至於能不能平行執行，取決於 `GOMAXPROCS` 與機器上有幾顆核心。

```go
package main

import (
	"fmt"
	"runtime"
)

func main() {
	// GOMAXPROCS 決定「最多有幾個 goroutine 能真正同時執行」
	fmt.Println("邏輯 CPU 數：", runtime.NumCPU())
	fmt.Println("GOMAXPROCS：", runtime.GOMAXPROCS(0)) // 傳 0 表示只查詢不修改
}
```

!!! version "Go 1.25 起：GOMAXPROCS 會看容器限制"
    Go 1.25 之前，`GOMAXPROCS` 預設等於**整台機器**的邏輯 CPU 數，完全不管容器設了多少 CPU limit。在 Kubernetes 上這會造成嚴重問題：Pod 限制 2 核，Go 卻開 64 個 P，結果被 CFS 節流（throttling）到延遲飆高。

    Go 1.25 起，Linux 上的 runtime 會讀取 cgroup 的 CPU 頻寬限制，若該限制低於邏輯 CPU 數，就以限制值為準；而且它會**定期重新檢查**，因為編排系統可能在執行中調整 limit。注意它看的是 CPU **limit**，不是 CPU **request**。

    這代表 `uber-go/automaxprocs` 這類函式庫在 Go 1.25+ 已不再必要（前提是你的 `go.mod` 宣告了 `go 1.25` 以上）。

---

## 怎麼讀這份筆記

三種讀法都可以：

1. **從頭讀到尾。** 章節有刻意排序：先建立編譯器的概念（Part 1），才有辦法理解為什麼 `for-range` 會被改寫、為什麼 `defer` 有三種實作。再進資料結構（Part 2）與語言核心（Part 3），最後才是 runtime（Part 5、6）。
2. **按問題查。** 右上角的搜尋（快捷鍵 <kbd>/</kbd>）會搜全文。想知道「map 為什麼不能取位址」就搜 `map`。
3. **對照原始碼讀。** 每一節提到 runtime 函式時，都會標注原始碼路徑，例如 `runtime/slice.go`。你的機器上就有一份：路徑是 `$(go env GOROOT)/src/`。

下一節先把工具準備好——沒有工具，所有的說明都只能是「聽說」。
