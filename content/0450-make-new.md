---
title: make 與 new
slug: make-new
part: p4
number: "4.5"
order: 450
summary: 兩個內建函式的分工、它們各自被改寫成哪個 runtime 呼叫、零值可用的設計原則，以及 Go 1.26 對 new 的擴充。
updated: "1.26"
---

## 一句話分清楚

| | `new(T)` | `make(T, ...)` |
| --- | --- | --- |
| 回傳 | `*T`（指標） | `T`（值本身） |
| 適用型別 | **任何型別** | 只有 slice、map、chan |
| 初始化 | 零值 | 建立可用的內部結構 |

```go
package main

import "fmt"

func main() {
	p := new(int)      // *int，指向一個值為 0 的 int
	fmt.Println(*p)    // 0

	s := make([]int, 3) // []int，長度 3
	fmt.Println(s)      // [0 0 0]

	// ✗ 編譯錯誤：cannot make int
	// make(int)

	// new 也能用在 slice，但幾乎沒用
	sp := new([]int)   // *[]int，指向一個 nil slice
	fmt.Println(*sp == nil) // true
}
```

**判斷準則：**「我需要的是**指標**，還是一個**可以直接用的容器**？」

---

## 為什麼 slice、map、chan 需要 `make`

因為這三個型別的**零值不可直接使用**（map 與 chan），或使用範圍有限（slice）。它們都需要 runtime 先建立內部結構。

```go
package main

import "fmt"

func main() {
	// slice 的零值是 nil，但可以 append 與 range
	var s []int
	s = append(s, 1) // ✓
	fmt.Println(s, len(s))

	// map 的零值是 nil，可以讀但不能寫
	var m map[string]int
	fmt.Println(m["missing"], len(m)) // ✓ 讀取回傳零值

	defer func() { fmt.Println("panic:", recover()) }()
	m["key"] = 1 // ✗ panic: assignment to entry in nil map
}
```

```text
[1] 1
0 0
panic: assignment to entry in nil map
```

```go
// chan 的零值是 nil，讀寫都會永久阻塞
var ch chan int
// <-ch    // 永久阻塞
// ch <- 1 // 永久阻塞
close(ch)  // panic: close of nil channel
```

### 對應的 runtime 呼叫

