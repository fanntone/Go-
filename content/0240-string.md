---
title: 字串 string
slug: string
part: p2
number: "2.4"
order: 240
summary: 字串的兩欄位結構與不可變性、byte 與 rune 的差別、字串拼接的成本，以及零複製轉換的正確做法。
updated: "1.26"
---

## 兩個欄位，不可變

字串的內部結構比 slice 少一個欄位（`runtime/string.go`）：

```go
type stringStruct struct {
	str unsafe.Pointer // 指向底層位元組陣列
	len int            // 位元組長度
}
```

沒有 `cap`，因為**字串不可變**——不會擴容，所以不需要容量。

```go
package main

import (
	"fmt"
	"unsafe"
)

func main() {
	s := "hello"
	fmt.Println(unsafe.Sizeof(s)) // 16（兩個欄位，各 8 位元組）

	// ✗ 編譯錯誤：cannot assign to s[0] (neither addressable nor a map index expression)
	// s[0] = 'H'
}
```

### 不可變帶來什麼

**① 子字串是零複製的。**

```go
s := "hello, world"
sub := s[7:] // 只是產生一個新標頭：指標往後移 7、長度改成 5。沒有複製任何位元組
```

**② 字串可以安全共享。** 多個 goroutine 同時讀同一個字串完全沒問題，不需要鎖。

**③ 字串可以當 map 鍵。** 因為內容不會變，雜湊值就穩定。

**④ 字面值放在唯讀記憶體。** 程式碼裡的 `"hello"` 存在執行檔的唯讀資料段，多次使用同一個字面值只有一份。

!!! warning "子字串零複製也會造成記憶體洩漏"
    跟 slice 一樣的問題：

    ```go
    func extractID(hugeJSON string) string {
        return hugeJSON[12:24] // 只要這 12 個位元組還活著，整個 hugeJSON 都不能被回收
    }
    ```

    如果 `hugeJSON` 是 10 MB 而回傳值要長期保存，就複製一份：

    ```go
    return strings.Clone(hugeJSON[12:24]) // Go 1.18+
    ```

---

## byte 與 rune：索引與走訪的差別

Go 的字串是 **UTF-8 編碼的位元組序列**。這個事實決定了兩件事：

- `len(s)` 回傳**位元組數**，不是字元數。
- `s[i]` 回傳**第 i 個位元組**（型別是 `byte`，即 `uint8`），不是第 i 個字元。

```go
package main

import "fmt"

func main() {
	s := "Go語言"

	fmt.Println(len(s))    // 8 —— G(1) + o(1) + 語(3) + 言(3)
	fmt.Println(s[0])      // 71 —— 'G' 的位元組值
	fmt.Printf("%c\n", s[0]) // G
	fmt.Println(s[2])      // 232 —— '語' 的第一個位元組，不是完整字元
	fmt.Printf("%q\n", s[2:5]) // "語" —— 要切三個位元組才是完整的
}
```

