---
title: 記憶體配置器
slug: allocator
part: p6
number: "6.1"
order: 610
summary: mcache / mcentral / mheap 三層結構、size class 分級、tiny allocator，以及 sync.Pool 的正確用法。
updated: "1.26"
---

## 為什麼不直接用 malloc

Go 自己實作了一套記憶體配置器（設計上源自 TCMalloc），而不是呼叫 C 的 `malloc`。理由有三個：

1. **鎖競爭。** 通用的 `malloc` 需要全域鎖或複雜的執行緒快取。Go 有現成的 P 抽象，可以把快取掛在 P 上做到**完全無鎖**。
2. **GC 需要中繼資料。** 垃圾回收器必須知道「這塊記憶體裡哪些位置是指標」。這些資訊要跟配置器整合。
3. **cgo 的成本。** 每次呼叫 C 函式都要切換到系統堆疊，在熱路徑上不可接受。

---

## 三層結構

<figure class="diagram"><svg viewBox="0 0 700 420" role="img" aria-label="Go 記憶體配置器的三層結構"><rect class="d-box-o" x="15" y="14" width="670" height="92" rx="7"/><text class="d-t-b" x="30" y="36">① mcache —— 每個 P 一份，完全無鎖</text><text class="d-t-s" x="30" y="56">內含約 68×2 個 mspan（每個 size class 有「含指標」與「不含指標」兩種）</text><text class="d-t-s" x="30" y="74">配置時：算出 size class → 取對應的 mspan → 從中拿一個空閒物件。沒有任何原子操作，就是幾道指令。</text><text class="d-t-a" x="30" y="96">→ 絕大多數配置在這一層完成。這是 Go 配置速度快的主因。</text><path class="d-line" d="M350 106 L350 124" marker-end="url(#ar21)"/><text class="d-t-s" x="360" y="120">mcache 的那個 span 用完了 ↓</text><rect class="d-box-w" x="15" y="126" width="670" height="84" rx="7"/><text class="d-t-b" x="30" y="148">② mcentral —— 每個 size class 一個，全域共用（有鎖）</text><text class="d-t-s" x="30" y="168">維護兩個 span 列表：partial（還有空位）與 full（滿了）</text><text class="d-t-s" x="30" y="186">mcache 來要 span 時，先從 partial 拿；沒有就去 mheap 要新的</text><text class="d-t-s" x="30" y="204">鎖的粒度是「每個 size class 一把」，所以不同大小的配置不會互相競爭</text><path class="d-line" d="M350 210 L350 228" marker-end="url(#ar21)"/><text class="d-t-s" x="360" y="224">mcentral 也沒 span 了 ↓</text><rect class="d-box-a" x="15" y="230" width="670" height="92" rx="7"/><text class="d-t-b" x="30" y="252">③ mheap —— 全域唯一，管理所有記憶體</text><text class="d-t-s" x="30" y="272">以 8 KB 的 page 為單位管理；用基數樹（radix tree）追蹤哪些頁是空閒的</text><text class="d-t-s" x="30" y="290">切出對應大小的 mspan 給 mcentral</text><text class="d-t-s" x="30" y="308">記憶體不夠 → 向作業系統要（Linux: mmap，Windows: VirtualAlloc），一次至少 4 MB（一個 heap arena 是 64 MB）</text><rect class="d-box" x="15" y="342" width="670" height="62" rx="7"/><text class="d-t-b" x="30" y="364">大物件的捷徑</text><text class="d-t-s" x="30" y="384">超過 32 KB 的物件跳過 mcache 與 mcentral，直接向 mheap 要連續的頁 —— 這種配置叫 large object，</text><text class="d-t-s" x="30" y="400">每次都要拿 mheap 的鎖。所以「盡量避免大物件配置」在熱路徑上是有意義的建議。</text><defs><marker id="ar21" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M0 0 L10 5 L0 10 z" style="fill:var(--line-strong)"/></marker></defs></svg><figcaption><b>快取分層。</b>跟 CPU 的 L1/L2/L3 快取是同樣的思路：把最頻繁的操作放在最近、最快、無競爭的一層。<code>mcache</code> 掛在 P 上，而 P 的數量等於 <code>GOMAXPROCS</code>，所以既無鎖又不會浪費太多記憶體。</figcaption></figure>