walk 階段的改寫（見 [中間表示與 SSA](ssa.html#walk語法糖的終點)）：

| 你寫的 | 改寫成 |
| --- | --- |
| `new(T)`（逃逸時） | `runtime.newobject(typ)` |
| `new(T)`（不逃逸） | 堆疊上配置，清零 |
| `make([]T, n, m)` | `runtime.makeslice(typ, n, m)` |
| `make(map[K]V, n)` | `runtime.makemap(typ, n, h)` |
| `make(chan T, n)` | `runtime.makechan(typ, n)` |
| `&T{...}`（逃逸時） | `runtime.newobject` + 初始化 |

注意 **`new(T)` 與 `&T{}` 是等價的**：

```go
p1 := new(Point)      // 指向零值 Point
p2 := &Point{}        // 完全一樣
p3 := &Point{X: 1}    // 指向已初始化的 Point
```

實務上 `&T{}` 更常用，因為它可以順便設欄位。`new` 主要用在**基本型別**：

```go
count := new(int)      // 需要一個 *int
flag := new(bool)
```

不過現在更常見的寫法是：

```go
count := 0
p := &count
```

或用泛型輔助函式：

```go
func Ptr[T any](v T) *T { return &v }

// 設定結構常見的 *bool 欄位
cfg := Config{Enabled: Ptr(true), Retries: Ptr(3)}
```

!!! version "Go 1.26：`new` 可以接運算式"
    Go 1.26 擴充了 `new`，讓它直接接受一個值：

    ```go
    p := new(int64(300))   // 等同 v := int64(300); p := &v
    q := new("hello")      // *string
    ```

    這消除了「宣告變數只為了取它的位址」這種樣板程式碼，在建構含大量選用欄位（`*bool`、`*int`）的設定結構時特別方便。上面那個 `Ptr[T]` 輔助函式現在可以不用寫了。

---

## `make` 的容量參數

### slice：長度與容量分開

```go
package main

import "fmt"

func main() {
	a := make([]int, 3)     // len=3 cap=3，內容 [0 0 0]
	b := make([]int, 0, 3)  // len=0 cap=3，內容 []
	c := make([]int, 2, 5)  // len=2 cap=5，內容 [0 0]

	fmt.Println(len(a), cap(a), a)
	fmt.Println(len(b), cap(b), b)
	fmt.Println(len(c), cap(c), c)
}
```

```text
3 3 [0 0 0]
0 3 []
2 5 [0 0]
```

!!! danger "最常見的 make + append 錯誤"
    ```go
    // ✗ 想建一個空 slice 然後 append
    s := make([]int, 10) // 建了 10 個 0！
    for i := 0; i < 10; i++ {
        s = append(s, i) // 變成 20 個元素
    }
    fmt.Println(s) // [0 0 0 0 0 0 0 0 0 0 0 1 2 3 4 5 6 7 8 9]

    // ✓ 正確：長度 0、容量 10
    s := make([]int, 0, 10)
    for i := 0; i < 10; i++ {
        s = append(s, i)
    }
    fmt.Println(s) // [0 1 2 3 4 5 6 7 8 9]
    ```

    第二個參數是**長度**不是容量。要預留容量得寫第三個參數。

### map：容量是提示

```go
m := make(map[string]int, 1000) // 預先配置能裝約 1000 個元素的空間
```

這是**提示**不是硬性上限，超過會自動擴張。給對數字可以避免多次分裂與搬移。

```go
package main

import (
	"fmt"
	"testing"
)

func main() {
	const n = 100000

	r1 := testing.Benchmark(func(b *testing.B) {
		b.ReportAllocs()
		for i := 0; i < b.N; i++ {
			m := make(map[int]int)
			for j := 0; j < n; j++ {
				m[j] = j
			}
		}
	})
	r2 := testing.Benchmark(func(b *testing.B) {
		b.ReportAllocs()
		for i := 0; i < b.N; i++ {
			m := make(map[int]int, n)
			for j := 0; j < n; j++ {
				m[j] = j
			}
		}
	})
	fmt.Println("無提示:", r1, r1.MemString())
	fmt.Println("有提示:", r2, r2.MemString())
}
```

有提示的版本通常快 30–50%，配置次數也少很多。

### channel：緩衝大小

```go
unbuffered := make(chan int)    // 無緩衝：傳送方會等到有人接收
buffered := make(chan int, 10)  // 緩衝 10：未滿時傳送不阻塞
```

這是**語意上的差別**，不只是效能：

- **無緩衝** channel 提供**同步保證**——傳送完成代表接收方已經拿到了。
- **有緩衝** channel 是**非同步佇列**——傳送完成只代表資料進了佇列。

詳見 [channel](channel.html)。

---

## 零值可用：Go 的核心設計原則

Go 鼓勵讓型別的**零值就是可用狀態**（zero value is useful）。這讓 `var x T` 直接就能用，不需要建構函式。

標準庫的典範：

```go
package main

import (
	"bytes"
	"fmt"
	"strings"
	"sync"
)

func main() {
	// ✓ sync.Mutex 零值可用
	var mu sync.Mutex
	mu.Lock()
	mu.Unlock()

	// ✓ sync.WaitGroup 零值可用
	var wg sync.WaitGroup
	wg.Add(1)
	go func() { defer wg.Done() }()
	wg.Wait()

	// ✓ bytes.Buffer 零值可用
	var buf bytes.Buffer
	buf.WriteString("hello")
	fmt.Println(buf.String())

	// ✓ strings.Builder 零值可用
	var sb strings.Builder
	sb.WriteString("world")
	fmt.Println(sb.String())

	// ✓ sync.Once、sync.Map、sync.Pool 都是零值可用
	var once sync.Once
	once.Do(func() { fmt.Println("只執行一次") })
}
```

### 自己的型別怎麼做到

```go
package main

import (
	"fmt"
	"sync"
)

// ✓ 零值就能用：內部 map 延後建立
type Registry struct {
	mu sync.RWMutex
	m  map[string]int // nil 也沒關係
}

func (r *Registry) Set(k string, v int) {
	r.mu.Lock()
	defer r.mu.Unlock()

	if r.m == nil {
		r.m = make(map[string]int) // 第一次寫入時才建立
	}
	r.m[k] = v
}

func (r *Registry) Get(k string) (int, bool) {
	r.mu.RLock()
	defer r.mu.RUnlock()
	v, ok := r.m[k] // 讀 nil map 是安全的
	return v, ok
}

func main() {
	var r Registry // 不需要 NewRegistry()
	r.Set("a", 1)
	fmt.Println(r.Get("a")) // 1 true
	fmt.Println(r.Get("b")) // 0 false
}
```

這種設計讓 `Registry` 可以直接內嵌在其他 struct 裡，不用擔心初始化順序。

### 什麼時候還是需要建構函式

零值可用不是萬能的。這些情況還是要寫 `NewXxx`：

1. **需要驗證參數。** `NewClient(addr string)` 應該檢查 addr 格式。
2. **需要建立無法延後的資源。** 例如開啟連線、啟動背景 goroutine。
3. **有必填的相依。** `NewService(db *sql.DB, logger *slog.Logger)`。
4. **零值有意義但不是你要的預設值。** 例如 timeout 的零值是 0（立刻逾時），你想要的預設是 30 秒。

```go
type Client struct {
	addr    string
	timeout time.Duration
	client  *http.Client
}

func NewClient(addr string, opts ...Option) (*Client, error) {
	if addr == "" {
		return nil, errors.New("addr 不可為空")
	}

	c := &Client{
		addr:    addr,
		timeout: 30 * time.Second, // 有意義的預設值
	}
	for _, o := range opts {
		o(c)
	}
	c.client = &http.Client{Timeout: c.timeout}
	return c, nil
}
```

---

## 記憶體是怎麼配置的

`new` 與 `make` 最終都會走到記憶體配置器：

<figure class="diagram"><svg viewBox="0 0 700 250" role="img" aria-label="new 與 make 到記憶體配置器的路徑"><rect class="d-box-a" x="15" y="14" width="150" height="40" rx="5"/><text class="d-t-m d-mid" x="90" y="39">new(T) / &amp;T{}</text><rect class="d-box-a" x="185" y="14" width="150" height="40" rx="5"/><text class="d-t-m d-mid" x="260" y="39">make([]T, n)</text><rect class="d-box-a" x="355" y="14" width="150" height="40" rx="5"/><text class="d-t-m d-mid" x="430" y="39">make(map[K]V)</text><rect class="d-box-a" x="525" y="14" width="160" height="40" rx="5"/><text class="d-t-m d-mid" x="605" y="39">make(chan T, n)</text><path class="d-line" d="M90 54 L200 84" marker-end="url(#ar15)"/><path class="d-line" d="M260 54 L280 84" marker-end="url(#ar15)"/><path class="d-line" d="M430 54 L400 84" marker-end="url(#ar15)"/><path class="d-line" d="M605 54 L490 84" marker-end="url(#ar15)"/><rect class="d-box-w" x="180" y="88" width="330" height="42" rx="5"/><text class="d-t-b d-mid" x="345" y="108">runtime.mallocgc(size, typ, needzero)</text><text class="d-t-s d-mid" x="345" y="124">所有堆積配置的唯一入口</text><path class="d-line-a" d="M345 130 L345 152" marker-end="url(#ar15a)"/><rect class="d-box-o" x="120" y="156" width="450" height="62" rx="5"/><text class="d-t-b d-mid" x="345" y="178">三層記憶體配置器</text><text class="d-t-s d-mid" x="345" y="198">mcache（每個 P 一份，無鎖）→ mcentral（依 size class）→ mheap（全域）</text><text class="d-t-s d-mid" x="345" y="212">小於 16 位元組且無指標 → tiny allocator 合併配置</text><defs><marker id="ar15" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M0 0 L10 5 L0 10 z" style="fill:var(--line-strong)"/></marker><marker id="ar15a" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M0 0 L10 5 L0 10 z" style="fill:var(--accent)"/></marker></defs><text class="d-t-s" x="15" y="240">如果逃逸分析判定不逃逸，這整條路徑都不會走 —— 直接在堆疊上配置。</text></svg><figcaption><b>統一的配置入口。</b>不管你寫 <code>new</code>、<code>make</code> 還是複合字面值，逃逸的配置最後都走 <code>mallocgc</code>。詳細機制見 <a href="allocator.html">記憶體配置器</a>。</figcaption></figure>

**最重要的一點：如果編譯器證明不逃逸，這些呼叫根本不會發生。**

```go
package main

func stackAlloc() int {
	p := new(int) // 不逃逸 → 就在堆疊上，不呼叫 newobject
	*p = 42
	return *p
}

func heapAlloc() *int {
	p := new(int) // 逃逸 → runtime.newobject
	*p = 42
	return p
}

func main() {
	println(stackAlloc())
	println(*heapAlloc())
}
```

```bash
go build -gcflags="-m" ./main.go
```

```text
./main.go:4:10: new(int) does not escape
./main.go:10:10: new(int) escapes to heap
```

---

Part 4 到此結束。你已經看過那些「看起來像語法糖、其實是 runtime 呼叫」的關鍵字。

Part 5 進入 Go 最有特色的部分：並行。從 `context` 開始，一路走到 GMP 排程器。
