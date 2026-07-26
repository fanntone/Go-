---
title: context
slug: context
part: p5
number: "5.1"
order: 510
summary: context 的樹狀傳播、四種建構函式的實作、cancelCtx 怎麼通知子節點，以及實務上的使用規範。
updated: "1.26"
---

## context 解決什麼問題

一個 HTTP 請求進來，handler 開了 3 個 goroutine 查資料庫、呼叫外部 API、寫快取。使用者按了取消，或請求逾時了——**怎麼通知這些 goroutine 停手？**

Go 沒有「殺死 goroutine」的機制（這是刻意的，強制中斷會讓資源清理變得不可能）。唯一的方式是**合作式取消**：每個 goroutine 定期檢查「我還該繼續嗎」。

`context` 就是這個「該不該繼續」訊號的標準化載體，外加兩個附加功能：截止時間與請求範圍的值傳遞。

---

## 介面本身很小

```go
type Context interface {
	Deadline() (deadline time.Time, ok bool)
	Done() <-chan struct{}
	Err() error
	Value(key any) any
}
```

四個方法，各司其職：

| 方法 | 用途 |
| --- | --- |
| `Done()` | 回傳一個 channel，取消時會被 **close** |
| `Err()` | `Done` 關閉後說明原因：`Canceled` 或 `DeadlineExceeded` |
| `Deadline()` | 有截止時間的話回傳它 |
| `Value(k)` | 取出請求範圍的值 |

**關鍵設計：用「關閉 channel」當廣播機制。** 關閉一個 channel 會讓所有等待它的接收者同時被喚醒，而且之後任何接收都立刻回傳零值。這正好是「一對多、只發一次、之後永久有效」的取消訊號所需要的語意。

```go
package main

import (
	"context"
	"fmt"
	"time"
)

func worker(ctx context.Context, id int) {
	for {
		select {
		case <-ctx.Done():
			fmt.Printf("worker %d 停止：%v\n", id, ctx.Err())
			return
		case <-time.After(30 * time.Millisecond):
			fmt.Printf("worker %d 工作中\n", id)
		}
	}
}

func main() {
	ctx, cancel := context.WithCancel(context.Background())

	for i := 1; i <= 2; i++ {
		go worker(ctx, i)
	}

	time.Sleep(100 * time.Millisecond)
	cancel() // 一次通知所有人
	time.Sleep(50 * time.Millisecond)
}
```

```text
worker 1 工作中
worker 2 工作中
worker 1 工作中
worker 2 工作中
worker 1 工作中
worker 2 工作中
worker 2 停止：context canceled
worker 1 停止：context canceled
```

---

## 樹狀結構

context 的實作核心是一棵樹。每次呼叫 `WithCancel`、`WithTimeout`、`WithValue` 都是**在既有 context 底下掛一個子節點**。

