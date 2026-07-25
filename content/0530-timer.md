---
title: 計時器 timer
slug: timer
part: p5
number: "5.3"
order: 530
summary: Go 1.23 的計時器重寫、四叉堆積的組織方式、Timer 與 Ticker 的正確用法，以及舊版的洩漏陷阱。
updated: "1.26"
---

## 計時器的三代演進

計時器看起來簡單，但要在有數十萬個計時器的服務裡做到低延遲，實作上並不容易。Go 的計時器改過三次架構：

| 版本 | 架構 | 主要問題 |
| --- | --- | --- |
| ≤ 1.9 | 全域單一四叉堆積 + 一把全域鎖 | 鎖競爭嚴重，多核心無法擴展 |
| 1.10–1.13 | 分成 64 個桶，各自有鎖 | 桶之間負載不均，仍有競爭 |
| 1.14–1.22 | **每個 P 一個堆積**，由排程器整合 | 大幅改善，但 channel 語意有坑 |
| **≥ 1.23** | 同上，但 **channel 語意重寫** | 修掉 `Reset`／`Stop` 的競爭問題 |

現行架構的核心思想：**把計時器交給排程器管理**。每個 P 有自己的計時器堆積，排程器在找工作時順便檢查有沒有到期的計時器。

---

## 每個 P 一個四叉堆積

```text
type p struct {
    // ...
    timers timers   // 這個 P 的計時器集合
}
```

`timers` 內部是一個**四叉最小堆積（4-ary min-heap）**，依到期時間排序，堆頂就是最快到期的那個。

<figure class="diagram"><svg viewBox="0 0 700 290" role="img" aria-label="每個 P 的計時器四叉堆積"><rect class="d-box" x="15" y="14" width="200" height="76" rx="6"/><text class="d-t-b d-mid" x="115" y="36">P0</text><text class="d-t-s d-mid" x="115" y="56">本地執行佇列</text><text class="d-t-a d-mid" x="115" y="78">timers 堆積</text><rect class="d-box" x="250" y="14" width="200" height="76" rx="6"/><text class="d-t-b d-mid" x="350" y="36">P1</text><text class="d-t-s d-mid" x="350" y="56">本地執行佇列</text><text class="d-t-a d-mid" x="350" y="78">timers 堆積</text><rect class="d-box" x="485" y="14" width="200" height="76" rx="6"/><text class="d-t-b d-mid" x="585" y="36">P2 …</text><text class="d-t-s d-mid" x="585" y="56">本地執行佇列</text><text class="d-t-a d-mid" x="585" y="78">timers 堆積</text><text class="d-t-b" x="15" y="122">四叉最小堆積（每個節點最多 4 個子節點）</text><rect class="d-box-a" x="290" y="132" width="120" height="30" rx="4"/><text class="d-t-m d-mid" x="350" y="152">到期 10:00:01</text><path class="d-line" d="M330 162 L120 190"/><path class="d-line" d="M343 162 L270 190"/><path class="d-line" d="M357 162 L420 190"/><path class="d-line" d="M370 162 L570 190"/><rect class="d-box" x="55" y="192" width="120" height="28" rx="4"/><text class="d-t-m d-mid" x="115" y="211">10:00:05</text><rect class="d-box" x="205" y="192" width="120" height="28" rx="4"/><text class="d-t-m d-mid" x="265" y="211">10:00:03</text><rect class="d-box" x="365" y="192" width="120" height="28" rx="4"/><text class="d-t-m d-mid" x="425" y="211">10:00:08</text><rect class="d-box" x="515" y="192" width="120" height="28" rx="4"/><text class="d-t-m d-mid" x="575" y="211">10:00:02</text><text class="d-t-s" x="15" y="248">為什麼是四叉不是二叉？樹更矮（層數少約一半），上浮／下沉時的比較次數雖然多一點，</text><text class="d-t-s" x="15" y="268">但記憶體存取更集中，同一層的 4 個子節點常在同一條快取線上 —— 對現代 CPU 更友善。</text></svg><figcaption><b>P 本地的計時器堆積。</b>加入計時器時放進「目前 P」的堆積，操作幾乎無鎖。工作竊取時，閒置的 P 也會順便竊取其他 P 的到期計時器，避免某個 P 忙碌時它的計時器被延誤。</figcaption></figure>

