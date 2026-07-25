---
title: select
slug: select
part: p4
number: "4.2"
order: 420
summary: selectgo 的四步驟、隨機選擇為什麼是必要的、空 select 與 default 的特殊處理，以及常見的 select 慣用寫法。
updated: "1.26"
---

## select 做什麼

`select` 讓一個 goroutine 同時等待多個 channel 操作，哪個先就緒就執行哪個。

```go
package main

import (
	"fmt"
	"time"
)

func main() {
	fast := make(chan string)
	slow := make(chan string)

	go func() { time.Sleep(10 * time.Millisecond); fast <- "快的" }()
	go func() { time.Sleep(100 * time.Millisecond); slow <- "慢的" }()

	select {
	case v := <-fast:
		fmt.Println(v)
	case v := <-slow:
		fmt.Println(v)
	}
}
```

```text
快的
```

它跟 `switch` 長得像，但語意完全不同：`switch` 是**依值分支**，`select` 是**等待事件**。

---

## 四種特殊情況

在談一般情況之前，先看編譯器特別處理的四種：

### ① 空 select：永久阻塞

```go
select {} // 直接呼叫 runtime.block()，永久休眠
```

這個 goroutine 永遠不會被喚醒。如果是 `main` goroutine 執行到這裡，而其他 goroutine 也都在等待，runtime 會偵測到死結：

```text
fatal error: all goroutines are asleep - deadlock!
```

有時候會看到 `select {}` 被用在 `main` 結尾當「不要結束」的手段。**不建議**——用 `sync.WaitGroup` 或等待訊號更清楚。

### ② 只有一個 case：等同直接操作

```go
select {
case v := <-ch:
	use(v)
}
```

編譯器直接改寫成 `v := <-ch; use(v)`，不走 `selectgo`。

### ③ 一個 case 加 default：非阻塞操作

```go
select {
case v := <-ch:
	use(v)
default:
	// channel 沒東西時走這裡
}
```

改寫成非阻塞版本的 channel 操作（`runtime.selectnbrecv`／`selectnbsend`），只是「試一下」，不會讓 goroutine 睡著。

### ④ 有 default 的多 case：先掃一遍，都不行就走 default

不需要進入休眠邏輯，掃過所有 case 看有沒有能立刻完成的。

---

## 一般情況：`runtime.selectgo`

多個 case 且沒有 `default` 時，編譯器把整個 `select` 改寫成一次 `runtime.selectgo` 呼叫（`runtime/select.go`）。它做四件事：

<figure class="diagram"><svg viewBox="0 0 700 350" role="img" aria-label="selectgo 的四個步驟"><rect class="d-box-a" x="15" y="14" width="670" height="62" rx="7"/><text class="d-t-b" x="30" y="36">① 隨機打亂順序 + 依位址排序</text><text class="d-t-s" x="30" y="56">pollorder：隨機排列，決定「檢查誰先」——避免飢餓　·　lockorder：依 channel 位址排序，決定「上鎖順序」——避免死結</text><path class="d-line" d="M350 76 L350 90" marker-end="url(#ar13)"/><rect class="d-box-a" x="15" y="92" width="670" height="62" rx="7"/><text class="d-t-b" x="30" y="114">② 依 pollorder 掃一遍，找有沒有能立刻完成的</text><text class="d-t-s" x="30" y="134">接收：channel 有緩衝資料、有等待中的傳送者、或已關閉　·　傳送：有等待中的接收者、或緩衝區未滿</text><text class="d-t-a" x="480" y="176">找到 → 直接完成，回傳</text><path class="d-line" d="M350 154 L350 190" marker-end="url(#ar13)"/><text class="d-t-s" x="200" y="176">都不行 ↓</text><rect class="d-box-w" x="15" y="192" width="670" height="62" rx="7"/><text class="d-t-b" x="30" y="214">③ 把自己掛到「所有」channel 的等待佇列上，然後休眠</text><text class="d-t-s" x="30" y="234">為每個 case 建立一個 sudog（等待記錄），全部串進對應 channel 的 sendq / recvq，最後呼叫 gopark 讓出 CPU</text><path class="d-line" d="M350 254 L350 268" marker-end="url(#ar13)"/><rect class="d-box-o" x="15" y="270" width="670" height="62" rx="7"/><text class="d-t-b" x="30" y="292">④ 被某個 channel 喚醒後，從其他所有佇列上把自己摘掉</text><text class="d-t-s" x="30" y="312">喚醒者已經把資料直接寫進 sudog，所以醒來即完成；接著清理其餘 sudog，回傳中選的 case 索引</text><defs><marker id="ar13" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M0 0 L10 5 L0 10 z" style="fill:var(--line-strong)"/></marker></defs></svg><figcaption><b>selectgo 的四步。</b>關鍵在步驟 ③：goroutine 會同時掛在所有 channel 的等待佇列上。任何一個 channel 就緒都能喚醒它，醒來後負責從其他佇列清掉自己——這是 <code>select</code> 比手動輪詢高效的原因。</figcaption></figure>

