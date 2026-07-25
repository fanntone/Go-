---
title: 切片 slice
slug: slice
part: p2
number: "2.2"
order: 220
summary: 三欄位標頭、append 的擴容規則、共享底層陣列造成的別名陷阱，以及三索引切片與記憶體洩漏。
updated: "1.26"
---

## slice 就是一個三欄位的 struct

理解 slice 的一切，只需要記住這個結構（`runtime/slice.go`）：

```go
type slice struct {
	array unsafe.Pointer // 指向底層陣列的第一個元素
	len   int            // 目前長度
	cap   int            // 從 array 起算到底層陣列結尾的容量
}
```

就這樣，24 個位元組（64 位元平台）。**slice 不擁有資料，它只是一個「看向某段陣列的視窗」。**

```go
package main

import (
	"fmt"
	"unsafe"
)

func main() {
	s := make([]int, 3, 10)
	fmt.Println(unsafe.Sizeof(s)) // 24，跟元素數量無關

	// 用 unsafe 直接看標頭內容（僅供教學，正式程式碼別這樣寫）
	type header struct {
		array uintptr
		len   int
		cap   int
	}
	h := (*header)(unsafe.Pointer(&s))
	fmt.Printf("array=%#x len=%d cap=%d\n", h.array, h.len, h.cap)
}
```

```text
24
array=0xc0000160c0 len=3 cap=10
```

<figure class="diagram"><svg viewBox="0 0 700 260" role="img" aria-label="slice 標頭與底層陣列的關係"><text class="d-t-b" x="15" y="20">arr := [8]int{0,1,2,3,4,5,6,7}</text><rect class="d-box" x="15" y="30" width="72" height="40" rx="4"/><text class="d-t-m d-mid" x="51" y="55">0</text><rect class="d-box" x="87" y="30" width="72" height="40" rx="4"/><text class="d-t-m d-mid" x="123" y="55">1</text><rect class="d-box-a" x="159" y="30" width="72" height="40" rx="4"/><text class="d-t-m d-mid" x="195" y="55">2</text><rect class="d-box-a" x="231" y="30" width="72" height="40" rx="4"/><text class="d-t-m d-mid" x="267" y="55">3</text><rect class="d-box-a" x="303" y="30" width="72" height="40" rx="4"/><text class="d-t-m d-mid" x="339" y="55">4</text><rect class="d-box-w" x="375" y="30" width="72" height="40" rx="4"/><text class="d-t-m d-mid" x="411" y="55">5</text><rect class="d-box-w" x="447" y="30" width="72" height="40" rx="4"/><text class="d-t-m d-mid" x="483" y="55">6</text><rect class="d-box" x="519" y="30" width="72" height="40" rx="4"/><text class="d-t-m d-mid" x="555" y="55">7</text><text class="d-t-s d-mid" x="51" y="86">[0]</text><text class="d-t-s d-mid" x="195" y="86">[2]</text><text class="d-t-s d-mid" x="411" y="86">[5]</text><text class="d-t-s d-mid" x="555" y="86">[7]</text><text class="d-t-a" x="15" y="122">s := arr[2:5]　→　len=3　cap=6</text><path class="d-line-a" d="M159 108 L159 74" marker-end="url(#ar8a)"/><line class="d-line-a" x1="159" y1="108" x2="375" y2="108"/><text class="d-t-a d-mid" x="267" y="103">len = 3</text><line class="d-dash" x1="375" y1="108" x2="591" y2="108"/><text class="d-t-s d-mid" x="483" y="103">cap 延伸到底層陣列結尾 = 6</text><text class="d-t-b" x="15" y="158">s2 := arr[2:5:5]　→　len=3　cap=3（三索引切片限制了容量）</text><line class="d-line" x1="159" y1="172" x2="375" y2="172"/><path class="d-line" d="M375 172 L375 164" /><text class="d-t-s" x="385" y="176">cap 被切在這裡，append 一定會重新配置</text><line class="d-dash" x1="15" y1="196" x2="683" y2="196"/><text class="d-t-s" x="15" y="220">關鍵：s[0] 就是 arr[2]。改 s[0] 等於改 arr[2] —— 它們是同一塊記憶體。</text><text class="d-t-s" x="15" y="242">append(s, x) 在 cap 還夠時會直接寫進 arr[5]，把原本的 5 蓋掉。</text><defs><marker id="ar8a" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M0 0 L10 5 L0 10 z" style="fill:var(--accent)"/></marker></defs></svg><figcaption><b>切片就是開一個視窗。</b><code>arr[low:high]</code> 產生的 slice：<code>len = high - low</code>，<code>cap = len(arr) - low</code>。三索引形式 <code>arr[low:high:max]</code> 則讓 <code>cap = max - low</code>。</figcaption></figure>

