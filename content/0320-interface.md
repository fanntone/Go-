---
title: 介面 interface
slug: interface
part: p3
number: "3.2"
order: 320
summary: iface 與 eface 的兩字組結構、itab 怎麼建立與快取、nil 介面的經典陷阱，以及型別斷言與 switch 的成本。
updated: "1.26"
---

## 介面值就是兩個指標

Go 的介面在執行期是一個**兩個字組（16 位元組）**的結構。依介面有沒有方法，分成兩種。

### 有方法的介面：`iface`

```go
type iface struct {
	tab  *itab          // 型別 + 方法表
	data unsafe.Pointer // 指向實際資料
}
```

### 空介面 `any`：`eface`

```go
type eface struct {
	_type *_type         // 型別描述子
	data  unsafe.Pointer // 指向實際資料
}
```

差別只在第一個欄位：`eface` 沒有方法要查，所以直接放型別描述子就夠了。

<figure class="diagram"><svg viewBox="0 0 700 340" role="img" aria-label="iface 與 itab 的結構"><text class="d-t-b" x="15" y="20">var w io.Writer = os.Stdout</text><rect class="d-box-a" x="15" y="32" width="150" height="42" rx="5"/><text class="d-t-m d-mid" x="90" y="52">tab *itab</text><text class="d-t-s d-mid" x="90" y="68">8 位元組</text><rect class="d-box-a" x="165" y="32" width="150" height="42" rx="5"/><text class="d-t-m d-mid" x="240" y="52">data unsafe.Pointer</text><text class="d-t-s d-mid" x="240" y="68">8 位元組</text><rect class="d-box-w" x="15" y="110" width="290" height="176" rx="6"/><text class="d-t-b" x="28" y="132">itab（介面 + 具體型別 的配對，全域快取）</text><rect class="d-box" x="28" y="142" width="264" height="26" rx="3"/><text class="d-t-m" x="38" y="160">inter  *interfacetype  → io.Writer</text><rect class="d-box" x="28" y="170" width="264" height="26" rx="3"/><text class="d-t-m" x="38" y="188">_type  *_type          → *os.File</text><rect class="d-box" x="28" y="198" width="264" height="26" rx="3"/><text class="d-t-m" x="38" y="216">hash   uint32          型別斷言快篩</text><rect class="d-box-o" x="28" y="226" width="264" height="26" rx="3"/><text class="d-t-m" x="38" y="244">fun[0] → (*os.File).Write</text><rect class="d-box-o" x="28" y="254" width="264" height="26" rx="3"/><text class="d-t-m" x="38" y="272">fun[1] …（依介面方法排序）</text><rect class="d-box-o" x="360" y="110" width="325" height="72" rx="6"/><text class="d-t-b" x="373" y="132">實際資料</text><text class="d-t-m" x="373" y="154">os.Stdout（*os.File 值）</text><text class="d-t-s" x="373" y="172">介面只存指標；值型別裝箱時會配置一份到堆積</text><path class="d-line-a" d="M90 74 L90 106" marker-end="url(#ar11)"/><path class="d-line-a" d="M240 74 L240 92 L500 92 L500 106" marker-end="url(#ar11)"/><defs><marker id="ar11" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M0 0 L10 5 L0 10 z" style="fill:var(--accent)"/></marker></defs><line class="d-dash" x1="15" y1="300" x2="685" y2="300"/><text class="d-t-s" x="15" y="322">w.Write(b) 展開成：載入 w.tab → 取 fun[0] → 以 w.data 為接收者間接呼叫。兩次記憶體存取 + 一次間接跳轉。</text></svg><figcaption><b>介面呼叫的三步。</b>itab 的 <code>fun</code> 陣列就是方法表（類似 C++ 的 vtable），但 Go 的 itab 是 <b>(介面, 具體型別)</b> 的配對，而不是掛在型別上——因為一個型別可以滿足任意多個介面，而且是隱式的。</figcaption></figure>

---

## itab 從哪裡來

itab 的產生分兩種情況：

**① 編譯期就能確定的**，由連結器產生並放進 `itablinks` 區段。例如：

