---
title: 編譯流程總覽
slug: pipeline
part: p1
number: "1.1"
order: 110
summary: 從 go build 到可執行檔，編譯器內部的六個階段各自負責什麼，以及怎麼把每個階段的中間產物 dump 出來看。
updated: "1.26"
---

## `go build` 只是個總指揮

輸入 `go build` 的時候，`go` 這個指令本身不做編譯。它負責解析模組相依、決定編譯順序、管理建置快取，然後對每個套件呼叫真正的編譯器 `cmd/compile`，最後呼叫連結器 `cmd/link`。

想看它到底跑了哪些指令，加上 `-x`：

```bash
go build -x -o hello.exe ./main.go
```

輸出會很長，但骨幹只有三步：

```text
# 1. 編譯：把 .go 檔編成目標檔 _pkg_.a
"C:\\Program Files\\Go\\pkg\\tool\\windows_amd64\\compile.exe" -o $WORK\b001\_pkg_.a -p main ... ./main.go

# 2. 打包相依資訊
"...\\buildid.exe" -w $WORK\b001\_pkg_.a

# 3. 連結：把所有 .a 與 runtime 合成執行檔
"...\\link.exe" -o $WORK\b001\exe\a.out.exe -importcfg $WORK\b001\importcfg.link ... $WORK\b001\_pkg_.a
```

!!! tip "建置快取"
    Go 會把每個套件的編譯結果依「原始碼 + 編譯旗標 + 相依」的雜湊值存進快取（`go env GOCACHE`）。所以第二次 `go build` 幾乎是瞬間完成。想量測真實的編譯時間要先 `go clean -cache`。

---

## 編譯器的六個階段

`cmd/compile` 的內部流程，可以拆成下面六段。這張圖是後續五節的地圖。

<figure class="diagram"><svg viewBox="0 0 740 470" role="img" aria-label="Go 編譯器的六個階段"><rect class="d-box-a" x="20" y="14" width="700" height="52" rx="8"/><text class="d-t-b" x="38" y="38">① 詞法與語法分析　Lexing &amp; Parsing</text><text class="d-t-s" x="38" y="57">cmd/compile/internal/syntax　.go 原始碼 → token 串流 → 語法樹（syntax AST）</text><path class="d-line" d="M370 66 L370 82" marker-end="url(#ar2)"/><rect class="d-box-a" x="20" y="84" width="700" height="52" rx="8"/><text class="d-t-b" x="38" y="108">② 型別檢查　Type Checking</text><text class="d-t-s" x="38" y="127">cmd/compile/internal/types2　解析識別字、推導型別、檢查介面滿足、泛型實例化</text><path class="d-line" d="M370 136 L370 152" marker-end="url(#ar2)"/><rect class="d-box" x="20" y="154" width="700" height="52" rx="8"/><text class="d-t-b" x="38" y="178">③ 建構 IR　Unified IR → ir.Node</text><text class="d-t-s" x="38" y="197">cmd/compile/internal/noder　把 syntax AST 與型別資訊寫成統一中間格式，再讀成編譯器的 IR</text><path class="d-line" d="M370 206 L370 222" marker-end="url(#ar2)"/><rect class="d-box-w" x="20" y="224" width="700" height="70" rx="8"/><text class="d-t-b" x="38" y="248">④ 中端最佳化　Middle End</text><text class="d-t-s" x="38" y="267">內聯 inline　·　逃逸分析 escape　·　去虛擬化 devirtualize　·　PGO 引導</text><text class="d-t-s" x="38" y="285">walk：把語法糖改寫成 runtime 呼叫（append / range / map 存取 / defer / channel）</text><path class="d-line" d="M370 294 L370 310" marker-end="url(#ar2)"/><rect class="d-box-o" x="20" y="312" width="700" height="52" rx="8"/><text class="d-t-b" x="38" y="336">⑤ SSA 與機器碼　SSA &amp; Codegen</text><text class="d-t-s" x="38" y="355">cmd/compile/internal/ssa　數十個最佳化 pass → 暫存器配置 → 平台指令 → 目標檔 .a</text><path class="d-line" d="M370 364 L370 380" marker-end="url(#ar2)"/><rect class="d-box-o" x="20" y="382" width="700" height="52" rx="8"/><text class="d-t-b" x="38" y="406">⑥ 連結　Linking</text><text class="d-t-s" x="38" y="425">cmd/link　合併目標檔與 runtime、去除死碼、產生型別與 GC 中繼資料 → 可執行檔</text><defs><marker id="ar2" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M0 0 L10 5 L0 10 z" style="fill:var(--line-strong)"/></marker></defs><text class="d-t-s" x="20" y="460">前端（①②）與後端（⑤⑥）中間隔著 IR，這讓 Go 能支援十多種 CPU 架構而不用重寫前端。</text></svg><figcaption><b>編譯器全貌。</b>階段 ①② 只跟 Go 的語言規格有關，階段 ⑤⑥ 只跟目標平台有關，中間用 IR 解耦。本書 Part 1 的後續五節，就是逐一走過這六格。</figcaption></figure>