---

## 共享底層陣列：最常見的坑

因為 slice 只是視窗，**兩個 slice 可以看向同一塊記憶體**。這是效能的來源，也是 bug 的來源。

```go
package main

import "fmt"

func main() {
	arr := [8]int{0, 1, 2, 3, 4, 5, 6, 7}

	s := arr[2:5] // len=3 cap=6
	fmt.Println(s, len(s), cap(s)) // [2 3 4] 3 6

	s[0] = 999
	fmt.Println(arr) // [0 1 999 3 4 5 6 7] ← 原陣列被改了

	s = append(s, -1)
	fmt.Println(arr) // [0 1 999 3 4 -1 6 7] ← arr[5] 被 append 蓋掉了
}
```

`append` 沒有超出容量，所以直接寫進底層陣列的下一格——**把不屬於 `s` 的資料蓋掉了**。

### 真實世界的版本

這個坑在切分資料時最常出現：

```go
package main

import "fmt"

// ✗ 有問題的版本
func splitBad(data []byte) ([]byte, []byte) {
	head := data[:4]
	tail := data[4:]
	return head, tail
}

func main() {
	data := []byte("HEADbody")
	head, tail := splitBad(data)

	// 呼叫端無辜地對 head 做 append
	head = append(head, '!')

	fmt.Printf("head=%q tail=%q\n", head, tail)
	// head="HEAD!" tail="ody"  ← tail 的第一個位元組被吃掉了
}
```

`head` 的 `cap` 是 8（延伸到底層結尾），所以 `append` 直接寫進 `data[4]`，也就是 `tail[0]`。

**解法：三索引切片。**

```go
// ✓ 正確版本
func splitGood(data []byte) ([]byte, []byte) {
	head := data[:4:4] // cap 也切成 4
	tail := data[4:]
	return head, tail
}
```

現在 `head` 的 `cap` 是 4，任何 `append` 都會觸發重新配置，不會碰到 `tail`。

!!! tip "什麼時候該用三索引切片"
    **只要你把 slice 的一部分交給別人，就該考慮限制 cap。** 這是函式庫作者的基本禮儀：

    ```go
    func (b *Buffer) Bytes() []byte {
        return b.buf[:b.n:b.n] // 呼叫端 append 不會弄壞 Buffer 的內部狀態
    }
    ```

---

## append 與擴容

`s = append(s, x)` 被編譯器改寫成大致這樣的邏輯：

```text
if len(s)+1 > cap(s) {
    s = runtime.growslice(s, len(s)+1)   // 配置更大的陣列並複製過去
}
s = s[:len(s)+1]
s[len(s)-1] = x
```

**注意 `append` 一定要接回傳值。** 因為它可能回傳一個全新的標頭（指向新陣列）。忘記接住是 Go 新手最常見的錯誤之一，`go vet` 會抓（`lostcancel` 之外的 `appends` 檢查）。

### 擴容規則（Go 1.18 起）

`runtime.growslice` 的成長邏輯分三步。

**第一步：算出「期望容量」`newcap`。**

```text
newLen = 需要的新長度
if newLen > 2 * oldCap {
    newcap = newLen                    // 一次加太多，直接照需求給
} else if oldCap < 256 {
    newcap = 2 * oldCap                // 小 slice：翻倍
} else {
    newcap = oldCap
    for newcap < newLen {
        newcap += (newcap + 3*256) / 4 // 大 slice：每次約成長 1.25 倍
    }
}
```

**第二步：換算成位元組數，並對齊到記憶體配置器的 size class。**

這一步由 `roundupsize` 完成。記憶體配置器只提供固定的幾十種區塊大小（見 [記憶體配置器](allocator.html)），所以實際拿到的容量通常**大於**計算出來的 `newcap`。

**第三步：配置新陣列、`memmove` 舊資料、回傳新標頭。**