### 誰負責檢查到期

三個地方：

1. **排程器的 `schedule()`** —— 每次要找下一個 goroutine 執行前，先呼叫 `checkTimers()`。
2. **`findRunnable()` 的工作竊取階段** —— 沒工作可做時，順便檢查（並竊取）其他 P 的計時器。
3. **`sysmon` 監控執行緒** —— 所有 P 都在睡覺時，由它負責喚醒。詳見 [系統監控](sysmon.html)。

這個設計的好處是**計時器檢查搭便車**，不需要專門的計時器執行緒。代價是精度受排程器影響——`time.Sleep(1ms)` 實際可能睡 1.1ms 或更久。

!!! note "Go 的計時器精度"
    不要期待微秒級精度。實際誤差通常在數百微秒到數毫秒之間，取決於：

    - 排程器多久檢查一次（跟 P 的忙碌程度有關）
    - 作業系統的計時器精度（Windows 預設約 15.6 毫秒，Go runtime 會提高解析度）
    - `GOMAXPROCS` 與整體負載

    需要高精度定時（音訊、即時控制）的場景，Go 不是好選擇。

---

## Go 1.23 的 channel 語意重寫

!!! version "Go 1.23：Timer channel 變成無緩衝"
    這是使用者最有感的一次改變。

    **舊行為（≤ 1.22）**：`Timer.C` 是**容量 1 的緩衝 channel**。計時器到期時，runtime 把時間值塞進緩衝區。

    **新行為（≥ 1.23）**：`Timer.C` 對外表現為**容量 0**（`len` 與 `cap` 都回傳 0）。runtime 內部仍有緩衝，但透過特殊處理讓它表現得像無緩衝 channel。

    這個改變需要 `go.mod` 宣告 `go 1.23` 以上才生效。可用 `GODEBUG=asynctimerchan=1` 強制舊行為。

### 舊行為的問題

```go
package main

import (
	"fmt"
	"time"
)

func main() {
	t := time.NewTimer(10 * time.Millisecond)
	time.Sleep(20 * time.Millisecond) // 計時器已經到期，值進了緩衝區

	if !t.Stop() {
		// Go 1.22：這裡回傳 false，表示已經到期
		// 但緩衝區裡還有一個舊值！
		fmt.Println("Stop 回傳 false")
	}

	t.Reset(1 * time.Hour) // 重設成一小時後

	select {
	case v := <-t.C:
		// Go ≤ 1.22：立刻收到「陳舊的」舊值！
		fmt.Println("收到（不該發生）:", v)
	case <-time.After(50 * time.Millisecond):
		fmt.Println("正常：沒有陳舊值")
	}
}
```

在 Go 1.22 及之前，這段程式會印出「收到（不該發生）」——因為緩衝區裡還躺著一個舊的時間值。

為了避免這個問題，舊版的正確 `Reset` 寫法非常繁瑣：

```go
// Go ≤ 1.22 的正確寫法（現在不需要了）
if !t.Stop() {
	select {
	case <-t.C: // 排空緩衝區
	default:
	}
}
t.Reset(d)
```

而且這個寫法在有其他 goroutine 也在讀 `t.C` 時仍然有競爭。

### 新行為

Go 1.23 起，規格保證：**`Stop` 或 `Reset` 呼叫之後，不會再收到該呼叫之前準備的任何值。**

```go
// Go ≥ 1.23：直接 Reset 就對了
t.Stop()
t.Reset(d)
```

### 另一個改善：立即可回收

**Go 1.23 起，不再被參照的 Timer 與 Ticker 可以立刻被 GC 回收，即使沒有呼叫 `Stop()`。**

這修掉了一個經典的洩漏：

```go
// 在 Go 1.22 及之前，這是真實的記憶體洩漏
for {
	select {
	case v := <-ch:
		process(v)
	case <-time.After(time.Minute): // 每輪建立一個活一分鐘的計時器
		return
	}
}
```

舊版裡，這些計時器要等到一分鐘後到期才會被釋放。如果迴圈每秒跑 1000 次，就會累積 60000 個計時器。Go 1.23 起，離開 `select` 後這些計時器立刻變成垃圾。

