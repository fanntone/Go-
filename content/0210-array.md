---
title: 陣列 array
slug: array
part: p2
number: "2.1"
order: 210
summary: 為什麼陣列長度是型別的一部分、值語意帶來的複製成本，以及編譯器對陣列做的最佳化。
updated: "1.26"
---

## 陣列是值，長度是型別

Go 的陣列跟 C 的陣列差很多。最重要的兩點：

1. **長度是型別的一部分。** `[3]int` 與 `[4]int` 是**兩個不同的型別**，不能互相賦值。
2. **陣列是值型別。** 賦值與傳參數都是**整份複製**，不是傳指標。

```go
package main

import "fmt"

func main() {
	a := [3]int{1, 2, 3}
	b := a // 整份複製，b 與 a 完全獨立

	b[0] = 99
	fmt.Println(a, b) // [1 2 3] [99 2 3]

	// ✗ 編譯錯誤：cannot use c (variable of type [4]int) as [3]int value
	// var c [4]int
	// a = c

	fmt.Printf("%T %T\n", [3]int{}, [4]int{}) // [3]int [4]int
}
```

長度屬於型別，代表**陣列大小必須在編譯期確定**，只能是常數運算式：

```go
const n = 5
var ok [n]int    // ✓ n 是常數

m := 5
// var bad [m]int  // ✗ 編譯錯誤：invalid array length m
```

要在執行期決定長度，就得用 slice（下一節）。

### 記憶體佈局：連續、無標頭

陣列在記憶體裡就是**一塊連續的元素**，沒有任何額外的標頭或中繼資料。

<figure class="diagram"><svg viewBox="0 0 700 200" role="img" aria-label="陣列與 slice 的記憶體佈局對比"><text class="d-t-b" x="15" y="22">[4]int32 的記憶體佈局 —— 就是 16 個位元組，沒別的</text><rect class="d-box-a" x="15" y="34" width="90" height="44" rx="4"/><text class="d-t-m d-mid" x="60" y="53">10</text><text class="d-t-s d-mid" x="60" y="70">offset 0</text><rect class="d-box-a" x="105" y="34" width="90" height="44" rx="4"/><text class="d-t-m d-mid" x="150" y="53">20</text><text class="d-t-s d-mid" x="150" y="70">offset 4</text><rect class="d-box-a" x="195" y="34" width="90" height="44" rx="4"/><text class="d-t-m d-mid" x="240" y="53">30</text><text class="d-t-s d-mid" x="240" y="70">offset 8</text><rect class="d-box-a" x="285" y="34" width="90" height="44" rx="4"/><text class="d-t-m d-mid" x="330" y="53">40</text><text class="d-t-s d-mid" x="330" y="70">offset 12</text><text class="d-t-s" x="392" y="60">sizeof([4]int32) = 16　·　長度存在型別裡，不佔記憶體</text><text class="d-t-b" x="15" y="118">對比：[]int32 的 slice 標頭 —— 24 位元組，指向別處的陣列</text><rect class="d-box-w" x="15" y="130" width="120" height="44" rx="4"/><text class="d-t-m d-mid" x="75" y="149">array *int32</text><text class="d-t-s d-mid" x="75" y="166">8 位元組</text><rect class="d-box" x="135" y="130" width="100" height="44" rx="4"/><text class="d-t-m d-mid" x="185" y="149">len int</text><text class="d-t-s d-mid" x="185" y="166">8 位元組</text><rect class="d-box" x="235" y="130" width="100" height="44" rx="4"/><text class="d-t-m d-mid" x="285" y="149">cap int</text><text class="d-t-s d-mid" x="285" y="166">8 位元組</text><path class="d-line-a" d="M75 130 L75 100 L430 100 L430 84" marker-end="url(#ar7)"/><text class="d-t-s" x="440" y="150">slice 的長度存在執行期的 len 欄位，所以可以變動。</text><defs><marker id="ar7" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M0 0 L10 5 L0 10 z" style="fill:var(--accent)"/></marker></defs></svg><figcaption><b>陣列 vs slice。</b>陣列沒有執行期的長度欄位——它的長度編在型別裡，編譯期就固定。slice 則有一個 24 位元組的標頭（64 位元平台），指向某個底層陣列。</figcaption></figure>

