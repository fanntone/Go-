---
title: for 與 range
slug: for-range
part: p4
number: "4.1"
order: 410
summary: range 被改寫成什麼、Go 1.22 迴圈變數的語意變更、range 陣列與 slice 的差異，以及 Go 1.23 的 range over func 迭代器。
updated: "1.26"
---

## Go 只有一個迴圈關鍵字

`for` 涵蓋了其他語言的 `while`、`do-while`、`foreach`：

```go
// ① 三段式
for i := 0; i < 10; i++ { }

// ② 只有條件（等同 while）
for x < 100 { x *= 2 }

// ③ 無限迴圈
for { }

// ④ range
for i, v := range s { }
```

前三種沒什麼好說的，直接對應到條件跳轉。有趣的是第四種——`range` 會被編譯器**改寫成前三種之一**。

---

## range slice 的改寫

```go
for i, v := range s {
	body(i, v)
}
```

被 walk 階段改寫成大致這樣：

```go
{
	s_ := s              // ① 複製 slice 標頭
	len_ := len(s_)      // ② 長度先算好，之後不再重算
	for i_ := 0; i_ < len_; i_++ {
		i := i_          // ③ Go 1.22 起：每輪建立新變數
		v := s_[i_]      //    v 是元素的複本
		body(i, v)
	}
}
```

三個改寫細節，各自對應一個常見的困惑。

### ① 長度只算一次

```go
package main

import "fmt"

func main() {
	s := []int{1, 2, 3}
	for i, v := range s {
		if i == 0 {
			s = append(s, 4, 5, 6) // 迴圈中修改 s
		}
		fmt.Print(v, " ")
	}
	fmt.Println("\n最終 s =", s)
}
```

```text
1 2 3 
最終 s = [1 2 3 4 5 6]
```

只跑三輪。因為 `len_` 在迴圈開始前就算好了，之後的 `append` 影響不到它。

**這是好事** —— 否則在迴圈中 append 會變成無窮迴圈。

### ② `v` 是複本

```go
package main

import "fmt"

type Item struct{ N int }

func main() {
	items := []Item{{1}, {2}, {3}}

	for _, it := range items {
		it.N *= 10 // 改的是複本，原本的 slice 不受影響
	}
	fmt.Println(items) // [{1} {2} {3}]

	// ✓ 正確做法：用索引
	for i := range items {
		items[i].N *= 10
	}
	fmt.Println(items) // [{10} {20} {30}]
}
```

這也意味著**大型元素的 range 會有複製成本**：

```go
type Big struct{ data [4096]byte }

var arr []Big
for _, b := range arr {  // 每輪複製 4 KB
	_ = b
}

for i := range arr {     // 不複製
	_ = &arr[i]
}
```

!!! version "Go 1.22：`for i := range n`"
    Go 1.22 開始，`range` 可以直接接一個整數：

    ```go
    for i := range 10 {
        fmt.Println(i) // 0 到 9
    }

    for range 3 {
        fmt.Println("重複三次")
    }
    ```

    比 `for i := 0; i < 10; i++` 簡潔，而且不會寫錯邊界。

### ③ 迴圈變數：Go 1.22 的重大變更

這是 Go 最著名的坑，也是 Go 1 相容性承諾下少見的**語意變更**。

```go
package main

import (
	"fmt"
	"sync"
)

func main() {
	var wg sync.WaitGroup
	for i := 0; i < 3; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			fmt.Print(i, " ")
		}()
	}
	wg.Wait()
	fmt.Println()
}
```

| 版本 | 輸出 |
| --- | --- |
| Go ≤ 1.21 | `3 3 3`（或其他非預期組合） |
| Go ≥ 1.22 | `0 1 2` 的某種排列 |

