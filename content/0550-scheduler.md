---
title: GMP 排程器
slug: scheduler
part: p5
number: "5.5"
order: 550
summary: G、M、P 三者的分工、工作竊取、hand-off、非同步搶佔，以及 GOMAXPROCS 該怎麼設。
updated: "1.26"
---

## 為什麼需要自己的排程器

作業系統已經有排程器了，為什麼 Go 還要再做一層？

因為**執行緒（thread）太貴**：

| 項目 | OS 執行緒 | goroutine |
| --- | --- | --- |
| 初始堆疊 | 通常 1–8 MB（虛擬位址） | **2 KB**，需要時自動成長 |
| 建立成本 | 微秒級（要進核心） | 奈秒級（純使用者空間） |
| 切換成本 | 1–2 微秒（陷入核心、換頁表、清 TLB） | 約 100–200 奈秒（只換幾個暫存器） |
| 實務上限 | 數千個 | **數百萬個** |

Go 的策略是**兩層排程**：OS 排程 M（執行緒），Go runtime 排程 G（goroutine）到 M 上。這叫 **M:N 排程**——M 個 goroutine 對應 N 個執行緒。

---

## G、M、P 三個角色

<figure class="diagram"><svg viewBox="0 0 700 400" role="img" aria-label="GMP 模型的整體結構"><rect class="d-box-w" x="15" y="14" width="670" height="66" rx="7"/><text class="d-t-b" x="30" y="36">全域執行佇列 global run queue（有鎖）</text><rect class="d-box" x="30" y="46" width="70" height="24" rx="3"/><text class="d-t-m d-mid" x="65" y="63">G</text><rect class="d-box" x="106" y="46" width="70" height="24" rx="3"/><text class="d-t-m d-mid" x="141" y="63">G</text><rect class="d-box" x="182" y="46" width="70" height="24" rx="3"/><text class="d-t-m d-mid" x="217" y="63">G</text><text class="d-t-s" x="270" y="63">P 的本地佇列空了、或每 61 次排程一定會來這裡拿一個（避免飢餓）</text><rect class="d-box-a" x="15" y="96" width="215" height="150" rx="7"/><text class="d-t-b d-mid" x="122" y="118">P0　（processor）</text><text class="d-t-s d-mid" x="122" y="136">執行 Go 程式碼的「許可證」</text><rect class="d-box" x="28" y="146" width="189" height="52" rx="4"/><text class="d-t-s" x="38" y="164">本地執行佇列（最多 256 個，無鎖）</text><text class="d-t-m" x="38" y="188">runnext → G　|　G　G　G</text><rect class="d-box" x="28" y="204" width="189" height="32" rx="4"/><text class="d-t-s" x="38" y="224">mcache　·　timers 堆積　·　deferpool</text><rect class="d-box-a" x="242" y="96" width="215" height="150" rx="7"/><text class="d-t-b d-mid" x="349" y="118">P1</text><text class="d-t-s d-mid" x="349" y="136">數量 = GOMAXPROCS</text><rect class="d-box" x="255" y="146" width="189" height="52" rx="4"/><text class="d-t-m" x="265" y="176">G　G</text><rect class="d-box" x="255" y="204" width="189" height="32" rx="4"/><text class="d-t-s" x="265" y="224">mcache　·　timers　·　deferpool</text><rect class="d-box" x="470" y="96" width="215" height="150" rx="7"/><text class="d-t-b d-mid" x="577" y="118">P2 …</text><text class="d-t-s d-mid" x="577" y="176">（空佇列 → 會去竊取）</text><path class="d-line-a" d="M470 170 L450 170" marker-end="url(#ar18)"/><text class="d-t-a" x="300" y="266">工作竊取：隨機挑一個 P，偷走它一半的 G</text><rect class="d-box-o" x="15" y="284" width="130" height="60" rx="6"/><text class="d-t-b d-mid" x="80" y="306">M0（執行緒）</text><text class="d-t-s d-mid" x="80" y="326">綁定 P0，跑 G</text><rect class="d-box-o" x="155" y="284" width="130" height="60" rx="6"/><text class="d-t-b d-mid" x="220" y="306">M1</text><text class="d-t-s d-mid" x="220" y="326">綁定 P1，跑 G</text><rect class="d-box-d" x="295" y="284" width="180" height="60" rx="6"/><text class="d-t-b d-mid" x="385" y="306">M2</text><text class="d-t-s d-mid" x="385" y="326">阻塞在系統呼叫，已交出 P</text><rect class="d-box" x="485" y="284" width="200" height="60" rx="6"/><text class="d-t-b d-mid" x="585" y="306">M3、M4 …（閒置）</text><text class="d-t-s d-mid" x="585" y="326">在 sched.midle 待命，可被喚醒</text><path class="d-line" d="M80 284 L100 250" marker-end="url(#ar18b)"/><path class="d-line" d="M220 284 L300 250" marker-end="url(#ar18b)"/><defs><marker id="ar18" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M0 0 L10 5 L0 10 z" style="fill:var(--accent)"/></marker><marker id="ar18b" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M0 0 L10 5 L0 10 z" style="fill:var(--line-strong)"/></marker></defs><text class="d-t-s" x="15" y="372">M 必須持有一個 P 才能執行 Go 程式碼。M 的數量可以遠多於 P（預設上限 10000），但同時執行 Go 程式碼的只有 GOMAXPROCS 個。</text><text class="d-t-s" x="15" y="392">P 是關鍵的中間層：它讓「執行緒」與「可執行工作」解耦，使得 M 阻塞時 P 能立刻轉交給別的 M。</text></svg><figcaption><b>GMP 的三層結構。</b>G 是工作、M 是工人、P 是工作臺。工人必須站在工作臺前才能幹活；工作臺數量固定（<code>GOMAXPROCS</code>），這就限制了同時執行的 Go 程式碼量。工人去倉庫（系統呼叫）時會把工作臺讓出來給別人。</figcaption></figure>