驗證一下：

```go
package main

import (
	"fmt"
	"unsafe"
)

func main() {
	var arr [4]int32
	var sli []int32

	fmt.Println(unsafe.Sizeof(arr)) // 16
	fmt.Println(unsafe.Sizeof(sli)) // 24（slice 標頭大小，與元素數量無關）

	var arr2 [1000]int32
	fmt.Println(unsafe.Sizeof(arr2)) // 4000
}
```

---

## 值語意的代價與價值

### 代價：複製

```go
package main

import (
	"fmt"
	"testing"
)

type Big [1024]int64 // 8 KB

func byValue(b Big) int64   { return b[0] }
func byPointer(b *Big) int64 { return b[0] }

func main() {
	var b Big
	r1 := testing.Benchmark(func(bn *testing.B) {
		for i := 0; i < bn.N; i++ {
			_ = byValue(b)
		}
	})
	r2 := testing.Benchmark(func(bn *testing.B) {
		for i := 0; i < bn.N; i++ {
			_ = byPointer(&b)
		}
	})
	fmt.Println("byValue  ", r1)
	fmt.Println("byPointer", r2)
}
```

典型輸出（數字會依機器不同）：

```text
byValue   	 3062434	       389.1 ns/op
byPointer 	1000000000	       0.31 ns/op
```

差三個數量級。每次呼叫 `byValue` 都要 `memmove` 8 KB。

!!! warning "range 也會複製"
    這是最容易踩的一個坑：

    ```go
    var arr [1000]Big
    for i, v := range arr {  // v 是每個元素的複製品！
        _ = i
        _ = v
    }
    ```

    這個迴圈會複製 1000 次 8 KB。改用索引或指標：

    ```go
    for i := range arr {
        _ = &arr[i]  // 不複製
    }
    ```

    小陣列與小 struct 不用擔心，編譯器通常會最佳化掉。但元素大於一兩百位元組時就要留意。

### 價值：可比較、可當 map 鍵、無別名

值語意也帶來三個好處。

**① 陣列可以用 `==` 比較**（只要元素型別可比較）：

```go
a := [3]int{1, 2, 3}
b := [3]int{1, 2, 3}
fmt.Println(a == b) // true

// slice 不行：invalid operation: s1 == s2 (slice can only be compared to nil)
```

**② 陣列可以當 map 的鍵**：

```go
package main

import "fmt"

type Coord [2]int

func main() {
	grid := map[Coord]string{}
	grid[Coord{0, 0}] = "原點"
	grid[Coord{3, 4}] = "目標"

	fmt.Println(grid[Coord{3, 4}]) // 目標
}
```

這是陣列在實務上最常見的正當用途。用 `[16]byte` 存 MD5、用 `[4]byte` 存 IPv4、用 `[2]int` 存座標，都可以直接當 map 鍵。

**③ 沒有別名問題**。傳一個陣列給函式，你確定對方改不到你的資料。傳 slice 就沒這個保證。

---

## 陣列的初始化

三種寫法：

```go
// ① 明確長度
a := [5]int{1, 2, 3, 4, 5}

// ② 讓編譯器數
b := [...]int{1, 2, 3, 4, 5} // 型別是 [5]int

// ③ 指定索引（其餘補零值）
c := [10]int{0: 1, 5: 99, 9: 7}
fmt.Println(c) // [1 0 0 0 0 99 0 0 0 7]
```

第三種寫法在建查表時很好用：

