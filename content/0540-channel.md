---
title: channel
slug: channel
part: p5
number: "5.4"
order: 540
summary: hchan 的結構、有無緩衝的差異、直接交遞最佳化、關閉的規則，以及 goroutine 洩漏的成因與防範。
updated: "1.26"
---

## hchan 的結構

channel 在 runtime 裡是 `hchan`（`runtime/chan.go`），關鍵欄位：

```go
type hchan struct {
	qcount   uint           // 緩衝區目前有幾個元素
	dataqsiz uint           // 緩衝區容量（make 的第二個參數）
	buf      unsafe.Pointer // 環形緩衝區
	elemsize uint16
	closed   uint32
	elemtype *_type
	sendx    uint           // 環形緩衝區的寫入位置
	recvx    uint           // 環形緩衝區的讀取位置
	recvq    waitq          // 等待接收的 goroutine 佇列
	sendq    waitq          // 等待傳送的 goroutine 佇列
	lock     mutex          // 保護以上所有欄位
}
```

<figure class="diagram"><svg viewBox="0 0 700 310" role="img" aria-label="hchan 的內部結構"><rect class="d-box-a" x="15" y="14" width="300" height="150" rx="6"/><text class="d-t-b" x="30" y="36">hchan</text><rect class="d-box" x="30" y="46" width="270" height="24" rx="3"/><text class="d-t-m" x="40" y="63">qcount=2　dataqsiz=4</text><rect class="d-box" x="30" y="74" width="270" height="24" rx="3"/><text class="d-t-m" x="40" y="91">sendx=3　recvx=1</text><rect class="d-box" x="30" y="102" width="270" height="24" rx="3"/><text class="d-t-m" x="40" y="119">closed=0　lock</text><rect class="d-box" x="30" y="130" width="270" height="24" rx="3"/><text class="d-t-m" x="40" y="147">buf → 環形緩衝區</text><text class="d-t-b" x="360" y="36">環形緩衝區 buf（容量 4）</text><rect class="d-box" x="360" y="46" width="76" height="42" rx="4"/><text class="d-t-s d-mid" x="398" y="66">空</text><text class="d-t-s d-mid" x="398" y="82">[0]</text><rect class="d-box-o" x="436" y="46" width="76" height="42" rx="4"/><text class="d-t-m d-mid" x="474" y="66">v1</text><text class="d-t-s d-mid" x="474" y="82">[1] ← recvx</text><rect class="d-box-o" x="512" y="46" width="76" height="42" rx="4"/><text class="d-t-m d-mid" x="550" y="66">v2</text><text class="d-t-s d-mid" x="550" y="82">[2]</text><rect class="d-box" x="588" y="46" width="76" height="42" rx="4"/><text class="d-t-s d-mid" x="626" y="66">空</text><text class="d-t-s d-mid" x="626" y="82">[3] ← sendx</text><text class="d-t-s" x="360" y="112">讀寫指標繞圈前進，滿了 sendx 追上 recvx</text><text class="d-t-b" x="15" y="196">等待佇列（雙向鏈結串列，元素是 sudog）</text><rect class="d-box-w" x="15" y="206" width="325" height="70" rx="6"/><text class="d-t-b" x="28" y="228">sendq — 想送但送不出去的 goroutine</text><text class="d-t-s" x="28" y="248">每個 sudog 記錄：g 指標、要傳送的值的位址</text><text class="d-t-s" x="28" y="266">緩衝區滿 或 無緩衝且沒人接 → 掛在這裡休眠</text><rect class="d-box-w" x="360" y="206" width="325" height="70" rx="6"/><text class="d-t-b" x="373" y="228">recvq — 想收但收不到的 goroutine</text><text class="d-t-s" x="373" y="248">每個 sudog 記錄：g 指標、接收目標的位址</text><text class="d-t-s" x="373" y="266">緩衝區空 或 無緩衝且沒人送 → 掛在這裡休眠</text><text class="d-t-s" x="15" y="300">所有操作都在 lock 保護下進行。channel 是有鎖的資料結構 —— 這是它比 atomic 慢的原因。</text></svg><figcaption><b>hchan 三大部件。</b>環形緩衝區存資料、兩個等待佇列存被阻塞的 goroutine、一把鎖保護全部。無緩衝 channel 的 <code>dataqsiz</code> 是 0，只靠兩個佇列運作。</figcaption></figure>