<figure class="diagram"><svg viewBox="0 0 700 300" role="img" aria-label="context 的樹狀傳播"><rect class="d-box" x="270" y="14" width="170" height="40" rx="5"/><text class="d-t-m d-mid" x="355" y="34">context.Background()</text><text class="d-t-s d-mid" x="355" y="49">emptyCtx，永不取消</text><path class="d-line" d="M355 54 L355 74" marker-end="url(#ar16)"/><rect class="d-box-a" x="255" y="76" width="200" height="42" rx="5"/><text class="d-t-m d-mid" x="355" y="96">WithTimeout(5s)</text><text class="d-t-s d-mid" x="355" y="112">timerCtx　·　HTTP 請求層級</text><path class="d-line" d="M300 118 L180 148" marker-end="url(#ar16)"/><path class="d-line" d="M355 118 L355 148" marker-end="url(#ar16)"/><path class="d-line" d="M410 118 L530 148" marker-end="url(#ar16)"/><rect class="d-box-o" x="60" y="150" width="240" height="42" rx="5"/><text class="d-t-m d-mid" x="180" y="170">WithValue(traceID)</text><text class="d-t-s d-mid" x="180" y="186">valueCtx　·　不影響取消</text><rect class="d-box-w" x="310" y="150" width="90" height="42" rx="5"/><text class="d-t-s d-mid" x="355" y="176">DB 查詢</text><rect class="d-box-w" x="440" y="150" width="200" height="42" rx="5"/><text class="d-t-m d-mid" x="540" y="170">WithTimeout(1s)</text><text class="d-t-s d-mid" x="540" y="186">外部 API 呼叫</text><path class="d-line" d="M180 192 L180 214" marker-end="url(#ar16)"/><rect class="d-box-w" x="90" y="216" width="180" height="36" rx="5"/><text class="d-t-s d-mid" x="180" y="239">快取寫入 goroutine</text><defs><marker id="ar16" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M0 0 L10 5 L0 10 z" style="fill:var(--line-strong)"/></marker></defs><line class="d-dash" x1="15" y1="266" x2="685" y2="266"/><text class="d-t-s" x="15" y="288">取消由上往下傳播：父節點取消 → 所有後代立刻取消。子節點取消不影響父節點與兄弟節點。</text></svg><figcaption><b>取消單向往下傳播。</b>父 context 取消時，所有子孫的 <code>Done()</code> channel 都會被關閉。這讓「請求結束就清理整棵工作樹」變成自動的事。注意 <code>WithValue</code> 產生的節點只加值、不影響取消行為。</figcaption></figure>

### `cancelCtx` 的實作

```go
type cancelCtx struct {
	Context                        // 父 context（嵌入）
	mu       sync.Mutex
	done     atomic.Value          // chan struct{}，延後建立
	children map[canceler]struct{} // 子節點集合
	err      error
	cause    error
}
```

`cancel()` 被呼叫時：

1. 上鎖，檢查是否已經取消過（冪等）。
2. 設定 `err`（`Canceled` 或 `DeadlineExceeded`）。
3. **關閉 `done` channel** —— 所有等待者被喚醒。
4. **遞迴呼叫每個子節點的 `cancel`**。
5. 從父節點的 `children` 中移除自己。

第 5 步是關鍵：**它避免了記憶體洩漏**。如果子 context 完成了卻不從父節點移除，長壽命的父 context（例如整個服務的生命週期）會累積越來越多子節點。

!!! danger "這就是為什麼一定要呼叫 cancel"
    ```go
    // ✗ 洩漏：ctx 永遠掛在父節點的 children 裡
    func bad(parent context.Context) {
        ctx, _ := context.WithTimeout(parent, time.Second)
        doWork(ctx)
    }

    // ✓ 正確
    func good(parent context.Context) {
        ctx, cancel := context.WithTimeout(parent, time.Second)
        defer cancel()
        doWork(ctx)
    }
    ```

    **即使 context 已經因為逾時而取消，你還是應該呼叫 `cancel()`** ——它同時負責停止內部的計時器與從父節點移除自己。

    `go vet` 的 `lostcancel` 檢查會抓這個問題：

    ```bash
    go vet ./...
    ```

    ```text
    ./main.go:8:7: the cancel function is not used on all paths (possible context leak)
    ```

---

## 四種建構函式

### `WithCancel`：手動取消

```go
ctx, cancel := context.WithCancel(parent)
defer cancel()
```

### `WithTimeout` / `WithDeadline`：時間取消

```go
ctx, cancel := context.WithTimeout(parent, 3*time.Second)
defer cancel()

// 等價於
ctx, cancel := context.WithDeadline(parent, time.Now().Add(3*time.Second))
```

內部是 `timerCtx`，包著一個 `cancelCtx` 加上一個 `time.Timer`。

