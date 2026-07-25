---
title: 系統監控 sysmon
slug: sysmon
part: p5
number: "5.7"
order: 570
summary: 那條不需要 P 就能執行的監控執行緒，它負責搶佔、回收系統呼叫中的 P、強制 GC 與歸還記憶體。
updated: "1.26"
---

## 一條特別的執行緒

`sysmon` 是 Go runtime 在啟動時建立的一條**專用執行緒**，它有兩個特殊之處：

1. **不需要 P 就能執行。** 一般的 M 必須持有 P 才能跑 Go 程式碼，`sysmon` 是例外——它在 `g0` 系統堆疊上執行純 runtime 邏輯。
2. **不會被搶佔。** 它是「監工」，如果自己也被排程管理就沒意義了。

這個設計讓它成為整個 runtime 的**安全網**：即使所有 P 都被佔滿、所有 M 都阻塞，`sysmon` 仍然在跑。

```text
runtime.main()
    ↓
newm(sysmon, nil, -1)   ← 建立不綁 P 的 M
    ↓
sysmon() 進入無限迴圈
```

---

## 巡邏節奏

`sysmon` 的迴圈間隔是**自適應**的：

```text
delay = 20 微秒（起始）

for {
    if 閒置已久 {
        delay *= 2
        if delay > 10 毫秒 {
            delay = 10 毫秒   // 上限
        }
    }
    usleep(delay)

    ... 執行各項檢查 ...

    if 有發現需要處理的事 {
        delay = 20 微秒       // 重置成高頻
    }
}
```

**忙碌時每 20 微秒巡一次，閒置時降到每 10 毫秒一次。** 這讓 `sysmon` 在系統閒置時幾乎不消耗 CPU（對筆電電池與雲端帳單都有幫助），忙碌時又能及時反應。

---

## 五項工作

<figure class="diagram"><svg viewBox="0 0 700 400" role="img" aria-label="sysmon 的五項工作"><rect class="d-box-a" x="15" y="14" width="670" height="40" rx="6"/><text class="d-t-b d-mid" x="350" y="32">sysmon —— 不綁 P、不被搶佔、20 微秒到 10 毫秒自適應巡邏</text><text class="d-t-s d-mid" x="350" y="48">runtime/proc.go</text><rect class="d-box-o" x="15" y="68" width="330" height="72" rx="6"/><text class="d-t-b" x="28" y="90">① 搶佔執行過久的 G</text><text class="d-t-s" x="28" y="110">發現某個 G 在 _Grunning 超過 10 毫秒</text><text class="d-t-s" x="28" y="128">→ 發送 SIGURG 觸發非同步搶佔</text><rect class="d-box-o" x="355" y="68" width="330" height="72" rx="6"/><text class="d-t-b" x="368" y="90">② 回收阻塞在系統呼叫的 P</text><text class="d-t-s" x="368" y="110">P 處於 _Psyscall 超過 20 微秒</text><text class="d-t-s" x="368" y="128">→ handoffp：把 P 交給別的 M 繼續用</text><rect class="d-box-w" x="15" y="152" width="330" height="72" rx="6"/><text class="d-t-b" x="28" y="174">③ 輪詢網路 netpoll</text><text class="d-t-s" x="28" y="194">距離上次 netpoll 超過 10 毫秒</text><text class="d-t-s" x="28" y="212">→ 呼叫 netpoll(0)，喚醒 I/O 就緒的 G</text><rect class="d-box-w" x="355" y="152" width="330" height="72" rx="6"/><text class="d-t-b" x="368" y="174">④ 強制 GC</text><text class="d-t-s" x="368" y="194">距離上次 GC 超過 2 分鐘</text><text class="d-t-s" x="368" y="212">→ 觸發一次，避免低配置率的程式永不 GC</text><rect class="d-box" x="15" y="236" width="670" height="72" rx="6"/><text class="d-t-b" x="28" y="258">⑤ 歸還閒置記憶體給作業系統</text><text class="d-t-s" x="28" y="278">scavenger：找出長時間沒用的 span，用 madvise(MADV_FREE / MADV_DONTNEED) 告訴 OS 可以回收</text><text class="d-t-s" x="28" y="296">這是「RSS 在 GC 之後不會立刻下降」的原因 —— 歸還是漸進的</text><line class="d-dash" x1="15" y1="326" x2="685" y2="326"/><text class="d-t-s" x="15" y="350">沒有 sysmon 會怎樣？緊密迴圈永遠不讓出 CPU（GC 也做不了）、阻塞的系統呼叫拖住 P、</text><text class="d-t-s" x="15" y="372">閒置服務的記憶體永遠不歸還、低配置率的程式從不 GC。它是 runtime 的安全網。</text></svg><figcaption><b>五項職責。</b>共通點是：這些都是「沒有人會主動去做，但一定要有人做」的工作。把它們集中在一條不受排程約束的執行緒上，是最可靠的安排。</figcaption></figure>