```go
var w io.Writer = os.Stdout // 編譯器知道是 (io.Writer, *os.File)
```

**② 執行期才確定的**，由 `runtime.getitab` 動態產生，並存進一個全域雜湊表快取。例如：

```go
func store(vals []any, w io.Writer) {
	for _, v := range vals {
		if s, ok := v.(fmt.Stringer); ok { // 具體型別要執行期才知道
			io.WriteString(w, s.String())
		}
	}
}
```

第一次遇到某個 (介面, 型別) 配對時，runtime 會走過具體型別的方法表，比對介面要求的方法，填好 `fun` 陣列。之後同樣的配對直接查快取。

!!! note "為什麼 Go 不需要 `implements` 宣告"
    因為 itab 是**配對**而非型別的固有屬性。C++/Java 的物件裡有一個 vptr 指向它的 vtable，vtable 的佈局在編譯類別時就固定了——所以必須事先宣告實作了哪些介面。

    Go 把方法表從物件搬到介面值裡。一個 `*os.File` 值本身沒有任何 vptr，是「裝進 `io.Writer`」這個動作產生了 itab。這就是隱式介面滿足能成立的技術基礎，代價是第一次裝箱時可能要動態建表。

---

## 裝箱：把值放進介面

介面的 `data` 是一個**指標**。所以把值型別放進介面時，需要一塊記憶體來存那個值。

```go
package main

import "fmt"

type Num int

func (n Num) String() string { return fmt.Sprint(int(n)) }

func main() {
	n := Num(42)
	var s fmt.Stringer = n // 裝箱：n 的複本要放到某處
	fmt.Println(s)
}
```

```bash
go build -gcflags="-m" ./main.go
```

```text
./main.go:11:22: n escapes to heap
```

**裝箱通常伴隨一次堆積配置**。這是 `interface{}`／`any` 在熱路徑上的主要成本，也是 `fmt.Println` 比 `println` 慢很多的原因之一。

### runtime 的兩個最佳化

**① 小整數快取。** runtime 有一個預先配置的 `staticuint64s` 陣列，存著 0–255 的值。裝箱一個小於 256 的整數時直接指過去，不配置：

```go
package main

import (
	"fmt"
	"testing"
)

func box(n int) any { return n }

func main() {
	r1 := testing.Benchmark(func(b *testing.B) {
		b.ReportAllocs()
		for i := 0; i < b.N; i++ {
			_ = box(42) // < 256，走快取
		}
	})
	r2 := testing.Benchmark(func(b *testing.B) {
		b.ReportAllocs()
		for i := 0; i < b.N; i++ {
			_ = box(100000) // 需要配置
		}
	})
	fmt.Println("small:", r1.MemString())
	fmt.Println("large:", r2.MemString())
}
```

```text
small:        0 B/op        0 allocs/op
large:        8 B/op        1 allocs/op
```

**② 指標型別直接放。** 如果具體型別本身就是指標（`*T`、`map`、`chan`、`func`），`data` 直接存那個指標，不需要額外配置。

**這是「傳指標進介面」的一個實際好處**：

```go
var w io.Writer = &myWriter{}  // 不配置（指標直接放進 data）
var s fmt.Stringer = myValue{} // 配置一次（值要複製到堆積）
```

---

## nil 介面的經典陷阱

這大概是 Go 最常見的 bug 之一。

```go
package main

import "fmt"

type MyError struct{ msg string }

func (e *MyError) Error() string { return e.msg }

// ✗ 有問題的函式
func doWork(fail bool) error {
	var err *MyError // 具體型別的 nil 指標
	if fail {
		err = &MyError{"炸了"}
	}
	return err // 不管 fail 是什麼，回傳的介面都不是 nil！
}

func main() {
	err := doWork(false)
	fmt.Println(err)          // <nil>
	fmt.Println(err == nil)   // false ← 這裡
	fmt.Printf("%T %v\n", err, err) // *main.MyError <nil>
}
```

### 為什麼

介面值是 `(型別, 資料)` 這一對。`err == nil` 只有在**兩個欄位都是零**時才成立。

