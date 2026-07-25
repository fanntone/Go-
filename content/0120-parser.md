---
title: 詞法與語法分析
slug: parser
part: p1
number: "1.2"
order: 120
summary: token 是怎麼切出來的、分號為什麼會自動插入、語法樹長什麼樣子，以及 Go 語法設計背後對編譯速度的考量。
updated: "1.26"
---

## 兩個階段，一個目標

編譯器拿到的是一串位元組。要把它變成有結構的東西，需要兩步：

1. **詞法分析（lexing／scanning）** —— 把字元串切成有意義的最小單位 **token**。`total += n` 會變成 `[IDENT "total"] [ADD_ASSIGN] [IDENT "n"]`。
2. **語法分析（parsing）** —— 依照語言文法，把 token 串組成一棵**抽象語法樹（AST）**。

在 Go 編譯器裡，這兩件事都由 `cmd/compile/internal/syntax` 完成。標準庫還有一套幾乎平行的實作 `go/scanner` 與 `go/parser`，是給工具程式（`gofmt`、`go vet`、IDE）用的。兩套的行為一致，但編譯器那套為了速度做了更多手工最佳化。

<figure class="diagram"><svg viewBox="0 0 700 250" role="img" aria-label="從原始碼到語法樹"><rect class="d-box" x="15" y="20" width="150" height="86" rx="8"/><text class="d-t-s" x="28" y="42">原始碼（位元組）</text><text class="d-t-m" x="28" y="66">total := 0</text><text class="d-t-m" x="28" y="86">total += n</text><path class="d-line-a" d="M165 63 L205 63" marker-end="url(#ar4)"/><text class="d-t-s d-mid" x="185" y="54">scanner</text><rect class="d-box" x="209" y="20" width="180" height="86" rx="8"/><text class="d-t-s" x="222" y="42">token 串流</text><text class="d-t-m" x="222" y="64">IDENT DEFINE INT ;</text><text class="d-t-m" x="222" y="84">IDENT ADD_ASSIGN IDENT ;</text><path class="d-line-a" d="M389 63 L429 63" marker-end="url(#ar4)"/><text class="d-t-s d-mid" x="409" y="54">parser</text><rect class="d-box-a" x="433" y="20" width="250" height="86" rx="8"/><text class="d-t-s" x="446" y="42">語法樹（syntax AST）</text><text class="d-t-m" x="446" y="64">AssignStmt{Op: Def, Lhs, Rhs}</text><text class="d-t-m" x="446" y="84">AssignStmt{Op: Add, Lhs, Rhs}</text><defs><marker id="ar4" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M0 0 L10 5 L0 10 z" style="fill:var(--accent)"/></marker></defs><line class="d-dash" x1="15" y1="130" x2="683" y2="130"/><text class="d-t-s" x="15" y="156">scanner 只認得「字元怎麼組成 token」——它不知道 total 是變數還是函式，也不檢查型別。</text><text class="d-t-s" x="15" y="178">parser 只認得「token 怎麼組成句子」——它會擋掉 if x { } else 缺 body，但不會擋 if 條件不是 bool。</text><text class="d-t-s" x="15" y="200">兩者都不做型別檢查。「x 是 int 不能當 string 用」這種錯誤要等到下一階段才會被發現。</text><text class="d-t-s" x="15" y="228">分號是 scanner 自動補的（見下節），不是你漏寫了。</text></svg><figcaption><b>職責分工。</b>把「認字」與「認句子」分開，是編譯器設計的通則。這也解釋了為什麼有些錯誤訊息會一次冒出一堆——語法分析失敗後，parser 需要「復原」到下一個看起來安全的位置繼續，中間可能誤報。</figcaption></figure>

---

## 詞法分析：token 是什麼

Go 的 token 分成四類：

| 類別 | 例子 |
| --- | --- |
| **識別字（identifier）** | `total`、`fmt`、`Println`、`_` |
| **關鍵字（keyword）** | 共 25 個：`break` `case` `chan` `const` `continue` `default` `defer` `else` `fallthrough` `for` `func` `go` `goto` `if` `import` `interface` `map` `package` `range` `return` `select` `struct` `switch` `type` `var` |
| **運算子與標點** | `+` `-` `:=` `<-` `...` `{` `}` 等 |
| **字面值（literal）** | `42`、`3.14`、`'A'`、`"hello"`、`` `raw` `` |

值得注意的是**哪些東西不是關鍵字**：

- `int`、`string`、`bool`、`byte`、`rune`、`error` —— 這些是**預先宣告的識別字**，不是關鍵字。所以 `int := 5` 在語法上完全合法（雖然是自找麻煩）。
- `true`、`false`、`nil`、`iota` —— 同上，都是可以被遮蔽的識別字。
- `make`、`new`、`len`、`cap`、`append`、`copy`、`delete`、`panic`、`recover` —— **內建函式**，也不是關鍵字。

自己驗證一下：