### G — goroutine

一個 `g` 結構（`runtime/runtime2.go`）包含：

- **`stack`** —— 這個 goroutine 的堆疊範圍（起始與結束位址）
- **`sched`** —— 被切換出去時，暫存器的快照（PC、SP、BP 等）
- **`atomicstatus`** —— 目前狀態
- **`m`** —— 目前在哪個 M 上執行（沒在執行時是 nil）
- **`_defer` / `_panic`** —— 這個 goroutine 的 defer 與 panic 鏈

主要狀態：

| 狀態 | 意義 |
| --- | --- |
| `_Grunnable` | 在執行佇列裡，等著被執行 |
| `_Grunning` | 正在某個 M 上執行 |
| `_Gsyscall` | 正在執行系統呼叫 |
| `_Gwaiting` | 阻塞中（等 channel、鎖、計時器、網路） |
| `_Gdead` | 已結束，或剛建立還沒初始化 |

### M — machine（OS 執行緒）

`m` 對應一個真正的作業系統執行緒。關鍵欄位：

- **`g0`** —— 一個特殊的 goroutine，擁有較大的**系統堆疊**。排程、GC、堆疊成長等 runtime 工作都在 `g0` 上執行。
- **`curg`** —— 目前正在執行的使用者 goroutine。
- **`p`** —— 目前綁定的 P。

M 的數量由 runtime 動態管理，預設上限 10000（`runtime.SetMaxThreads` 可調）。**達到上限會直接 crash**，這通常代表程式有大量阻塞的系統呼叫。

### P — processor（處理器）

P 是 Go 1.1 引入的關鍵抽象，它代表「執行 Go 程式碼所需的資源」：

