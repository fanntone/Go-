---
title: 反射 reflect
slug: reflect
part: p3
number: "3.3"
order: 330
summary: reflect.Type 與 reflect.Value 怎麼從介面來、可設定性（settability）的三個條件、struct tag 的解析，以及反射的真實成本。
updated: "1.26"
---

## 反射的三條定律

Rob Pike 在 2011 年的一篇文章裡整理了三條規則，到現在仍然是理解 `reflect` 最好的框架。

**① 從介面值可以得到反射物件。**
**② 從反射物件可以還原成介面值。**
**③ 要修改反射物件，它必須是「可設定的（settable）」。**

第三條是絕大多數 `reflect` 錯誤的來源。

---

## 定律一：介面 → 反射物件

`reflect.TypeOf` 與 `reflect.ValueOf` 的參數型別都是 `any`。這不是巧合——**反射的輸入永遠是一個介面值**，它拆的就是上一節講的 `eface` 兩欄位結構。

```go
package main

import (
	"fmt"
	"reflect"
)

func main() {
	x := 3.14

	t := reflect.TypeOf(x)  // 拿的是 eface 的 _type 欄位
	v := reflect.ValueOf(x) // 拿的是整個 eface

	fmt.Println(t)             // float64
	fmt.Println(t.Kind())      // float64
	fmt.Println(v)             // 3.14
	fmt.Println(v.Kind())      // float64
	fmt.Println(v.Float())     // 3.14
	fmt.Println(v.Type() == t) // true
}
```

### `Type` 與 `Kind` 的差別

這兩個很容易混淆，但差別很重要：

```go
package main

import (
	"fmt"
	"reflect"
)

type Celsius float64
type Point struct{ X, Y int }

func main() {
	for _, v := range []any{
		Celsius(36.5),
		Point{1, 2},
		&Point{1, 2},
		[]int{1, 2},
		map[string]int{},
		make(chan int),
		func() {},
	} {
		t := reflect.TypeOf(v)
		fmt.Printf("%-20s Kind=%-10s\n", t.String(), t.Kind())
	}
}
```

```text
main.Celsius         Kind=float64   
main.Point           Kind=struct    
*main.Point          Kind=ptr       
[]int                Kind=slice     
map[string]int       Kind=map       
chan int             Kind=chan      
func()               Kind=func      
```

- **`Type`** 是完整的型別身分，包含套件路徑與名稱。`Celsius` 與 `float64` 是不同的 `Type`。
- **`Kind`** 是底層的分類，只有 26 種（`Bool`、`Int`…`Struct`、`Ptr`、`Slice`…）。`Celsius` 的 Kind 就是 `Float64`。

寫通用程式碼時**幾乎總是用 `Kind` 分支**，因為你不可能列舉所有可能的具名型別。

---

## 定律二：反射物件 → 介面

```go
package main

import (
	"fmt"
	"reflect"
)

func main() {
	v := reflect.ValueOf(42)

	// Interface() 回傳 any，要自己斷言
	i := v.Interface()
	n := i.(int)
	fmt.Println(n + 1) // 43

	// 也有型別特定的取值方法（更快，但 Kind 不對會 panic）
	fmt.Println(v.Int()) // 42
}
```

`Value.Interface()` 是反射與一般程式碼之間的橋。注意它回傳 `any`，會有裝箱成本。

---

## 定律三：可設定性

```go
package main

import (
	"fmt"
	"reflect"
)

func main() {
	x := 3.14

	v := reflect.ValueOf(x)
	fmt.Println(v.CanSet()) // false

	defer func() { fmt.Println("panic:", recover()) }()
	v.SetFloat(2.71) // panic
}
```

```text
false
panic: reflect: reflect.Value.SetFloat using unaddressable value
```

### 為什麼不能設定

因為 `reflect.ValueOf(x)` 拿到的是 **`x` 的一份複本**。`ValueOf` 的參數是 `any`，傳進去的當下就已經複製了。就算能修改，改到的也是那份複本，原本的 `x` 完全不受影響——這種靜默失效比 panic 糟糕，所以 `reflect` 直接禁止。

### 正確做法：傳指標，再 `Elem()`