!!! version "Go 1.18 改掉了「1024 之後 1.25 倍」的舊規則"
    很多中文資料還寫著「小於 1024 翻倍，大於 1024 每次 1.25 倍」。那是 **Go 1.17 以前**的規則。

    Go 1.18 改成上面的版本：門檻從 **1024 降到 256**，而且成長公式改成 `newcap += (newcap + 3*256) / 4`，讓成長率從 2 倍**平滑地**過渡到 1.25 倍，而不是斷崖式切換。這減少了在門檻附近的記憶體浪費。

### 親眼看擴容

```go
package main

import "fmt"

func main() {
	var s []int
	prev := -1
	for i := 0; i < 2000; i++ {
		s = append(s, i)
		if cap(s) != prev {
			fmt.Printf("len=%-5d cap=%d\n", len(s), cap(s))
			prev = cap(s)
		}
	}
}
```

```text
len=1     cap=1
len=2     cap=2
len=3     cap=4
len=5     cap=8
len=9     cap=16
len=17    cap=32
len=33    cap=64
len=65    cap=128
len=129   cap=256
len=257   cap=512
len=513   cap=848
len=849   cap=1280
len=1281  cap=1792
len=1793  cap=2560
```

前面幾次是漂亮的 2 的冪（翻倍 + size class 剛好對齊）。過了 256 之後就不是了：`512 → 848` 是因為 `512 + (512 + 768)/4 = 832`，再對齊到 size class 變成 848。

!!! warning "不要依賴具體的 cap 數值"
    擴容規則是**實作細節**，Go 團隊改過好幾次，未來還可能再改。寫測試斷言 `cap(s) == 848` 這種事，升級版本時就會壞掉。

### 先配置容量：最有效的最佳化之一

```go
package main

import (
	"fmt"
	"testing"
)

func without(n int) []int {
	var s []int
	for i := 0; i < n; i++ {
		s = append(s, i)
	}
	return s
}

func with(n int) []int {
	s := make([]int, 0, n) // 一次配置到位
	for i := 0; i < n; i++ {
		s = append(s, i)
	}
	return s
}

func main() {
	const n = 100000
	r1 := testing.Benchmark(func(b *testing.B) {
		b.ReportAllocs()
		for i := 0; i < b.N; i++ {
			_ = without(n)
		}
	})
	r2 := testing.Benchmark(func(b *testing.B) {
		b.ReportAllocs()
		for i := 0; i < b.N; i++ {
			_ = with(n)
		}
	})
	fmt.Println("without:", r1, r1.MemString())
	fmt.Println("with:   ", r2, r2.MemString())
}
```

典型輸出：

```text
without: 	    3448	    342847 ns/op	   4101187 B/op	        29 allocs/op
with:    	   14562	     81234 ns/op	    802816 B/op	         1 allocs/op
```

29 次配置變成 1 次，總配置量從 4 MB 降到 0.8 MB。**只要你大致知道最終長度，就用 `make([]T, 0, n)`。**

---

## 記憶體洩漏：小 slice 綁住大陣列

因為 slice 持有指向底層陣列的指標，**只要 slice 還活著，整個底層陣列就不能被 GC 回收**——即使你只用到其中三個元素。

```go
package main

import "fmt"

// ✗ 回傳的小 slice 讓 100 MB 的陣列無法被回收
func firstThreeBad() []byte {
	huge := make([]byte, 100<<20) // 100 MB
	// ... 處理 huge ...
	return huge[:3] // 標頭指向 huge 的開頭 → 整塊 100 MB 都活著
}

// ✓ 明確複製一份
func firstThreeGood() []byte {
	huge := make([]byte, 100<<20)
	// ... 處理 huge ...
	out := make([]byte, 3)
	copy(out, huge)
	return out // huge 可以被回收了
}

func main() {
	fmt.Println(len(firstThreeGood()))
}
```

同樣的問題也出現在「從大 slice 中篩選少數元素」的場景。判斷準則：**如果保留的比例很小、而且結果會活很久，就複製一份。**

### 指標元素的洩漏

還有一種更隱蔽的：縮短 slice 不會清掉被移除元素的指標。

