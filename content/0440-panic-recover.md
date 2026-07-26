---
title: panic 與 recover
slug: panic
part: p4
number: "4.4"
order: 440
summary: gopanic 怎麼展開堆疊、recover 為什麼只能在 defer 裡用、哪些錯誤 recover 攔不住，以及什麼時候該 panic。
updated: "1.26"
---

## panic 的執行流程

`panic(v)` 被改寫成 `runtime.gopanic(v)`。它做的事情依序是：

1. 建立一個 `_panic` 記錄，掛到目前 goroutine 的 `g._panic` 鏈上。
2. **從最內層開始，依序執行目前 goroutine 的每一個 `defer`**。
3. 如果某個 `defer` 裡呼叫了 `recover()`，panic 被標記為已恢復，堆疊展開停止，程式從那個 `defer` 所屬函式的回傳點繼續。
4. 如果所有 `defer` 都執行完仍沒有 `recover`，呼叫 `runtime.fatalpanic`：印出 panic 訊息與**所有 goroutine** 的堆疊追蹤，然後終止行程（結束碼 2）。

<figure class="diagram"><svg viewBox="0 0 700 330" role="img" aria-label="panic 的堆疊展開過程"><text class="d-t-b" x="15" y="20">呼叫堆疊（由外而內）</text><rect class="d-box" x="15" y="30" width="290" height="36" rx="4"/><text class="d-t-m" x="28" y="53">main()</text><rect class="d-box" x="35" y="70" width="290" height="36" rx="4"/><text class="d-t-m" x="48" y="93">handler()　defer recover()</text><rect class="d-box" x="55" y="110" width="290" height="36" rx="4"/><text class="d-t-m" x="68" y="133">service()　defer cleanup()</text><rect class="d-box-d" x="75" y="150" width="290" height="36" rx="4"/><text class="d-t-m" x="88" y="173">parse()　panic("壞資料")</text><path class="d-line-a" d="M380 168 L420 168" marker-end="url(#ar14)"/><rect class="d-box-w" x="424" y="150" width="261" height="36" rx="4"/><text class="d-t-s" x="436" y="173">① gopanic 建立 _panic 記錄</text><path class="d-line-a" d="M370 132 L420 132" marker-end="url(#ar14)"/><rect class="d-box-w" x="424" y="114" width="261" height="36" rx="4"/><text class="d-t-s" x="436" y="137">② 執行 cleanup()，沒有 recover → 繼續</text><path class="d-line-a" d="M350 92 L420 92" marker-end="url(#ar14)"/><rect class="d-box-o" x="424" y="74" width="261" height="36" rx="4"/><text class="d-t-s" x="436" y="97">③ 執行 defer，呼叫了 recover() → 停止</text><path class="d-line" d="M330 52 L420 52" stroke-dasharray="4 3" marker-end="url(#ar14b)"/><rect class="d-box" x="424" y="34" width="261" height="36" rx="4"/><text class="d-t-s" x="436" y="57">④ handler 正常回傳，main 繼續執行</text><defs><marker id="ar14" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M0 0 L10 5 L0 10 z" style="fill:var(--accent)"/></marker><marker id="ar14b" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M0 0 L10 5 L0 10 z" style="fill:var(--line-strong)"/></marker></defs><line class="d-dash" x1="15" y1="210" x2="685" y2="210"/><text class="d-t-s" x="15" y="234">如果 handler 沒有 recover：展開會一路走到 main 的 defer，全部執行完之後 → fatalpanic → 行程終止。</text><text class="d-t-s" x="15" y="258">關鍵：recover 只在「正在被 panic 展開的那個 defer 內部直接呼叫」時才有效。</text><text class="d-t-s" x="15" y="282">panic 只沿著「目前這個 goroutine」的堆疊展開 —— 它不會傳播到其他 goroutine，也不會被其他 goroutine 攔截。</text><text class="d-t-s" x="15" y="306">因此：任何一個 goroutine 的未處理 panic，都會讓整個行程死掉。</text></svg><figcaption><b>展開只走一個 goroutine。</b>這是 Go 與 Java／C++ 例外機制最大的差別。你不能在 goroutine 外面「攔截」它拋出的 panic——必須在那個 goroutine 內部處理。</figcaption></figure>