`return err` 的時候發生了裝箱：

```text
tab  = *itab(error, *MyError)   ← 不是 nil！
data = nil                       ← 這個是 nil
```

型別欄位有值，所以整個介面不是 nil。

<figure class="diagram"><svg viewBox="0 0 700 200" role="img" aria-label="nil 介面與含 nil 指標的介面"><text class="d-t-b" x="15" y="22">情況 A：真正的 nil 介面　var err error</text><rect class="d-box-o" x="15" y="34" width="180" height="40" rx="4"/><text class="d-t-m d-mid" x="105" y="59">tab = nil</text><rect class="d-box-o" x="195" y="34" width="180" height="40" rx="4"/><text class="d-t-m d-mid" x="285" y="59">data = nil</text><text class="d-t-a" x="400" y="59">err == nil　→　true ✓</text><text class="d-t-b" x="15" y="112">情況 B：裝了 nil 指標的介面　var p *MyError = nil; var err error = p</text><rect class="d-box-d" x="15" y="124" width="180" height="40" rx="4"/><text class="d-t-m d-mid" x="105" y="149">tab = *itab(error,*MyError)</text><rect class="d-box-o" x="195" y="124" width="180" height="40" rx="4"/><text class="d-t-m d-mid" x="285" y="149">data = nil</text><text class="d-t-a" x="400" y="149">err == nil　→　false ✗</text><text class="d-t-s" x="15" y="190">兩個欄位都要是 nil，介面才等於 nil。這是規格明定的行為，不是 bug。</text></svg><figcaption><b>兩種「nil」。</b>情況 B 裡的介面「非 nil，但裝著一個 nil 指標」。呼叫 <code>err.Error()</code> 會進到方法本體（不會立刻 panic，因為方法接收者是指標），如果方法內部解參照 <code>e.msg</code> 才會 panic。</figcaption></figure>

### 三個正確寫法

```go
// ✓ 寫法一：宣告成 error 型別
func doWork1(fail bool) error {
	var err error // 直接用介面型別
	if fail {
		err = &MyError{"炸了"}
	}
	return err
}

// ✓ 寫法二：明確回傳 nil
func doWork2(fail bool) error {
	if fail {
		return &MyError{"炸了"}
	}
	return nil
}

// ✓ 寫法三：如果一定要用具體型別的區域變數，回傳時判斷
func doWork3(fail bool) error {
	var err *MyError
	if fail {
		err = &MyError{"炸了"}
	}
	if err == nil {
		return nil
	}
	return err
}
```

**寫法二最好** —— 早回傳（early return），沒有中間狀態。

`go vet` 有一個 `nilness` 分析可以抓部分這類問題，但不完整。最可靠的還是養成「函式簽章回傳 `error` 時，內部就不要用具體錯誤型別的變數」的習慣。

---

## 型別斷言與型別 switch

### 兩種斷言形式

```go
package main

import "fmt"

func main() {
	var v any = "hello"

	// ① 單值形式：失敗會 panic
	s := v.(string)
	fmt.Println(s)

	// ② 雙值形式：失敗回傳零值與 false
	n, ok := v.(int)
	fmt.Println(n, ok) // 0 false

	// 單值形式失敗的樣子
	defer func() { fmt.Println("recovered:", recover()) }()
	_ = v.(int)
}
```

```text
hello
0 false
recovered: interface conversion: interface {} is string, not int
```

**幾乎總是應該用雙值形式**，除非你能百分之百確定型別。

### 斷言的成本

斷言到**具體型別**很快——只要比對 `_type` 指標是否相同：

```text
CMPQ  AX, $type:string(SB)   // 一次指標比較
JNE   失敗路徑
```

斷言到**另一個介面**比較貴——要查 itab 快取，可能得動態建表：

```go
if s, ok := v.(fmt.Stringer); ok { // 需要 (fmt.Stringer, 具體型別) 的 itab
	_ = s
}
```

第一次遇到某個型別會走 `runtime.getitab`（有鎖），之後查快取。熱路徑上大量對不同型別做介面斷言，會看到 itab 查找出現在剖析結果裡。