### 為什麼要兩種順序

**`pollorder`（隨機）** 決定檢查哪個 case 的順序。如果固定按程式碼順序檢查，寫在前面的 case 永遠優先，後面的可能永遠輪不到。

**`lockorder`（依位址排序）** 決定上鎖的順序。`selectgo` 需要同時鎖住所有涉及的 channel。如果兩個 goroutine 用不同順序鎖，就會死結。**依記憶體位址排序**是解決這類問題的經典手法——所有人都用同一個全域一致的順序。

### 隨機選擇：親眼驗證

```go
package main

import "fmt"

func main() {
	a := make(chan int, 10)
	b := make(chan int, 10)

	counts := map[string]int{}
	for i := 0; i < 10000; i++ {
		a <- 1
		b <- 2

		select {
		case <-a:
			counts["a"]++
		case <-b:
			counts["b"]++
		}

		// 清空另一個
		select {
		case <-a:
		default:
		}
		select {
		case <-b:
		default:
		}
	}
	fmt.Println(counts)
}
```

```text
map[a:5027 b:4973]
```

兩者都就緒時，機率大約各半。

!!! warning "不要依賴 case 的順序"
    ```go
    // ✗ 錯誤的假設：以為 ctx.Done() 會優先
    select {
    case <-ctx.Done():
        return ctx.Err()
    case v := <-work:
        process(v)
    }
    ```

    如果 context 已取消**且** `work` 有資料，這段程式碼有一半的機率會處理那筆資料。要保證優先檢查取消，得寫成兩層：

    ```go
    // ✓ 明確的優先順序
    select {
    case <-ctx.Done():
        return ctx.Err()
    default:
    }

    select {
    case <-ctx.Done():
        return ctx.Err()
    case v := <-work:
        process(v)
    }
    ```

---

## nil channel：動態關閉某個 case

對 `nil` channel 的傳送與接收都會**永久阻塞**。這看起來像個坑，其實是 `select` 最實用的技巧之一：**把某個 case 設成 nil，等於暫時停用它。**

```go
package main

import "fmt"

// 合併兩個 channel，任一關閉後就不再從它讀取
func merge(a, b <-chan int) <-chan int {
	out := make(chan int)

	go func() {
		defer close(out)
		for a != nil || b != nil {
			select {
			case v, ok := <-a:
				if !ok {
					a = nil // 停用這個 case
					continue
				}
				out <- v
			case v, ok := <-b:
				if !ok {
					b = nil
					continue
				}
				out <- v
			}
		}
	}()

	return out
}

func main() {
	a := make(chan int, 3)
	b := make(chan int, 3)
	a <- 1
	a <- 3
	close(a)
	b <- 2
	close(b)

	sum := 0
	for v := range merge(a, b) {
		sum += v
	}
	fmt.Println("總和:", sum) // 6
}
```

如果沒有 `a = nil` 這一招，已關閉的 channel 會**一直立刻就緒**（回傳零值與 `false`），讓 `select` 陷入忙碌迴圈。

---

## 常見慣用寫法

### 逾時

