---
title: 逃逸分析、內聯與 PGO
slug: escape-inline
part: p1
number: "1.5"
order: 150
summary: 編譯器如何決定變數放堆疊還是堆積、函式能不能被內聯，以及 profile 引導最佳化怎麼用。
updated: "1.26"
---

## 這一節為什麼重要

前面幾節談的是「編譯器怎麼運作」。這一節談的是「編譯器的哪些決定會直接影響你程式的效能」。

兩個決定：

- **逃逸分析** 決定變數配置在**堆疊（stack）**還是**堆積（heap）**。堆疊配置幾乎免費（移動一下堆疊指標），堆積配置要走記憶體配置器，而且之後要靠 GC 回收。
- **內聯** 決定函式呼叫要不要真的跳轉。內聯不只省掉呼叫開銷，更重要的是它讓後續的最佳化（常數傳播、逃逸分析）能跨函式邊界看到更多東西。

這兩件事互相影響，而且**內聯先做**——內聯之後逃逸分析才跑，所以被內聯的函式裡的變數，有機會不逃逸。

---

## 逃逸分析

### 基本規則

Go 的規則可以濃縮成一句話：**如果編譯器不能證明「這個變數的生命週期不超過建立它的函式」，就把它配置在堆積。**

這跟 C++ 完全相反。C++ 預設放堆疊，你要 `new` 才上堆積，回傳區域變數的位址是未定義行為。Go 讓編譯器自己判斷，所以下面這段在 Go 裡完全安全：

```go
package main

import "fmt"

type Config struct {
	Name string
	Port int
}

// 回傳區域變數的位址 —— 在 C 是災難，在 Go 完全正確
func NewConfig() *Config {
	c := Config{Name: "api", Port: 8080}
	return &c // 編譯器發現 c 逃逸了，改配置在堆積
}

func main() {
	fmt.Println(NewConfig())
}
```

```bash
go build -gcflags="-m" ./main.go
```

```text
./main.go:12:2: moved to heap: c
```

### 六種常見的逃逸情況

```go
package main

import "fmt"

type T struct{ n int }

// ① 回傳指標 → 逃逸
func f1() *T {
	t := T{1}
	return &t
}

// ② 傳給 interface{} → 逃逸（最容易被忽略的一個）
func f2() {
	t := T{2}
	fmt.Println(t) // fmt.Println 的參數是 ...any
}

// ③ 送進 channel → 逃逸
func f3(ch chan *T) {
	t := T{3}
	ch <- &t
}

// ④ 大小在編譯期不確定 → 逃逸
func f4(n int) []int {
	return make([]int, n)
}

// ⑤ 被逃逸的閉包捕捉 → 逃逸
func f5() func() int {
	n := 5
	return func() int { return n }
}

// ⑥ 存進逃逸物件的欄位 → 逃逸
func f6(dst *[]*T) {
	t := T{6}
	*dst = append(*dst, &t)
}

func main() {
	_ = f1()
	f2()
	_ = f4(10)
	_ = f5()
}
```

```text
./main.go:10:2: moved to heap: t
./main.go:16:2: moved to heap: t
./main.go:16:14: ... argument does not escape
./main.go:22:2: moved to heap: t
./main.go:27:9: make([]int, n) escapes to heap
./main.go:32:2: moved to heap: n
./main.go:33:9: func literal escapes to heap
./main.go:38:2: moved to heap: t
```

第 ② 個特別值得注意。`fmt.Println(t)` 看起來人畜無害，但 `t` 被塞進 `any` 介面，而 `fmt` 內部會用反射存取它，編譯器無法證明它不會被留下來，於是逃逸。**這就是為什麼在熱路徑上呼叫 `fmt` 系列函式代價不小。**

### 逃逸分析的真實規則：不是「大小」

網路上常見的說法是「大物件會逃逸到堆積」，這**不準確**。真正的規則是**生命週期**，大小只是次要條件。

不過確實有大小上限：預設情況下，超過 **10 MB** 左右的區域變數（`maxStackVarSize`）或超過 **64 KB** 的隱式配置（`maxImplicitStackVarSize`，例如 `new(T)`、複合字面值）會被移到堆積，因為 goroutine 堆疊放不下。