### 型別 switch

```go
package main

import "fmt"

func describe(v any) string {
	switch x := v.(type) {
	case nil:
		return "nil"
	case int, int64:
		return fmt.Sprintf("整數 %v", x) // 注意：多型別 case 裡 x 仍是 any
	case string:
		return fmt.Sprintf("字串長度 %d", len(x)) // 單型別 case 裡 x 是 string
	case []byte:
		return fmt.Sprintf("位元組 %d 個", len(x))
	case error:
		return "錯誤：" + x.Error()
	case fmt.Stringer:
		return "Stringer：" + x.String()
	default:
		return fmt.Sprintf("其他 %T", x)
	}
}

func main() {
	fmt.Println(describe(42))
	fmt.Println(describe("hi"))
	fmt.Println(describe(nil))
	fmt.Println(describe(3.14))
}
```

```text
整數 42
字串長度 2
nil
其他 float64
```

三個要注意的地方：

1. **`case nil` 匹配的是「介面本身是 nil」**，不是「裝了 nil 指標」。
2. **多型別的 case 裡，變數維持原本的介面型別**。上面 `case int, int64` 裡的 `x` 型別是 `any`，不是 `int`。
3. **順序有意義**。介面 case（`error`、`fmt.Stringer`）要放在具體型別 case 後面，否則會先被攔截。

!!! tip "編譯器對型別 switch 的最佳化"
    如果 case 全都是具體型別且數量夠多，編譯器會產生一個依型別雜湊值查找的**跳轉表**，而不是逐一比較。這讓大型型別 switch 的成本接近 O(1)。

    混入介面型別的 case 會破壞這個最佳化，因為介面判定需要查 itab。效能敏感的地方，考慮把介面 case 拆到 `default` 分支裡另外處理。

---

## 常見錯誤與解法

介面的問題有兩類：**執行期才炸的**（nil 陷阱、比較 panic、斷言失敗）與**編譯不過但訊息不好懂的**（方法集）。兩類的根因都是「介面值其實是 (型別, 資料) 這一對」這件事沒有內化。

| 症狀 | 原因 | 解法 |
| --- | --- | --- |
| `err != nil` 但印出來是 `<nil>` | 裝了 nil 指標的介面不等於 nil | 函式內不要用具體錯誤型別的變數 |
| `X does not implement Y (method Z has pointer receiver)` | 方法集規則 | 傳 `&x` 而非 `x` |
| `panic: comparing uncomparable type` | 動態型別不可比較 | 別用 `==`，改用 `reflect.DeepEqual` 或 `go-cmp` |
| `panic: interface conversion` | 單值型別斷言失敗 | 一律用雙值形式 |
| 熱路徑效能不如預期 | 介面呼叫無法內聯 | 具體型別、泛型、或 PGO 去虛擬化 |
| 改介面就要動一堆檔案 | 介面定義在生產端且太大 | 在消費端定義最小介面 |

### ① 介面比較會在執行期 panic

這個很少人知道，但踩到很痛——**它編譯得過，只在執行期炸**：

```go
package main

import "fmt"

func main() {
	var a any = []int{1, 2}
	var b any = []int{1, 2}

	defer func() { fmt.Println("panic:", recover()) }()
	fmt.Println(a == b)
}
```

```text
panic: runtime error: comparing uncomparable type []int
```

介面的 `==` 會先比型別，型別相同才比值。但 **slice、map、func 是不可比較的型別**，比到值的時候 runtime 只能 panic。

更隱蔽的版本是**把含 slice 欄位的 struct 當 map 鍵**：

```go
package main

import "fmt"

type Key struct {
	ID   int
	Tags []string // ← 讓整個 struct 變成不可比較
}

func main() {
	// 直接用 map[Key]string 是編譯錯誤，但透過 any 就繞過檢查了
	m := map[any]string{}

	defer func() { fmt.Println("panic:", recover()) }()
	m[Key{1, []string{"a"}}] = "x"
}
```