---

## size class

Go 不會「你要幾個位元組就給幾個位元組」，而是把配置歸類到約 **68 個預定義的大小等級**。

節錄前幾級與後幾級：

| class | 物件大小 | 每個 span 的頁數 | 每個 span 可放幾個 | 浪費率 |
| --- | --- | --- | --- | --- |
| 1 | 8 B | 1 | 1024 | 0% |
| 2 | 16 B | 1 | 512 | 0% |
| 3 | 24 B | 1 | 341 | 0.92% |
| 4 | 32 B | 1 | 256 | 0% |
| 5 | 48 B | 1 | 170 | 0.78% |
| … | … | … | … | … |
| 40 | 3072 B | 3 | 8 | 0.25% |
| … | … | … | … | … |
| 67 | 32768 B | 4 | 1 | 0% |

**為什麼要分級？**

- **消除外部碎片。** 同一個 span 裡所有物件大小相同，釋放後的空位一定能被下一次同大小的配置用上。
- **配置變成 O(1)。** 不需要搜尋合適的空閒區塊，直接從 span 的空閒位元圖找下一個 0。
- **釋放也是 O(1)。** 從物件位址就能算出它屬於哪個 span、第幾個位置。

**代價是內部碎片。** 你要 33 位元組，實際拿到 48 位元組（class 5），浪費 15 位元組。Go 的 size class 表經過調校，最大浪費率控制在約 12.5% 以內。

### 觀察 size class 的影響

```go
package main

import (
	"fmt"
	"runtime"
)

func allocSize(n int) uint64 {
	var before, after runtime.MemStats
	runtime.GC()
	runtime.ReadMemStats(&before)

	const iterations = 100000
	sink := make([][]byte, iterations)
	for i := range sink {
		sink[i] = make([]byte, n)
	}

	runtime.ReadMemStats(&after)
	runtime.KeepAlive(sink)
	return (after.TotalAlloc - before.TotalAlloc) / iterations
}

func main() {
	for _, n := range []int{1, 8, 9, 16, 17, 32, 33, 48, 49, 64, 100, 128, 129} {
		fmt.Printf("要求 %3d 位元組 → 實際約 %3d 位元組\n", n, allocSize(n))
	}
}
```

輸出會顯示明顯的階梯：要求 9、要求 16 都拿到 16；要求 17 到 24 都拿到 24。

**實務意義**：設計熱路徑上的 struct 時，讓它剛好落在 size class 邊界內可以避免浪費。例如一個 40 位元組的 struct 會拿到 48 位元組的空間，加一個 8 位元組欄位「免費」。

```go
package main

import (
	"fmt"
	"unsafe"
)

// 欄位順序影響大小（對齊填充）
type Bad struct {
	a bool   // 1 + 7 padding
	b int64  // 8
	c bool   // 1 + 7 padding
} // 24 位元組

type Good struct {
	b int64 // 8
	a bool  // 1
	c bool  // 1 + 6 padding
} // 16 位元組

func main() {
	fmt.Println(unsafe.Sizeof(Bad{}))  // 24
	fmt.Println(unsafe.Sizeof(Good{})) // 16
}
```

**把大欄位放前面、小欄位放後面**，可以減少對齊填充。工具 `fieldalignment`（`golang.org/x/tools/go/analysis/passes/fieldalignment`）能自動檢查：

```bash
go run golang.org/x/tools/go/analysis/passes/fieldalignment/cmd/fieldalignment@latest ./...
```

!!! warning "不要為了這個犧牲可讀性"
    欄位重排的收益只在「這個 struct 會被配置數百萬次」時才顯著。一般的設定 struct、請求 struct 不值得為此打亂邏輯分組。

---

## tiny allocator

**小於 16 位元組且不含指標**的物件走一條特別的路：它們會被**合併**到同一個 16 位元組的區塊裡。