---

## 傳送的三條路徑

`ch <- v` 被改寫成 `runtime.chansend1(ch, &v)`，內部依情況走三條路：

### 路徑一：有人在等接收 → 直接交遞

```text
if recvq 不為空 {
    取出一個等待的接收者
    把 v 直接複製到「它的接收目標位址」   ← 跳過緩衝區！
    喚醒它
    return
}
```

**這是最快的路徑。** 資料從傳送者的堆疊**直接寫進接收者的堆疊**，完全不經過 channel 的緩衝區——省下一次記憶體複製。

這個最佳化叫**直接交遞（direct handoff）**，是 Go channel 效能的關鍵之一。

### 路徑二：緩衝區還有空間 → 放進去

```text
if qcount < dataqsiz {
    把 v 複製到 buf[sendx]
    sendx = (sendx + 1) % dataqsiz
    qcount++
    return
}
```

### 路徑三：都不行 → 掛起休眠

```text
建立一個 sudog，記下「我要送的值在哪」
把 sudog 加入 sendq
呼叫 gopark 讓出 CPU

（之後被某個接收者喚醒，那時值已經被對方取走了）
```

接收 `<-ch` 的邏輯完全對稱。

!!! note "有緩衝 channel 的一個細節"
    如果緩衝區**滿了**而且 `recvq` 有等待者——這種情況不可能發生。因為只要 `recvq` 有人在等，緩衝區就一定是空的（等待者會先把緩衝區清空）。runtime 靠這個不變式簡化了邏輯。

---

## 無緩衝 vs 有緩衝：語意的差別

這不只是效能問題，是**同步保證**的差別。

```go
package main

import (
	"fmt"
	"time"
)

func main() {
	fmt.Println("=== 無緩衝 ===")
	unbuffered := make(chan int)
	go func() {
		time.Sleep(50 * time.Millisecond)
		v := <-unbuffered
		fmt.Println("  接收:", v)
	}()
	start := time.Now()
	unbuffered <- 1 // 阻塞到接收方真的拿到
	fmt.Printf("  傳送完成，耗時 %v\n", time.Since(start).Round(10*time.Millisecond))

	fmt.Println("=== 有緩衝 ===")
	buffered := make(chan int, 1)
	start = time.Now()
	buffered <- 1 // 立刻回來
	fmt.Printf("  傳送完成，耗時 %v\n", time.Since(start).Round(10*time.Millisecond))
	fmt.Println("  但接收方還沒拿到")
}
```

```text
=== 無緩衝 ===
  接收: 1
  傳送完成，耗時 50ms
=== 有緩衝 ===
  傳送完成，耗時 0s
  但接收方還沒拿到
```

| | 無緩衝 | 有緩衝 |
| --- | --- | --- |
| 傳送何時完成 | 接收方真的拿到時 | 放進緩衝區時 |
| 提供什麼保證 | **同步會合（rendezvous）** | 只保證「已送出」 |
| 適合什麼 | 交接所有權、需要確認對方收到 | 解耦生產與消費速率 |

### 該用哪一種

**預設用無緩衝。** 它的語意最強、最不容易出錯。

用有緩衝的時機：

1. **已知的固定數量。** 例如「我要收 N 個結果」，`make(chan Result, N)` 讓所有生產者都能寫完就走，不會卡住。
2. **平滑突發流量。** 生產速率有尖峰但平均低於消費速率時，緩衝可以吸收尖峰。
3. **避免 goroutine 洩漏。** 見下文。