```text
panic: runtime error: hash of unhashable type main.Key
```

**判斷準則**：只要動態型別可能含 slice、map、func，就不要用 `==`、不要當 map 鍵。要比較內容用 `reflect.DeepEqual`（測試裡建議用 `google/go-cmp`，錯誤訊息好得多）。

!!! tip "怎麼提早發現"
    這類問題編譯期抓不到。實務做法是**在型別上加一個編譯期斷言**，強制它可比較：

    ```go
    type Key struct {
        ID   int
        Tags []string
    }

    // 如果 Key 變成不可比較，這行會編譯失敗
    var _ = map[Key]struct{}{}
    ```

    把它放在定義旁邊，之後有人加了 slice 欄位就會立刻編譯不過，而不是上線後才 panic。

### ② 單值型別斷言

```go
s := v.(string)      // ✗ 型別不對就 panic
s, ok := v.(string)  // ✓ 失敗回傳零值與 false
```

**幾乎總是該用雙值形式**，除非你能百分之百確定型別（例如剛剛才自己裝進去的）。

處理外部輸入時特別重要：

```go
// ✗ JSON 裡的 id 是字串的話，直接 panic
func badParse(data []byte) int {
	var m map[string]any
	json.Unmarshal(data, &m)
	return int(m["id"].(float64))
}

// ✓
func goodParse(data []byte) (int, error) {
	var m map[string]any
	if err := json.Unmarshal(data, &m); err != nil {
		return 0, err
	}
	f, ok := m["id"].(float64) // JSON 數字一律是 float64
	if !ok {
		return 0, fmt.Errorf("id 不是數字，實際是 %T", m["id"])
	}
	return int(f), nil
}
```

錯誤訊息裡用 `%T` 印出實際型別，除錯時省很多時間。

### ③ 熱路徑上的介面：問題不是呼叫開銷

介面方法呼叫本身只比直接呼叫慢 1–2 奈秒。**真正的代價是它無法被內聯**——而內聯失敗會連帶讓常數傳播、邊界檢查消除全部失效。

```go
package main

import (
	"fmt"
	"testing"
)

type Adder interface{ Add(int) int }

type Impl struct{ n int }

func (i Impl) Add(x int) int { return i.n + x }

func viaInterface(a Adder, times int) int {
	sum := 0
	for i := 0; i < times; i++ {
		sum = a.Add(sum)
	}
	return sum
}

func viaConcrete(a Impl, times int) int {
	sum := 0
	for i := 0; i < times; i++ {
		sum = a.Add(sum) // 可以被內聯成單純的加法
	}
	return sum
}

func main() {
	impl := Impl{1}

	r1 := testing.Benchmark(func(b *testing.B) {
		for i := 0; i < b.N; i++ {
			_ = viaInterface(impl, 1000)
		}
	})
	r2 := testing.Benchmark(func(b *testing.B) {
		for i := 0; i < b.N; i++ {
			_ = viaConcrete(impl, 1000)
		}
	})

	fmt.Println("interface:", r1)
	fmt.Println("concrete: ", r2)
}
```

差距通常在數倍而非數十 %，取決於被內聯後編譯器還能做多少最佳化。

**但不要因此就不用介面。** 判準是：

