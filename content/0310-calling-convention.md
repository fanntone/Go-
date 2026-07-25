---
title: 函式呼叫與呼叫慣例
slug: calling-convention
part: p3
number: "3.1"
order: 310
summary: Go 1.17 從堆疊傳參改成暫存器傳參、堆疊影格的佈局、多回傳值怎麼實作、以及方法值與閉包的成本。
updated: "1.26"
---

## 呼叫慣例是什麼

「呼叫慣例（calling convention）」規定了函式呼叫的協定：參數放哪裡、回傳值放哪裡、誰負責清理堆疊、哪些暫存器呼叫後保證不變。

這件事平常你不用管，但它決定了函式呼叫的成本，也是讀組合語言時的必備知識。

!!! version "Go 1.17：從堆疊傳參改成暫存器傳參"
    這是 Go 效能史上重要的一步。

    **Go 1.17 之前**：所有參數與回傳值都經由**堆疊**傳遞。實作簡單、跨平台一致、垃圾回收器容易掃描，但每次呼叫都要寫記憶體再讀記憶體。

    **Go 1.17 起**：`amd64` 導入**暫存器 ABI**（`ABIInternal`），Go 1.18 擴及 `arm64`、`ppc64`，之後涵蓋更多架構。官方回報的效能提升約 **5%**，執行檔小約 2%。

    如果你看到的中文資料說「Go 的參數都放在堆疊上」，那是 1.17 以前的描述。

---

## 暫存器 ABI

在 `amd64` 上，Go 用 9 個整數暫存器與 15 個浮點暫存器傳參：

| 用途 | 暫存器 |
| --- | --- |
| 整數參數與回傳值（依序） | `AX`, `BX`, `CX`, `DI`, `SI`, `R8`, `R9`, `R10`, `R11` |
| 浮點參數與回傳值（依序） | `X0` – `X14` |
| 目前的 goroutine（`g`） | `R14`（固定佔用） |
| 閉包的 context 指標 | `DX` |

規則：

1. 參數依宣告順序，依型別分別從整數組或浮點組取下一個可用暫存器。
2. **struct 會被攤平（flatten）**：`Point{X, Y int}` 佔用兩個整數暫存器。
3. 暫存器不夠時，剩下的參數改走堆疊。
4. 回傳值用同一組暫存器，從頭開始重新分配。

### 親眼驗證

```go
package main

type Point struct{ X, Y int }

//go:noinline
func Add(a, b int) int { return a + b }

//go:noinline
func Swap(p Point) Point { return Point{p.Y, p.X} }

//go:noinline
func Div(a, b int) (int, int) { return a / b, a % b }

func main() {
	println(Add(1, 2))
	q := Swap(Point{3, 4})
	println(q.X, q.Y)
	println(Div(7, 3))
}
```

```bash
go tool compile -S main.go
```

`Add` 的核心（節錄）：

```text
main.Add STEXT nosplit size=4 args=0x10 locals=0x0
	TEXT	main.Add(SB), NOSPLIT|ABIInternal, $0-16
	ADDQ	BX, AX          // a 在 AX，b 在 BX，結果留在 AX
	RET
```

`Swap` 的核心：

```text
main.Swap STEXT nosplit size=5 args=0x20 locals=0x0
	TEXT	main.Swap(SB), NOSPLIT|ABIInternal, $0-32
	XCHGQ	AX, BX          // p.X 在 AX、p.Y 在 BX，交換後直接當回傳值
	RET
```

`Swap` 傳入一個 struct、回傳一個 struct，整個函式只有一道 `XCHGQ`。**沒有碰記憶體。**

`Div` 的多回傳值：

```text
	MOVQ	AX, CX
	...
	IDIVQ	BX
	// 商在 AX，餘數在 DX → 搬到回傳暫存器 AX 與 BX
```

**多回傳值沒有魔法**，就是用多個暫存器（或堆疊位置）。這也是為什麼 Go 的 `(value, error)` 慣例成本很低——`error` 是一個介面（兩個字組），總共佔用少數幾個暫存器。

---

## 堆疊影格

當參數超過暫存器數量、或函式有較多區域變數時，還是需要堆疊。每次函式呼叫會建立一個**堆疊影格（stack frame）**。