```go
// 判斷 ASCII 字元是否為十六進位數字
var isHex = [256]bool{
	'0': true, '1': true, '2': true, '3': true, '4': true,
	'5': true, '6': true, '7': true, '8': true, '9': true,
	'a': true, 'b': true, 'c': true, 'd': true, 'e': true, 'f': true,
	'A': true, 'B': true, 'C': true, 'D': true, 'E': true, 'F': true,
}
```

查表 `isHex[c]` 是單一記憶體存取，比一連串 `if` 比較快得多，而且分支預測友善。

!!! note "編譯器怎麼處理初始化"
    元素少的時候，編譯器直接產生一連串 `MOV` 指令逐一寫入。元素多的時候（門檻約 4 個），它會把初始值放進**唯讀資料段**，執行期用 `memmove` 一次搬過來。這在 `-S` 輸出裡看得到 `runtime.memmove` 或 `statictmp_` 之類的符號。

---

## 邊界檢查

Go 對陣列與 slice 的每次索引都做邊界檢查。這是記憶體安全的基礎。

**常數索引在編譯期就檢查**：

```go
a := [3]int{}
// _ = a[5] // ✗ 編譯錯誤：invalid argument: index 5 out of bounds [0:3]
```

**變數索引在執行期檢查**：

```go
package main

import "fmt"

func main() {
	a := [3]int{1, 2, 3}
	i := 5

	defer func() {
		if r := recover(); r != nil {
			fmt.Println("recovered:", r)
		}
	}()

	fmt.Println(a[i])
}
```

```text
recovered: runtime error: index out of range [5] with length 3
```

檢查失敗會呼叫 `runtime.goPanicIndex`，產生一個 `runtime.boundsError`。

如同 [SSA](ssa.html) 那節提過的，編譯器會盡量消除可證明安全的檢查。陣列的情況特別好——因為長度是編譯期常數，`for i := 0; i < len(a); i++` 裡的檢查一定會被消除。

---

## 什麼時候該用陣列

老實說，Go 的日常程式碼裡陣列用得不多，因為 slice 更靈活。但這幾個場景陣列是正解：

| 場景 | 為什麼用陣列 |
| --- | --- |
| 固定大小的識別值（雜湊、UUID、IP） | 可比較、可當 map 鍵、無額外配置 |
| map 的鍵需要複合值 | slice 不能當鍵 |
| 小型查表 | 編譯期常數大小，索引檢查會被消除 |
| 想要值語意、避免別名 | 傳進函式後對方改不到原本的 |
| 環形緩衝區的儲存體 | 固定容量、無配置 |
| struct 內嵌固定大小欄位 | 整個 struct 一塊記憶體，快取友善 |

反過來，**不該用陣列**的情況：大小可能改變、大小很大且要常傳遞、大小要在執行期決定。

### 一個實用範例：無配置的小型集合

```go
package main

import "fmt"

// 用固定陣列當儲存體，避免堆積配置
type SmallSet struct {
	items [8]string
	n     int
}

func (s *SmallSet) Add(v string) bool {
	for i := 0; i < s.n; i++ {
		if s.items[i] == v {
			return false // 已存在
		}
	}
	if s.n == len(s.items) {
		return false // 滿了
	}
	s.items[s.n] = v
	s.n++
	return true
}

func (s *SmallSet) Has(v string) bool {
	for i := 0; i < s.n; i++ {
		if s.items[i] == v {
			return true
		}
	}
	return false
}

func main() {
	var s SmallSet
	s.Add("a")
	s.Add("b")
	s.Add("a")
	fmt.Println(s.n, s.Has("b"), s.Has("z")) // 2 true false
}
```

元素數量少時，**線性掃描陣列往往比 map 快** —— 沒有雜湊計算、沒有指標追逐、資料全在同一條快取線上。實測的交叉點大約在 8–16 個元素之間，依鍵的型別而定。

---

下一節談 slice。它是 Go 裡最常用也最常被誤解的型別，而它的一切行為都建立在「slice 只是一個指向陣列的三欄位標頭」這個事實上。