<figure class="diagram"><svg viewBox="0 0 700 230" role="img" aria-label="UTF-8 字串的位元組與 rune 對應"><text class="d-t-b" x="15" y="20">"Go語言" 在記憶體裡的 8 個位元組</text><rect class="d-box-a" x="15" y="32" width="76" height="42" rx="4"/><text class="d-t-m d-mid" x="53" y="58">0x47</text><rect class="d-box-a" x="91" y="32" width="76" height="42" rx="4"/><text class="d-t-m d-mid" x="129" y="58">0x6F</text><rect class="d-box-w" x="167" y="32" width="76" height="42" rx="4"/><text class="d-t-m d-mid" x="205" y="58">0xE8</text><rect class="d-box-w" x="243" y="32" width="76" height="42" rx="4"/><text class="d-t-m d-mid" x="281" y="58">0xAA</text><rect class="d-box-w" x="319" y="32" width="76" height="42" rx="4"/><text class="d-t-m d-mid" x="357" y="58">0x9E</text><rect class="d-box-o" x="395" y="32" width="76" height="42" rx="4"/><text class="d-t-m d-mid" x="433" y="58">0xE8</text><rect class="d-box-o" x="471" y="32" width="76" height="42" rx="4"/><text class="d-t-m d-mid" x="509" y="58">0xA8</text><rect class="d-box-o" x="547" y="32" width="76" height="42" rx="4"/><text class="d-t-m d-mid" x="585" y="58">0x80</text><text class="d-t-s d-mid" x="53" y="90">s[0]</text><text class="d-t-s d-mid" x="129" y="90">s[1]</text><text class="d-t-s d-mid" x="205" y="90">s[2]</text><text class="d-t-s d-mid" x="281" y="90">s[3]</text><text class="d-t-s d-mid" x="357" y="90">s[4]</text><text class="d-t-s d-mid" x="433" y="90">s[5]</text><text class="d-t-s d-mid" x="509" y="90">s[6]</text><text class="d-t-s d-mid" x="585" y="90">s[7]</text><line class="d-line-a" x1="15" y1="106" x2="91" y2="106"/><text class="d-t-a d-mid" x="53" y="124">'G'</text><line class="d-line-a" x1="91" y1="106" x2="167" y2="106"/><text class="d-t-a d-mid" x="129" y="124">'o'</text><line class="d-line-a" x1="167" y1="106" x2="395" y2="106"/><text class="d-t-a d-mid" x="281" y="124">'語' U+8A9E</text><line class="d-line-a" x1="395" y1="106" x2="623" y2="106"/><text class="d-t-a d-mid" x="509" y="124">'言' U+8A00</text><line class="d-dash" x1="15" y1="146" x2="685" y2="146"/><text class="d-t-s" x="15" y="170">len(s) = 8（位元組數）　·　utf8.RuneCountInString(s) = 4（字元數）</text><text class="d-t-s" x="15" y="192">for i, r := range s → i 依序是 0, 1, 2, 5（位元組索引），r 是完整的 rune</text><text class="d-t-s" x="15" y="214">[]rune(s) 會配置一個 4 元素的 int32 陣列並解碼 —— 有成本，別在熱路徑上做</text></svg><figcaption><b>UTF-8 是變長編碼。</b>ASCII 佔 1 位元組，中日韓文字通常佔 3 位元組，emoji 常是 4 位元組。索引 <code>s[i]</code> 取的是位元組；要取字元必須解碼。</figcaption></figure>

### 兩種走訪，兩種結果

```go
package main

import "fmt"

func main() {
	s := "Go語言"

	fmt.Println("--- 依位元組（傳統 for）---")
	for i := 0; i < len(s); i++ {
		fmt.Printf("%d:%#x ", i, s[i])
	}
	fmt.Println()

	fmt.Println("--- 依 rune（range）---")
	for i, r := range s {
		fmt.Printf("%d:%c(%d) ", i, r, r)
	}
	fmt.Println()
}
```

```text
--- 依位元組（傳統 for）---
0:0x47 1:0x6f 2:0xe8 3:0xaa 4:0x9e 5:0xe8 6:0xa8 7:0x80 
--- 依 rune（range）---
0:G(71) 1:o(111) 2:語(35486) 5:言(35328) 
```

**`range` 字串會自動解碼 UTF-8**，這是編譯器在 walk 階段插入的（會呼叫 `runtime.decoderune`）。注意索引是**位元組位置**（0, 1, 2, 5），不是字元序號。

!!! danger "「字元」這個概念比你想的複雜"
    即使用 rune 也不等於使用者感知的「一個字」。例如 `"é"` 可能是一個 rune（U+00E9）或兩個（U+0065 + U+0301 組合附加符號）。emoji 更誇張：`"👨‍👩‍👧"` 是三個人物 emoji 加兩個零寬連接符，共 5 個 rune、18 個位元組，但顯示成一個圖案。

    要正確處理「使用者看到的一個字」（grapheme cluster），需要 `golang.org/x/text` 或專門的函式庫。標準庫沒有提供。

### 常見錯誤：反轉字串

```go
package main

import "fmt"

// ✗ 按位元組反轉 —— 多位元組字元會壞掉
func reverseBad(s string) string {
	b := []byte(s)
	for i, j := 0, len(b)-1; i < j; i, j = i+1, j-1 {
		b[i], b[j] = b[j], b[i]
	}
	return string(b)
}

// ✓ 按 rune 反轉
func reverseRunes(s string) string {
	r := []rune(s)
	for i, j := 0, len(r)-1; i < j; i, j = i+1, j-1 {
		r[i], r[j] = r[j], r[i]
	}
	return string(r)
}

func main() {
	s := "Go語言"
	fmt.Printf("%q\n", reverseBad(s))   // "\x80\xa8\xe8\x9e\xaa\xe8oG" —— 亂碼
	fmt.Printf("%q\n", reverseRunes(s)) // "言語oG"
}
```

