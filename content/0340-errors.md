---
title: 錯誤處理與傳遞
slug: errors
part: p3
number: "3.4"
order: 340
summary: error 就是一個介面、誰該接住錯誤、%w 與 errors.Is/As 的包裝鏈，以及為什麼 goroutine 是錯誤傳遞鏈的斷點。
updated: "1.26"
---

## error 只是一個介面

```go
type error interface {
	Error() string
}
```

整個標準庫就這樣定義。沒有基底類別、沒有例外階層、沒有 `throws` 宣告。**回傳 error 就是回傳一個介面值**——所以 [介面](interface.html) 那節講的一切都適用，包括最有名的那個坑：

```go
var err *MyError   // nil 指標
return err         // ✗ 裝箱後介面不是 nil
```

細節見 [nil 介面的經典陷阱](interface.html#nil-介面的經典陷阱)。這是 error 相關 bug 的第一名。

---

## 誰該接住錯誤

這是最常被問、也最少被講清楚的問題。「每一層都要 `if err != nil` 往上傳，那到底誰處理？」

判準不是深度，是：**握有「該怎麼辦」所需資訊的那一層。**

看一個四層的例子：

```go
package main

import (
	"errors"
	"fmt"
)

var ErrNotFound = errors.New("找不到資料")

// ── 第 4 層（最底）：只知道「查不到」
func queryDB(id int) error {
	if id == 42 {
		return ErrNotFound
	}
	return nil
}

// ── 第 3 層：加上下文，不做決定
func repoGet(id int) error {
	if err := queryDB(id); err != nil {
		return fmt.Errorf("repo.Get(%d): %w", id, err)
	}
	return nil
}

// ── 第 2 層：同上
func serviceLoad(id int) error {
	if err := repoGet(id); err != nil {
		return fmt.Errorf("service.Load: %w", err)
	}
	return nil
}

// ── 第 1 層：邊界，這裡才做決定
func handle(id int) int {
	err := serviceLoad(id)
	switch {
	case err == nil:
		return 200
	case errors.Is(err, ErrNotFound): // 穿過三層包裝仍認得出來
		fmt.Printf("   完整訊息: %v\n", err)
		return 404
	default:
		return 500
	}
}

func main() {
	for _, id := range []int{1, 42} {
		fmt.Printf("id=%-3d → HTTP %d\n", id, handle(id))
	}
}
```

```text
id=1   → HTTP 200
   完整訊息: service.Load: repo.Get(42): 找不到資料
id=42  → HTTP 404
```

`repoGet` 拿到 `ErrNotFound` 時，它**沒有能力**判斷這該變成 404、該重試、還是根本是正常情況——那取決於呼叫者在做什麼。所以它不該決定，只該把資訊補足後往上傳。

### 常見的邊界

| 邊界 | 做的決定 |
| --- | --- |
| HTTP handler | 轉成 status code 與回應主體 |
| gRPC interceptor | 轉成 `codes.NotFound` 等狀態碼 |
| `main()` | 記錄後 `os.Exit(1)` |
| worker 迴圈 | 重試／跳過這一筆／整個停掉 |
| CLI 指令 | 印到 stderr + 設定 exit code |
| 測試 | `t.Fatal` / `t.Error` |

### 中間層只做兩件事

1. **加上下文**（我是誰、輸入是什麼）
2. **往上傳**

```go
// ✓ 中間層該長的樣子
if err := repoGet(id); err != nil {
	return fmt.Errorf("service.Load: %w", err)
}
```

!!! warning "反模式：每一層都 log"
    ```go
    // ✗ 同一個錯誤會在日誌裡出現四次
    if err := repoGet(id); err != nil {
        slog.Error("repo get failed", "err", err)   // ← 不要
        return fmt.Errorf("service.Load: %w", err)
    }
    ```

    這會讓日誌爆量，而且很難判斷「到底發生了幾次錯誤」。

    規則：**中間層只包裝，邊界才 log 一次**。包裝時累積的上下文已經足以還原整條路徑。

---

## `%w` 與包裝鏈

!!! version "Go 1.13 起：`%w`、`errors.Is`、`errors.As`"
    在此之前，要判斷錯誤型別只能用 `err == ErrFoo`（一包裝就失效）或字串比對（更糟）。Go 1.13 引入了錯誤包裝機制，這是 Go 錯誤處理最重要的一次演進。

`fmt.Errorf` 的 `%w` 與 `%v` 差在**有沒有保留原始錯誤**：

```go
package main

import (
	"errors"
	"fmt"
)

var ErrBase = errors.New("底層錯誤")

func main() {
	wrapped := fmt.Errorf("外層: %w", ErrBase) // 保留
	flat := fmt.Errorf("外層: %v", ErrBase)    // 只留字串

	fmt.Println(wrapped)                       // 外層: 底層錯誤
	fmt.Println(flat)                          // 外層: 底層錯誤 ← 印出來一樣！

	fmt.Println(errors.Is(wrapped, ErrBase))   // true
	fmt.Println(errors.Is(flat, ErrBase))      // false ← 鏈斷了
	fmt.Println(errors.Unwrap(wrapped) == ErrBase) // true
}
```

**兩者印出來完全一樣，但一個能被 `errors.Is` 認出、一個不行。** 這是很容易漏掉的差異——預設就用 `%w`。

### `errors.Is`：比對特定的錯誤值

適合**哨兵錯誤（sentinel error）**——那些代表某種狀況、不需要攜帶額外資料的錯誤：

```go
var (
	ErrNotFound     = errors.New("找不到資料")
	ErrPermission   = errors.New("權限不足")
	ErrRateLimited  = errors.New("超過速率限制")
)

if errors.Is(err, ErrNotFound) {
	return 404
}
```

標準庫也有一堆：`io.EOF`、`sql.ErrNoRows`、`os.ErrNotExist`、`context.Canceled`、`context.DeadlineExceeded`。

### `errors.As`：取出帶欄位的錯誤

當錯誤需要**攜帶資料**時，定義自己的型別：

```go
package main

import (
	"errors"
	"fmt"
)

type ValidationError struct {
	Field  string
	Reason string
}

func (e *ValidationError) Error() string {
	return fmt.Sprintf("欄位 %s 無效: %s", e.Field, e.Reason)
}

func validate(age int) error {
	if age < 0 {
		return &ValidationError{Field: "age", Reason: "不可為負數"}
	}
	return nil
}

func main() {
	err := fmt.Errorf("建立使用者: %w", validate(-5))

	var ve *ValidationError
	if errors.As(err, &ve) { // 從鏈中找出這個型別
		fmt.Println("有問題的欄位:", ve.Field)  // age
		fmt.Println("原因:", ve.Reason)        // 不可為負數
	}
}
```

!!! tip "`Is` 還是 `As`？"
    - **不需要額外資料** → 哨兵錯誤 + `errors.Is`
    - **需要知道細節**（哪個欄位、重試幾次、HTTP 狀態碼） → 自訂型別 + `errors.As`

    注意 `errors.As` 的第二個參數必須是**指標的指標**（`&ve`，其中 `ve` 是 `*ValidationError`），因為它要寫入。傳錯會 panic。

### `errors.Join`：多個錯誤合成一個

!!! version "Go 1.20 起"
    以前要回報多個錯誤只能自己拼字串或引入第三方套件。

```go
package main

import (
	"errors"
	"fmt"
)

var ErrA = errors.New("錯誤 A")
var ErrB = errors.New("錯誤 B")

func main() {
	err := errors.Join(ErrA, ErrB)

	fmt.Println(err)
	fmt.Println(errors.Is(err, ErrA)) // true
	fmt.Println(errors.Is(err, ErrB)) // true ← 兩個都認得
}
```

```text
錯誤 A
錯誤 B
true
true
```

`Join` 會忽略 `nil`，所以可以放心地把可能為 nil 的錯誤丟進去。這對「收集多個驗證失敗」或「等一組並行工作」特別好用。

---

## goroutine 是傳遞鏈的斷點

這是整章最重要的一節。

`return err` 的意思是「交給我的呼叫者」。但 **`go func()` 沒有呼叫者**：

```go
go func() {
	if err := doWork(); err != nil {
		return   // ← 回到哪裡？沒有。錯誤就此消失
	}
}()
```

<figure class="diagram"><svg viewBox="0 0 700 300" role="img" aria-label="錯誤在 goroutine 邊界中斷"><text class="d-t-b" x="15" y="20">一般呼叫：錯誤沿著堆疊往回傳</text><rect class="d-box-o" x="15" y="32" width="130" height="34" rx="4"/><text class="d-t-s d-mid" x="80" y="54">main / handler</text><rect class="d-box" x="165" y="32" width="130" height="34" rx="4"/><text class="d-t-s d-mid" x="230" y="54">service</text><rect class="d-box" x="315" y="32" width="130" height="34" rx="4"/><text class="d-t-s d-mid" x="380" y="54">repo</text><rect class="d-box" x="465" y="32" width="130" height="34" rx="4"/><text class="d-t-s d-mid" x="530" y="54">db</text><path class="d-line-a" d="M461 78 L319 78" marker-end="url(#are1)"/><path class="d-line-a" d="M311 78 L169 78" marker-end="url(#are1)"/><path class="d-line-a" d="M161 78 L19 78" marker-end="url(#are1)"/><text class="d-t-a d-mid" x="300" y="98">err 逐層往上，每層加上下文 → 邊界做決定</text><line class="d-dash" x1="15" y1="120" x2="685" y2="120"/><text class="d-t-b" x="15" y="148">go func()：鏈斷在這裡</text><rect class="d-box-o" x="15" y="160" width="130" height="34" rx="4"/><text class="d-t-s d-mid" x="80" y="182">handler</text><rect class="d-box-d" x="230" y="160" width="200" height="34" rx="4"/><text class="d-t-s d-mid" x="330" y="182">go func() { … }</text><rect class="d-box" x="465" y="160" width="130" height="34" rx="4"/><text class="d-t-s d-mid" x="530" y="182">doWork()</text><path class="d-line-a" d="M461 206 L434 206" marker-end="url(#are1)"/><text class="d-t-a" x="240" y="222">err 回到這裡就沒了 —— 沒有呼叫者</text><path class="d-dash" d="M226 178 L149 178"/><text class="d-t-s" x="150" y="160">✗ 傳不回去</text><rect class="d-box-a" x="15" y="240" width="670" height="46" rx="6"/><text class="d-t-b" x="30" y="262">同樣的道理：goroutine 裡的 panic 也沒有上層可以展開 → 直接終止整個行程</text><text class="d-t-s" x="30" y="280">所以每一個 go 陳述式，都必須明確回答一個問題：「錯誤要去哪裡？」</text><defs><marker id="are1" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M0 0 L10 5 L0 10 z" style="fill:var(--accent)"/></marker></defs></svg><figcaption><b>goroutine 沒有呼叫者。</b>這同時解釋了兩件事：為什麼 goroutine 裡的 error 會憑空消失，以及為什麼 goroutine 裡的 panic 會殺掉整個行程（見 <a href="panic.html">panic 與 recover</a>）。兩者根因相同。</figcaption></figure>

### 規則：每個 `go` 都要有錯誤出口

答不出「錯誤去哪裡」就是 bug。三種給法：

**① `errgroup` —— 有子任務要等的話，這是預設答案**

```go
import "golang.org/x/sync/errgroup"

g, ctx := errgroup.WithContext(ctx)
g.SetLimit(10) // 順便限制並行度

for _, id := range ids {
	g.Go(func() error {
		return serviceLoad(ctx, id) // ← 出口就是回傳值
	})
}

if err := g.Wait(); err != nil { // 回傳第一個錯誤，並自動取消 ctx
	return err
}
```

**② 自己收集 —— 需要「全部跑完並拿到所有錯誤」時**

```go
package main

import (
	"errors"
	"fmt"
	"sync"
)

var ErrNotFound = errors.New("找不到資料")

func load(id int) error {
	if id == 42 {
		return fmt.Errorf("load(%d): %w", id, ErrNotFound)
	}
	return nil
}

func fanOut(ids []int) error {
	var (
		mu   sync.Mutex
		errs []error
		wg   sync.WaitGroup
	)

	for _, id := range ids {
		wg.Add(1)
		go func() {
			defer wg.Done()
			if err := load(id); err != nil {
				mu.Lock()
				errs = append(errs, err) // ← 出口：收集到共用 slice
				mu.Unlock()
			}
		}()
	}

	wg.Wait()
	return errors.Join(errs...)
}

func main() {
	err := fanOut([]int{1, 42, 3, 42})
	fmt.Println(err)
	fmt.Println("仍認得原始錯誤:", errors.Is(err, ErrNotFound))
}
```

```text
load(42): 找不到資料
load(42): 找不到資料
仍認得原始錯誤: true
```

**③ fire-and-forget —— 沒人等的話，出口就是日誌**

```go
func safeGo(fn func() error) {
	go func() {
		defer func() {
			if r := recover(); r != nil {
				slog.Error("goroutine panic", "err", r, "stack", string(debug.Stack()))
			}
		}()

		if err := fn(); err != nil {
			slog.Error("background task failed", "err", err) // ← 出口
		}
	}()
}
```

!!! danger "fire-and-forget 一定要自己 recover"
    HTTP 中介層的 recover **保護不到你在 handler 裡開的 goroutine**——它們是不同的 goroutine，展開路徑完全分開。

    ```go
    func handler(w http.ResponseWriter, r *http.Request) {
        go doBackgroundWork()   // ✗ 這裡面 panic，整個服務下線
    }
    ```

    這是 Go 服務最常見的當機原因。詳見 [在服務邊界攔截 panic](panic.html#在服務邊界攔截-panic)。

---

## 什麼時候用 panic 而不是 error

一句話：**error 用於「預期內的失敗」，panic 用於「程式有 bug」。**

| 情況 | 用什麼 |
| --- | --- |
| 檔案不存在、網路失敗、輸入格式錯誤 | error |
| 查無資料、權限不足、超過配額 | error |
| 索引越界、nil 解參照（自己寫錯） | panic（runtime 幫你發） |
| 內建常數的正規表示式編譯失敗 | panic（`MustCompile` 慣例） |
| 初始化必要資源失敗且無法繼續 | panic 或 `log.Fatal`（僅限 `main`） |

完整討論見 [panic 與 recover](panic.html#什麼時候該-panic)。

!!! warning "函式庫不要用 `log.Fatal`"
    `log.Fatal` 內部呼叫 `os.Exit`，**不會執行任何 `defer`**。在函式庫裡用它，會讓呼叫端所有的清理邏輯失效，而且對方完全沒有機會處理。

    `log.Fatal` 只屬於 `main`。

---

## 檢查清單

| 項目 | 理由 |
| --- | --- |
| ✓ 包裝用 `%w` 不用 `%v` | 否則 `errors.Is`／`As` 會失效 |
| ✓ 中間層只加上下文，不 log | 避免同一個錯誤被記錄多次 |
| ✓ 判斷用 `errors.Is`／`As`，不用 `==` 或字串比對 | 包裝後仍然正確 |
| ✓ 每個 `go` 都有明確的錯誤出口 | 否則錯誤會憑空消失 |
| ✓ fire-and-forget 的 goroutine 自己 `recover` | 否則一次 panic 就整個服務下線 |
| ✓ 回傳 `error` 的函式不要用具體錯誤型別的變數 | 避免 nil 介面陷阱 |
| ✓ CI 跑 `errcheck` | 自動抓出「回傳了 error 卻沒接」 |

`errcheck` 已包含在 `golangci-lint` 裡：

```bash
golangci-lint run --enable=errcheck ./...
```

這是錯誤處理中唯一能被完全自動化的部分，很值得放進 CI。