!!! warning "仍然建議明確 Stop"
    雖然不再洩漏，但在高頻迴圈裡重複建立計時器**仍有配置成本**。重用是更好的做法：

    ```go
    t := time.NewTimer(time.Minute)
    defer t.Stop()

    for {
        if !t.Stop() {
            select { case <-t.C: default: }  // Go 1.23+ 其實可以省略這段
        }
        t.Reset(time.Minute)

        select {
        case v := <-ch:
            process(v)
        case <-t.C:
            return
        }
    }
    ```

### `len(t.C)` 的相容性影響

```go
// ✗ 這段程式在 Go 1.23 之後會壞掉
if len(t.C) > 0 {
	<-t.C // 以為有值可以拿
}
```

新版 `len(t.C)` 永遠回傳 0。要檢查有沒有值，用非阻塞接收：

```go
// ✓
select {
case <-t.C:
default:
}
```

---

## Timer 與 Ticker 的正確用法

### `time.Sleep`

最簡單的形式，讓目前 goroutine 休眠。

```go
time.Sleep(100 * time.Millisecond)
```

實作是 `runtime.timeSleep`：把目前 g 加入計時器堆積，然後 `gopark` 讓出 CPU。到期時排程器把它設回可執行狀態。

**它不會阻塞 OS 執行緒**——M 會去跑其他 goroutine。這是為什麼 Go 可以同時有百萬個 sleeping goroutine，而傳統執行緒模型不行。

### `time.After`

```go
select {
case v := <-ch:
	process(v)
case <-time.After(3 * time.Second):
	return errors.New("逾時")
}
```

方便，但如上所述，高頻迴圈裡要注意配置成本。

### `time.NewTimer`：可控制的單次計時器

```go
package main

import (
	"fmt"
	"time"
)

func main() {
	t := time.NewTimer(50 * time.Millisecond)
	defer t.Stop()

	select {
	case now := <-t.C:
		fmt.Println("到期:", now.Format("15:04:05.000"))
	}

	// 重用
	t.Reset(30 * time.Millisecond)
	<-t.C
	fmt.Println("第二次到期")
}
```

### `time.NewTicker`：週期性

```go
package main

import (
	"context"
	"fmt"
	"time"
)

func main() {
	ctx, cancel := context.WithTimeout(context.Background(), 350*time.Millisecond)
	defer cancel()

	tk := time.NewTicker(100 * time.Millisecond)
	defer tk.Stop() // ✓ Ticker 一定要 Stop

	n := 0
	for {
		select {
		case <-ctx.Done():
			fmt.Println("結束，共觸發", n, "次")
			return
		case <-tk.C:
			n++
			fmt.Println("tick", n)
		}
	}
}
```

```text
tick 1
tick 2
tick 3
結束，共觸發 3 次
```

!!! warning "Ticker 會丟棄來不及處理的 tick"
    Ticker 的 channel 容量是 1。如果你的處理邏輯比週期慢，多餘的 tick 會被**直接丟掉**，不會累積。

    ```go
    tk := time.NewTicker(100 * time.Millisecond)
    for range tk.C {
        time.Sleep(500 * time.Millisecond) // 處理太慢
        // 每 500ms 只會收到一次 tick，中間的 4 次被丟棄
    }
    ```

    **這通常是你要的行為**（避免積壓爆炸），但如果你需要「執行 N 次」的語意，就要自己計數，不能假設 tick 次數等於經過的週期數。

### `time.AfterFunc`：到期時執行函式

```go
package main

import (
	"fmt"
	"time"
)

func main() {
	done := make(chan struct{})

	t := time.AfterFunc(50*time.Millisecond, func() {
		fmt.Println("在新的 goroutine 中執行")
		close(done)
	})

	// 可以取消
	// t.Stop()

	<-done
	_ = t
}
```

注意 `AfterFunc` 的函式**在一個新的 goroutine 中執行**，不是在計時器的內部執行緒上。所以裡面可以做任何事，包括阻塞操作。

---

## 常見模式

### 指數退避重試