### 各階段一句話

| 階段 | 套件 | 輸入 → 輸出 | 這一階段抓什麼錯 |
| --- | --- | --- | --- |
| ① 詞法／語法 | `syntax` | 位元組 → syntax AST | `syntax error: unexpected }` |
| ② 型別檢查 | `types2` | AST → 帶型別的 AST | `cannot use x (int) as string` |
| ③ 建構 IR | `noder` | 帶型別的 AST → `ir.Node` 樹 | （幾乎不報錯） |
| ④ 中端 | `inline` / `escape` / `walk` | IR → 改寫過的 IR | （只做轉換，不報錯） |
| ⑤ SSA／後端 | `ssa` / `ssagen` | IR → 機器指令 | 極少數暫存器壓力錯誤 |
| ⑥ 連結 | `cmd/link` | `.a` → 執行檔 | `undefined: xxx` |

錯誤訊息屬於哪一階段，其實從訊息本身就看得出來，這對排查建置失敗很有幫助。

---

## 前端與後端的分界：IR

第 ③ 階段是整個編譯器的樞紐。它把「Go 語言長什麼樣」跟「機器指令長什麼樣」這兩件事切開：

- **前端**只關心 Go 的規格。不管你的目標是 `amd64` 還是 `riscv64`，語法樹跟型別檢查完全一樣。
- **後端**只關心目標平台。它拿到的是一棵不再有「Go 語法」概念的 IR 樹。

這就是為什麼 Go 能支援十幾種 `GOOS`／`GOARCH` 組合，而增加一個新架構主要只需要動後端。

!!! version "Unified IR：Go 1.21 起的預設"
    Go 1.18 引入泛型時，編譯器需要一種能跨套件傳遞「未實例化的泛型函式」的中間格式。原本的 export data 格式做不到，於是 Go 團隊做了 **Unified IR**：一種同時當作 export data（給其他套件讀）與內部 IR 建構來源的統一序列化格式。

    它在 Go 1.20 可透過 `GOEXPERIMENT=unified` 試用，Go 1.21 成為預設並移除舊路徑。實務上的影響是**跨套件內聯**變得更積極——以前只有很簡單的函式能跨套件內聯，現在包含泛型在內的更多函式都可以。

---

## 一個套件一個編譯單位

Go 的編譯單位是**套件（package）**，不是檔案。`cmd/compile` 一次收下整個套件的所有 `.go` 檔：

```bash
go tool compile -p mypkg a.go b.go c.go
```

這帶來幾個直接後果：

1. **同一套件內沒有宣告順序問題。** `a.go` 可以呼叫 `c.go` 定義的函式，不需要前向宣告或標頭檔。
2. **套件之間有嚴格的相依順序。** 編譯 `main` 之前，它 import 的每個套件都必須先編好。這也是 Go **禁止循環 import** 的根本原因——不是設計潔癖，是編譯模型使然。
3. **跨套件的資訊靠 export data 傳遞。** 編譯 `mypkg` 時會把「其他套件需要知道的部分」（匯出的型別、常數、可內聯函式的 IR）寫進 `.a` 檔。編譯 `main` 時只讀這份摘要，不重新剖析 `mypkg` 的原始碼。

<figure class="diagram"><svg viewBox="0 0 700 220" role="img" aria-label="套件層級的編譯相依"><rect class="d-box" x="20" y="30" width="130" height="58" rx="8"/><text class="d-t-m d-mid" x="85" y="55">fmt</text><text class="d-t-s d-mid" x="85" y="74">（標準庫，已預編）</text><rect class="d-box" x="20" y="118" width="130" height="58" rx="8"/><text class="d-t-m d-mid" x="85" y="143">myapp/store</text><text class="d-t-s d-mid" x="85" y="162">store.a</text><rect class="d-box-a" x="285" y="74" width="140" height="58" rx="8"/><text class="d-t-m d-mid" x="355" y="99">myapp/service</text><text class="d-t-s d-mid" x="355" y="118">service.a</text><rect class="d-box-o" x="540" y="74" width="140" height="58" rx="8"/><text class="d-t-m d-mid" x="610" y="99">main</text><text class="d-t-s d-mid" x="610" y="118">app.exe</text><path class="d-line" d="M150 59 L230 59 L230 96 L281 96" marker-end="url(#ar3)"/><path class="d-line" d="M150 147 L230 147 L230 110 L281 110" marker-end="url(#ar3)"/><path class="d-line-a" d="M425 103 L536 103" marker-end="url(#ar3a)"/><text class="d-t-s d-mid" x="480" y="94">export data</text><defs><marker id="ar3" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M0 0 L10 5 L0 10 z" style="fill:var(--line-strong)"/></marker><marker id="ar3a" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M0 0 L10 5 L0 10 z" style="fill:var(--accent)"/></marker></defs><text class="d-t-s" x="20" y="205">相依圖必須是 DAG（無環有向圖）。編譯器依拓撲順序處理，彼此獨立的套件可以平行編譯。</text></svg><figcaption><b>套件相依決定編譯順序。</b>`go build` 會先算出拓撲順序，再把彼此無關的套件丟給多個 worker 平行編譯（數量取決於 <code>GOMAXPROCS</code>）。</figcaption></figure>

