---
title: 堆疊記憶體管理
slug: stack
part: p6
number: "6.3"
order: 630
summary: goroutine 堆疊如何從 2 KB 開始自動成長、morestack 的機制、堆疊複製與指標調整，以及堆疊溢位的成因。
updated: "1.26"
---

## 為什麼 goroutine 可以有百萬個

答案是**小而可變的堆疊**。

一條 OS 執行緒的堆疊通常是固定的 1–8 MB（在 Linux 上預設 8 MB 虛擬位址空間）。就算實際只用到幾 KB，位址空間還是被預留了。一萬條執行緒 = 80 GB 虛擬位址空間。

goroutine 的堆疊從 **2 KB** 開始，需要時自動長大，用不到時自動縮小。一百萬個 goroutine 的初始堆疊總共只要 2 GB，而且大部分永遠不會長大。

```go
package main

import (
	"fmt"
	"runtime"
	"sync"
)

func main() {
	var m runtime.MemStats
	var wg sync.WaitGroup

	runtime.ReadMemStats(&m)
	fmt.Printf("啟動時堆疊總量: %d KB\n", m.StackSys/1024)

	const n = 100000
	block := make(chan struct{})
	for range n {
		wg.Add(1)
		go func() { defer wg.Done(); <-block }()
	}

	runtime.ReadMemStats(&m)
	fmt.Printf("%d 個 goroutine 後: %d KB（平均每個 %.1f KB）\n",
		n, m.StackSys/1024, float64(m.StackSys)/n/1024)

	close(block)
	wg.Wait()
}
```

```text
啟動時堆疊總量: 288 KB
100000 個 goroutine 後: 331776 KB（平均每個 3.3 KB）
```

十萬個 goroutine 只用了約 320 MB 堆疊。換成執行緒需要 800 GB。

---

## 堆疊成長的機制

<figure class="diagram"><svg viewBox="0 0 700 380" role="img" aria-label="堆疊成長的完整流程"><rect class="d-box-a" x="15" y="14" width="670" height="58" rx="6"/><text class="d-t-b" x="30" y="36">① 函式序言的檢查（編譯器自動插入）</text><text class="d-t-m" x="30" y="58">MOVQ (R14), CX　·　CMPQ SP, 16(CX)　·　JLS morestack　　← R14 固定存著目前的 g</text><path class="d-line" d="M350 72 L350 88" marker-end="url(#ar22)"/><rect class="d-box-w" x="15" y="90" width="670" height="58" rx="6"/><text class="d-t-b" x="30" y="112">② SP 低於 stackguard0 → 呼叫 runtime.morestack</text><text class="d-t-s" x="30" y="132">切換到 g0 系統堆疊執行（因為現在這個 g 的堆疊不夠用了，不能在上面做事）</text><path class="d-line" d="M350 148 L350 164" marker-end="url(#ar22)"/><rect class="d-box-w" x="15" y="166" width="670" height="58" rx="6"/><text class="d-t-b" x="30" y="188">③ newstack：配置一塊「兩倍大」的新堆疊</text><text class="d-t-s" x="30" y="208">2 KB → 4 KB → 8 KB → 16 KB …　從 stackpool（小堆疊快取）或 mheap 取得</text><path class="d-line" d="M350 224 L350 240" marker-end="url(#ar22)"/><rect class="d-box-d" x="15" y="242" width="670" height="74" rx="6"/><text class="d-t-b" x="30" y="264">④ copystack：把舊堆疊整份複製過去，並「調整所有指向堆疊的指標」</text><text class="d-t-s" x="30" y="286">堆疊搬到新位址了，任何指向舊堆疊的指標都會失效 → 必須逐一修正</text><text class="d-t-s" x="30" y="306">靠編譯器產生的 stack map（funcdata/pcdata）精確知道哪些位置是指標 → 每個都加上位移量</text><path class="d-line" d="M350 316 L350 332" marker-end="url(#ar22)"/><rect class="d-box-o" x="15" y="334" width="670" height="40" rx="6"/><text class="d-t-b" x="30" y="358">⑤ 釋放舊堆疊，跳回原本的函式重新執行序言 —— 這次檢查會通過</text><defs><marker id="ar22" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M0 0 L10 5 L0 10 z" style="fill:var(--line-strong)"/></marker></defs></svg><figcaption><b>連續堆疊（contiguous stack）。</b>Go 1.4 起採用這個方案：堆疊永遠是一塊連續記憶體，成長時整份搬家。第 ④ 步的指標調整是關鍵，也是 Go 的 GC 必須是「精確式」的原因之一——不知道哪些是指標就沒辦法搬。</figcaption></figure>

!!! version "Go 1.3 之前：分段堆疊與「熱分裂」問題"
    早期的 Go 用**分段堆疊（segmented stack）**：空間不夠時另外配置一塊，用鏈結串起來。優點是不用複製。

    但它有個致命問題叫 **hot split（熱分裂）**：如果一個迴圈剛好跨在堆疊邊界上，每次迭代都要配置新段、回傳時又釋放——效能斷崖式下降，而且難以預測。

    ```go
    for i := 0; i < 1000000; i++ {
        someFunc() // 剛好觸發分段 → 每次迭代都配置+釋放一個堆疊段
    }
    ```

    Go 1.3–1.4 改成連續堆疊：成長時整份複製到兩倍大的新空間。複製有成本，但因為是**倍增**，總複製成本是攤提 O(1)，而且不會有熱分裂。