```go
package main

import (
	"context"
	"errors"
	"fmt"
	"math/rand/v2"
	"time"
)

func retry(ctx context.Context, attempts int, fn func() error) error {
	backoff := 100 * time.Millisecond
	const maxBackoff = 5 * time.Second

	t := time.NewTimer(0)
	if !t.Stop() {
		<-t.C
	}
	defer t.Stop()

	var lastErr error
	for i := range attempts {
		if err := fn(); err == nil {
			return nil
		} else {
			lastErr = err
		}

		if i == attempts-1 {
			break
		}

		// 加入抖動（jitter），避免大量客戶端同時重試
		jitter := time.Duration(rand.Int64N(int64(backoff / 2)))
		wait := backoff + jitter

		t.Reset(wait)
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-t.C:
		}

		backoff = min(backoff*2, maxBackoff)
	}
	return fmt.Errorf("重試 %d 次後失敗: %w", attempts, lastErr)
}

func main() {
	n := 0
	err := retry(context.Background(), 5, func() error {
		n++
		if n < 3 {
			return errors.New("暫時失敗")
		}
		return nil
	})
	fmt.Println("嘗試次數:", n, "錯誤:", err)
}
```

```text
嘗試次數: 3 錯誤: <nil>
```

**抖動很重要。** 沒有它，一次服務中斷後所有客戶端會在同一瞬間重試，形成「驚群效應（thundering herd）」把服務再打掛一次。

### 節流（throttle）與去抖（debounce）

```go
package main

import (
	"fmt"
	"time"
)

// 去抖：事件停止 d 時間後才觸發一次
func debounce(d time.Duration, fn func()) func() {
	var t *time.Timer
	return func() {
		if t != nil {
			t.Stop()
		}
		t = time.AfterFunc(d, fn)
	}
}

func main() {
	trigger := debounce(50*time.Millisecond, func() {
		fmt.Println("實際執行（只有一次）")
	})

	for range 5 {
		trigger()
		time.Sleep(10 * time.Millisecond) // 事件連續進來
	}

	time.Sleep(100 * time.Millisecond) // 等安靜下來
}
```

```text
實際執行（只有一次）
```

（上面的版本沒有加鎖，只適用於單一 goroutine 呼叫。多 goroutine 場景要加 mutex 保護 `t`。）

### 超時控制的正確層次

```go
// ✗ 只在最外層設 timeout，內部的慢操作仍會拖住
func handler(w http.ResponseWriter, r *http.Request) {
	ctx, cancel := context.WithTimeout(r.Context(), 5*time.Second)
	defer cancel()
	slowOperation() // 沒吃 ctx，timeout 完全沒作用
}

// ✓ 每一層都傳遞並尊重 ctx
func handler(w http.ResponseWriter, r *http.Request) {
	ctx, cancel := context.WithTimeout(r.Context(), 5*time.Second)
	defer cancel()

	if err := slowOperation(ctx); err != nil {
		http.Error(w, err.Error(), http.StatusGatewayTimeout)
		return
	}
}
```

**timeout 只有在整條呼叫鏈都尊重 context 時才有意義。**

---

## `time.Time` 的兩個時鐘

Go 的 `time.Time` 內部同時存了**牆上時鐘（wall clock）**與**單調時鐘（monotonic clock）**：

```go
package main

import (
	"fmt"
	"time"
)

func main() {
	t := time.Now()
	fmt.Println(t) // 2026-07-25 14:30:00.123456 +0800 CST m=+0.000123456
	//                                                     ^^^^^^^^^^^^^^ 單調時鐘讀數
}
```

- **牆上時鐘**用於顯示與格式化。它可能因為 NTP 校時而**跳躍**（甚至倒退）。
- **單調時鐘**只會前進，用於量測時間差。

`time.Since(t)`、`t2.Sub(t1)` 會**優先使用單調時鐘**，所以測量出來的時間不會被 NTP 校時影響。

```go
start := time.Now()
doWork()
elapsed := time.Since(start) // ✓ 用單調時鐘，可靠
```

!!! warning "序列化會丟掉單調時鐘"
    ```go
    t1 := time.Now()
    data, _ := json.Marshal(t1)
    var t2 time.Time
    json.Unmarshal(data, &t2)

    // t2 只有牆上時鐘，沒有單調讀數
    fmt.Println(t1 == t2) // false！即使看起來一樣
    ```

    這也是 `reflect.DeepEqual` 比較 `time.Time` 會出意外的原因。要比較時間點用 `t1.Equal(t2)`，它只比較實際時刻。

    需要剝掉單調時鐘時用 `t.Round(0)`。

---

下一節談 channel——Go 並行模型的核心。