```go
package main

import "fmt"

func main() {
	// 完全合法：把預先宣告的識別字遮蔽掉
	true := "我不是布林值"
	len := 42
	fmt.Println(true, len)
}
```

```text
我不是布林值 42
```

!!! warning "能做不代表該做"
    上面的程式碼會通過編譯，但 `go vet` 不會警告，而後面任何想用真正 `len()` 的程式碼都會爆炸。這只是用來說明「關鍵字」與「預先宣告識別字」的差別。

用標準庫的 scanner 自己跑一次，看得最清楚：

```go
package main

import (
	"fmt"
	"go/scanner"
	"go/token"
)

func main() {
	src := []byte("total := 0\nfor _, n := range nums {\n\ttotal += n\n}\n")

	fset := token.NewFileSet()
	file := fset.AddFile("demo.go", fset.Base(), len(src))

	var s scanner.Scanner
	s.Init(file, src, nil, 0)

	for {
		pos, tok, lit := s.Scan()
		if tok == token.EOF {
			break
		}
		fmt.Printf("%-12s %-10s %q\n", fset.Position(pos), tok, lit)
	}
}
```

```text
demo.go:1:1   IDENT      "total"
demo.go:1:7   :=         ""
demo.go:1:10  INT        "0"
demo.go:1:11  ;          "\n"
demo.go:2:1   for        ""
demo.go:2:5   IDENT      "_"
demo.go:2:6   ,          ""
demo.go:2:8   IDENT      "n"
demo.go:2:10  :=         ""
demo.go:2:13  range      ""
demo.go:2:19  IDENT      "nums"
demo.go:2:24  {          ""
demo.go:2:25  ;          "\n"
demo.go:3:9   IDENT      "total"
demo.go:3:15  +=         ""
demo.go:3:18  IDENT      "n"
demo.go:3:19  ;          "\n"
demo.go:4:1   }          ""
demo.go:4:2   ;          "\n"
```

看到那些 `;` 了嗎？原始碼裡一個分號都沒有。

---

## 分號自動插入

Go 的文法其實**要求**用分號結束陳述式，就像 C 一樣。你不用寫，是因為 scanner 幫你插了。規則寫在 Go 語言規格裡，只有兩條：

**規則一：** 當一行的最後一個 token 是下列其中之一時，在換行處自動插入分號。

- 識別字
- 字面值（整數、浮點數、虛數、字元、字串）
- 關鍵字 `break`、`continue`、`fallthrough`、`return`
- 運算子 `++`、`--`
- 右括號 `)`、`]`、`}`

**規則二：** 為了讓一行的陳述式能寫在同一行，在 `)` 或 `}` 之前可以省略分號。

這兩條規則造成了 Go 最著名的一個「不可協商」：

```go
// ✓ 正確
func good() {
	fmt.Println("hi")
}

// ✗ 編譯錯誤
func bad()
{
	fmt.Println("hi")
}
```

第二種寫法為什麼壞掉？因為 `func bad()` 這一行的最後一個 token 是 `)`，符合規則一，scanner 會插入分號變成 `func bad();` —— 一個沒有函式本體的宣告。下一行的 `{` 就變成孤兒了。

同樣的道理也適用於：

```go
// ✗ 這樣寫，return 後面會被插分號，變成回傳零值
func broken() int {
	return
		42
}
```

以及跨行的複合字面值必須留下逗號：

```go
// ✓ 最後一個元素後面的逗號不能省
nums := []int{
	1,
	2,
	3,   // ← 這個逗號是必要的
}
```

`3` 後面如果沒有逗號，scanner 會依規則一插入分號（`3` 是字面值），而 `{...}` 裡不能出現分號。

!!! note "這其實是刻意的"
    Go 團隊很清楚這會惹惱一部分人。他們的理由是：**排版風格不應該是團隊要吵架的事**。用文法強制一種寫法，加上 `gofmt` 統一格式，就沒得吵了。這跟 Go 沒有三元運算子、沒有可選參數，是同一種設計哲學。

---

## 語法分析：從 EBNF 到樹

Go 的文法用 **EBNF** 描述。例如陳述式的定義大致是：

```ebnf
Statement =
	Declaration | LabeledStmt | SimpleStmt |
	GoStmt | ReturnStmt | BreakStmt | ContinueStmt | GotoStmt |
	FallthroughStmt | Block | IfStmt | SwitchStmt | SelectStmt |
	ForStmt | DeferStmt .

IfStmt = "if" [ SimpleStmt ";" ] Expression Block
         [ "else" ( IfStmt | Block ) ] .
```

Go 的 parser 是**手寫的遞迴下降（recursive descent）剖析器**，不是 yacc/bison 這類工具產生的。