```go
func big() {
	var arr [1 << 20]byte // 1 MB，仍在堆疊上
	_ = arr
}

func tooBig() {
	var arr [1 << 24]byte // 16 MB，超過上限 → 移到堆積
	_ = arr
}
```

### `make` 的兩種情況

```go
func known() []int {
	s := make([]int, 8) // 常數大小、不逃逸 → 直接在堆疊上開一塊
	s[0] = 1
	return nil
}

func unknown(n int) {
	s := make([]int, n) // n 是變數 → 編譯期不知道大小 → 堆積
	_ = s
}
```

!!! version "Go 1.26：更多 slice 可以放堆疊"
    Go 1.26 讓編譯器在更多情況下能把 slice 的底層陣列（backing store）配置在堆疊上。原本某些會逃逸的模式現在不會了，直接減少堆積配置與 GC 壓力。

    如果你有針對配置次數寫的基準測試（`-benchmem` 的 `allocs/op`），升到 1.26 後數字可能會變小，這是預期行為。

### 為什麼不能「強制放堆疊」

Go 沒有提供 `//go:stackalloc` 之類的指示詞，這是刻意的：**逃逸分析是記憶體安全的一部分**。如果讓你強制一個真的會逃逸的變數放堆疊，函式回傳後那個指標就懸空了，Go 的記憶體安全保證會被打破。

想減少堆積配置，正確做法是**改寫程式碼讓它不逃逸**：

```go
// ✗ 每次呼叫都配置一個新的 buffer
func format(vals []int) string {
	var sb strings.Builder
	for _, v := range vals {
		fmt.Fprintf(&sb, "%d,", v)
	}
	return sb.String()
}

// ✓ 用 sync.Pool 或呼叫端提供的 buffer
func formatInto(dst []byte, vals []int) []byte {
	for _, v := range vals {
		dst = strconv.AppendInt(dst, int64(v), 10)
		dst = append(dst, ',')
	}
	return dst
}
```

---

## 內聯

### 成本模型

Go 的內聯決策基於一個簡單的**成本預算**：走訪函式的 IR，每個節點記一點成本，總和不超過 **80** 就可以內聯（`inlineMaxBudget`）。

有些構造成本特別高，或直接讓函式**不可內聯**：

| 情況 | 結果 |
| --- | --- |
| 函式太大（成本 > 80） | 不內聯 |
| 包含 `for` / `range` 迴圈 | Go 1.20 前不可內聯；之後在成本內可以 |
| 包含 `select` | 不可內聯 |
| 包含 `defer`（部分情況） | 成本很高 |
| 包含 `recover` | 不可內聯 |
| 包含 `go` 陳述式 | 成本很高 |
| 遞迴函式 | 不內聯（Go 1.22 起支援有限的自遞迴內聯） |
| 呼叫 `runtime` 的某些特殊函式 | 不可內聯 |
| 標記 `//go:noinline` | 明確禁止 |

觀察方式：

```bash
go build -gcflags="-m -m" ./main.go 2>&1 | findstr "inline"
```

```text
./main.go:5:6: can inline add with cost 4 as: func(int, int) int { return a + b }
./main.go:9:6: cannot inline heavy: function too complex: cost 132 exceeds budget 80
./main.go:20:13: inlining call to add
```

`cost 132 exceeds budget 80` 直接告訴你差多少。

### 內聯的連鎖效應

內聯真正的價值不在省下 `CALL` 指令，而在**打開最佳化的視野**。

```go
package main

//go:noinline
func lenNoInline(s []int) int { return len(s) }

func lenInline(s []int) int { return len(s) }

func withInline(s []int) int {
	if lenInline(s) == 0 {
		return 0
	}
	return s[0] // 邊界檢查可以被消除
}

func withoutInline(s []int) int {
	if lenNoInline(s) == 0 {
		return 0
	}
	return s[0] // 邊界檢查留著：編譯器不知道 lenNoInline 回傳什麼
}

func main() {
	println(withInline([]int{1}), withoutInline([]int{1}))
}
```

```bash
go build -gcflags="-d=ssa/check_bce/debug=1" ./main.go
```

```text
./main.go:20:9: Found IsInBounds
```

只有 `withoutInline` 那一行留下邊界檢查。內聯讓編譯器看穿了 `lenInline`，於是 `prove` pass 能推導出 `len(s) > 0`，索引 `s[0]` 一定安全。