<figure class="diagram"><svg viewBox="0 0 700 330" role="img" aria-label="Go 的堆疊影格佈局"><text class="d-t-s" x="480" y="24">高位址</text><text class="d-t-s" x="480" y="300">低位址（堆疊往下長）</text><rect class="d-box" x="140" y="32" width="300" height="34" rx="4"/><text class="d-t-m d-mid" x="290" y="54">呼叫者的區域變數 …</text><rect class="d-box-w" x="140" y="66" width="300" height="40" rx="4"/><text class="d-t-m d-mid" x="290" y="84">溢出的參數 / 回傳值</text><text class="d-t-s d-mid" x="290" y="100">暫存器裝不下的部分才會在這</text><rect class="d-box-a" x="140" y="106" width="300" height="34" rx="4"/><text class="d-t-m d-mid" x="290" y="128">回傳位址（CALL 自動壓入）</text><rect class="d-box-a" x="140" y="140" width="300" height="34" rx="4"/><text class="d-t-m d-mid" x="290" y="162">呼叫者的 BP（影格指標）</text><rect class="d-box-o" x="140" y="174" width="300" height="40" rx="4"/><text class="d-t-m d-mid" x="290" y="192">被呼叫者的區域變數</text><text class="d-t-s d-mid" x="290" y="208">逃逸分析判定不逃逸的那些</text><rect class="d-box-o" x="140" y="214" width="300" height="40" rx="4"/><text class="d-t-m d-mid" x="290" y="232">暫存器溢出區 spill slots</text><text class="d-t-s d-mid" x="290" y="248">呼叫其他函式前保存暫存器用</text><rect class="d-box" x="140" y="254" width="300" height="34" rx="4"/><text class="d-t-m d-mid" x="290" y="276">給下一層呼叫預留的空間</text><path class="d-line" d="M460 40 L460 290" marker-end="url(#ar10)"/><text class="d-t-a" x="60" y="128">← BP</text><text class="d-t-a" x="60" y="276">← SP</text><line class="d-line-a" x1="100" y1="123" x2="136" y2="123"/><line class="d-line-a" x1="100" y1="271" x2="136" y2="271"/><defs><marker id="ar10" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M0 0 L10 5 L0 10 z" style="fill:var(--line-strong)"/></marker></defs><text class="d-t-s" x="15" y="320">SP 與 BP 之間就是目前函式的影格。BP 鏈結成單向串列，讓除錯器能走回上層。</text></svg><figcaption><b>一個堆疊影格。</b>暫存器 ABI 讓上面「溢出的參數」那一格在大多數呼叫中是空的。影格指標（BP）鏈結雖然佔一個暫存器，但讓 panic 堆疊追蹤與 profiler 能可靠地回溯。</figcaption></figure>

### 函式序言：堆疊成長檢查

每個**可能需要更多堆疊空間**的函式，開頭都有一段序言（prologue）：

```text
	MOVQ	(R14), CX        // R14 是目前的 g，取出 g.stackguard0
	CMPQ	SP, 16(CX)       // 比較目前 SP 與 stackguard
	JLS	morestack            // 不夠 → 跳去擴充堆疊
	// ... 函式本體 ...
morestack:
	CALL	runtime.morestack_noctxt(SB)
	JMP	開頭                  // 擴充完重跑一次
```

這就是 Go 能讓 goroutine 從 2 KB 堆疊起步、需要時自動長大的機制。詳見 [堆疊記憶體管理](stack.html)。

標記 `NOSPLIT` 的函式沒有這段序言——因為編譯器算出它不會超過既有的安全邊界（red zone）。

---

## 方法呼叫

方法只是**第一個參數是接收者的函式**。編譯器會把方法名稱改寫成 `型別.方法名` 的符號：

```go
package main

type Counter struct{ n int }

//go:noinline
func (c *Counter) Inc() { c.n++ }

func main() {
	c := &Counter{}
	c.Inc()
}
```

```bash
go tool compile -S main.go | findstr "Inc"
```

```text
main.(*Counter).Inc STEXT nosplit size=... args=0x8 locals=0x0
	TEXT	main.(*Counter).Inc(SB), NOSPLIT|ABIInternal, $0-8
	INCQ	(AX)             // 接收者在 AX，就是第一個參數
	RET
```

`args=0x8` —— 一個指標大小的參數，就是接收者。

### 值接收者與指標接收者的成本

```go
type Big struct{ data [256]byte }

func (b Big) ByValue()   {} // 每次呼叫複製 256 位元組
func (b *Big) ByPointer() {} // 每次呼叫傳 8 位元組
```

判斷準則：

- **接收者需要修改狀態** → 一定用指標接收者。
- **型別很大**（超過幾個字組） → 用指標接收者。
- **型別內含 mutex 或其他不可複製的東西** → 一定用指標接收者。
- **小型不可變值**（`time.Time`、小 struct） → 值接收者，語意更清楚也避免 nil 問題。