---

## 逐項細看

### ① 搶佔執行過久的 goroutine

`retake()` 走過所有 P，檢查它們正在執行的 G：

```text
如果 P 狀態是 _Prunning 且該 G 已執行超過 forcePreemptNS（10 毫秒）:
    preemptone(p)
        → 設定 g.stackguard0 = stackPreempt（協作式：下次函式呼叫時讓出）
        → 發送 SIGURG 給該 M（非同步式：立刻中斷）
```

沒有這一項，下面的程式在 `GOMAXPROCS=1` 時會永遠卡住：

```go
package main

import (
	"fmt"
	"runtime"
	"time"
)

func main() {
	runtime.GOMAXPROCS(1)

	go func() {
		for {
		} // 沒有函式呼叫、沒有 channel 操作
	}()

	time.Sleep(50 * time.Millisecond)
	fmt.Println("sysmon 讓我印得出來")
}
```

```bash
go run main.go                              # 正常印出
GODEBUG=asyncpreemptoff=1 go run main.go    # 卡死
```

第二個指令會卡住——關掉非同步搶佔後，`sysmon` 只能設定 `stackguard0`，但那個空迴圈永遠不會呼叫函式，檢查點永遠不會被觸發。

!!! version "這在 Go 1.14 之前是真實的問題"
    Go 1.13 及之前沒有非同步搶佔。任何純運算的緊密迴圈都可能：

    - 卡住整個 GC（STW 階段要等所有 G 到達安全點）
    - 在 `GOMAXPROCS=1` 時餓死其他所有 goroutine

    當時的建議是「在長迴圈裡插入 `runtime.Gosched()`」。Go 1.14 之後不再需要。

### ② 回收系統呼叫中的 P

當 M 進入系統呼叫時，它持有的 P 會被標記為 `_Psyscall`。如果系統呼叫很快回來（大部分情況），M 直接拿回 P，零成本。

如果超過 **20 微秒**（`sysmonSyscallThreshold`）還沒回來，`sysmon` 就介入：

```text
handoffp(p):
    如果 P 的本地佇列有工作，或全域佇列非空，或有到期的計時器:
        startm(p)   // 找一個閒置的 M（或建立新的）來接手這個 P
    否則:
        把 P 放進閒置列表
```

**這是「一個 goroutine 阻塞在檔案讀取，不會拖累其他 goroutine」的機制。**