- **本地執行佇列** —— 最多 256 個 G，加上一個 `runnext` 插槽
- **`mcache`** —— 這個 P 專屬的記憶體配置快取（見 [記憶體配置器](allocator.html)）
- **`timers`** —— 這個 P 的計時器堆積
- **`deferpool`** —— `_defer` 記錄的重用池

P 的數量等於 `GOMAXPROCS`，**這決定了同時能執行多少 Go 程式碼**。

!!! note "為什麼要有 P？"
    Go 1.0 只有 G 和 M，用一把全域鎖保護全域執行佇列。問題是：

    1. 全域鎖競爭嚴重，多核心無法擴展。
    2. 每個 M 都需要自己的記憶體快取，但 M 可能有上千個 → 記憶體浪費。
    3. M 阻塞在系統呼叫時，它持有的資源沒辦法給別人用。

    引入 P 之後：本地佇列消除了大部分鎖競爭；`mcache` 掛在 P 上（數量固定且少）；M 阻塞時可以把 P 交給別的 M。

---

## 排程迴圈

每個 M 的主迴圈（`runtime.schedule`）：

```text
schedule():
    ① 每 61 次排程，強制從全域佇列拿一個 G
       （避免全域佇列裡的 G 被本地佇列餓死）

    ② 從 P 的 runnext 拿（最近被喚醒的，快取最熱）

    ③ 從 P 的本地佇列拿

    ④ 本地空了 → findRunnable():
         a. 再試一次本地佇列
         b. 從全域佇列拿一批（拿 1/GOMAXPROCS 的量）
         c. 檢查 netpoll（有沒有網路 I/O 完成）
         d. 隨機挑其他 P 偷一半的 G（最多嘗試 4 輪）
         e. 檢查其他 P 的到期計時器
         f. 都沒有 → 把 P 還回去，M 進入休眠

    ⑤ execute(g)：切換到那個 goroutine 執行
```

### 為什麼是 61

第 ① 步的「每 61 次」是一個經驗常數。理由：

- 如果**永遠優先本地佇列**，全域佇列裡的 G 可能永遠不被執行（飢餓）。
- 如果**每次都檢查全域佇列**，就要每次都拿全域鎖，效能崩潰。

61 是質數，可以避免跟其他週期性行為（例如 GC 週期、計時器週期）產生共振。這是排程器設計裡常見的手法。

### `runnext` 的作用

當一個 goroutine 喚醒另一個（例如 channel 交遞），被喚醒的那個會被放進 `runnext` 而非佇列尾端。

```go
ch <- v  // 喚醒等待的接收者 → 放進 runnext
```

理由是**快取局部性**：剛被喚醒的 goroutine 要處理的資料，很可能還在 CPU 快取裡。立刻執行它比排到隊尾更有效率。

這也讓「乒乓式」的 goroutine 通訊（A 送給 B，B 處理完送回 A）非常快。

---

## 工作竊取

當一個 P 的本地佇列空了，它不會閒著——會去偷別人的。

```text
for i := 0; i < 4; i++ {          // 最多 4 輪
    for enum := randomOrder() {   // 隨機順序走訪所有 P
        if 目標 P 有 G {
            偷走它佇列裡「一半」的 G
            return
        }
    }
}
```

三個細節：

1. **隨機順序** —— 避免所有閒置的 P 都去偷同一個 P，造成鎖競爭熱點。
2. **偷一半** —— 偷太少會馬上又要再偷；偷太多會讓被偷的 P 反過來變空。一半是經典的平衡點。
3. **從佇列頭偷** —— 被偷的 P 從尾端取用（LIFO，快取熱），小偷從頭端拿（FIFO，最舊的），減少衝突。

---

## 系統呼叫的處理

這是 P 這層抽象最能發揮價值的地方。