!!! warning "同一型別不要混用兩種接收者"
    ```go
    func (c Counter) Value() int { return c.n }  // 值
    func (c *Counter) Inc()      { c.n++ }       // 指標
    ```

    這在語法上合法，但會造成方法集混亂（見 [型別檢查](typecheck.html#介面滿足結構化型別)），介面滿足的規則變得難以預測。**選一種，整個型別統一。** 只要有任何一個方法需要修改狀態，就全部用指標接收者。

---

## 方法值與方法運算式

這兩個容易搞混，而且成本不同。

```go
package main

import "fmt"

type Counter struct{ n int }

func (c *Counter) Inc() { c.n++ }

func main() {
	c := &Counter{}

	// ① 方法值（method value）：綁定了接收者
	f := c.Inc // 型別是 func()
	f()
	f()
	fmt.Println(c.n) // 2

	// ② 方法運算式（method expression）：接收者變成第一個參數
	g := (*Counter).Inc // 型別是 func(*Counter)
	g(c)
	fmt.Println(c.n) // 3
}
```

**方法值會配置記憶體。** 因為它要把接收者捕捉起來，實際上會產生一個閉包物件：

```bash
go build -gcflags="-m" ./main.go
```

```text
./main.go:14:7: c.Inc escapes to heap
```

在熱路徑上這是隱藏的配置來源。如果你發現 `allocs/op` 莫名其妙變高，檢查有沒有把方法當值傳來傳去。

**方法運算式不會配置**，因為它就是一個普通的靜態函式符號。

---

## 閉包怎麼實作

閉包就是「函式 + 捕捉的變數」。Go 的實作是一個 struct：

```text
type closure struct {
    fn  uintptr   // 函式碼的位址
    // 捕捉的變數依序排在後面
    v1  T1
    v2  T2
}
```

呼叫時，這個 struct 的位址放在 `DX` 暫存器（context 暫存器），函式本體透過 `DX` 存取捕捉的變數。

```go
package main

import "fmt"

func counter() func() int {
	n := 0            // 被捕捉 → 逃逸到堆積
	return func() int {
		n++
		return n
	}
}

func main() {
	c1 := counter()
	c2 := counter()
	fmt.Println(c1(), c1(), c1()) // 1 2 3
	fmt.Println(c2())             // 1 —— 每個閉包有自己的 n
}
```

```text
./main.go:6:2: moved to heap: n
./main.go:7:9: func literal escapes to heap
```

兩次配置：一次給 `n`，一次給閉包物件本身。

### 捕捉方式：按參照，不是按值

```go
package main

import "fmt"

func main() {
	x := 1
	f := func() { fmt.Println(x) }

	x = 2
	f() // 印出 2，不是 1
}
```

閉包捕捉的是**變數本身**，不是它當時的值。如果需要當時的值，明確傳參數：

```go
x := 1
f := func(v int) func() { return func() { fmt.Println(v) } }(x)
x = 2
f() // 1
```

!!! version "Go 1.22：迴圈變數終於是每輪一個"
    這是 Go 史上最著名的坑，Go 1.22 修好了：

    ```go
    for i := 0; i < 3; i++ {
        go func() { fmt.Println(i) }()
    }
    ```

    **Go 1.21 及之前**：整個迴圈只有一個 `i`，三個 goroutine 捕捉同一個變數，通常都印 3。
    **Go 1.22 起**：每一輪迭代建立新的 `i`，印出 0、1、2（順序不定）。

    這個改變由 `go.mod` 的 `go` 指令行閘門控制——宣告 `go 1.22` 以上才生效。詳見 [for 與 range](for-range.html)。

    順帶一提：`for ... range` 的迴圈變數也適用同一規則。

### 減少閉包配置

如果閉包沒有逃逸，編譯器可以把它配置在堆疊上：

```go
package main

func apply(nums []int, f func(int) int) {
	for i := range nums {
		nums[i] = f(nums[i])
	}
}

func main() {
	factor := 3
	nums := []int{1, 2, 3}
	// 這個閉包不逃逸（apply 不會保存它），可以在堆疊上
	apply(nums, func(n int) int { return n * factor })
	println(nums[0], nums[1], nums[2])
}
```

```text
./main.go:14:14: func literal does not escape
```

反之，如果 `apply` 把 `f` 存進某個結構或送進 channel，閉包就會逃逸。

---

## 呼叫成本一覽

把這一節整理成一張表（數字是數量級概念，不是精確值）：

| 呼叫形式 | 相對成本 | 說明 |
| --- | --- | --- |
| 被內聯的函式 | 0 | 沒有呼叫，直接展開 |
| 直接呼叫（靜態） | ~1 ns | `CALL` + 序言 |
| 方法呼叫（靜態） | ~1 ns | 跟一般函式一樣 |
| 閉包呼叫（不逃逸） | ~1–2 ns | 多一次 `DX` 間接存取 |
| 介面方法呼叫 | ~2–3 ns | 要透過 itab 間接跳轉，且無法內聯 |
| 反射呼叫 `Value.Call` | ~200+ ns | 要打包參數、動態分派 |
| cgo 呼叫 | ~30–50 ns | 要切換到系統堆疊（Go 1.26 已改善約 30%） |

最重要的一條資訊是**介面呼叫無法被內聯**。這通常比呼叫本身的開銷更重要——內聯失敗會連帶讓常數傳播、邊界檢查消除都做不了。

下一節就來看介面的實作。