### 堆疊縮小

GC 時會檢查每個 goroutine 的堆疊使用量。**如果實際使用不到目前容量的 1/4，就縮小成一半。**

這讓「偶爾需要深遞迴」的 goroutine 不會永久佔著大堆疊。

### 大小限制

| 平台 | 預設最大堆疊 |
| --- | --- |
| 64 位元 | **1 GB** |
| 32 位元 | 250 MB |

超過就是 `fatal error: stack overflow`，而且**無法 recover**。

```go
import "runtime/debug"

debug.SetMaxStack(2 << 30) // 調成 2 GB
```

調大它通常是治標。真正的問題幾乎都是無窮遞迴。

---

## 堆疊溢位

```go
package main

import "fmt"

type Node struct {
	Val      int
	Children []*Node
}

// ✗ 有環的圖會無窮遞迴
func sum(n *Node) int {
	total := n.Val
	for _, c := range n.Children {
		total += sum(c)
	}
	return total
}

func main() {
	a := &Node{Val: 1}
	b := &Node{Val: 2}
	a.Children = []*Node{b}
	b.Children = []*Node{a} // 環！

	fmt.Println(sum(a))
}
```

```text
runtime: goroutine stack exceeds 1000000000-byte limit
runtime: sp=0xc0200e0398 stack=[0xc0200e0000, 0xc0400e0000]
fatal error: stack overflow

goroutine 1 [running]:
main.sum(...)
	/tmp/main.go:13
main.sum(...)
	/tmp/main.go:13
...（重複數萬次）
```

!!! danger "堆疊溢位是 fatal error，recover 攔不住"
    ```go
    defer func() {
        recover() // ✗ 完全沒用
    }()
    infiniteRecursion()
    ```

    原因很直接：`recover` 需要執行 `defer` 函式，而執行函式需要堆疊空間——但堆疊已經滿了。runtime 只能直接終止。

    這表示**任何遞迴深度取決於外部輸入的程式碼都是潛在的 DoS 漏洞**。解析使用者提供的 JSON、XML、正規表示式時要特別小心。

### 三種對策

**① 加深度限制**

```go
package main

import (
	"errors"
	"fmt"
)

const maxDepth = 1000

type Node struct {
	Val      int
	Children []*Node
}

var ErrTooDeep = errors.New("結構過深")

func sum(n *Node, depth int) (int, error) {
	if depth > maxDepth {
		return 0, ErrTooDeep
	}
	total := n.Val
	for _, c := range n.Children {
		s, err := sum(c, depth+1)
		if err != nil {
			return 0, err
		}
		total += s
	}
	return total, nil
}

func main() {
	a := &Node{Val: 1}
	b := &Node{Val: 2}
	a.Children = []*Node{b}
	b.Children = []*Node{a}

	_, err := sum(a, 0)
	fmt.Println(err) // 結構過深
}
```

**② 記錄已走訪過的節點**

```go
func sum(n *Node, seen map[*Node]bool) int {
	if seen[n] {
		return 0 // 已經走過，不再遞迴
	}
	seen[n] = true

	total := n.Val
	for _, c := range n.Children {
		total += sum(c, seen)
	}
	return total
}
```

**③ 改成迭代 + 明確的堆疊**

```go
package main

import "fmt"

type Node struct {
	Val      int
	Children []*Node
}

func sumIterative(root *Node) int {
	total := 0
	stack := []*Node{root}
	seen := map[*Node]bool{}

	for len(stack) > 0 {
		n := stack[len(stack)-1]
		stack = stack[:len(stack)-1]

		if seen[n] {
			continue
		}
		seen[n] = true

		total += n.Val
		stack = append(stack, n.Children...)
	}
	return total
}

func main() {
	a := &Node{Val: 1}
	b := &Node{Val: 2}
	a.Children = []*Node{b}
	b.Children = []*Node{a}

	fmt.Println(sumIterative(a)) // 3
}
```

**方法 ③ 最穩健**——堆疊在堆積上，可以長到記憶體上限，而且深度可以明確檢查。處理不受信任的輸入時，這是唯一安全的選擇。

---

## `//go:nosplit`

有些函式不能有堆疊成長檢查——例如**堆疊成長本身的實作**（否則會無窮遞迴）。這些函式標記 `//go:nosplit`：

```go
//go:nosplit
func getg() *g {
	// ...
}
```

編譯器不會為它插入序言檢查，但要求它（連同它呼叫的所有 nosplit 函式）的堆疊用量不超過 **red zone**（`amd64` 上約 800 位元組）。超過會編譯失敗：

```text
nosplit stack over 792 byte limit
```

**應用程式碼幾乎永遠不需要這個指示詞。** 它是 runtime 內部用的。

---

## 堆疊 vs 堆積：實務影響