```go
select {
case res := <-work:
	handle(res)
case <-time.After(3 * time.Second):
	return errors.New("逾時")
}
```

!!! warning "`time.After` 在迴圈裡會累積計時器"
    ```go
    // ✗ 每一輪都建立一個新計時器，到期前不會被回收
    for {
        select {
        case v := <-ch:
            process(v)
        case <-time.After(time.Second):
            return
        }
    }
    ```

    在 Go 1.23 之前，這是真實的記憶體洩漏來源。**Go 1.23 起，未被參照的 Timer 可以立刻被 GC 回收**（不再需要呼叫 `Stop`），問題大幅緩解。

    但即使如此，在高頻迴圈裡重複建立計時器仍有成本。正確做法是重用：

    ```go
    t := time.NewTimer(time.Second)
    defer t.Stop()

    for {
        t.Reset(time.Second)
        select {
        case v := <-ch:
            process(v)
        case <-t.C:
            return
        }
    }
    ```

### 取消

```go
func worker(ctx context.Context, jobs <-chan Job) error {
	for {
		select {
		case <-ctx.Done():
			return ctx.Err()
		case j, ok := <-jobs:
			if !ok {
				return nil
			}
			if err := process(j); err != nil {
				return err
			}
		}
	}
}
```

這是 Go 服務裡最常見的迴圈骨架。

### 非阻塞傳送：滿了就丟棄

```go
func tryPublish(ch chan<- Event, e Event) bool {
	select {
	case ch <- e:
		return true
	default:
		return false // 緩衝區滿了，丟棄
	}
}
```

日誌、指標上報這類「掉了也沒關係」的場景很適用，可以避免生產者被慢消費者拖住。

### 心跳

```go
func run(ctx context.Context, work <-chan Job) {
	tick := time.NewTicker(30 * time.Second)
	defer tick.Stop()

	for {
		select {
		case <-ctx.Done():
			return
		case j := <-work:
			process(j)
		case <-tick.C:
			reportHealth()
		}
	}
}
```

### fan-in：多個來源合併

```go
package main

import (
	"fmt"
	"sync"
)

func fanIn[T any](chans ...<-chan T) <-chan T {
	out := make(chan T)
	var wg sync.WaitGroup

	for _, c := range chans {
		wg.Add(1)
		go func(c <-chan T) {
			defer wg.Done()
			for v := range c {
				out <- v
			}
		}(c)
	}

	go func() {
		wg.Wait()
		close(out)
	}()

	return out
}

func main() {
	mk := func(vals ...int) <-chan int {
		c := make(chan int, len(vals))
		for _, v := range vals {
			c <- v
		}
		close(c)
		return c
	}

	total := 0
	for v := range fanIn(mk(1, 2), mk(3, 4), mk(5)) {
		total += v
	}
	fmt.Println(total) // 15
}
```

當來源數量在執行期才知道時，用這種「每個來源一個 goroutine」的做法，比動態建構 `select`（只能用 `reflect.Select`，很慢）好得多。

---

## 效能特性

| 情況 | 大致成本 |
| --- | --- |
| 單一 case（編譯器改寫） | 等同直接 channel 操作 |
| 有 default 且立刻就緒 | 幾十 ns，無休眠 |
| 多 case 且立刻有就緒的 | 掃描 + 鎖，約 100 ns 級 |
| 多 case 需要休眠 | 建立 N 個 sudog + gopark + 喚醒，微秒級 |

`selectgo` 的固定成本跟 case 數量成正比（要建立 N 個 sudog、鎖 N 個 channel）。case 很多時考慮改架構——例如把多個 channel 合併成一個帶標籤的 channel。

!!! tip "`reflect.Select` 只在萬不得已時用"
    如果 case 數量在執行期才確定，只能用 `reflect.Select`。它比一般 `select` 慢一到兩個數量級，而且沒有編譯期檢查。

    絕大多數情況下，改用「每個 channel 一個 goroutine，全部寫進同一個出口 channel」的 fan-in 模式更好。

---

下一節談 `defer`。它是 Go 錯誤處理與資源清理的核心，實作上有三種完全不同的路徑。