!!! warning "緩衝大小不是效能旋鈕"
    `make(chan T, 1000)` 不會讓你的程式變快。如果消費者跟不上，緩衝只是把問題往後延——延到記憶體用完為止。

    緩衝真正的作用是**解耦時序**，不是提升吞吐。如果你需要調緩衝大小才能讓系統運作，通常代表消費端的處理能力不足，該解決的是那裡。

---

## 關閉 channel

### 規則

| 操作 | 對已關閉的 channel |
| --- | --- |
| 接收 | 立刻回傳零值，`ok` 為 `false`。**不會阻塞** |
| 傳送 | **panic**: send on closed channel |
| 再次關閉 | **panic**: close of closed channel |
| 關閉 nil channel | **panic**: close of nil channel |

```go
package main

import "fmt"

func main() {
	ch := make(chan int, 2)
	ch <- 1
	ch <- 2
	close(ch)

	// 關閉後仍可讀出緩衝區裡的值
	fmt.Println(<-ch) // 1
	fmt.Println(<-ch) // 2

	// 緩衝區空了之後，回傳零值
	v, ok := <-ch
	fmt.Println(v, ok) // 0 false

	// range 會自動在關閉且清空後結束
	ch2 := make(chan int, 3)
	ch2 <- 10
	ch2 <- 20
	close(ch2)
	for v := range ch2 {
		fmt.Print(v, " ")
	}
	fmt.Println()
}
```

### 誰該關閉

**核心原則：由傳送方關閉，而且只能有一個關閉者。**

```go
// ✓ 單一生產者：生產者關閉
func produce(n int) <-chan int {
	ch := make(chan int)
	go func() {
		defer close(ch) // 生產完就關
		for i := range n {
			ch <- i
		}
	}()
	return ch
}
```

多個生產者時，用 `WaitGroup` 協調：

```go
package main

import (
	"fmt"
	"sync"
)

func produceMany(producers, each int) <-chan int {
	out := make(chan int)
	var wg sync.WaitGroup

	for p := range producers {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for i := range each {
				out <- p*100 + i
			}
		}()
	}

	// 專門一個 goroutine 負責關閉
	go func() {
		wg.Wait()
		close(out)
	}()

	return out
}

func main() {
	n := 0
	for range produceMany(3, 4) {
		n++
	}
	fmt.Println("收到", n, "筆") // 12
}
```

### 需要「停止訊號」時，另開一個 channel

```go
package main

import (
	"fmt"
	"sync"
)

func worker(id int, jobs <-chan int, done <-chan struct{}, wg *sync.WaitGroup) {
	defer wg.Done()
	for {
		select {
		case <-done: // 收到停止訊號
			fmt.Printf("worker %d 停止\n", id)
			return
		case j, ok := <-jobs:
			if !ok {
				return
			}
			_ = j
		}
	}
}

func main() {
	jobs := make(chan int)
	done := make(chan struct{})
	var wg sync.WaitGroup

	for i := range 3 {
		wg.Add(1)
		go worker(i, jobs, done, &wg)
	}

	jobs <- 1
	jobs <- 2

	close(done) // ✓ 關閉是廣播：所有 worker 同時收到
	wg.Wait()
}
```

**關閉一個 channel 是最乾淨的一對多廣播機制。** 這也是 `context.Done()` 的實作原理。

（實務上直接用 `context` 就好，上面只是說明機制。）

---

## goroutine 洩漏

這是 Go 服務最常見的資源問題。**每個洩漏的 goroutine 至少佔 2 KB 堆疊**，加上它持有的所有物件都無法被 GC。

### 成因一：傳送到沒人接收的 channel