```go
package main

import (
	"fmt"
	"runtime"
)

func main() {
	var before, after runtime.MemStats

	runtime.GC()
	runtime.ReadMemStats(&before)

	const n = 100000
	sink := make([]*byte, n)
	for i := range sink {
		b := byte(i)
		sink[i] = &b // 1 位元組、無指標 → tiny allocator
	}

	runtime.ReadMemStats(&after)
	runtime.KeepAlive(sink)

	fmt.Printf("100000 個 1 位元組物件，總配置 %d 位元組\n", after.TotalAlloc-before.TotalAlloc)
	fmt.Printf("平均每個 %.1f 位元組\n", float64(after.TotalAlloc-before.TotalAlloc)/n)
}
```

如果沒有 tiny allocator，每個 1 位元組物件都會佔用一個完整的 8 位元組 class-1 slot。有了它，最多 16 個小物件可以擠在同一個 16 位元組區塊裡。

**為什麼要求「不含指標」？** 因為 GC 需要能精確判斷「這個區塊還有沒有人參照」。如果混雜了指標，只要其中一個小物件還活著，整塊都不能釋放——含指標時風險太高。

常見受益者：`bool`、`byte`、小型整數的裝箱、`struct{}{}`。

---

## 配置的完整流程

`runtime.mallocgc(size, typ, needzero)` 的決策樹：

```text
mallocgc(size, typ, needzero):

    if size <= 32 KB:                       // 小物件
        if size < 16 B && typ 不含指標:
            → tiny allocator：試著擠進目前的 tiny 區塊
        else:
            sizeclass = size_to_class[size]
            span = mcache.alloc[sizeclass]
            if span 有空位:
                → 直接取用（無鎖，最快路徑）
            else:
                → 向 mcentral 要一個新的 span（有鎖）
                   → mcentral 沒有 → 向 mheap 要（有鎖）
                      → mheap 沒有 → 向 OS 要（mmap / VirtualAlloc）
    else:                                   // 大物件
        → 直接向 mheap 要連續的頁

    if 需要觸發 GC（配置量達到門檻）:
        → gcStart() 或協助標記（mutator assist）

    return 指標
```

注意最後那個 **GC 協助（mutator assist）**：配置得越快，你的 goroutine 就要幫忙做越多標記工作。這是 GC 的節流機制，見下一節。

---

## 觀察配置行為

### 基準測試的 `-benchmem`

```go
package main

import (
	"fmt"
	"strings"
	"testing"
)

func concat(parts []string) string {
	var s string
	for _, p := range parts {
		s += p
	}
	return s
}

func build(parts []string) string {
	var sb strings.Builder
	for _, p := range parts {
		sb.WriteString(p)
	}
	return sb.String()
}

func main() {
	parts := make([]string, 100)
	for i := range parts {
		parts[i] = "xxxxxxxx"
	}

	for name, fn := range map[string]func([]string) string{"concat": concat, "build": build} {
		r := testing.Benchmark(func(b *testing.B) {
			b.ReportAllocs()
			for i := 0; i < b.N; i++ {
				_ = fn(parts)
			}
		})
		fmt.Printf("%-8s %s  %s\n", name, r, r.MemString())
	}
}
```

**`allocs/op` 比 `ns/op` 更值得盯。** 配置次數直接決定 GC 壓力，而 GC 壓力會以難以預測的方式影響整體延遲。把 `allocs/op` 降下來，通常 `ns/op` 也會跟著降。

### 堆積剖析

```bash
go test -bench=. -memprofile=mem.out
go tool pprof -http=:8081 mem.out
```

兩種檢視：

| 檢視 | 指令 | 回答什麼 |
| --- | --- | --- |
| `inuse_space` | 預設 | 現在還活著的物件由誰配置（找洩漏） |
| `alloc_space` | `-sample_index=alloc_space` | 從啟動到現在總共配置了多少（找 GC 壓力來源） |
| `alloc_objects` | `-sample_index=alloc_objects` | 配置**次數**（找頻繁的小配置） |