<figure class="diagram"><svg viewBox="0 0 700 330" role="img" aria-label="系統呼叫時的 P 交接"><text class="d-t-b" x="15" y="20">情況一：快速系統呼叫（&lt; 20 微秒）</text><rect class="d-box-o" x="15" y="30" width="200" height="54" rx="5"/><text class="d-t-s d-mid" x="115" y="52">M 持有 P，執行 G</text><text class="d-t-s d-mid" x="115" y="72">狀態 → _Gsyscall</text><path class="d-line" d="M215 57 L250 57" marker-end="url(#ar19)"/><rect class="d-box-w" x="254" y="30" width="200" height="54" rx="5"/><text class="d-t-s d-mid" x="354" y="52">進入系統呼叫</text><text class="d-t-s d-mid" x="354" y="72">P 進入 _Psyscall，仍掛在 M 上</text><path class="d-line" d="M454 57 L490 57" marker-end="url(#ar19)"/><rect class="d-box-o" x="494" y="30" width="191" height="54" rx="5"/><text class="d-t-s d-mid" x="589" y="52">很快回來，直接拿回 P</text><text class="d-t-s d-mid" x="589" y="72">零成本，沒有交接</text><text class="d-t-b" x="15" y="122">情況二：慢系統呼叫（sysmon 偵測到超過 20 微秒）</text><rect class="d-box-o" x="15" y="132" width="160" height="54" rx="5"/><text class="d-t-s d-mid" x="95" y="154">M 阻塞在系統呼叫</text><text class="d-t-s d-mid" x="95" y="174">（例如讀檔案）</text><path class="d-line-a" d="M175 159 L210 159" marker-end="url(#ar19a)"/><rect class="d-box-d" x="214" y="132" width="200" height="54" rx="5"/><text class="d-t-s d-mid" x="314" y="154">sysmon 執行 handoffp</text><text class="d-t-s d-mid" x="314" y="174">把 P 從 M 身上摘下來</text><path class="d-line-a" d="M414 159 L450 159" marker-end="url(#ar19a)"/><rect class="d-box-a" x="454" y="132" width="231" height="54" rx="5"/><text class="d-t-s d-mid" x="569" y="154">交給閒置的 M（或新建一個）</text><text class="d-t-s d-mid" x="569" y="174">P 上的其他 G 繼續執行</text><text class="d-t-s" x="15" y="212">系統呼叫回來後，原本的 M 會嘗試拿回一個 P：先要原本那個 → 再要任何閒置的 → 都沒有就把 G 丟進全域佇列，M 去睡覺。</text><line class="d-dash" x1="15" y1="234" x2="685" y2="234"/><text class="d-t-b" x="15" y="258">情況三：網路 I/O —— 根本不阻塞執行緒</text><text class="d-t-s" x="15" y="280">net 套件的 socket 都是非阻塞模式。讀不到資料時，G 被掛到 netpoller（epoll/kqueue/IOCP）並 gopark，</text><text class="d-t-s" x="15" y="300">M 立刻去跑別的 G。資料就緒時 netpoller 把 G 設回 _Grunnable。全程沒有執行緒被浪費。</text><text class="d-t-s" x="15" y="322">→ 這就是 Go 能用少數執行緒處理數萬條連線的原因。詳見「網路輪詢器」一節。</text></svg><figcaption><b>三種 I/O 的差別。</b>網路 I/O 完全不阻塞執行緒（走 netpoller）；檔案 I/O 與大部分 syscall 會阻塞，但 P 會被交接出去；快速的系統呼叫則不做交接以省下成本。</figcaption></figure>

!!! warning "檔案 I/O 仍會佔用執行緒"
    在 Linux 上，一般的檔案讀寫**無法**用 epoll 做非阻塞（epoll 對常規檔案永遠回報「就緒」）。所以 `os.File` 的讀寫會真的阻塞 M。

    如果你的程式同時做大量檔案 I/O，執行緒數量會膨脹。用 `runtime.NumGoroutine()` 與 `/debug/pprof/threadcreate` 觀察，必要時自己限制並行度。