- **這段程式碼每秒跑幾百萬次嗎？** 不是的話，可讀性遠比這點差距重要。
- **量測過嗎？** 用 `-gcflags="-m"` 確認內聯真的被擋住、用 pprof 確認它真的是熱點。
- **能用 PGO 嗎？** Go 1.21+ 的推測式去虛擬化能在熱點自動把介面呼叫變回直接呼叫，見 [PGO](escape-inline.html#pgoprofile-guided-optimization)。

### ④ 過早抽象：只有一個實作就定介面

```go
// ✗ 只有一個實作，介面純粹是額外的一層
type UserRepository interface {
	Get(ctx context.Context, id int64) (*User, error)
	Create(ctx context.Context, u *User) error
	Update(ctx context.Context, u *User) error
	Delete(ctx context.Context, id int64) error
	List(ctx context.Context, filter Filter) ([]*User, error)
}

type postgresUserRepository struct{ db *sql.DB } // 唯一的實作
```

這種寫法從 Java/C# 帶過來的人特別容易寫。它的問題是：

- 跳到定義會跳到介面，還要再找一次實作
- 加一個方法要改兩個地方
- 為了「可以換成 MySQL」而抽象，但那一天通常不會來

**Go 的慣例是反過來的**：先寫具體型別，等到**真的出現第二個實作或測試需要替換**時，再在**使用端**定義最小介面：

```go
// repository 套件：直接提供具體型別
package repository

type UserRepo struct{ db *sql.DB }

func New(db *sql.DB) *UserRepo { ... }
func (r *UserRepo) Get(ctx context.Context, id int64) (*User, error) { ... }

// report 套件：我只需要讀，就只定義讀
package report

type userGetter interface {
	Get(ctx context.Context, id int64) (*User, error)
}

func Generate(ctx context.Context, g userGetter) error { ... }
```

好處：`report` 不需要 import `repository`，測試時傳一個只有 `Get` 的假物件就好，而且 `repository` 加新方法完全不影響 `report`。

### ⑤ 回傳介面

```go
// ✗ 呼叫端拿不到具體型別的其他方法，之後加方法還是破壞性變更
func NewClient(addr string) Client { ... }

// ✓ 回傳具體型別
func NewClient(addr string) *Client { ... }
```

Go 諺語：**接受介面，回傳具體型別**。

回傳介面唯一合理的時機是「真的有多個實作、而呼叫端不該知道是哪一個」——例如工廠函式依設定回傳不同的儲存後端。

!!! warning "回傳介面還有 nil 陷阱"
    ```go
    func New() Storage {
        var s *S3Storage // nil
        return s         // ✗ 回傳的介面不是 nil
    }
    ```

    跟 [nil 介面陷阱](#nil-介面的經典陷阱) 是同一個問題。回傳具體型別 `*S3Storage` 就不會有這件事——`nil` 就是 `nil`。

---

## 介面的設計建議

技術細節之外，幾條實務原則：

### 介面要小

```go
// ✓ 標準庫的典範
type Reader interface{ Read(p []byte) (n int, err error) }
type Writer interface{ Write(p []byte) (n int, err error) }
type Closer interface{ Close() error }

// 需要組合時再組合
type ReadWriteCloser interface {
	Reader
	Writer
	Closer
}
```

Go 諺語：**「介面越大，抽象越弱。」** 單方法介面最容易被滿足、最容易被替換、最容易寫測試假物件。

### 在消費端定義介面，不在生產端

```go
// ✗ 常見的錯誤：在實作套件定義介面
package storage
type Storage interface {   // 定義了一個大介面，包含所有方法
	Get(k string) ([]byte, error)
	Put(k string, v []byte) error
	Delete(k string) error
	List(prefix string) ([]string, error)
	// ... 20 個方法
}
type S3Storage struct{}    // 實作它

// ✓ 正確：在使用端定義自己需要的最小介面
package report
type reader interface {    // 我只需要讀
	Get(k string) ([]byte, error)
}
func Generate(r reader) error { ... }
```

這樣 `report` 套件不需要 import `storage`，測試時傳一個只有 `Get` 的假物件就好。這是 Go 跟 Java/C# 最不一樣的地方之一。

### 回傳具體型別，接受介面

```go
// ✓ 建構函式回傳具體型別，呼叫端保有全部能力
func NewClient(addr string) *Client { ... }

// ✓ 函式參數接受介面，保持彈性
func Process(r io.Reader) error { ... }
```

回傳介面會限制呼叫端能用的方法，也讓之後加新方法變成破壞性變更。

### 什麼時候不要用介面

- **只有一個實作，而且看不到第二個的需求。** 為了「將來可能」而抽象，通常是浪費。
- **熱路徑上。** 介面呼叫無法被內聯，這往往比呼叫開銷本身更重要。
- **純資料傳遞。** 用 struct。

---

下一節談反射。它建立在介面的 `_type` 描述子之上，是同一套機制的延伸。