**找 GC 壓力用 `alloc_space` 或 `alloc_objects`，找記憶體洩漏用 `inuse_space`。** 這兩者常常指向完全不同的地方。

### 執行期統計

```go
package main

import (
	"fmt"
	"runtime"
)

func printStats() {
	var m runtime.MemStats
	runtime.ReadMemStats(&m)

	fmt.Printf("HeapAlloc    %6d KB  (存活物件)\n", m.HeapAlloc/1024)
	fmt.Printf("HeapSys      %6d KB  (向 OS 取得的總量)\n", m.HeapSys/1024)
	fmt.Printf("HeapIdle     %6d KB  (取得但閒置)\n", m.HeapIdle/1024)
	fmt.Printf("HeapReleased %6d KB  (已歸還 OS)\n", m.HeapReleased/1024)
	fmt.Printf("HeapObjects  %6d     (物件數量)\n", m.HeapObjects)
	fmt.Printf("Mallocs      %6d     (累計配置次數)\n", m.Mallocs)
	fmt.Printf("Frees        %6d     (累計釋放次數)\n", m.Frees)
	fmt.Printf("NumGC        %6d     (GC 次數)\n", m.NumGC)
	fmt.Printf("GCCPUFraction  %.4f  (GC 佔用的 CPU 比例)\n", m.GCCPUFraction)
}

func main() { printStats() }
```

!!! warning "`ReadMemStats` 會 STW"
    它需要停止整個世界來取得一致的快照。**不要在高頻迴圈裡呼叫。** 每幾秒一次做監控是可以的。

    Go 1.16 起有更輕量的 `runtime/metrics` 套件，提供不需要 STW 的取樣式指標，適合高頻收集：

    ```go
    import "runtime/metrics"

    samples := []metrics.Sample{
        {Name: "/gc/heap/allocs:bytes"},
        {Name: "/sched/goroutines:goroutines"},
    }
    metrics.Read(samples)
    ```

---

## `sync.Pool`

物件重用池，用來降低高頻配置的 GC 壓力。

```go
package main

import (
	"bytes"
	"fmt"
	"sync"
)

var bufPool = sync.Pool{
	New: func() any {
		return new(bytes.Buffer) // 池空時呼叫
	},
}

func render(name string) string {
	buf := bufPool.Get().(*bytes.Buffer)
	defer func() {
		buf.Reset()        // ✓ 一定要重置狀態
		bufPool.Put(buf)
	}()

	buf.WriteString("Hello, ")
	buf.WriteString(name)
	buf.WriteString("!")
	return buf.String()
}

func main() {
	var wg sync.WaitGroup
	for i := range 100 {
		wg.Add(1)
		go func() {
			defer wg.Done()
			_ = render(fmt.Sprint(i))
		}()
	}
	wg.Wait()
	fmt.Println(render("world"))
}
```

### 內部結構

`sync.Pool` 為每個 P 維護一個私有槽加一個共享佇列：

```text
每個 P:
    private  一個物件，只有這個 P 能存取（無鎖、最快）
    shared   雙端佇列
             ├─ 本 P 從「頭部」推入與取出（LIFO，快取熱）
             └─ 其他 P 從「尾部」竊取（避免衝突）
```

`Get()` 的順序：私有槽 → 本 P 的 shared 頭部 → 竊取其他 P 的 shared 尾部 → victim cache → 呼叫 `New`。

### GC 會清空 Pool

**每次 GC 都會清理 `sync.Pool`。** 這是刻意的——否則 Pool 會變成記憶體洩漏。

Go 1.13 起加入 **victim cache** 機制：GC 時不直接丟棄，而是先把目前的池搬到「受害者區」，下一次 GC 才真的釋放。這讓物件至少能撐過一個 GC 週期，避免每次 GC 後的效能斷崖。

### 什麼時候該用

**✓ 適合：**

- 高頻建立與丟棄**同一種**大型物件（buffer、序列化器、加解密上下文）
- 物件的初始化成本高
- 已經量測確認配置是瓶頸

**✗ 不適合：**