---

## recover 的三個限制

`recover()` 只有在滿足**全部三個條件**時才會回傳非 nil：

1. **必須在 `defer` 的函式裡呼叫。**
2. **必須是直接呼叫**，不能再包一層。
3. **必須真的正在 panic。**

```go
package main

import "fmt"

func main() {
	// ① 不在 defer 裡：無效
	fmt.Println("直接呼叫:", recover()) // <nil>

	// ② 包了一層：無效
	func() {
		defer func() {
			helper() // recover 在 helper 裡面，隔了一層
			fmt.Println("② 沒攔到，會繼續 panic")
		}()
		// panic("test") // 取消註解會終止程式
	}()

	// ③ 沒有 panic 時：回傳 nil
	func() {
		defer func() {
			fmt.Println("③ 沒有 panic:", recover()) // <nil>
		}()
	}()

	// ✓ 正確用法
	func() {
		defer func() {
			if r := recover(); r != nil {
				fmt.Println("✓ 攔到了:", r)
			}
		}()
		panic("出事了")
	}()

	fmt.Println("程式繼續執行")
}

func helper() {
	if r := recover(); r != nil { // 這裡的 recover 沒有作用
		fmt.Println("helper 攔到:", r)
	}
}
```

```text
直接呼叫: <nil>
② 沒攔到，會繼續 panic
③ 沒有 panic: <nil>
✓ 攔到了: 出事了
程式繼續執行
```

!!! note "為什麼限制這麼嚴"
    `recover` 需要知道「我正在展開哪一個 panic」。runtime 的實作是：檢查呼叫 `recover` 的函式，是否正好是目前正在執行的那個 `defer` 函式。

    如果允許任意巢狀，語意會變得非常難定義——`helper()` 應該恢復哪一層的 panic？把它限制成「直接呼叫」讓規則保持簡單且可預測。

    要寫可重用的恢復邏輯，改成回傳閉包：

    ```go
    func recovery(err *error) func() {
        return func() {
            if r := recover(); r != nil {
                *err = fmt.Errorf("panic: %v", r)
            }
        }
    }

    func work() (err error) {
        defer recovery(&err)()  // 注意兩對括號：recovery 立刻執行，回傳的閉包才是 defer 的目標
        panic("boom")
    }
    ```

---

## recover 攔不住的東西

這是很多人的誤解來源：**不是所有「程式崩潰」都是 panic。**

| 狀況 | 型別 | recover 能攔嗎 |
| --- | --- | --- |
| `panic("...")` | panic | ✓ |
| nil 指標解參照 | runtime panic | ✓ |
| 陣列／slice 越界 | runtime panic | ✓ |
| 除以零 | runtime panic | ✓ |
| 型別斷言失敗（單值形式） | runtime panic | ✓ |
| 對已關閉的 channel 傳送 | runtime panic | ✓ |
| 重複 `close` channel | runtime panic | ✓ |
| 寫入 nil map | runtime panic | ✓ |
| **map 並行讀寫** | **fatal error** | **✗** |
| **死結（所有 goroutine 休眠）** | **fatal error** | **✗** |
| **堆疊溢位** | **fatal error** | **✗** |
| **記憶體不足（OOM）** | **fatal error** | **✗** |
| **`os.Exit(n)`** | 直接結束 | **✗**（連 defer 都不執行） |

```go
package main

import "fmt"

func main() {
	defer func() {
		fmt.Println("這行不會執行:", recover())
	}()

	m := map[int]int{}
	go func() {
		for i := 0; ; i++ {
			m[i] = i
		}
	}()
	for {
		_ = m[0]
	}
}
```

```text
fatal error: concurrent map read and map write
```

**`fatal error` 是 runtime 判定「繼續執行會導致記憶體毀損」時的緊急停機**。它繞過整個 panic 機制，直接終止行程。這是設計上的安全考量，不是缺陷。

---

## panic 的堆疊追蹤怎麼讀

```go
package main

import "fmt"

type User struct{ Name string }

func (u *User) Greet() string { return "Hi " + u.Name }

func process(users []*User) {
	for _, u := range users {
		fmt.Println(u.Greet())
	}
}

func main() {
	process([]*User{{"小明"}, nil})
}
```