（就算是 rune 版本，遇到組合字元或 emoji 序列仍會壞掉。「反轉字串」是個比表面上難得多的問題。）

---

## 字串拼接的成本

`s1 + s2` 會被改寫成 `runtime.concatstring2`，它**配置一塊新記憶體並複製兩邊的內容**。因為字串不可變，沒有別的做法。

在迴圈裡拼接是經典的效能陷阱：

```go
package main

import (
	"fmt"
	"strings"
	"testing"
)

func plusEq(parts []string) string {
	var s string
	for _, p := range parts {
		s += p // 每次都配置新記憶體 + 複製全部既有內容 → O(n²)
	}
	return s
}

func builder(parts []string) string {
	var sb strings.Builder
	for _, p := range parts {
		sb.WriteString(p)
	}
	return sb.String()
}

func builderGrow(parts []string) string {
	n := 0
	for _, p := range parts {
		n += len(p)
	}
	var sb strings.Builder
	sb.Grow(n) // 一次配置到位
	for _, p := range parts {
		sb.WriteString(p)
	}
	return sb.String()
}

func joined(parts []string) string {
	return strings.Join(parts, "")
}

func main() {
	parts := make([]string, 2000)
	for i := range parts {
		parts[i] = "abcdefgh"
	}

	for name, fn := range map[string]func([]string) string{
		"+=":          plusEq,
		"Builder":     builder,
		"BuilderGrow": builderGrow,
		"Join":        joined,
	} {
		r := testing.Benchmark(func(b *testing.B) {
			b.ReportAllocs()
			for i := 0; i < b.N; i++ {
				_ = fn(parts)
			}
		})
		fmt.Printf("%-12s %s  %s\n", name, r, r.MemString())
	}
}
```

典型結果：

```text
+=              1204   1002341 ns/op   16094208 B/op    1999 allocs/op
Builder        68142     17203 ns/op      49152 B/op      12 allocs/op
BuilderGrow   152331      7841 ns/op      16384 B/op       1 allocs/op
Join          148902      8012 ns/op      16384 B/op       1 allocs/op
```

`+=` 慢了兩個數量級，配置量差 1000 倍。

### 選哪一種

| 情況 | 用什麼 |
| --- | --- |
| 已經有 `[]string` 且要用分隔符連接 | `strings.Join` |
| 迴圈中逐步建構、知道大概長度 | `strings.Builder` + `Grow` |
| 迴圈中逐步建構、長度未知 | `strings.Builder` |
| 少數幾個（2–5 個）字串一次拼 | 直接用 `+`，編譯器會最佳化成 `concatstringN` |
| 有格式化需求 | `fmt.Sprintf`（慢但可讀，非熱路徑無妨） |
| 建構 `[]byte` 給 I/O 用 | `append` 或 `bytes.Buffer` |

!!! tip "`strings.Builder` 為什麼快"
    它內部就是一個 `[]byte`，`WriteString` 是 `append`。最後 `String()` 用 `unsafe` 把 `[]byte` 直接轉成 `string`，**不複製**——因為 Builder 保證那塊記憶體之後不會再被修改。

    這也是為什麼 `Builder` **不能複製**：複製之後兩份會共用同一塊底層陣列，破壞前述保證。它內部有一個 `addr` 欄位專門偵測這件事，複製後使用會 panic：`strings: illegal use of non-zero Builder copied by value`。

---

## string 與 []byte 的轉換

```go
b := []byte(s)  // 配置 + 複製
s := string(b)  // 配置 + 複製
```

**兩個方向都會複製**，因為 `string` 必須不可變，而 `[]byte` 可變。如果不複製，改 `b` 就會改到 `s`。

### 編譯器的最佳化

有幾種情況編譯器可以省掉複製：

```go
// ① map 查找：不會複製
m := map[string]int{"key": 1}
b := []byte("key")
v := m[string(b)] // 編譯器知道這個字串只用來查找，不會逃逸

// ② 比較：不會複製
if string(b) == "key" { }

// ③ range：不會複製
for _, r := range string(b) { }

// ④ 拼接的運算元：不會複製
s := "prefix" + string(b)
```