<figure class="diagram"><svg viewBox="0 0 700 260" role="img" aria-label="Go 1.21 與 1.22 的迴圈變數差異"><text class="d-t-b" x="15" y="20">Go ≤ 1.21：整個迴圈共用一個 i</text><rect class="d-box-d" x="15" y="32" width="120" height="44" rx="5"/><text class="d-t-m d-mid" x="75" y="52">i</text><text class="d-t-s d-mid" x="75" y="70">單一變數</text><rect class="d-box" x="220" y="30" width="140" height="24" rx="3"/><text class="d-t-s d-mid" x="290" y="47">goroutine 1</text><rect class="d-box" x="220" y="58" width="140" height="24" rx="3"/><text class="d-t-s d-mid" x="290" y="75">goroutine 2</text><rect class="d-box" x="220" y="86" width="140" height="24" rx="3"/><text class="d-t-s d-mid" x="290" y="103">goroutine 3</text><path class="d-line" d="M216 42 L139 50" marker-end="url(#ar12)"/><path class="d-line" d="M216 70 L139 56" marker-end="url(#ar12)"/><path class="d-line" d="M216 98 L139 62" marker-end="url(#ar12)"/><text class="d-t-s" x="390" y="60">三個 goroutine 都指向同一個 i。</text><text class="d-t-s" x="390" y="80">它們真正跑的時候，迴圈往往已經結束，i = 3。</text><line class="d-dash" x1="15" y1="130" x2="685" y2="130"/><text class="d-t-b" x="15" y="158">Go ≥ 1.22：每一輪迭代都是新的 i</text><rect class="d-box-o" x="15" y="170" width="120" height="24" rx="3"/><text class="d-t-m d-mid" x="75" y="187">i₀ = 0</text><rect class="d-box-o" x="15" y="198" width="120" height="24" rx="3"/><text class="d-t-m d-mid" x="75" y="215">i₁ = 1</text><rect class="d-box-o" x="15" y="226" width="120" height="24" rx="3"/><text class="d-t-m d-mid" x="75" y="243">i₂ = 2</text><rect class="d-box" x="220" y="170" width="140" height="24" rx="3"/><text class="d-t-s d-mid" x="290" y="187">goroutine 1</text><rect class="d-box" x="220" y="198" width="140" height="24" rx="3"/><text class="d-t-s d-mid" x="290" y="215">goroutine 2</text><rect class="d-box" x="220" y="226" width="140" height="24" rx="3"/><text class="d-t-s d-mid" x="290" y="243">goroutine 3</text><path class="d-line-a" d="M216 182 L139 182" marker-end="url(#ar12a)"/><path class="d-line-a" d="M216 210 L139 210" marker-end="url(#ar12a)"/><path class="d-line-a" d="M216 238 L139 238" marker-end="url(#ar12a)"/><text class="d-t-s" x="390" y="200">每個 goroutine 捕捉到自己那一份。</text><text class="d-t-s" x="390" y="220">只有真的被閉包捕捉的迭代才會配置，不影響效能。</text><defs><marker id="ar12" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M0 0 L10 5 L0 10 z" style="fill:var(--line-strong)"/></marker><marker id="ar12a" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M0 0 L10 5 L0 10 z" style="fill:var(--accent)"/></marker></defs></svg><figcaption><b>per-iteration 語意。</b>Go 1.22 讓每輪迭代都有獨立的迴圈變數。編譯器很聰明：只有當變數真的被閉包捕捉時才會產生額外配置，一般迴圈完全不受影響。</figcaption></figure>

!!! version "這個變更由 go.mod 閘門控制"
    **不是**「用 Go 1.22 編譯就生效」，而是**「`go.mod` 宣告 `go 1.22` 以上才生效」**。

    這是 Go 團隊在相容性與修 bug 之間的折衷：舊模組維持舊行為（就算你用新工具鏈編譯），你主動升級 `go.mod` 才切換。

    ```text
    module example.com/myapp

    go 1.22   ← 這一行決定語意
    ```

    也可以用 `GOEXPERIMENT=loopvar` / `GODEBUG=loopvar=1` 個別控制，但一般不需要。

**舊版的解法**（如果你還在維護舊模組）：

```go
for i := 0; i < 3; i++ {
	i := i // 遮蔽：建立一個新的區域變數
	go func() { fmt.Print(i) }()
}

// 或當成參數傳
for i := 0; i < 3; i++ {
	go func(n int) { fmt.Print(n) }(i)
}
```

---

## range 陣列 vs range slice

這個差異很細微但值得知道。