```go
package main

import (
	"fmt"
	"runtime"
	"time"
)

// ✗ 洩漏版本
func leaky() <-chan int {
	ch := make(chan int) // 無緩衝
	go func() {
		time.Sleep(10 * time.Millisecond)
		ch <- 42 // 如果沒人接，這裡永遠阻塞
	}()
	return ch
}

func main() {
	before := runtime.NumGoroutine()

	for range 100 {
		ch := leaky()
		select {
		case <-ch:
		case <-time.After(1 * time.Millisecond): // 逾時，不再接收
		}
	}

	time.Sleep(100 * time.Millisecond)
	runtime.GC()
	fmt.Printf("goroutine 數：%d → %d\n", before, runtime.NumGoroutine())
}
```

```text
goroutine 數：1 → 101
```

100 個 goroutine 永遠卡在 `ch <- 42`。

**解法一：用容量 1 的緩衝 channel。**

```go
func fixed1() <-chan int {
	ch := make(chan int, 1) // ✓ 就算沒人接，傳送也能完成
	go func() {
		time.Sleep(10 * time.Millisecond)
		ch <- 42
	}()
	return ch
}
```

這是「**只送一次結果**」場景的標準做法。goroutine 送完就結束，channel 之後會被 GC。

**解法二：用 context 讓 goroutine 知道要放棄。**

```go
func fixed2(ctx context.Context) <-chan int {
	ch := make(chan int)
	go func() {
		select {
		case <-time.After(10 * time.Millisecond):
			select {
			case ch <- 42:
			case <-ctx.Done(): // 沒人要了，放棄
			}
		case <-ctx.Done():
		}
	}()
	return ch
}
```

### 成因二：從沒人傳送、也不會關閉的 channel 接收

```go
// ✗ 如果 producer 出錯提早結束而沒 close，這裡永遠卡住
for v := range ch {
	process(v)
}
```

**永遠用 `defer close(ch)`**，確保不管怎麼結束都會關閉。

### 成因三：忘記 `WaitGroup.Done`

```go
// ✗
go func() {
	if err := work(); err != nil {
		return // 忘了 wg.Done()，Wait 永遠不返回
	}
	wg.Done()
}()

// ✓
go func() {
	defer wg.Done()
	if err := work(); err != nil {
		return
	}
}()
```

### 偵測洩漏

**方法一：`runtime.NumGoroutine()`**

```go
func TestNoLeak(t *testing.T) {
	before := runtime.NumGoroutine()

	doWork()

	time.Sleep(100 * time.Millisecond) // 給收尾時間
	if after := runtime.NumGoroutine(); after > before {
		t.Errorf("goroutine 洩漏：%d → %d", before, after)
	}
}
```

**方法二：`go.uber.org/goleak`**（推薦）

```go
func TestMain(m *testing.M) {
	goleak.VerifyTestMain(m)
}
```

它會在測試結束時檢查有沒有殘留的 goroutine，並印出它們的堆疊追蹤。加進既有專案常常會發現一堆意外的洩漏。

**方法三：pprof**

```bash
curl "http://localhost:6060/debug/pprof/goroutine?debug=2"
```

會列出所有 goroutine 的完整堆疊。持續成長的服務，隔一段時間抓兩次比對，就能找出洩漏點。

!!! version "Go 1.26：goroutineleak 剖析"
    Go 1.26 在 `runtime/pprof` 加入實驗性的 `goroutineleak` 剖析，會嘗試自動找出**永遠不可能被喚醒**的 goroutine（例如阻塞在一個沒有其他人持有參照的 channel 上）。這比人工比對兩份快照精準得多。

---

## 常見錯誤與解法

channel 的 panic 全部來自「關閉」相關的操作，阻塞則全部來自「沒有對手」。先一張總表：

| 操作 | nil channel | 開啟中 | 已關閉 |
| --- | --- | --- | --- |
| `ch <- v` | **永久阻塞** | 阻塞到有人收／緩衝有空 | **panic** |
| `<-ch` | **永久阻塞** | 阻塞到有人送／緩衝有值 | 立刻回傳零值，`ok = false` |
| `close(ch)` | **panic** | 正常關閉 | **panic** |
| `len` / `cap` | 0 | 目前值 | 目前值 |