代價是可能建立新的執行緒。大量並行的阻塞式系統呼叫會讓執行緒數量膨脹——這就是 [netpoller](netpoller.html#為什麼檔案-io-不走-netpoller) 那節提到的檔案 I/O 問題。

### ③ 輪詢網路

如果距離上次 `netpoll` 超過 10 毫秒，`sysmon` 主動呼叫一次。

正常情況下排程器的 `findRunnable()` 就會做這件事。但如果所有 P 都很忙（一直有工作可做，從不進入 `findRunnable` 的閒置分支），網路事件就可能被延誤。`sysmon` 保證至少每 10 毫秒檢查一次。

### ④ 強制 GC

```text
如果 距離上次 GC 超過 forcegcperiod（2 分鐘）:
    喚醒 forcegchelper goroutine 觸發 GC
```

GC 平常由**堆積成長**觸發（達到 `GOGC` 設定的目標）。但如果程式幾乎不配置記憶體，這個條件永遠不成立。

那為什麼還要 GC？因為：

- 有些 finalizer 需要執行
- 需要一個時機來歸還記憶體給 OS
- 堆積上可能有已死但佔著空間的物件

2 分鐘是個保底頻率。

### ⑤ 歸還記憶體（scavenging）

這一項最常被誤解，值得多說一點。

**GC 回收的是「Go 堆積裡的物件」，不是「還給作業系統的記憶體」。** 物件被回收後，那塊空間回到 Go 的記憶體池（`mheap`）等待重用，作業系統看到的 RSS 完全沒變。

scavenger 負責第二步：找出長時間沒被使用的記憶體頁，告訴作業系統可以拿走。

```go
package main

import (
	"fmt"
	"runtime"
	"time"
)

func printMem(label string) {
	var m runtime.MemStats
	runtime.ReadMemStats(&m)
	fmt.Printf("%-14s HeapAlloc=%4d MB  HeapIdle=%4d MB  HeapReleased=%4d MB\n",
		label, m.HeapAlloc>>20, m.HeapIdle>>20, m.HeapReleased>>20)
}

func main() {
	printMem("啟動")

	// 配置 200 MB
	data := make([][]byte, 200)
	for i := range data {
		data[i] = make([]byte, 1<<20)
	}
	printMem("配置後")

	// 全部釋放並強制 GC
	data = nil
	runtime.GC()
	printMem("GC 後")

	// 等 scavenger 工作
	time.Sleep(3 * time.Second)
	runtime.GC()
	printMem("等待 3 秒後")

	// 明確要求立刻歸還
	debugFreeOSMemory()
	printMem("FreeOSMemory 後")
}

func debugFreeOSMemory() {
	// 等同 debug.FreeOSMemory()
	runtime.GC()
}
```

三個關鍵指標：

| 指標 | 意義 |
| --- | --- |
| `HeapAlloc` | 目前存活物件佔用的位元組 |
| `HeapIdle` | 已從 OS 取得但目前沒在用的位元組 |
| `HeapReleased` | 已經歸還給 OS 的位元組（`HeapIdle` 的子集） |

**`HeapIdle` 高而 `HeapReleased` 低 = 記憶體被 Go 佔著但沒用。** 這是「Go 程式看起來很吃記憶體」的常見原因。

!!! version "Go 1.16：預設改用 MADV_DONTNEED"
    在 Linux 上，歸還記憶體有兩種方式：

    - **`MADV_FREE`**（Go 1.12–1.15 預設）：告訴核心「這些頁可以回收，但我可能還會用」。核心在記憶體壓力大時才真的回收。**RSS 不會立刻下降**，讓監控看起來很嚇人。
    - **`MADV_DONTNEED`**（Go 1.16 起預設）：立刻歸還，RSS 馬上下降。代價是下次要用時會有 page fault。

    Go 1.16 改回 `MADV_DONTNEED`，主要理由是「使用者看得懂的記憶體數字」比那一點效能重要。想切回舊行為：`GODEBUG=madvdontneed=0`。

!!! tip "`debug.FreeOSMemory()` 的正確用途"
    ```go
    import "runtime/debug"

    debug.FreeOSMemory() // 立刻 GC + 盡可能歸還記憶體
    ```

    它會**強制一次完整的 STW GC**，代價不小。適合的場景只有一個：**程式剛完成一個明確的大型階段**（例如批次任務跑完、大量資料匯入結束），你知道接下來會安靜一段時間。

    絕對不要放在請求處理路徑或定時任務裡。

---

## 觀察 sysmon 的效果

### 看搶佔

```bash
GODEBUG=schedtrace=1000,scheddetail=1 go run main.go
```

`scheddetail=1` 會列出每個 G 的狀態。反覆執行可以看到長時間執行的 G 被切換出去。

### 看記憶體歸還

```bash
GODEBUG=gctrace=1,scavtrace=1 go run main.go
```

```text
scav 1 KiB work (0 KiB bg, 1 KiB eager), 4 KiB total, 0.00% util
```

`scavtrace=1` 會在每次 scavenge 事件時印一行。

### 看執行緒膨脹

```go
package main

import (
	"fmt"
	"runtime/pprof"
	"time"
)

func main() {
	go func() {
		for range time.Tick(time.Second) {
			fmt.Println("執行緒總數:", pprof.Lookup("threadcreate").Count())
		}
	}()

	// ... 你的工作 ...
	select {}
}
```

如果這個數字持續成長，代表有大量阻塞的系統呼叫。

---

## 一張總結表

`sysmon` 解決的問題與對應機制：

| 問題 | 沒有 sysmon 會怎樣 | sysmon 的處理 |
| --- | --- | --- |
| goroutine 霸佔 CPU | 其他 G 餓死、GC 卡住 | 10ms 後強制搶佔 |
| 系統呼叫阻塞 M | 那個 P 上的其他 G 全部停擺 | 20µs 後把 P 交接出去 |
| 所有 P 都很忙 | 網路事件被延誤 | 每 10ms 主動 netpoll |
| 程式不配置記憶體 | 永遠不 GC，finalizer 不執行 | 每 2 分鐘強制一次 |
| 記憶體用完不歸還 | RSS 只增不減 | scavenger 漸進歸還 |

**共通模式：所有這些都是「沒有明確的觸發者，但一定要發生」的事。** 把它們交給一條獨立於排程系統之外的執行緒，是最穩健的設計。

---

Part 5 到此結束。你已經看過 Go 並行模型的完整圖像：從 `context` 的取消傳播、`sync` 的同步原語、計時器與 channel，到 GMP 排程器、netpoller 與 sysmon。

Part 6 換到記憶體：物件配置在哪裡、GC 怎麼找出垃圾、goroutine 的堆疊怎麼長大。