!!! note "子 context 的逾時不能超過父 context"
    ```go
    parent, _ := context.WithTimeout(context.Background(), 1*time.Second)
    child, _ := context.WithTimeout(parent, 10*time.Second)
    // child 實際上 1 秒後就取消了
    ```

    `WithDeadline` 會取「父截止時間」與「指定截止時間」的**較早者**。這符合直覺：子任務不該活得比父任務久。

### `WithValue`：傳遞請求範圍的值

```go
ctx := context.WithValue(parent, userIDKey, "u-1234")
```

`valueCtx` 只存**一組**鍵值對。取值時沿著樹往上找，直到找到或到根節點。**這是線性搜尋**，所以不該塞太多值。

### `WithCancelCause` / `Cause`（Go 1.20+）

!!! version "Go 1.20：取消原因"
    標準的 `ctx.Err()` 只會告訴你 `context canceled`，不知道是誰、為什麼取消。Go 1.20 加入：

    ```go
    package main

    import (
        "context"
        "errors"
        "fmt"
    )

    var ErrQuotaExceeded = errors.New("配額用盡")

    func main() {
        ctx, cancel := context.WithCancelCause(context.Background())

        cancel(ErrQuotaExceeded)

        fmt.Println(ctx.Err())            // context canceled
        fmt.Println(context.Cause(ctx))   // 配額用盡
        fmt.Println(errors.Is(context.Cause(ctx), ErrQuotaExceeded)) // true
    }
    ```

    在複雜的並行系統裡，這對除錯幫助很大。Go 1.21 另外加入 `context.WithoutCancel`（衍生一個不繼承取消的 context，用於「請求結束後仍要完成」的背景工作）與 `context.AfterFunc`（context 取消時執行一個函式）。

---

## `WithValue` 的正確用法

這是 `context` 最常被誤用的部分。

### 鍵一定要用未匯出的自訂型別

```go
package main

import (
	"context"
	"fmt"
)

// ✓ 未匯出的型別，其他套件不可能產生相同的鍵
type ctxKey int

const (
	userIDKey ctxKey = iota
	traceIDKey
)

func WithUserID(ctx context.Context, id string) context.Context {
	return context.WithValue(ctx, userIDKey, id)
}

// 提供型別安全的取值函式，不要讓呼叫端自己斷言
func UserID(ctx context.Context) (string, bool) {
	id, ok := ctx.Value(userIDKey).(string)
	return id, ok
}

func main() {
	ctx := WithUserID(context.Background(), "u-1234")
	fmt.Println(UserID(ctx))         // u-1234 true
	fmt.Println(UserID(context.Background())) // "" false
}
```

**為什麼不能用 `string` 當鍵？**

```go
// ✗ 危險
ctx = context.WithValue(ctx, "user_id", id)
```

任何套件都可能用同樣的字串 `"user_id"`，造成靜默的覆蓋。用未匯出型別可以從型別系統上杜絕碰撞——外部套件連提及你的 `ctxKey` 型別都做不到。

### 只放「請求範圍的中繼資料」

**✓ 適合放進 context：**

- request ID / trace ID
- 已認證的使用者身分
- 語系、時區偏好
- 分散式追蹤的 span

**✗ 不該放進 context：**

- 資料庫連線、logger、設定 —— 這些是**相依**，應該透過函式參數或 struct 欄位注入
- 函式的必要輸入參數 —— 那應該是明確的參數
- 可變狀態 —— context 應該視為不可變

```go
// ✗ 把相依藏在 context 裡
func handler(ctx context.Context) {
	db := ctx.Value("db").(*sql.DB) // 編譯期看不出相依、無法測試、可能 panic
	db.Query(...)
}

// ✓ 明確的相依注入
type Handler struct {
	db     *sql.DB
	logger *slog.Logger
}

func (h *Handler) Handle(ctx context.Context, req Request) error {
	// ctx 只用來傳取消訊號與 trace ID
	return h.db.QueryContext(ctx, ...)
}
```

---

## 使用規範

這幾條是 Go 社群公認的慣例：