```go
package main

import "fmt"

func main() {
	// ① 陣列：range 的是「複本」
	arr := [3]int{1, 2, 3}
	for i, v := range arr {
		if i == 0 {
			arr[1] = 99 // 改的是原陣列
		}
		fmt.Print(v, " ") // 印的是複本的值
	}
	fmt.Println("| arr =", arr)

	// ② 陣列指標：不複製
	for i, v := range &arr {
		if i == 0 {
			arr[1] = 777
		}
		fmt.Print(v, " ")
	}
	fmt.Println("| arr =", arr)

	// ③ slice：只複製標頭，元素共享
	s := []int{1, 2, 3}
	for i, v := range s {
		if i == 0 {
			s[1] = 99
		}
		fmt.Print(v, " ")
	}
	fmt.Println("| s =", s)
}
```

```text
1 2 3 | arr = [1 99 3]
1 777 3 | arr = [1 777 3]
1 99 3 | s = [1 99 3]
```

- **陣列**：整份複製（如果編譯器判斷需要）。改原陣列，迴圈讀到的還是舊值。
- **陣列指標**：不複製，改了就看得到。
- **slice**：只複製 24 位元組的標頭，底層陣列共享，改了就看得到。

實務上，大陣列直接 range 會產生明顯的複製成本，用 `range &arr` 或改用 slice。

---

## range map 與 range channel

### map：隨機順序，可安全刪除

```go
package main

import "fmt"

func main() {
	m := map[int]string{1: "a", 2: "b", 3: "c", 4: "d"}

	for k := range m {
		if k%2 == 0 {
			delete(m, k) // ✓ 在 range 中刪除是安全的
		}
	}
	fmt.Println(m) // map[1:a 3:c]
}
```

規格明確保證：**range 中刪除元素是安全的**，被刪的元素若尚未走訪到就不會出現。

但**新增**元素的行為未定義——新元素可能出現也可能不出現。要新增就先收集到 slice，迴圈結束後再處理。

### channel：讀到關閉為止

```go
package main

import "fmt"

func main() {
	ch := make(chan int, 3)
	go func() {
		for i := 1; i <= 3; i++ {
			ch <- i
		}
		close(ch) // 必須關閉，否則 range 永遠不會結束
	}()

	for v := range ch {
		fmt.Print(v, " ")
	}
	fmt.Println("| 完成")
}
```

`for v := range ch` 等同於：

```go
for {
	v, ok := <-ch
	if !ok {
		break
	}
	// body
}
```

**忘記 `close` 是 goroutine 洩漏的頭號原因。**

### range 字串：解碼 UTF-8

