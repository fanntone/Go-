---
title: 型別檢查與泛型實例化
slug: typecheck
part: p1
number: "1.3"
order: 130
summary: types2 做了哪些事、Go 的型別身分規則、介面滿足如何判定，以及泛型的 GC shape 實例化策略。
updated: "1.26"
---

## types2 負責什麼

語法分析給出一棵沒有型別的樹。型別檢查器（`cmd/compile/internal/types2`）走過這棵樹，做四件事：

1. **名稱解析** —— 把每個識別字綁定到它的宣告。這牽涉作用域規則：區塊 → 函式 → 檔案 → 套件 → 宇集（universe，內建識別字所在的最外層）。
2. **型別推導** —— 算出每個運算式的型別。`total := 0` 裡的 `total` 是 `int`，`x < 0` 的結果是 `bool`。
3. **合法性檢查** —— 這個運算對這些型別合不合法、這個型別滿不滿足那個介面、有沒有未使用的變數或 import。
4. **常數摺疊** —— 編譯期就能算出來的常數運算式直接算掉，並檢查有沒有溢位。

!!! note "types2 與 go/types 是雙胞胎"
    標準庫的 `go/types` 與編譯器的 `types2` 是**同一套演算法的兩份實作**。差別只在它們消費的 AST 不同（`go/ast` vs `cmd/compile/internal/syntax`）。Go 團隊靠一支同步工具讓兩邊保持一致。

    這對你的好處是：想理解編譯器的型別檢查行為，可以直接用 `go/types` 寫小程式做實驗，不用改編譯器。

---

## 型別身分：兩個型別什麼時候「相同」

這是 Go 型別系統的核心規則，也是很多困惑的來源。

### 具名型別（defined type）永遠自成一格

```go
package main

import "fmt"

type Celsius float64
type Fahrenheit float64

func main() {
	var c Celsius = 100
	var f Fahrenheit = 212

	// ✗ 編譯錯誤：invalid operation: c + f (mismatched types Celsius and Fahrenheit)
	// fmt.Println(c + f)

	// ✓ 必須明確轉換
	fmt.Println(c + Celsius(f))
}
```

`Celsius` 與 `Fahrenheit` 底層都是 `float64`，但它們是三個**不同**的型別。用 `type X Y` 宣告出來的叫**具名型別**，它跟 `Y` 不相同。

這正是它的價值：溫度單位、使用者 ID 與訂單 ID、公尺與英尺，用具名型別包起來，型別檢查器就會幫你擋掉混用。

### 型別別名（alias）則是同一個型別

```go
type Meters = float64   // 注意有等號：這是別名

var m Meters = 3.5
var f float64 = m       // ✓ 合法，它們是同一個型別
```

`type X = Y` 宣告的是**別名**，`X` 跟 `Y` 就是同一個型別，只是換個名字叫。別名的主要用途是**漸進式重構**：把型別搬到新套件時，在舊位置留一個別名，讓既有程式碼不用一次全改。

`byte` 就是 `uint8` 的別名，`rune` 就是 `int32` 的別名 —— 它們不是獨立型別。

!!! version "Go 1.24：泛型型別別名"
    Go 1.24 之前，型別別名不能有型別參數。Go 1.24 開始可以：

    ```go
    type Set[T comparable] = map[T]struct{}
    ```

    這在包裝泛型容器、簡化冗長的型別簽章時很有用。

### 未具名型別看結構

```go
a := struct{ X, Y int }{1, 2}
var b struct{ X, Y int }
b = a          // ✓ 合法：兩個未具名 struct 型別結構相同
```

未具名的複合型別（`[]int`、`map[string]int`、`struct{...}`、`func(int) error`）只要結構完全一致就是同一型別。struct 的欄位名稱、型別、順序、標籤（tag）都要相同。

---

## 介面滿足：結構化型別

Go 的介面是**隱式滿足**的，不需要 `implements` 宣告。型別檢查器的判定規則很單純：**方法集包含介面所需的全部方法**。

真正需要小心的是**方法集（method set）**的規則：

| 型別 | 方法集包含 |
| --- | --- |
| `T` | 所有接收者為 `T` 的方法 |
| `*T` | 所有接收者為 `T` **或** `*T` 的方法 |

換句話說：**指標的方法集比值的方法集大**。