### ① context 是第一個參數，命名為 `ctx`

```go
func DoSomething(ctx context.Context, arg string) error
```

**不要**放進 struct 欄位（除非該 struct 本身代表一次請求，例如 `http.Request`）。

### ② 不要傳 nil context

```go
// ✗
DoSomething(nil, "x")

// ✓ 不知道用什麼時
DoSomething(context.TODO(), "x")

// ✓ 頂層進入點
DoSomething(context.Background(), "x")
```

`Background()` 與 `TODO()` 實作完全相同，差別純粹是**意圖表達**：`TODO` 表示「這裡之後應該接上真正的 context」，靜態分析工具會標示出來。

### ③ 一路往下傳，不要中斷

```go
// ✗ 中斷了取消鏈
func handler(ctx context.Context) {
	go doWork(context.Background()) // 這個 goroutine 不會被取消
}

// ✓
func handler(ctx context.Context) {
	go doWork(ctx)
}

// ✓ 需要「請求結束後繼續執行」時，用明確的 API（Go 1.21+）
func handler(ctx context.Context) {
	bg := context.WithoutCancel(ctx) // 保留 value，但不繼承取消
	go auditLog(bg)
}
```

### ④ 在阻塞操作處檢查取消

```go
func process(ctx context.Context, items []Item) error {
	for i, item := range items {
		// 每處理一批就檢查一次
		if i%100 == 0 {
			select {
			case <-ctx.Done():
				return ctx.Err()
			default:
			}
		}
		if err := handle(item); err != nil {
			return err
		}
	}
	return nil
}
```

CPU 密集的迴圈要自己插入檢查點。I/O 操作則盡量使用支援 context 的 API：

```go
// 標準庫大多有 *Context 版本
db.QueryContext(ctx, query)
http.NewRequestWithContext(ctx, "GET", url, nil)
conn.PingContext(ctx)
```

---

## 常見錯誤與解法

| 症狀 | 原因 | 解法 |
| --- | --- | --- |
| 記憶體緩慢成長、goroutine 數只增不減 | 忘記呼叫 `cancel` | `defer cancel()`，CI 跑 `go vet` |
| 設了 timeout 卻沒作用 | 內層函式沒吃 `ctx` | 整條鏈都要傳，用 `*Context` 版本的 API |
| 取消訊號傳不進背景工作 | `go` 出去時傳了 `Background()` | 傳同一個 `ctx`；真要脫離用 `WithoutCancel` |
| CPU 密集迴圈停不下來 | 沒有檢查點 | 迴圈中定期 `select` 檢查 `ctx.Done()` |
| 測試難寫、相依看不出來 | 用 `WithValue` 傳相依 | 相依用參數或 struct 欄位注入 |
| `ctx.Value` 拿到別人的東西 | 用 `string` 當鍵 | 未匯出的自訂型別當鍵 |

### ① 忘記 `cancel`：最常見的一個

```go
// ✗ ctx 永遠留在父節點的 children 裡，計時器也不會停
func bad(parent context.Context) {
	ctx, _ := context.WithTimeout(parent, time.Second)
	doWork(ctx)
}

// ✓
func good(parent context.Context) {
	ctx, cancel := context.WithTimeout(parent, time.Second)
	defer cancel()
	doWork(ctx)
}
```

**即使 context 已經因逾時而取消，仍然要呼叫 `cancel()`**——它同時負責停掉內部計時器、以及把自己從父節點的子節點集合移除。長壽命的父 context（例如整個服務的生命週期）沒有這一步就會持續累積子節點。

`go vet` 的 `lostcancel` 檢查抓得到：

```bash
go vet ./...
```

```text
./main.go:8:7: the cancel function is not used on all paths (possible context leak)
```

**這個檢查一定要放進 CI。** 它是 context 相關問題中唯一能自動化的部分。

### ② timeout 設了卻沒用：中間有一層沒吃 ctx