```go
package main

import (
	"fmt"
	"reflect"
)

func main() {
	x := 3.14

	p := reflect.ValueOf(&x) // Kind 是 Ptr
	fmt.Println(p.CanSet())  // false —— 指標本身仍是複本

	v := p.Elem()            // 解參照，指向真正的 x
	fmt.Println(v.CanSet())  // true

	v.SetFloat(2.71)
	fmt.Println(x) // 2.71 ← 原本的變數真的被改了
}
```

### 可設定的三個條件

一個 `reflect.Value` 要可設定，必須同時滿足：

1. **可定址（addressable）** —— 透過指標的 `Elem()`、slice 的元素、可定址 struct 的欄位取得。
2. **不是未匯出的欄位** —— 小寫開頭的欄位即使可定址也不能設定。
3. **不是從 map 直接取出的元素** —— map 元素不可定址（原因見 [map](map.html#不能取-map-元素的位址)）。

```go
package main

import (
	"fmt"
	"reflect"
)

type User struct {
	Name string // 匯出
	age  int    // 未匯出
}

func main() {
	u := User{"小明", 30}
	v := reflect.ValueOf(&u).Elem()

	fmt.Println(v.Field(0).CanSet()) // true  —— Name
	fmt.Println(v.Field(1).CanSet()) // false —— age 未匯出

	v.Field(0).SetString("小華")
	fmt.Println(u) // {小華 30}

	// slice 元素可設定
	s := []int{1, 2, 3}
	sv := reflect.ValueOf(s) // 注意：不需要傳 &s
	sv.Index(0).SetInt(99)   // slice 的元素本來就可定址
	fmt.Println(s)           // [99 2 3]

	// map 元素不可設定
	m := map[string]int{"a": 1}
	mv := reflect.ValueOf(m)
	fmt.Println(mv.MapIndex(reflect.ValueOf("a")).CanSet()) // false
	// 要改 map 只能整個 SetMapIndex
	mv.SetMapIndex(reflect.ValueOf("a"), reflect.ValueOf(99))
	fmt.Println(m) // map[a:99]
}
```

注意 slice 那個例子：**不需要傳 `&s`**。因為 slice 標頭裡的 `array` 指標指向的元素本來就在別的地方，`Index(i)` 拿到的是真正的元素位址。

---

## struct tag：反射最常見的用途

struct tag 是附在欄位後面的字串字面值，編譯器完全不解讀它，只是原封不動存進型別中繼資料。真正解讀它的是使用反射的函式庫。

```go
package main

import (
	"fmt"
	"reflect"
)

type User struct {
	ID    int    `json:"id" db:"user_id" validate:"required"`
	Name  string `json:"name,omitempty" db:"name" validate:"min=2,max=50"`
	Email string `json:"email" db:"email" validate:"email"`
	pw    string `json:"-"`
}

func main() {
	t := reflect.TypeOf(User{})

	for i := 0; i < t.NumField(); i++ {
		f := t.Field(i)
		fmt.Printf("%-6s exported=%-5v json=%-16q db=%-10q validate=%q\n",
			f.Name,
			f.IsExported(),
			f.Tag.Get("json"),
			f.Tag.Get("db"),
			f.Tag.Get("validate"),
		)
	}
}
```

```text
ID     exported=true  json="id"             db="user_id"  validate="required"
Name   exported=true  json="name,omitempty" db="name"     validate="min=2,max=50"
Email  exported=true  json="email"          db="email"    validate="email"
pw     exported=false json="-"              db=""         validate=""
```

### tag 的格式規則

慣例格式是 `` `key1:"value1" key2:"value2"` ``：

- 鍵與值之間**不能有空格**（`json: "id"` 是錯的，`Get("json")` 會回傳空字串）。
- 多組 tag 之間用**一個空格**分隔。
- 值本身用雙引號包起來。
- 格式錯誤**不會有編譯錯誤**，只會安靜地失效。

!!! tip "用 `go vet` 抓 tag 錯誤"
    `go vet` 的 `structtag` 檢查會抓出格式錯誤與重複的 tag 鍵：

    ```bash
    go vet ./...
    ```

    這是少數能在編譯期抓到 tag 問題的方法，建議放進 CI。

---

## 反射的成本

反射慢，但慢多少？做個實測：

```go
package main

import (
	"fmt"
	"reflect"
	"testing"
)

type User struct {
	Name string
	Age  int
}

func direct(u *User) string { return u.Name }

func viaReflect(u *User) string {
	return reflect.ValueOf(u).Elem().Field(0).String()
}

func viaReflectCached(v reflect.Value) string {
	return v.Field(0).String()
}

func main() {
	u := &User{"小明", 30}
	cached := reflect.ValueOf(u).Elem()

	bench := func(name string, f func()) {
		r := testing.Benchmark(func(b *testing.B) {
			b.ReportAllocs()
			for i := 0; i < b.N; i++ {
				f()
			}
		})
		fmt.Printf("%-18s %s  %s\n", name, r, r.MemString())
	}

	bench("direct", func() { _ = direct(u) })
	bench("reflect", func() { _ = viaReflect(u) })
	bench("reflect(cached)", func() { _ = viaReflectCached(cached) })
}
```

典型結果：

```text
direct             1000000000    0.29 ns/op       0 B/op    0 allocs/op
reflect              38412561   31.2  ns/op       0 B/op    0 allocs/op
reflect(cached)     215043210    5.6  ns/op       0 B/op    0 allocs/op
```

約 **100 倍**的差距。但注意第三行：**如果把 `reflect.Value` 快取起來重複使用，成本降到 20 倍**。

### 成本來自哪裡

| 來源 | 說明 |
| --- | --- |
| 裝箱 | `ValueOf(x)` 的參數是 `any`，值型別要配置 |
| 型別檢查 | 每個操作都要驗證 Kind 對不對 |
| 無法內聯 | 反射呼叫是動態的，編譯器優化不了 |
| 額外的間接層 | 每次 `Field(i)` 都要算偏移量 |
| `Value.Call` 特別貴 | 要打包參數成 `[]Value`、動態建立堆疊影格 |

### 怎麼降低成本

**① 快取 `reflect.Type` 的分析結果。** 這是所有正經反射函式庫（`encoding/json`、ORM、驗證器）的標準做法：

```go
package main

import (
	"fmt"
	"reflect"
	"sync"
)

type fieldInfo struct {
	Index int
	Name  string
	JSON  string
}

var cache sync.Map // reflect.Type → []fieldInfo

func fieldsOf(t reflect.Type) []fieldInfo {
	if v, ok := cache.Load(t); ok {
		return v.([]fieldInfo)
	}

	var out []fieldInfo
	for i := 0; i < t.NumField(); i++ {
		f := t.Field(i)
		if !f.IsExported() {
			continue
		}
		out = append(out, fieldInfo{i, f.Name, f.Tag.Get("json")})
	}

	cache.Store(t, out)
	return out
}

type User struct {
	Name string `json:"name"`
	Age  int    `json:"age"`
	pw   string
}

func main() {
	t := reflect.TypeOf(User{})
	fmt.Println(fieldsOf(t)) // 第一次：走反射
	fmt.Println(fieldsOf(t)) // 第二次：查快取
}
```

型別分析（欄位數、tag、偏移量）只需要做一次，之後每個實例都能重用。

**② 考慮改用程式碼生成。** 如果反射是你的效能瓶頸，`go:generate` 加上程式碼產生器可以完全消除反射。這是 `easyjson`、`protobuf-go`、`sqlc` 這類工具的做法。詳見 [程式碼生成](codegen.html)。

**③ 泛型能取代部分反射用途。** 以前要用反射處理「任意型別的 slice」，現在用泛型就好，而且是編譯期展開：

```go
// 以前
func Map(in any, f any) any { /* 一大堆反射 */ }

// 現在
func Map[T, U any](in []T, f func(T) U) []U {
	out := make([]U, len(in))
	for i, v := range in {
		out[i] = f(v)
	}
	return out
}
```

---

## 動態呼叫函式

`reflect` 可以呼叫任意函式，這是 RPC 框架與依賴注入容器的基礎。

```go
package main

import (
	"fmt"
	"reflect"
)

func Greet(name string, times int) (string, error) {
	if times <= 0 {
		return "", fmt.Errorf("times 必須為正數，得到 %d", times)
	}
	out := ""
	for i := 0; i < times; i++ {
		out += "Hello, " + name + "! "
	}
	return out, nil
}

func main() {
	fn := reflect.ValueOf(Greet)
	t := fn.Type()

	// 先看看簽章
	fmt.Printf("參數 %d 個，回傳 %d 個\n", t.NumIn(), t.NumOut())
	for i := 0; i < t.NumIn(); i++ {
		fmt.Printf("  in[%d]: %s\n", i, t.In(i))
	}

	// 呼叫
	args := []reflect.Value{
		reflect.ValueOf("Go"),
		reflect.ValueOf(2),
	}
	results := fn.Call(args)

	fmt.Println(results[0].String())
	if err := results[1].Interface(); err != nil {
		fmt.Println("錯誤:", err)
	}

	// 錯誤路徑
	bad := fn.Call([]reflect.Value{reflect.ValueOf("Go"), reflect.ValueOf(0)})
	fmt.Println("錯誤:", bad[1].Interface())
}
```

```text
參數 2 個，回傳 2 個
  in[0]: string
  in[1]: int
Hello, Go! Hello, Go! 
錯誤: times 必須為正數，得到 0
```

!!! danger "`Call` 的每個細節都會 panic"
    參數數量不對、型別不對、`Value` 不合法——全部都是 panic 而非回傳錯誤。使用 `reflect.Call` 的程式碼**必須**包在 `recover` 裡，或事先做完整的簽章驗證。

    另外 `Call` 每次都會配置一個 `[]Value` 給參數、一個給回傳值，成本大約是直接呼叫的數百倍。

---

## 什麼時候該用反射

反射是強大但昂貴的工具。判斷準則：**當型別資訊只有在執行期才知道時**。

**適合的場景：**

| 場景 | 例子 |
| --- | --- |
| 序列化／反序列化 | `encoding/json`、YAML、protobuf |
| ORM 與資料庫映射 | 把 `sql.Rows` 掃進任意 struct |
| 設定檔綁定 | `viper`、環境變數 → struct |
| 驗證框架 | 依 tag 檢查欄位 |
| 依賴注入容器 | 依型別自動組裝 |
| 測試輔助 | `reflect.DeepEqual`、產生假資料 |
| 泛型無法表達的多型 | 需要走訪任意 struct 的欄位 |

**不適合的場景：**

- **有具體型別可用時。** 直接寫，不要為了「通用」而反射。
- **熱路徑。** 100 倍的差距在迴圈裡會非常明顯。
- **泛型能解決的問題。** Go 1.18 之後，很多以前需要反射的通用容器與演算法都能用泛型表達。
- **只是想少寫幾行 if。** 可讀性的損失遠大於收益。

!!! warning "反射會讓連結器的死碼消除失效"
    如果程式使用了 `reflect.Value.Method` 或 `reflect.Type.Method`，連結器無法判斷哪些方法會被呼叫，只好**保留所有型別的所有方法**。這會讓執行檔明顯變大。

    大量使用反射的框架，產出的執行檔常常比預期大好幾 MB，原因就在這裡。

### `reflect.DeepEqual` 的注意事項

```go
package main

import (
	"fmt"
	"reflect"
)

func main() {
	var a []int          // nil
	b := []int{}         // 空但非 nil

	fmt.Println(reflect.DeepEqual(a, b)) // false ← 注意！

	m1 := map[string][]int{"x": {1, 2}}
	m2 := map[string][]int{"x": {1, 2}}
	fmt.Println(reflect.DeepEqual(m1, m2)) // true

	// 函式永遠不相等（除非兩者都是 nil）
	f := func() {}
	fmt.Println(reflect.DeepEqual(f, f)) // false
}
```

`DeepEqual` 在測試裡很方便，但有幾個陷阱：nil slice ≠ 空 slice、函式永遠不等、`time.Time` 因為內部有單調時鐘欄位可能誤判。

測試中建議改用 `github.com/google/go-cmp/cmp`，它的錯誤訊息會直接指出差異在哪，也支援自訂比較規則。

---

Part 3 結束。下一部分回到語言表層，看那些「看起來像語法、其實是編譯器改寫」的關鍵字。