### ① 誰負責 close：由傳送方，而且只能有一個

這是所有 channel 錯誤的源頭。**接收方永遠不要 close**——它無法知道傳送方是不是還要送，關掉之後對方一送就 panic。

```go
// ✗ 多個生產者各自 close → 第二個就 panic
for i := 0; i < 3; i++ {
	go func() {
		produce(out)
		close(out) // ✗ close of closed channel
	}()
}

// ✓ 用 WaitGroup 協調，由一個專門的 goroutine 關
var wg sync.WaitGroup
for i := 0; i < 3; i++ {
	wg.Add(1)
	go func() { defer wg.Done(); produce(out) }()
}
go func() {
	wg.Wait()
	close(out) // ✓ 確定所有人都送完了
}()
```

!!! tip "不確定該不該 close 時，就不要 close"
    channel 不像檔案，**不關也不會洩漏資源**——沒有人參照它的時候會被 GC 回收。

    `close` 的用途只有一個：**通知接收方「不會再有東西了」**。如果接收方本來就知道要收幾筆（例如 `for i := 0; i < n; i++ { <-ch }`），根本不需要 close。

### ② 要「停止訊號」時，另開一個 channel

想叫工作者停下來，不要去 close 資料 channel：

```go
// ✗ 生產者可能還在送 → panic
close(jobs)

// ✓ 用另一個專門的 channel 廣播（或直接用 context）
done := make(chan struct{})
close(done) // 關閉 = 一對多廣播，所有等待者同時被喚醒
```

實務上直接用 `context`：它就是把這個模式標準化，而且能沿著呼叫樹自動傳播。見 [context](context.html)。

### ③ `for range` 沒有 close 就永遠不會結束

```go
// ✗ 生產者中途 return（例如出錯），忘了 close → 消費端永遠卡住
go func() {
	for _, v := range data {
		if err := check(v); err != nil {
			return // ← 沒 close
		}
		out <- v
	}
	close(out)
}()

// ✓ 用 defer，不管怎麼離開都會關
go func() {
	defer close(out)
	for _, v := range data {
		if err := check(v); err != nil {
			return
		}
		out <- v
	}
}()
```

**`defer close(ch)` 應該是肌肉記憶**。錯誤路徑忘記 close 是 goroutine 洩漏的頭號來源。

### ④ 同一個 goroutine 收發無緩衝 channel

```go
package main

func main() {
	ch := make(chan int) // 無緩衝
	ch <- 1              // ✗ 沒有其他 goroutine 在接收 → 自己把自己鎖死
	<-ch                 // 永遠執行不到
}
```

```text
fatal error: all goroutines are asleep - deadlock!
```

無緩衝 channel 的傳送**必須有另一個 goroutine 同時在接收**。這在寫測試時特別容易犯——想「先塞幾筆資料再讀」，就要用有緩衝的：

```go
ch := make(chan int, 3) // ✓ 緩衝夠的話，同一個 goroutine 可以先塞後讀
ch <- 1
ch <- 2
close(ch)
for v := range ch {
	_ = v
}
```