```text
Hi 小明
panic: runtime error: invalid memory address or nil pointer dereference
[signal 0xc0000005 code=0x0 addr=0x0 pc=0x...]

goroutine 1 [running]:
main.(*User).Greet(...)
        C:/tmp/main.go:7
main.process({0xc000010030?, 0x2?, 0x0?})
        C:/tmp/main.go:11 +0x5e
main.main()
        C:/tmp/main.go:16 +0x45
exit status 2
```

讀法：

- **第一行**是 panic 的原因。`invalid memory address or nil pointer dereference` 就是 nil 指標。
- **`goroutine 1 [running]`** —— 出事的 goroutine 編號與狀態。
- **由上而下是由內而外**：最上面 `main.(*User).Greet` 是實際出錯的地方（`main.go:7`），下面是呼叫它的人。
- **`+0x5e`** 是函式內的位元組偏移，通常不需要管。
- **`{0xc000010030?, 0x2?, 0x0?}`** 是參數的原始值（slice 標頭的三個欄位）。問號表示編譯器最佳化後值可能不精確。

!!! tip "看到所有 goroutine 的堆疊"
    預設只印出 panic 的那個 goroutine。要看全部（診斷死結或洩漏時很有用）：

    ```bash
    GOTRACEBACK=all go run main.go
    ```

    | 值 | 顯示範圍 |
    | --- | --- |
    | `none` | 不印堆疊 |
    | `single`（預設） | 只印當前 goroutine |
    | `all` | 所有使用者 goroutine |
    | `system` | 加上 runtime 內部的 goroutine |
    | `crash` | 同 system，並產生 core dump |

---

## 什麼時候該 panic

Go 的核心原則：**panic 用於「程式有 bug」，error 用於「預期內的失敗」。**

（error 那一半——誰該接住它、怎麼跨越多層傳遞、goroutine 裡的錯誤該去哪——見 [錯誤處理與傳遞](errors.html)。）

### 該用 error

```go
// 檔案可能不存在 —— 這是正常世界的一部分
func readConfig(path string) (*Config, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("讀取設定: %w", err)
	}
	// ...
}

// 使用者輸入可能無效
func parseAge(s string) (int, error) {
	n, err := strconv.Atoi(s)
	if err != nil {
		return 0, fmt.Errorf("年齡格式錯誤: %w", err)
	}
	if n < 0 || n > 150 {
		return 0, fmt.Errorf("年齡超出範圍: %d", n)
	}
	return n, nil
}
```

網路失敗、檔案不存在、輸入格式錯誤、權限不足——這些都是**預期會發生**的事，用 error。

### 該用 panic

**① 不可能發生的情況（程式邏輯錯誤）**

```go
func mustCompile(pattern string) *regexp.Regexp {
	re, err := regexp.Compile(pattern)
	if err != nil {
		panic("內建的正規表示式編譯失敗: " + err.Error())
	}
	return re
}

// 套件層級的常數 pattern，編譯失敗代表原始碼有錯
var emailRe = regexp.MustCompile(`^[^@]+@[^@]+$`)
```

標準庫的 `Must*` 慣例就是這個意思：**「這件事失敗代表程式寫錯了，不是執行環境的問題。」**

**② 初始化失敗且無法繼續**

```go
func init() {
	var err error
	tmpl, err = template.ParseFS(assets, "templates/*.html")
	if err != nil {
		panic("模板解析失敗: " + err.Error()) // 沒有模板服務就沒有意義
	}
}
```

**③ 違反了函式的前置條件**

```go
func (r *Ring) Set(i int, v any) {
	if i < 0 || i >= len(r.buf) {
		panic(fmt.Sprintf("Ring.Set: 索引 %d 超出範圍 [0,%d)", i, len(r.buf)))
	}
	r.buf[i] = v
}
```

### 絕對不該做的：用 panic 當控制流

```go
// ✗ 把 panic 當成 exception 用
func find(id int) *User {
	for _, u := range users {
		if u.ID == id {
			return u
		}
	}
	panic("找不到使用者") // 找不到是很正常的事，應該回傳 error
}
```

---

## 在服務邊界攔截 panic

即使遵守上述原則，第三方套件或你自己的疏忽仍可能 panic。在 HTTP 伺服器這類長期執行的服務裡，**一個請求的 panic 不應該讓整個服務掛掉**。