### 中端內聯與跨套件內聯

!!! version "Go 1.20 起：中端內聯（mid-stack inlining）"
    早期的 Go 只能內聯「葉子函式」（不呼叫其他函式的函式）。這讓很多薄包裝層（wrapper）無法被優化掉。

    Go 逐步放寬了這個限制，現在**呼叫其他函式的函式也能被內聯**（只要總成本在預算內），這叫中端內聯。它讓「小函式 → 呼叫小函式 → 呼叫小函式」這種常見的抽象層次能被完全攤平。

    另外，Unified IR（Go 1.21 預設）讓**跨套件內聯**變得更完整，包含泛型函式。

### 什麼時候該用 `//go:noinline`

幾乎不需要。三個合理場景：

1. **寫基準測試**，要確保被測函式真的被呼叫。
2. **除錯**，內聯後的堆疊追蹤比較難讀（雖然 Go 會保留內聯資訊）。
3. **驗證某個效能問題是不是內聯造成的**。

---

## PGO：Profile-Guided Optimization

!!! version "Go 1.21 起正式可用"
    PGO 在 Go 1.20 是預覽，Go 1.21 正式支援。官方測得的典型收益是 **2%–7%** 的 CPU 節省，實際數字依工作負載差很多。

### 原理

編譯器的靜態啟發式規則不知道**哪些程式碼真的是熱點**。PGO 的作法是：先跑一次真實負載收集 CPU 剖析檔，再把它餵給編譯器，讓編譯器對熱點函式放寬內聯預算、對熱點介面呼叫做推測式去虛擬化、調整基本區塊的排列以改善指令快取命中。

### 三步驟

**① 收集剖析檔。** 在**正式環境**的真實負載下收集，這點很重要——用測試資料收集出來的剖析檔會誤導編譯器。

```go
package main

import (
	"log"
	"net/http"
	_ "net/http/pprof"
)

func main() {
	go func() { log.Println(http.ListenAndServe("localhost:6060", nil)) }()
	// ... 你的服務
}
```

```bash
curl -o cpu.pprof "http://localhost:6060/debug/pprof/profile?seconds=60"
```

**② 放進專案根目錄，命名為 `default.pgo`。**

```bash
cp cpu.pprof ./default.pgo
```

`go build` 會**自動偵測**主套件目錄下的 `default.pgo` 並啟用 PGO，不需要額外旗標。想指定其他檔案：

```bash
go build -pgo=./profiles/prod-2026-07.pprof -o app .
```

想關掉：

```bash
go build -pgo=off -o app .
```

**③ 驗證有生效。**

```bash
go build -gcflags="-m" . 2>&1 | findstr "PGO"
```

```text
./handler.go:42:6: can inline processRequest with cost 156 as: ... (hot, PGO budget 2000)
./handler.go:88:14: PGO devirtualizing interface call to (*json.Encoder).Encode
```

### 實務建議

- **把 `default.pgo` 提交進版控。** 它是建置輸入的一部分，就像 `go.sum`。
- **定期更新，但不用太頻繁。** 每季或每次大改版更新一次即可。過期的剖析檔仍然有用（Go 保證舊剖析檔不會讓效能倒退），只是收益遞減。
- **合併多份剖析檔**以涵蓋不同流量模式：

```bash
go tool pprof -proto peak.pprof offpeak.pprof > default.pgo
```

- **注意編譯時間會變長。** PGO 建置通常慢 5–15%，因為要做更多內聯。

---

## 一張決策表

把這一節收斂成實用的檢查清單：

| 症狀 | 檢查什麼 | 工具 |
| --- | --- | --- |
| GC 太頻繁、`allocs/op` 高 | 哪些變數逃逸了 | `-gcflags="-m"` |
| 函式呼叫開銷明顯 | 有沒有被內聯、為什麼沒有 | `-gcflags="-m -m"` |
| 迴圈比預期慢 | 邊界檢查有沒有被消除 | `-d=ssa/check_bce/debug=1` |
| 介面呼叫是熱點 | 能不能去虛擬化 | PGO |
| 想知道最佳化到底做了什麼 | 逐 pass 檢視 | `GOSSAFUNC=fn` |

---

到這裡，編譯器的前端與中端都走完了。下一節看最後兩步：機器碼生成與連結，以及執行檔裡到底有什麼。