```go
package main

import "fmt"

type Counter struct{ n int }

func (c Counter) Value() int { return c.n }  // 值接收者
func (c *Counter) Inc()      { c.n++ }       // 指標接收者

type Incrementer interface{ Inc() }

func main() {
	c := Counter{}

	// ✓ *Counter 的方法集有 Inc
	var i Incrementer = &c

	// ✗ 編譯錯誤：Counter does not implement Incrementer
	//    (method Inc has pointer receiver)
	// var j Incrementer = c

	i.Inc()
	i.Inc()
	fmt.Println(c.Value()) // 2
}
```

!!! tip "為什麼 `c.Inc()` 可以直接呼叫，賦值給介面卻不行？"
    因為這是兩件不同的事。`c.Inc()` 是**方法呼叫**，編譯器可以自動取位址（因為 `c` 是可定址的變數），改寫成 `(&c).Inc()`。

    但**賦值給介面**需要把值複製到介面裡。如果允許 `var j Incrementer = c`，那 `j.Inc()` 修改的會是介面裡那份複製品，原本的 `c` 完全不變——這種靜默失效比編譯錯誤糟糕得多。所以 Go 直接禁止。

    判斷準則：**能不能自動取位址**。`c.Inc()` 可以，因為 `c` 是變數；`Counter{}.Inc()` 就不行，因為字面值不可定址。

!!! version "Go 1.18：介面可以有型別集"
    泛型引入後，介面除了方法之外還能列出**型別集（type set）**：

    ```go
    type Number interface {
        ~int | ~int8 | ~int16 | ~int32 | ~int64 | ~float32 | ~float64
    }
    ```

    `~int` 表示「底層型別是 int 的所有型別」，所以 `type MyInt int` 也符合。這種介面**只能當型別約束用**，不能當一般的介面型別（不能宣告 `var x Number`）。

    這讓「介面」這個詞在 Go 裡有了兩種角色：執行期的動態分派，以及編譯期的型別約束。

---

## 型別推導的邊界

Go 的推導是**局部**的，不像 Haskell 或 ML 那樣做全域的 Hindley-Milner 推導。它只在幾個明確的位置作用：

```go
x := 42                 // ✓ 從右值推導：int
var y = "hi"            // ✓ 同上：string
const c = 1 << 20       // ✓ 無型別常數
f := func(a int) int {  // ✓ 函式字面值
	return a * 2
}

// ✗ 參數與回傳值一定要明確寫
// func double(a) { return a * 2 }
```

### 無型別常數：一個容易忽略的特性

Go 的常數在被賦予型別之前是**無型別（untyped）**的，而且精度是任意的：

```go
package main

import "fmt"

const big = 1 << 100 // 完全合法，遠超過任何整數型別

func main() {
	// 用的時候才需要能裝得下
	fmt.Println(big >> 98) // 4

	// ✗ 編譯錯誤：constant overflows int
	// var x int = big
}
```

無型別常數會在**使用的位置**才轉成具體型別，這讓下面這種寫法自然成立：

```go
const ratio = 0.5

var a float32 = ratio  // ratio 變成 float32
var b float64 = ratio  // ratio 變成 float64
var c complex128 = ratio
```

如果 `ratio` 是 `float64` 型別的變數，上面第一行就要明確轉換了。

!!! warning "整數除法陷阱"
    無型別常數的除法規則跟你想的可能不同：

    ```go
    const a = 1 / 2        // 兩個都是無型別整數常數 → 結果是 0
    const b = 1.0 / 2      // 有一個是浮點 → 結果是 0.5
    var c float64 = 1 / 2  // 仍然是 0！除法在轉型前就算完了
    ```

---

## 泛型是怎麼實例化的

泛型是 Go 1.18 最大的變動，它的實作策略介於 C++ 的樣板（完全單態化）與 Java 的型別抹除之間。

### 兩種極端

- **完全單態化（monomorphization）**：每個具體型別產生一份專屬程式碼。速度最快，但執行檔會膨脹，編譯也變慢。C++ 走這條路。
- **型別抹除（type erasure）**：所有型別共用一份程式碼，型別資訊在執行期消失。體積小，但每次存取都要裝箱／拆箱。Java 走這條路。

### Go 的折衷：GC shape stenciling

Go 的策略是依 **GC shape** 分組。兩個型別如果對垃圾回收器來說「長得一樣」，就共用同一份實例化程式碼。

判定 GC shape 相同的條件大致是：**大小相同、指標欄位的分佈相同**。

- 所有指標型別（`*T`、`map`、`chan`、`func`、`unsafe.Pointer`）都是同一個 shape：一個字組大小，整個都是指標。
- `int`、`int64`、`uint64`、`float64` 在 64 位元平台上是同一個 shape：8 位元組，沒有指標。
- `string` 是另一個 shape：16 位元組，前 8 個是指標。
- `[]T` 又是另一個：24 位元組，前 8 個是指標。