```go
// ✗ 5 秒的 timeout 完全沒有作用
func handler(w http.ResponseWriter, r *http.Request) {
	ctx, cancel := context.WithTimeout(r.Context(), 5*time.Second)
	defer cancel()

	result := slowQuery() // ← 沒收 ctx，會一直跑到自己結束為止
	_ = result
}

// ✓ 整條鏈都要傳，而且要用支援 context 的 API
func handler(w http.ResponseWriter, r *http.Request) {
	ctx, cancel := context.WithTimeout(r.Context(), 5*time.Second)
	defer cancel()

	result, err := slowQuery(ctx)
	if err != nil {
		http.Error(w, err.Error(), http.StatusGatewayTimeout)
		return
	}
	_ = result
}
```

**timeout 只有在整條呼叫鏈都尊重 context 時才有意義。** 標準庫大多提供了 `*Context` 版本，優先用它們：

```go
db.QueryContext(ctx, query, args...)
http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
conn.PingContext(ctx)
cmd := exec.CommandContext(ctx, "ls")
```

### ③ 背景工作傳了 `Background()`

```go
// ✗ 請求結束了，這個 goroutine 還在跑，而且沒人能叫停
func handler(w http.ResponseWriter, r *http.Request) {
	go auditLog(context.Background(), r.URL.Path)
}
```

這通常是為了解決一個真實問題：**請求結束後 `r.Context()` 就被取消了**，直接傳它進去會讓背景工作立刻中斷。

正確做法（Go 1.21+）：

```go
func handler(w http.ResponseWriter, r *http.Request) {
	// 保留 trace ID 等值，但不繼承「請求結束就取消」
	bg := context.WithoutCancel(r.Context())

	// 再給它自己的期限，避免永遠跑下去
	bg, cancel := context.WithTimeout(bg, 30*time.Second)

	go func() {
		defer cancel()
		defer func() {
			if rec := recover(); rec != nil {
				slog.Error("audit panic", "err", rec)
			}
		}()
		auditLog(bg, r.URL.Path)
	}()
}
```