---

## 搶佔

早期的 Go 是**合作式**排程：goroutine 只在特定的「安全點」（函式呼叫、channel 操作、系統呼叫）才會讓出 CPU。這有個致命問題：

```go
package main

import (
	"fmt"
	"runtime"
	"time"
)

func main() {
	runtime.GOMAXPROCS(1) // 只有一個 P

	go func() {
		for { // 沒有任何函式呼叫的緊密迴圈
		}
	}()

	time.Sleep(10 * time.Millisecond)
	fmt.Println("我印得出來嗎？")
}
```

!!! version "Go 1.14：非同步搶佔"
    **Go 1.13 及之前**：上面的程式會卡死。那個空迴圈永遠不會讓出 CPU，`main` 永遠印不出東西。而且 GC 也無法進行（要等所有 goroutine 到達安全點）。

    **Go 1.14 起**：runtime 用**訊號**實作非同步搶佔。`sysmon` 發現某個 G 執行超過 **10 毫秒**，就向它所在的 M 發送 `SIGURG`。訊號處理常式把 G 的執行流導向 `asyncPreempt`，強制它讓出。

    所以現在上面的程式會正常印出訊息。

### 搶佔的兩種形式

| 形式 | 觸發時機 | 機制 |
| --- | --- | --- |
| **協作式** | 函式呼叫的堆疊檢查點 | 把 `g.stackguard0` 設成特殊值 `stackPreempt`，函式序言的檢查會失敗，進而讓出 |
| **非同步** | `sysmon` 偵測到執行超過 10ms | 發送 `SIGURG` 訊號，在訊號處理常式中注入呼叫 |

非同步搶佔並非隨時可行——runtime 必須確定當下的暫存器狀態是「可安全掃描」的（GC 需要知道哪些暫存器裝著指標）。做不到時會退回等待安全點。

### 觀察搶佔

```bash
GODEBUG=asyncpreemptoff=1 go run main.go
```

關掉非同步搶佔後，上面那個空迴圈的例子又會卡死。這可以用來驗證某個問題是否與搶佔有關。

---

## `GOMAXPROCS` 該設多少

### 預設值

!!! version "Go 1.25：容器感知"
    **Go 1.24 及之前**：預設 = 整台機器的邏輯 CPU 數（`runtime.NumCPU()`），**完全忽略容器的 CPU limit**。

    這在 Kubernetes 上是嚴重問題。假設節點有 64 核、Pod limit 是 2 核：Go 會開 64 個 P，同時想跑 64 個 goroutine。Linux CFS 會在配額用完後**完全暫停**整個 cgroup 直到下個週期——造成幾十毫秒的延遲毛刺。

    **Go 1.25 起**：Linux 上的 runtime 會讀取 cgroup 的 CPU 頻寬限制，取「限制值」與「邏輯 CPU 數」的較小者。而且會**定期重新檢查**，因為編排系統可能動態調整 limit。

    注意它看的是 CPU **limit**，不是 CPU **request**。只設 request 不設 limit 的話，行為跟以前一樣。

    這表示 `go.uber.org/automaxprocs` 在 Go 1.25+ 已不再必要（前提是 `go.mod` 宣告了 `go 1.25` 以上）。

### 什麼時候該手動調整

```go
runtime.GOMAXPROCS(n) // 或設定環境變數 GOMAXPROCS=n
```

| 情境 | 建議 |
| --- | --- |
| 一般服務（Go 1.25+，有設 CPU limit） | **不要動**，預設是對的 |
| 舊版 Go 在容器裡 | 用 `automaxprocs` 或手動設成 CPU limit |
| 延遲敏感、想減少排程抖動 | 可以試著設小一點（例如 limit 的 80%）並實測 |
| CPU 密集的批次運算 | 等於實體核心數（不要算超執行緒） |
| 大量檔案 I/O | 提高沒用——瓶頸在阻塞的 M，不在 P |
| 寫測試要重現競爭 | `GOMAXPROCS=1` 有時反而更容易觸發某些 bug |