```go
package main

import "fmt"

func Max[T int | float64 | string](a, b T) T {
	if a > b {
		return a
	}
	return b
}

func main() {
	fmt.Println(Max(3, 7))         // int
	fmt.Println(Max(2.5, 1.5))     // float64
	fmt.Println(Max("go", "rust")) // string
}
```

編譯器會產生**兩份**實例（不是三份）：`int` 與 `float64` 共用一份（同 shape），`string` 自己一份。可以驗證：

```bash
go build -gcflags="-m" . 2>&1 | findstr "instantiat"
```

或直接看符號表：

```bash
go tool nm app.exe | findstr "Max"
```

### shape 共用的代價：字典

共用程式碼有個問題：`int` 版本要用整數比較，`float64` 版本要用浮點比較，但它們是同一份程式碼。編譯器的解法是傳入一個隱藏參數——**字典（dictionary）**，裡面放著這次實例化所需的型別中繼資料、方法位址等。

<figure class="diagram"><svg viewBox="0 0 700 260" role="img" aria-label="泛型的 GC shape 實例化"><text class="d-t-b" x="15" y="22">原始泛型函式</text><rect class="d-box" x="15" y="32" width="200" height="42" rx="6"/><text class="d-t-m" x="28" y="58">Max[T](a, b T) T</text><path class="d-line-a" d="M120 74 L120 96" marker-end="url(#ar5)"/><text class="d-t-s" x="130" y="90">依 GC shape 分組</text><rect class="d-box-a" x="15" y="100" width="310" height="66" rx="6"/><text class="d-t-a" x="28" y="122">實例 A ── shape: 8 位元組、無指標</text><text class="d-t-m" x="28" y="143">Max[go.shape.int64]</text><text class="d-t-s" x="28" y="160">供 int / int64 / float64 共用，用字典區分行為</text><rect class="d-box-a" x="360" y="100" width="325" height="66" rx="6"/><text class="d-t-a" x="373" y="122">實例 B ── shape: 16 位元組、首欄位為指標</text><text class="d-t-m" x="373" y="143">Max[go.shape.string]</text><text class="d-t-s" x="373" y="160">供 string 使用</text><rect class="d-box-w" x="15" y="186" width="670" height="58" rx="6"/><text class="d-t-b" x="28" y="208">隱藏參數：字典 dictionary</text><text class="d-t-s" x="28" y="228">每個呼叫點傳入。內含：具體型別的 *_type、介面方法表、需要的子字典 —— 讓共用程式碼知道「這次的 T 到底是誰」。</text><defs><marker id="ar5" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M0 0 L10 5 L0 10 z" style="fill:var(--accent)"/></marker></defs></svg><figcaption><b>GC shape stenciling。</b>用「產生幾份程式碼」換「執行期查字典的成本」。相較完全單態化，執行檔小很多；相較型別抹除，避免了裝箱。代價是泛型程式碼**通常比手寫的具體版本略慢**。</figcaption></figure>

!!! warning "泛型不是效能最佳化手段"
    很多人以為「用泛型取代 `interface{}` 會變快」。這只有在你原本會發生**裝箱配置**時才成立。如果是簡單的數值運算，泛型版本因為字典間接層，往往比直接寫具體型別的版本慢一點，也比較難被內聯。

    泛型的價值是**型別安全與消除重複程式碼**，不是速度。要效能就寫具體型別，或用 `go:generate` 產生程式碼。

---

## 常見型別錯誤的真正原因

| 錯誤訊息 | 真正的原因 |
| --- | --- |
| `cannot use x (variable of type MyInt) as int value` | 具名型別不等於底層型別，要明確轉換 |
| `X does not implement Y (method Z has pointer receiver)` | 方法集規則，要傳 `&x` |
| `declared and not used: x` | Go 把未使用的**區域變數**當錯誤（未使用的套件層變數則不會） |
| `imported and not used: "fmt"` | 同上，未使用的 import 也是錯誤 |
| `invalid operation: operator < not defined on x (variable of type T)` | 泛型約束沒有包含 `cmp.Ordered`，加上約束 |
| `constant 300 overflows int8` | 無型別常數在轉成具體型別時裝不下 |
| `cannot range over x (variable of type T)` | 泛型型別參數不能直接 range，要用 `~[]E` 之類的約束 |

型別檢查完成後，AST 上的每個節點都掛好了型別。下一節，編譯器要把這棵樹轉成中間表示，並開始真正的最佳化。