三個重點：`WithoutCancel` 保留值但切斷取消、再加一個自己的 timeout、goroutine 自己 recover（見 [panic](panic.html#在服務邊界攔截-panic)）。

### ④ CPU 密集迴圈感受不到取消

context 是**合作式**的——沒有人能強制中斷你的迴圈。純運算的程式碼必須自己插檢查點：

```go
package main

import (
	"context"
	"fmt"
	"time"
)

func process(ctx context.Context, items []int) (int, error) {
	sum := 0
	for i, v := range items {
		// 每處理一批檢查一次，不要每輪都檢查（select 有成本）
		if i%1000 == 0 {
			select {
			case <-ctx.Done():
				return 0, ctx.Err()
			default:
			}
		}
		sum += v * v
	}
	return sum, nil
}

func main() {
	items := make([]int, 5_000_000)
	for i := range items {
		items[i] = i
	}

	ctx, cancel := context.WithTimeout(context.Background(), time.Millisecond)
	defer cancel()

	sum, err := process(ctx, items)
	fmt.Println(sum, err)
}
```

```text
0 context deadline exceeded
```

**檢查頻率是個取捨**：太頻繁（每輪都檢查）會拖慢迴圈，太稀疏則反應遲鈍。以「每 1–10 毫秒的工作量」為單位檢查一次通常剛好。

### ⑤ 把 context 存進 struct

```go
// ✗ 幾乎總是錯的
type Service struct {
	ctx context.Context
	db  *sql.DB
}

// ✓ context 走參數，第一個位置
type Service struct {
	db *sql.DB
}

func (s *Service) Load(ctx context.Context, id int64) (*User, error) {
	return s.query(ctx, id)
}
```

原因：**context 是「一次呼叫」的生命週期，struct 是「一個物件」的生命週期**，兩者不一致。存進 struct 之後，同一個 `Service` 被兩個請求共用時，第一個請求的取消會影響第二個。

唯一的例外是「這個 struct 本身就代表一次請求」，例如 `http.Request`。

### ⑥ 用 `WithValue` 傳相依

```go
// ✗ 相依藏在 context 裡：編譯期看不出來、無法測試、型別斷言可能 panic
func handler(ctx context.Context) {
	db := ctx.Value("db").(*sql.DB)
	db.Query(...)
}

// ✓ 明確注入
type Handler struct {
	db     *sql.DB
	logger *slog.Logger
}

func (h *Handler) Handle(ctx context.Context, req Request) error {
	return h.db.QueryContext(ctx, ...)  // ctx 只帶取消訊號與 trace ID
}
```

`WithValue` 的正當用途只有**請求範圍的中繼資料**：request ID、trace span、已認證的使用者身分、語系。資料庫連線、logger、設定這些是**相依**，應該用參數或欄位注入。

判準：**如果拿掉它函式就不能運作，那它是相依，不該放 context。**

（鍵一定要用未匯出的自訂型別，理由見[前面那節](#withvalue-的正確用法)。）

---

## 一個完整的例子

把所有東西串起來：

```go
package main

import (
	"context"
	"errors"
	"fmt"
	"sync"
	"time"
)

type Result struct {
	Source string
	Value  string
	Err    error
}

// 同時向多個來源查詢，取最快回來的那個
func fetchFastest(ctx context.Context, sources []string) (Result, error) {
	ctx, cancel := context.WithCancel(ctx)
	defer cancel() // 一旦有結果，取消其餘查詢

	results := make(chan Result, len(sources))
	var wg sync.WaitGroup

	for _, src := range sources {
		wg.Add(1)
		go func(src string) {
			defer wg.Done()
			results <- fetch(ctx, src)
		}(src)
	}

	go func() {
		wg.Wait()
		close(results)
	}()

	var lastErr error
	for r := range results {
		if r.Err == nil {
			return r, nil // 拿到第一個成功的，defer cancel() 會停掉其他人
		}
		if !errors.Is(r.Err, context.Canceled) {
			lastErr = r.Err
		}
	}

	if lastErr == nil {
		lastErr = errors.New("沒有可用的來源")
	}
	return Result{}, lastErr
}

func fetch(ctx context.Context, src string) Result {
	delay := map[string]time.Duration{
		"cache": 10 * time.Millisecond,
		"db":    50 * time.Millisecond,
		"api":   200 * time.Millisecond,
	}[src]

	select {
	case <-time.After(delay):
		return Result{Source: src, Value: "來自 " + src}
	case <-ctx.Done():
		return Result{Source: src, Err: ctx.Err()}
	}
}

func main() {
	ctx, cancel := context.WithTimeout(context.Background(), 100*time.Millisecond)
	defer cancel()

	r, err := fetchFastest(ctx, []string{"cache", "db", "api"})
	if err != nil {
		fmt.Println("失敗:", err)
		return
	}
	fmt.Printf("最快的是 %s：%s\n", r.Source, r.Value)
}
```

```text
最快的是 cache：來自 cache
```

!!! tip "更好的選擇：`errgroup`"
    上面手寫的協調邏輯，`golang.org/x/sync/errgroup` 已經封裝好了：

    ```go
    g, ctx := errgroup.WithContext(ctx)
    for _, src := range sources {
        g.Go(func() error { return fetchInto(ctx, src, &out) })
    }
    if err := g.Wait(); err != nil {
        return err
    }
    ```

    `errgroup.WithContext` 會在任一 goroutine 回傳錯誤時自動取消 context，並讓 `Wait()` 回傳第一個錯誤。它還有 `SetLimit` 可以限制並行數。這是實務上處理「一組並行子任務」的標準工具。

---

下一節談同步原語：`sync` 套件裡那些鎖是怎麼實作的，以及什麼時候該用哪一個。