!!! version "從 yacc 到手寫"
    早期的 Go 編譯器（`gc`，用 C 寫的）確實用 yacc 產生 parser。Go 1.5 編譯器自舉（用 Go 重寫自己）時仍保留了轉譯過來的 yacc 產物。後來在 Go 1.8 前後，`cmd/compile/internal/syntax` 導入了全新的手寫遞迴下降 parser，速度快了數倍，錯誤訊息也好很多。

    手寫 parser 的優勢在於：能給出「你是不是想寫 X？」這種貼心的錯誤訊息，也能在遇到錯誤時做更聰明的復原。代價是文法一改就要動手改程式碼。

遞迴下降的意思是：文法裡每一條產生式，對應剖析器裡一個函式。看到 `if` 就呼叫 `parseIfStmt()`，它內部再呼叫 `parseExpr()` 與 `parseBlock()`，如此遞迴下去。

### 看看樹長什麼樣

```go
package main

import (
	"go/ast"
	"go/parser"
	"go/token"
)

func main() {
	src := `package demo

func abs(x int) int {
	if x < 0 {
		return -x
	}
	return x
}
`
	fset := token.NewFileSet()
	f, err := parser.ParseFile(fset, "demo.go", src, 0)
	if err != nil {
		panic(err)
	}
	ast.Print(fset, f.Decls[0])
}
```

輸出（大幅節錄，保留骨架）：

```text
*ast.FuncDecl {
	Name: *ast.Ident { Name: "abs" }
	Type: *ast.FuncType {
		Params:  *ast.FieldList { ... x int ... }
		Results: *ast.FieldList { ... int ... }
	}
	Body: *ast.BlockStmt {
		List: []ast.Stmt (len = 2) {
			0: *ast.IfStmt {
				Cond: *ast.BinaryExpr { X: x, Op: <, Y: 0 }
				Body: *ast.BlockStmt {
					List: [ *ast.ReturnStmt { Results: [ *ast.UnaryExpr{Op: -, X: x} ] } ]
				}
			}
			1: *ast.ReturnStmt { Results: [ *ast.Ident { Name: "x" } ] }
		}
	}
}
```

注意這棵樹裡**完全沒有型別資訊**。`x` 只是一個名字，parser 不知道它是 `int`；`x < 0` 只是一個二元運算式，parser 不知道 `<` 對這兩個運算元合不合法。這些是下一階段的工作。

!!! tip "AST 不只給編譯器用"
    `go/ast` 是整個 Go 工具生態的基礎。`gofmt` 是「剖析成 AST 再依標準規則印回去」；`go vet`、`staticcheck`、`golangci-lint` 都是在 AST（加上型別資訊）上跑分析；IDE 的重新命名、跳到定義也是。

    寫自訂 linter 或程式碼產生器時，`go/ast` + `golang.org/x/tools/go/packages` 是標準組合。

---

## 語法設計如何服務編譯速度

Go 的一些「奇怪」語法選擇，回頭看都跟剖析效率有關。

### 型別寫在名字後面

```go
var x int              // Go
int x;                 // C
```

C 的宣告語法出了名地難剖析，因為 `a * b;` 可能是「宣告一個指向 b 的指標」也可能是「a 乘以 b」，要看 `a` 是不是型別名——但那是**語意**資訊，剖析器不該需要。這就是有名的 **lexer hack**。

Go 把型別放後面，`var` / `func` / `type` 這些關鍵字一出現就知道接下來要剖析什麼，剖析器不需要查符號表。

### 複合字面值的歧義與 `if` 的限制

這條規則常讓人踩坑：

```go
// ✗ 編譯錯誤
if v == Point{1, 2} {
	// ...
}
```

因為剖析 `if` 的條件式時，遇到 `{` 剖析器無法判斷這是「複合字面值的開頭」還是「if 主體的開頭」。Go 的解法是明確規定：在 `if`、`for`、`switch` 的條件位置，複合字面值必須加括號：

```go
// ✓ 正確
if v == (Point{1, 2}) {
	// ...
}
```

### 沒有前向宣告，但也沒有循環

同一個套件內的所有檔案一起剖析，所以順序無所謂；套件之間禁止循環，所以相依圖是 DAG。這兩條加起來，讓「先剖析誰」永遠有明確答案。

---

## 錯誤訊息怎麼讀

理解了兩階段的分工，錯誤訊息就好讀了。

| 訊息 | 階段 | 真正的原因 |
| --- | --- | --- |
| `syntax error: unexpected newline, expected {` | parser | 通常是 `{` 換行了 |
| `syntax error: unexpected semicolon or newline before {` | parser | 同上，分號自動插入造成的 |
| `expected declaration, found 'IDENT' xxx` | parser | 在函式外面寫了陳述式 |
| `undefined: xxx` | types2 | 語法沒問題，是名字找不到 |
| `cannot use x (variable of type int) as string value` | types2 | 型別不合 |
| `declared and not used: x` | types2 | 未使用的區域變數 |

只要看到 `syntax error`，就知道問題在標點符號、括號、換行位置，跟型別完全無關。

下一節進入型別檢查——Go 的型別系統怎麼運作，以及泛型是怎麼被實例化的。