見 [字串](string.html#byte-與-rune索引與走訪的差別)。重點是索引為位元組位置，值為完整的 rune。

---

## Go 1.23：range over func 迭代器

!!! version "Go 1.23 起：自訂迭代器"
    Go 1.23 讓 `range` 可以接受特定簽章的函式，這是 Go 1.18 泛型之後最重要的語言擴充。

### 三種簽章

```go
func(yield func() bool)          // 無值
func(yield func(V) bool)         // 單值 —— iter.Seq[V]
func(yield func(K, V) bool)      // 雙值 —— iter.Seq2[K, V]
```

`yield` 回傳 `false` 表示「消費端要提前結束」，迭代器應該立刻停止。

### 一個實際例子

```go
package main

import (
	"fmt"
	"iter"
)

// 回傳一個走訪 slice 中所有偶數的迭代器
func Evens(s []int) iter.Seq2[int, int] {
	return func(yield func(int, int) bool) {
		for i, v := range s {
			if v%2 != 0 {
				continue
			}
			if !yield(i, v) {
				return // 消費端 break 了
			}
		}
	}
}

func main() {
	s := []int{1, 2, 3, 4, 5, 6, 7, 8}

	for i, v := range Evens(s) {
		fmt.Printf("s[%d]=%d ", i, v)
	}
	fmt.Println()

	// break 也正常運作
	for i, v := range Evens(s) {
		if v > 4 {
			break
		}
		fmt.Printf("s[%d]=%d ", i, v)
	}
	fmt.Println()
}
```

```text
s[1]=2 s[3]=4 s[5]=6 s[7]=8 
s[1]=2 s[3]=4 
```

### 為什麼這個功能重要

**① 標準庫全面採用。** Go 1.23 起，`maps` 與 `slices` 都有迭代器版本：

```go
package main

import (
	"fmt"
	"maps"
	"slices"
)

func main() {
	m := map[string]int{"b": 2, "a": 1, "c": 3}

	// 排序後的鍵
	for _, k := range slices.Sorted(maps.Keys(m)) {
		fmt.Print(k, " ")
	}
	fmt.Println()

	// 收集成 slice
	vals := slices.Collect(maps.Values(m))
	slices.Sort(vals)
	fmt.Println(vals) // [1 2 3]

	// 反向走訪
	s := []int{1, 2, 3}
	for i, v := range slices.Backward(s) {
		fmt.Printf("%d:%d ", i, v)
	}
	fmt.Println() // 2:3 1:2 0:1
}
```

**② 讓「串流處理」變得自然。** 以前要處理大檔案而不全部載入記憶體，只能用 callback 或 channel。callback 不能 `break`，channel 有 goroutine 與同步成本。迭代器兩個問題都解決了：

```go
package main

import (
	"bufio"
	"fmt"
	"iter"
	"strings"
)

// 逐行讀取，不把整份內容載入記憶體
func Lines(r *bufio.Scanner) iter.Seq[string] {
	return func(yield func(string) bool) {
		for r.Scan() {
			if !yield(r.Text()) {
				return
			}
		}
	}
}

func main() {
	sc := bufio.NewScanner(strings.NewReader("alpha\nbeta\ngamma\ndelta"))

	for line := range Lines(sc) {
		if strings.HasPrefix(line, "g") {
			fmt.Println("找到:", line)
			break // 立刻停止讀取
		}
	}
}
```

```text
找到: gamma
```

**③ `iter.Pull`：把推送式轉成拉取式。** 有時候你需要「一次拿一個」的控制權（例如同時走訪兩個序列做合併）：

```go
package main

import (
	"fmt"
	"iter"
	"slices"
)

func main() {
	a := slices.Values([]int{1, 3, 5})
	b := slices.Values([]int{2, 4, 6})

	nextA, stopA := iter.Pull(a)
	nextB, stopB := iter.Pull(b)
	defer stopA()
	defer stopB()

	for {
		x, okA := nextA()
		y, okB := nextB()
		if !okA && !okB {
			break
		}
		fmt.Print(x, " ", y, " ")
	}
	fmt.Println()
}
```

```text
1 2 3 4 5 6 
```

!!! warning "`iter.Pull` 一定要 `defer stop()`"
    `iter.Pull` 內部用 goroutine 實作。忘記呼叫 `stop` 會讓那個 goroutine 永遠卡住，造成洩漏。

---

## `break`、`continue` 與標籤

```go
package main

import "fmt"

func main() {
	grid := [][]int{{1, 2, 3}, {4, 5, 6}, {7, 8, 9}}

outer:
	for i, row := range grid {
		for j, v := range row {
			if v == 5 {
				fmt.Printf("在 (%d,%d) 找到 5\n", i, j)
				break outer // 跳出外層迴圈
			}
		}
	}

	// continue 也可以帶標籤
	rows := [][]int{{1, 3, 5}, {4, 5, 6}, {7, 9, 11}}
next:
	for _, row := range rows {
		for _, v := range row {
			if v%2 == 0 {
				continue next // 這一列有偶數，直接跳到下一列
			}
		}
		fmt.Println("全是奇數的列:", row)
	}
}
```

```text
在 (1,1) 找到 5
全是奇數的列: [1 3 5]
全是奇數的列: [7 9 11]
```

!!! tip "`break` 在 `select` 與 `switch` 裡的陷阱"
    在 `for` 迴圈裡的 `switch` 或 `select` 中，`break` 只會跳出 `switch`／`select`，**不會跳出迴圈**：

    ```go
    for {
        select {
        case <-ch:
            break // ✗ 只跳出 select，迴圈繼續
        }
    }
    ```

    要跳出迴圈必須用標籤：

    ```go
    loop:
    for {
        select {
        case <-ch:
            break loop // ✓
        }
    }
    ```

    這是實務上很常見的 bug。

---

下一節談 `select`——channel 多路選擇的實作。