!!! note "`fatal error: deadlock` 只在「所有 goroutine 都睡著」時才會報"
    runtime 偵測得到的是**全域死結**。如果你的程式還有其他 goroutine 在跑（例如一個背景的 ticker），即使某個 goroutine 永遠卡住，runtime 也不會報錯——它就只是安靜地洩漏掉。

    所以「沒有 deadlock 錯誤」不代表沒有卡住的 goroutine。要抓這種，靠 `/debug/pprof/goroutine` 或 `goleak`（見[偵測洩漏](#goroutine-洩漏)）。

### ⑤ 用 channel 當互斥鎖

```go
// ✗ 可以動，但比 mutex 慢一個數量級
sem := make(chan struct{}, 1)
sem <- struct{}{}
// 臨界區
<-sem

// ✓
var mu sync.Mutex
mu.Lock()
// 臨界區
mu.Unlock()
```

channel 的收發約 50–150 ns，`Mutex.Lock`＋`Unlock` 約 15 ns。**channel 適合「傳遞資料所有權」與「協調流程」，不適合單純的互斥。**

反過來說，`make(chan struct{}, N)` 當**號誌**（限制並行數）是正當用法——那是在協調並行度，不是互斥。

### ⑥ 在 `select` 裡用 `break`

```go
// ✗ break 只跳出 select，迴圈繼續
for {
	select {
	case <-done:
		break
	}
}

// ✓ 用標籤，或直接 return
loop:
for {
	select {
	case <-done:
		break loop
	}
}
```

這是實務上很常見的無限迴圈來源。詳見 [for 與 range](for-range.html)。

---

## 常見模式

### 工作池（worker pool）

```go
package main

import (
	"fmt"
	"sync"
)

func workerPool(jobs []int, workers int) []int {
	jobCh := make(chan int)
	resCh := make(chan int, len(jobs))

	var wg sync.WaitGroup
	for range workers {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for j := range jobCh {
				resCh <- j * j
			}
		}()
	}

	go func() {
		for _, j := range jobs {
			jobCh <- j
		}
		close(jobCh) // 通知所有 worker 沒工作了
	}()

	wg.Wait()
	close(resCh)

	var out []int
	for r := range resCh {
		out = append(out, r)
	}
	return out
}

func main() {
	fmt.Println(len(workerPool([]int{1, 2, 3, 4, 5, 6, 7, 8}, 3))) // 8
}
```

注意 `resCh` 的緩衝設成 `len(jobs)`——這確保 worker 不會因為結果沒人收而阻塞，`wg.Wait()` 才能順利返回。

### 號誌（semaphore）：限制並行數

```go
package main

import (
	"fmt"
	"sync"
	"time"
)

func main() {
	sem := make(chan struct{}, 3) // 最多 3 個同時執行
	var wg sync.WaitGroup

	for i := range 10 {
		wg.Add(1)
		go func() {
			defer wg.Done()

			sem <- struct{}{}        // 取得許可
			defer func() { <-sem }() // 釋放

			time.Sleep(20 * time.Millisecond)
			_ = i
		}()
	}

	start := time.Now()
	wg.Wait()
	fmt.Printf("10 個任務、並行 3、耗時約 %v\n", time.Since(start).Round(10*time.Millisecond))
}
```

```text
10 個任務、並行 3、耗時約 80ms
```

（也可以用 `errgroup.SetLimit` 或 `golang.org/x/sync/semaphore`，後者支援加權。）

### pipeline

```go
package main

import "fmt"

func gen(nums ...int) <-chan int {
	out := make(chan int)
	go func() {
		defer close(out)
		for _, n := range nums {
			out <- n
		}
	}()
	return out
}

func square(in <-chan int) <-chan int {
	out := make(chan int)
	go func() {
		defer close(out)
		for n := range in {
			out <- n * n
		}
	}()
	return out
}

func main() {
	for v := range square(square(gen(1, 2, 3))) {
		fmt.Print(v, " ") // 1 16 81
	}
	fmt.Println()
}
```

每一段都遵守同樣的契約：**接收一個唯讀 channel，回傳一個唯讀 channel，自己負責關閉輸出。**

!!! tip "Go 1.23 之後，很多 pipeline 可以改用迭代器"
    `iter.Seq` 沒有 goroutine 與 channel 的開銷，而且天然支援提前終止。如果你的 pipeline 是純粹的資料轉換（不需要真正的並行），迭代器是更好的選擇。詳見 [for 與 range](for-range.html#go-123range-over-func-迭代器)。

    channel pipeline 的價值在於**各階段真的並行執行**（例如各自做 I/O）。

---

下一節是 Part 5 的核心：GMP 排程器。