這些是編譯器特別辨識的模式。稍微改一下寫法就會失效：

```go
tmp := string(b) // 存進變數 → 會複製
v := m[tmp]
```

### 真正的零複製轉換

!!! version "Go 1.20 起：`unsafe.String` 與 `unsafe.Slice`"
    在 Go 1.20 之前，零複製轉換要用 `reflect.SliceHeader` 之類的髒招，寫法脆弱且容易出錯。Go 1.20 加入了官方支援的 `unsafe.String` 與 `unsafe.StringData`。

```go
package main

import (
	"fmt"
	"unsafe"
)

// []byte → string，零複製
// 前提：呼叫後絕對不可再修改 b
func b2s(b []byte) string {
	if len(b) == 0 {
		return ""
	}
	return unsafe.String(unsafe.SliceData(b), len(b))
}

// string → []byte，零複製
// 前提：絕對不可修改回傳的 slice
func s2b(s string) []byte {
	return unsafe.Slice(unsafe.StringData(s), len(s))
}

func main() {
	b := []byte("hello")
	s := b2s(b)
	fmt.Println(s) // hello

	b[0] = 'H'     // 違反前提！
	fmt.Println(s) // Hello ← 「不可變」的字串被改了
}
```

!!! danger "只在你能證明安全時使用"
    上面示範的最後兩行就是它的危險之處：字串的不可變保證被打破了。如果那個字串被當成 map 的鍵、被其他 goroutine 讀取、或被存進長期結構，行為完全不可預測。

    合理的使用場景只有一種：**在一個明確的、短暫的作用域內，你完全掌控那塊記憶體的生命週期**。例如從網路讀進一塊 buffer、轉成字串做一次解析、然後 buffer 立刻歸還 pool。

    絕大多數程式碼**不應該**用這個。先量測，確定轉換真的是瓶頸，再考慮。

---

## 實用的字串處理

### `strings` 套件的高頻函式

```go
package main

import (
	"fmt"
	"strings"
)

func main() {
	s := "  Hello, Go World!  "

	fmt.Printf("%q\n", strings.TrimSpace(s))              // "Hello, Go World!"
	fmt.Println(strings.Contains(s, "Go"))                // true
	fmt.Println(strings.HasPrefix(strings.TrimSpace(s), "Hello")) // true
	fmt.Println(strings.Split("a,b,c", ","))              // [a b c]
	fmt.Println(strings.Join([]string{"a", "b"}, "-"))    // a-b
	fmt.Println(strings.ReplaceAll("aaa", "a", "b"))      // bbb
	fmt.Println(strings.ToUpper("go"))                    // GO
	fmt.Println(strings.Repeat("ab", 3))                  // ababab
	fmt.Println(strings.Fields("  a   b  c "))            // [a b c]
	fmt.Println(strings.Count("cheese", "e"))             // 3

	// Cut：比 Split 更適合「切一次」的場景（Go 1.18+）
	before, after, found := strings.Cut("key=value", "=")
	fmt.Println(before, after, found) // key value true
}
```

!!! version "Go 1.18 的 `strings.Cut`"
    這個函式應該取代大部分 `strings.SplitN(s, sep, 2)` 的用法。它更清楚地表達意圖（切一次），回傳值也直接告訴你有沒有找到分隔符，不用檢查 slice 長度。

!!! version "Go 1.24：`strings.Lines`、`SplitSeq` 等迭代器版本"
    Go 1.24 為 `strings` 與 `bytes` 加入了回傳迭代器的版本，避免配置中間的 slice：

    ```go
    for line := range strings.Lines(text) { ... }
    for part := range strings.SplitSeq(csv, ",") { ... }
    for f := range strings.FieldsSeq(s) { ... }
    ```

    處理大文字時這能省下可觀的配置。

### 大小寫與比較

```go
// ✗ 效率差：兩次配置
if strings.ToLower(a) == strings.ToLower(b) { }

// ✓ 無配置
if strings.EqualFold(a, b) { }
```

`EqualFold` 做的是 Unicode 的 case folding，比單純的小寫轉換更正確（例如土耳其文的 İ）。

---

Part 2 結束。你現在知道 array、slice、map、string 在記憶體裡真正的樣子。

Part 3 換個層次，看語言核心機制：函式怎麼呼叫、介面怎麼做動態分派、反射怎麼運作。