!!! danger "`GOMAXPROCS=1` 不等於「沒有並行問題」"
    很多人以為設成 1 就不用擔心資料競爭。**錯的。** 排程器仍然會在任意的搶佔點切換 goroutine，複合操作（例如 `x++`）仍然可能被打斷。

    `GOMAXPROCS=1` 只是消除了「真正的平行執行」，沒有消除「交錯執行」。你的鎖還是要好好加。

---

## 觀察排程器

### `GODEBUG=schedtrace`

```bash
GODEBUG=schedtrace=1000 go run main.go
```

```text
SCHED 1005ms: gomaxprocs=8 idleprocs=5 threads=9 spinningthreads=1 needspinning=0 idlethreads=3 runqueue=2 [0 3 0 1 0 0 0 0]
```

| 欄位 | 意義 | 怎麼看 |
| --- | --- | --- |
| `gomaxprocs` | P 的數量 | 確認容器感知有沒有生效 |
| `idleprocs` | 閒置的 P | 一直很高 = 並行度不足 |
| `threads` | M 的總數 | 遠大於 gomaxprocs = 有很多阻塞的系統呼叫 |
| `spinningthreads` | 正在找工作的 M | 持續很高 = 工作分佈不均 |
| `runqueue` | 全域佇列長度 | 持續成長 = 處理不過來 |
| `[...]` | 每個 P 的本地佇列長度 | 分佈不均 = 竊取沒發揮作用 |

### `go tool trace`

比 `schedtrace` 更精細，能看到時間軸：

```go
f, _ := os.Create("trace.out")
trace.Start(f)
defer trace.Stop()
```

```bash
go tool trace trace.out
```

重點看兩個檢視：

- **Scheduler latency profile** —— 從「變成可執行」到「真的開始跑」的延遲。這個數字大代表 P 不夠或有 goroutine 霸佔 CPU。
- **Goroutine analysis** —— 每個 goroutine 的時間分佈。「等同步」佔大宗代表鎖或 channel 設計有問題。

### `runtime.NumGoroutine()`

```go
package main

import (
	"log/slog"
	"runtime"
	"time"
)

func monitor(interval time.Duration) {
	var m runtime.MemStats
	for range time.Tick(interval) {
		runtime.ReadMemStats(&m)
		slog.Info("runtime",
			"goroutines", runtime.NumGoroutine(),
			"heap_mb", m.HeapAlloc/1024/1024,
			"gc_count", m.NumGC,
			"gomaxprocs", runtime.GOMAXPROCS(0),
		)
	}
}

func main() {
	go monitor(5 * time.Second)
	select {}
}
```

**把 goroutine 數量做成指標推到監控系統**，這是最便宜也最有效的洩漏預警。正常服務的 goroutine 數應該隨負載波動但保持穩定；單調成長就是洩漏。

---

## 常見誤解

**「goroutine 越多越好。」** 不對。每個 goroutine 至少 2 KB 堆疊，而且排程器要管理它們。CPU 密集的工作開超過核心數的 goroutine 只會增加切換開銷。用工作池限制並行度。

**「goroutine 是輕量級執行緒。」** 不精確。它是**使用者空間的協作單位**，跑在執行緒上。它輕量是因為堆疊小、切換不進核心。

**「開 goroutine 就會變快。」** 只有在有真正的並行機會（多核心 CPU 工作、或 I/O 等待）時才成立。純序列的邏輯拆成 goroutine 只會變慢。

**「`GOMAXPROCS` 越大越好。」** 超過實際可用的 CPU 只會增加切換與快取抖動。在有 CPU limit 的容器裡設太大更是災難。

---

下一節談網路輪詢器——Go 能用少數執行緒扛住數萬連線的關鍵。