| | 堆疊（stack） | 堆積（heap） |
| --- | --- | --- |
| 配置成本 | 移動 SP，幾乎免費 | 走 `mallocgc`，數十奈秒 |
| 釋放成本 | 函式回傳自動釋放 | 靠 GC，成本攤提但不可預測 |
| 誰決定 | 編譯器的[逃逸分析](escape-inline.html) | 同左 |
| GC 影響 | 需要被掃描（找根物件），但不需要回收 | 完整的標記與清除 |
| 快取友善度 | 極高（連續、熱） | 較低（分散） |
| 大小限制 | 1 GB（64 位元） | 可用記憶體 |

**優化的方向永遠是：把配置從堆積移到堆疊。** 具體技巧見 [逃逸分析、內聯與 PGO](escape-inline.html)。

### 一個容易忽略的成本

大量 goroutine 會讓 **GC 的根掃描變慢**——每個 goroutine 的堆疊都要被掃描。

```bash
GODEBUG=gctrace=1 ./app
```

```text
gc 5 @1.2s 2%: 0.03+8.1+0.01 ms clock, ..., 82->84->41 MB, 84 MB goal, 156 MB stacks, ...
```

注意 `156 MB stacks` 那一項。如果這個數字很大（幾百 MB 以上），代表：

1. goroutine 數量過多（可能有洩漏）
2. 有些 goroutine 的堆疊長得很大（深遞迴）

兩者都會拉長 GC 的標記時間。

---

## 觀察堆疊

### 目前 goroutine 的堆疊追蹤

```go
package main

import (
	"fmt"
	"runtime/debug"
)

func level3() { fmt.Println(string(debug.Stack())) }
func level2() { level3() }
func level1() { level2() }

func main() { level1() }
```

```text
goroutine 1 [running]:
runtime/debug.Stack()
	.../runtime/debug/stack.go:26 +0x5e
main.level3(...)
	/tmp/main.go:8
main.level2(...)
	/tmp/main.go:9
main.level1(...)
	/tmp/main.go:10
main.main()
	/tmp/main.go:12 +0x13
```

### 所有 goroutine 的堆疊

```go
buf := make([]byte, 1<<20)
n := runtime.Stack(buf, true) // true = 所有 goroutine
fmt.Println(string(buf[:n]))
```

或用 pprof：

```bash
curl "http://localhost:6060/debug/pprof/goroutine?debug=2"
```

### 統計堆疊用量

```go
package main

import (
	"fmt"
	"runtime"
)

func report(label string) {
	var m runtime.MemStats
	runtime.ReadMemStats(&m)
	fmt.Printf("%-10s StackInuse=%5d KB  StackSys=%5d KB  goroutines=%d\n",
		label, m.StackInuse/1024, m.StackSys/1024, runtime.NumGoroutine())
}

func deep(n int) {
	var pad [1024]byte // 每層佔 1 KB
	_ = pad
	if n > 0 {
		deep(n - 1)
	}
}

func main() {
	report("啟動")

	done := make(chan struct{})
	go func() {
		deep(2000) // 遞迴 2000 層 → 堆疊長到約 2 MB
		report("深遞迴中")
		close(done)
	}()
	<-done

	runtime.GC()
	report("GC 後")
}
```

- **`StackInuse`** —— 目前正在使用的堆疊空間
- **`StackSys`** —— 向作業系統取得的堆疊記憶體總量

---

## 實務建議

### ① 遞迴深度要有上界

處理外部輸入（JSON、XML、使用者上傳的資料結構）時，一定要限制遞迴深度或改用迭代。這是安全問題，不只是穩定性問題。

### ② 大型區域變數會逃逸

```go
func f() {
	var buf [10 << 20]byte // 10 MB，超過 maxStackVarSize → 移到堆積
	_ = buf
}
```

超過約 10 MB 的區域變數（或 64 KB 的隱式配置）會被移到堆積。想在堆疊上放大 buffer 是行不通的。

### ③ 控制 goroutine 數量

```go
// ✗ 每個請求無限制地開 goroutine
for _, item := range millionItems {
	go process(item)
}

// ✓ 用工作池限制
sem := make(chan struct{}, runtime.GOMAXPROCS(0)*4)
var wg sync.WaitGroup
for _, item := range millionItems {
	wg.Add(1)
	sem <- struct{}{}
	go func(it Item) {
		defer wg.Done()
		defer func() { <-sem }()
		process(it)
	}(item)
}
wg.Wait()
```

除了記憶體，goroutine 數量也直接影響 GC 的根掃描時間。

### ④ 深呼叫鏈的隱藏成本

```go
// 這條鏈每一層都要做堆疊檢查，而且可能觸發成長
handler → middleware1 → middleware2 → ... → middleware10 → service → repo → db
```

一般情況下這不是問題（檢查只有三道指令）。但如果是每秒數十萬次呼叫的熱路徑，減少抽象層次確實有可量測的收益。**先量測再優化。**

---

Part 6 結束。你現在知道記憶體從哪來（配置器）、怎麼回收（GC）、以及堆疊如何動態調整。

最後的 Part 7 回到應用層：標準函式庫的幾個重要套件是怎麼設計的，以及 cgo 與程式碼生成。