---

## 把中間產物 dump 出來

紙上談兵沒有意義。下面五個指令讓你親眼看到每個階段的產物。準備一份範例：

```go
package main

import "fmt"

func sum(nums []int) int {
	total := 0
	for _, n := range nums {
		total += n
	}
	return total
}

func main() {
	fmt.Println(sum([]int{1, 2, 3}))
}
```

### ① 看語法樹

```bash
go tool compile -W main.go
```

`-W` 會印出 walk **之前**的 IR 樹；`-w` 印出 walk **之後**的。對照這兩者，就能看到語法糖被展開的過程（下一節與 [for 與 range](for-range.html) 會詳談）。

### ② 看型別檢查結果

型別檢查沒有直接的 dump 旗標，但可以用標準庫的 `go/types`（與編譯器的 `types2` 是同一套演算法的兩份實作）自己跑一遍：

```go
package main

import (
	"fmt"
	"go/ast"
	"go/importer"
	"go/parser"
	"go/token"
	"go/types"
)

const src = `
package demo

func sum(nums []int) int {
	total := 0
	for _, n := range nums {
		total += n
	}
	return total
}
`

func main() {
	fset := token.NewFileSet()
	file, err := parser.ParseFile(fset, "demo.go", src, 0)
	if err != nil {
		panic(err)
	}

	info := &types.Info{
		Types: make(map[ast.Expr]types.TypeAndValue),
		Defs:  make(map[*ast.Ident]types.Object),
	}
	conf := types.Config{Importer: importer.Default()}
	if _, err := conf.Check("demo", fset, []*ast.File{file}, info); err != nil {
		panic(err)
	}

	for ident, obj := range info.Defs {
		if obj != nil {
			fmt.Printf("%-8s %s\n", ident.Name, obj.Type())
		}
	}
}
```

```text
sum      func(nums []int) int
nums     []int
total    int
n        int
```

注意 `total` 被推導成 `int`，`n` 也是 —— 這些型別在原始碼裡都沒寫出來，是型別檢查器推導出來的。

### ③ 看 SSA 的每一個 pass（最強的一招）

```bash
GOSSAFUNC=sum go build -o nul ./main.go
```

這會在目前目錄產生一份 **`ssa.html`**。打開它，你會看到一個橫向的表格，每一欄是一個 SSA pass 的結果，從最初的 IR 一路到最終的機器指令。點任何一個值，會把它在所有 pass 中的對應高亮起來。

這是理解「最佳化到底做了什麼」最直觀的工具。例如你會看到 `range` 迴圈的邊界檢查（bounds check）在 `prove` 這個 pass 被消除掉。

!!! tip "Windows 上的寫法"
    PowerShell 沒有 `VAR=x cmd` 的語法，要分兩行：

    ```text
    $env:GOSSAFUNC = "sum"
    go build -o nul .\main.go
    ```

    用完記得 `Remove-Item Env:GOSSAFUNC`，否則之後每次編譯都會產生 ssa.html。

### ④ 看最終機器碼

```bash
go tool compile -S main.go
```

或反組譯已編好的執行檔：

```bash
go build -o app.exe . && go tool objdump -s "main.sum" app.exe
```

### ⑤ 看連結器留下了什麼

```bash
go tool nm app.exe
```

會列出所有符號。你可以驗證「死碼消除」確實發生了 —— 沒被用到的匯出函式不會出現在清單裡。

```bash
go tool nm app.exe | findstr "runtime.gcStart"
```

---

## 為什麼 Go 編譯這麼快

這是 Go 最常被稱讚的特性，原因不只一個：

1. **沒有標頭檔。** C/C++ 的每個 `.cpp` 都要重新剖析成千上萬行標頭。Go 讀的是預先算好的 export data，是二進位摘要不是原始碼。
2. **相依圖是 DAG 且明確。** 不允許循環 import，讓編譯順序可以確定，也讓平行編譯與快取變簡單。
3. **未使用的 import 是編譯錯誤。** 這條常被抱怨的規則，實際上保證了相依圖不會因為疏忽而膨脹。
4. **最佳化刻意節制。** Go 編譯器不做 LLVM 那種等級的積極最佳化。它選擇「編譯快 + 產出夠好」，而不是「編譯慢 + 產出極致」。這是明確的取捨。
5. **語法設計對剖析友善。** 分號自動插入規則、沒有無限前瞻的語法歧義，讓剖析器可以單次掃描完成。

!!! note "「夠好」有多好？"
    Go 產生的機器碼通常比 `gcc -O2` 慢個 10–30%（依工作負載差異很大）。如果你真的需要壓榨最後那點效能，可以試 `gccgo`（用 GCC 後端）或針對熱點寫組合語言。但絕大多數服務的瓶頸在 I/O 與記憶體配置，不在指令品質。

下一節開始，我們從第一個階段——詞法與語法分析——細看。