```go
package main

import "fmt"

type Item struct{ data [1024]byte }

func main() {
	items := make([]*Item, 3)
	for i := range items {
		items[i] = &Item{}
	}

	// ✗ 只是把 len 改成 1，items[1] 與 items[2] 的指標還在底層陣列裡
	items = items[:1]
	fmt.Println(len(items), cap(items)) // 1 3
	// 那兩個 Item 仍然被參照著，GC 收不掉
}
```

正確的移除方式要把不用的位置設成零值：

```go
// 移除最後一個元素
items[len(items)-1] = nil
items = items[:len(items)-1]
```

Go 1.21 起，標準庫的 `slices` 套件已經幫你處理好了：

```go
import "slices"

items = slices.Delete(items, 1, 3) // 會把尾端清成零值
```

---

## `slices` 套件：別再自己寫了

!!! version "Go 1.21 起：`slices` 進入標準庫"
    以前處理 slice 的常見操作都要自己寫或抄程式碼片段。Go 1.21 把 `golang.org/x/exp/slices` 提升為標準庫的 `slices` 套件。

```go
package main

import (
	"fmt"
	"slices"
)

func main() {
	s := []int{3, 1, 4, 1, 5, 9, 2, 6}

	slices.Sort(s)
	fmt.Println(s) // [1 1 2 3 4 5 6 9]

	i, found := slices.BinarySearch(s, 5)
	fmt.Println(i, found) // 5 true

	fmt.Println(slices.Contains(s, 9))  // true
	fmt.Println(slices.Index(s, 4))     // 3
	fmt.Println(slices.Max(s), slices.Min(s)) // 9 1

	s = slices.Compact(s) // 移除相鄰重複（需先排序）
	fmt.Println(s)        // [1 2 3 4 5 6 9]

	r := slices.Clone(s)
	slices.Reverse(r)
	fmt.Println(r) // [9 6 5 4 3 2 1]

	fmt.Println(slices.Equal(s, r)) // false
}
```

常用函式：

| 函式 | 用途 |
| --- | --- |
| `slices.Sort` / `SortFunc` / `SortStableFunc` | 排序 |
| `slices.BinarySearch` / `BinarySearchFunc` | 二分搜尋（需已排序） |
| `slices.Contains` / `Index` / `IndexFunc` | 查找 |
| `slices.Insert` / `Delete` / `Replace` | 增刪改（會正確清零） |
| `slices.Clone` / `Concat` | 複製、串接 |
| `slices.Compact` / `CompactFunc` | 移除相鄰重複 |
| `slices.Equal` / `Compare` | 比較 |
| `slices.Grow` / `Clip` | 調整容量 |
| `slices.Reverse` | 反轉 |
| `slices.Max` / `Min` | 極值 |
| `slices.Values` / `All` / `Sorted`（1.23+） | 迭代器整合 |

---

## nil slice 與空 slice

```go
package main

import "fmt"

func main() {
	var a []int          // nil slice
	b := []int{}         // 空 slice，非 nil
	c := make([]int, 0)  // 空 slice，非 nil

	fmt.Println(a == nil, b == nil, c == nil) // true false false
	fmt.Println(len(a), len(b), len(c))       // 0 0 0

	// 兩者行為幾乎一樣
	a = append(a, 1) // ✓ 對 nil slice append 完全合法
	fmt.Println(a)   // [1]

	for range a {} // ✓ range nil slice 也沒問題
}
```

實務建議：

- **宣告時用 `var s []T`**（nil slice），不要用 `[]T{}`。前者不配置記憶體。
- **回傳空結果時回傳 nil**，呼叫端用 `len(s) == 0` 判斷，不要用 `s == nil`。
- **例外：JSON 序列化。** `nil` 會編碼成 `null`，空 slice 會編碼成 `[]`。如果 API 契約要求 `[]`，就要用 `[]T{}`。

```go
package main

import (
	"encoding/json"
	"fmt"
)

type Resp struct {
	Items []string `json:"items"`
}

func main() {
	var a Resp                        // Items 是 nil
	b := Resp{Items: []string{}}      // Items 是空 slice

	x, _ := json.Marshal(a)
	y, _ := json.Marshal(b)
	fmt.Println(string(x)) // {"items":null}
	fmt.Println(string(y)) // {"items":[]}
}
```

---

下一節是 map。Go 1.24 把它整個換掉了，從 bucket 鏈結改成 Swiss Table，是近年最大的一次資料結構重寫。