- 小物件（配置本身就很便宜，Pool 的簿記成本可能更高）
- 生命週期長的物件（Pool 是為短命物件設計的）
- 需要保證物件一定被重用（Pool 隨時可能清空）
- 大小差異很大的物件（會導致記憶體浪費，見下方警告）

!!! danger "Pool 裡的物件大小要一致"
    ```go
    // ✗ 危險：偶爾一個超大 buffer 會永遠佔著記憶體
    buf := bufPool.Get().(*bytes.Buffer)
    buf.Write(hugePayload) // 長到 100 MB
    buf.Reset()            // Reset 只把 len 設 0，cap 還是 100 MB
    bufPool.Put(buf)       // 這個 100 MB 的 buffer 進了池子

    // ✓ 加上大小上限
    const maxBufSize = 64 << 10
    if buf.Cap() <= maxBufSize {
        buf.Reset()
        bufPool.Put(buf)
    }
    // 太大的就讓 GC 收走
    ```

    這是 `sync.Pool` 最常見的誤用。標準庫的 `fmt` 套件內部就有這個上限檢查。

!!! danger "務必重置狀態"
    ```go
    // ✗ 上一次的資料留在裡面 → 資料外洩！
    buf := bufPool.Get().(*bytes.Buffer)
    // 忘記 buf.Reset()
    buf.WriteString(newData)  // 舊資料還在前面
    ```

    在處理不同使用者請求的服務裡，這種 bug 可能造成**跨使用者的資料外洩**，是嚴重的安全問題。

    保險做法是包一層：

    ```go
    func getBuf() *bytes.Buffer {
        b := bufPool.Get().(*bytes.Buffer)
        b.Reset()  // Get 的時候就重置，不依賴 Put 的人記得
        return b
    }
    ```

### 泛型版本

```go
package main

import (
	"fmt"
	"sync"
)

type Pool[T any] struct {
	p     sync.Pool
	reset func(*T)
}

func NewPool[T any](newFn func() *T, reset func(*T)) *Pool[T] {
	return &Pool[T]{
		p:     sync.Pool{New: func() any { return newFn() }},
		reset: reset,
	}
}

func (p *Pool[T]) Get() *T {
	v := p.p.Get().(*T)
	if p.reset != nil {
		p.reset(v)
	}
	return v
}

func (p *Pool[T]) Put(v *T) { p.p.Put(v) }

type Request struct {
	ID   string
	Body []byte
}

func main() {
	pool := NewPool(
		func() *Request { return &Request{Body: make([]byte, 0, 1024)} },
		func(r *Request) { r.ID = ""; r.Body = r.Body[:0] },
	)

	r := pool.Get()
	r.ID = "req-1"
	r.Body = append(r.Body, "hello"...)
	fmt.Println(r.ID, string(r.Body))
	pool.Put(r)

	r2 := pool.Get()
	fmt.Printf("重用後: ID=%q len=%d\n", r2.ID, len(r2.Body)) // ID="" len=0
}
```

型別安全，而且把 `Reset` 的責任移到 `Get` 這一側，不會忘記。

---

## 減少配置的實用清單

| 技巧 | 例子 |
| --- | --- |
| 預先配置容量 | `make([]T, 0, n)`、`make(map[K]V, n)` |
| 重用 buffer | `sync.Pool`、呼叫端提供的 `dst []byte` |
| 用 `strings.Builder` 取代 `+=` | 見 [字串](string.html#字串拼接的成本) |
| 傳指標而非大值 | 大於幾個字組的 struct |
| 避免不必要的介面裝箱 | 熱路徑上少用 `any`、少呼叫 `fmt` |
| 用值型別的 slice 而非指標 slice | `[]Item` 比 `[]*Item` 少 N 次配置且快取友善 |
| `append` 系列取代 `Sprintf` | `strconv.AppendInt(dst, n, 10)` |
| 用陣列取代小 slice | 見 [陣列](array.html#一個實用範例無配置的小型集合) |
| 讓變數不逃逸 | 見 [逃逸分析](escape-inline.html) |

---

下一節談垃圾回收器：這些配置出去的記憶體，最後是怎麼被找回來的。