```go
package main

import (
	"fmt"
	"log/slog"
	"net/http"
	"runtime/debug"
)

func Recoverer(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		defer func() {
			rec := recover()
			if rec == nil {
				return
			}

			// http.ErrAbortHandler 是約定的「靜默中止」訊號，不該記錄成錯誤
			if rec == http.ErrAbortHandler {
				panic(rec)
			}

			slog.Error("handler panic",
				"err", rec,
				"method", r.Method,
				"path", r.URL.Path,
				"stack", string(debug.Stack()),
			)

			w.WriteHeader(http.StatusInternalServerError)
			fmt.Fprintln(w, "internal server error")
		}()

		next.ServeHTTP(w, r)
	})
}

func main() {
	mux := http.NewServeMux()
	mux.HandleFunc("/boom", func(w http.ResponseWriter, r *http.Request) {
		panic("模擬錯誤")
	})
	mux.HandleFunc("/ok", func(w http.ResponseWriter, r *http.Request) {
		fmt.Fprintln(w, "正常")
	})

	http.ListenAndServe(":8080", Recoverer(mux))
}
```

三個重點：

1. **一定要記錄 `debug.Stack()`。** 沒有堆疊追蹤的 panic 記錄幾乎沒有除錯價值。
2. **重新 panic `http.ErrAbortHandler`。** 這是 `net/http` 用來表示「中止此回應但不記錄」的約定值。
3. **`net/http` 本身也有內建的 recover**，但它只印到 stderr、格式固定。自己寫一層才能整合到你的日誌系統。

!!! danger "goroutine 裡的 panic 必須自己攔"
    中介層的 `recover` **只保護 handler 所在的那個 goroutine**：

    ```go
    // ✗ 這個 panic 會讓整個服務掛掉
    func handler(w http.ResponseWriter, r *http.Request) {
        go func() {
            doBackgroundWork() // 如果這裡 panic，中介層攔不到
        }()
    }

    // ✓ 每個 goroutine 自己保護
    func safeGo(fn func()) {
        go func() {
            defer func() {
                if r := recover(); r != nil {
                    slog.Error("goroutine panic", "err", r, "stack", string(debug.Stack()))
                }
            }()
            fn()
        }()
    }
    ```

    **這是 Go 服務最常見的當機原因。** 任何在請求生命週期外啟動的 goroutine，都必須自己包一層 recover。

---

## panic 的巢狀與 `errors` 整合

### panic 中再 panic

```go
package main

import "fmt"

func main() {
	defer func() {
		fmt.Println("recover:", recover()) // 只會拿到最後一個
	}()

	defer func() {
		panic("第二個 panic")
	}()

	panic("第一個 panic")
}
```

```text
recover: 第二個 panic
```

如果最後沒有被 recover，輸出會顯示 panic 鏈：

```text
panic: 第一個 panic [recovered]
	panic: 第二個 panic
```

### 用具體型別而非字串 panic

```go
package main

import (
	"errors"
	"fmt"
)

type ParseError struct {
	Line int
	Msg  string
}

func (e *ParseError) Error() string {
	return fmt.Sprintf("第 %d 行: %s", e.Line, e.Msg)
}

func parse(lines []string) (err error) {
	defer func() {
		if r := recover(); r != nil {
			// 只恢復我們自己的 panic 型別，其他的重新拋出
			if pe, ok := r.(*ParseError); ok {
				err = pe
				return
			}
			panic(r)
		}
	}()

	for i, l := range lines {
		if l == "" {
			panic(&ParseError{Line: i + 1, Msg: "空行"})
		}
	}
	return nil
}

func main() {
	err := parse([]string{"a", "", "c"})
	fmt.Println(err)

	var pe *ParseError
	if errors.As(err, &pe) {
		fmt.Println("行號:", pe.Line)
	}
}
```

```text
第 2 行: 空行
行號: 2
```

**只恢復自己認得的 panic 型別，其他的重新拋出** —— 這一點很重要。無差別 recover 會把真正的程式 bug（nil 指標、越界）也吞掉，讓問題更難發現。

---

下一節是 Part 4 的最後一節：`make` 與 `new` 的差別，以及 Go 1.26 對 `new` 的擴充。
