---
title: defer
slug: defer
part: p4
number: "4.3"
order: 430
summary: 三種 defer 實作路徑（開放編碼、堆疊、堆積）、參數何時求值、迴圈中 defer 的陷阱，以及具名回傳值與 defer 的互動。
updated: "1.26"
---

## 兩條規則決定一切

`defer` 的語意只有兩條規則，但幾乎所有 `defer` 的困惑都來自沒把它們分清楚：

1. **`defer` 的函式在外層函式回傳前執行，順序是後進先出（LIFO）。**
2. **`defer` 的參數在 `defer` 陳述式執行的當下就求值，不是等到真正呼叫時。**

第二條是關鍵。

```go
package main

import "fmt"

func main() {
	for i := 0; i < 3; i++ {
		defer fmt.Println("defer:", i) // i 的值當下就被複製了
	}
	fmt.Println("函式本體結束")
}
```

```text
函式本體結束
defer: 2
defer: 1
defer: 0
```

`i` 的值在每次 `defer` 陳述式執行時就被複製進去了。LIFO 順序讓輸出倒過來。

### 對照：閉包捕捉的是變數

```go
package main

import "fmt"

func main() {
	x := 1

	defer fmt.Println("① 參數求值:", x)      // 現在就複製 x = 1
	defer func() { fmt.Println("② 閉包捕捉:", x) }() // 捕捉變數本身

	x = 99
	fmt.Println("修改後 x =", x)
}
```

```text
修改後 x = 99
② 閉包捕捉: 99
① 參數求值: 1
```

**要延後求值就用閉包，要當下求值就直接傳參數。** 這是刻意的設計，兩種都有用。

---

## 三種實作路徑

`defer` 的成本差異非常大，取決於編譯器選了哪條路徑。

<figure class="diagram"><svg viewBox="0 0 700 330" role="img" aria-label="defer 的三種實作路徑"><rect class="d-box-o" x="15" y="14" width="670" height="88" rx="7"/><text class="d-t-b" x="30" y="38">① 開放編碼 open-coded defer　—— 最快，約 1 ns</text><text class="d-t-s" x="30" y="60">條件：函式內 defer 數量 ≤ 8、沒有在迴圈裡 defer、沒有關閉最佳化</text><text class="d-t-s" x="30" y="80">做法：編譯器直接把延後的呼叫「內聯」到所有回傳點之前，用一個位元遮罩記錄哪些 defer 已註冊</text><text class="d-t-s" x="30" y="96">→ 完全沒有 runtime 呼叫、沒有堆積配置。絕大多數程式碼走這條路。</text><rect class="d-box-w" x="15" y="112" width="670" height="88" rx="7"/><text class="d-t-b" x="30" y="136">② 堆疊上的 _defer　—— 中等，約 30 ns</text><text class="d-t-s" x="30" y="158">條件：不符合開放編碼（例如 defer 在迴圈或條件分支中，但數量可控）</text><text class="d-t-s" x="30" y="178">做法：在堆疊上配置一個 _defer 記錄，呼叫 runtime.deferprocStack 串進 g._defer 鏈結串列</text><text class="d-t-s" x="30" y="194">→ 有 runtime 呼叫，但沒有堆積配置。</text><rect class="d-box-d" x="15" y="210" width="670" height="88" rx="7"/><text class="d-t-b" x="30" y="234">③ 堆積上的 _defer　—— 最慢，約 50–100 ns</text><text class="d-t-s" x="30" y="256">條件：defer 在迴圈中且次數不確定，堆疊上放不下</text><text class="d-t-s" x="30" y="276">做法：runtime.deferproc 從 P 的 deferpool 或堆積取得 _defer 記錄</text><text class="d-t-s" x="30" y="292">→ 有 runtime 呼叫 + 可能的堆積配置。要盡量避免。</text><text class="d-t-s" x="15" y="322">回傳時，runtime.deferreturn 走過 g._defer 鏈結串列依序執行（路徑 ②③）；路徑 ① 則是編譯器直接產生的指令。</text></svg><figcaption><b>三條路徑。</b>Go 1.13 引入堆疊 defer、Go 1.14 引入開放編碼 defer，把 <code>defer</code> 從「有明顯成本、熱路徑要避免」變成「幾乎免費」。舊資料說「defer 很慢」，那是 Go 1.12 以前的事。</figcaption></figure>

!!! version "defer 效能的演進"
    | 版本 | 變化 | 典型成本 |
    | --- | --- | --- |
    | ≤ 1.12 | 一律堆積配置 `_defer` | ~50 ns |
    | 1.13 | 加入堆疊配置路徑 | ~35 ns |
    | 1.14 | 加入開放編碼路徑 | ~1 ns（符合條件時） |
    | 1.22 | 進一步改善包含 `defer` 的函式的內聯 | — |

    現在 `defer` 在絕大多數情況下幾乎免費。**不要為了效能而避免使用 `defer`**，除非你已經量測證明它是瓶頸。

### 驗證走了哪條路

```go
package main

import "sync"

var mu sync.Mutex

// 開放編碼：defer 在函式頂層，數量少
func fast() {
	mu.Lock()
	defer mu.Unlock()
	// ...
}

// 堆積 defer：在迴圈裡
func slow(n int) {
	for i := 0; i < n; i++ {
		mu.Lock()
		defer mu.Unlock() // ✗ 而且會累積到函式結束才全部解鎖！
	}
}

func main() {
	fast()
}
```

```bash
go build -gcflags="-d=defer" ./main.go
```

```text
./main.go:10:2: open-coded defer
./main.go:18:3: heap-allocated defer
```

`-d=defer` 會直接告訴你每個 `defer` 用了哪條路徑。

---

## 迴圈中的 defer：兩個問題

```go
package main

import "os"

// ✗ 有兩個問題
func processBad(paths []string) error {
	for _, p := range paths {
		f, err := os.Open(p)
		if err != nil {
			return err
		}
		defer f.Close() // 問題一：直到函式結束才關
		                // 問題二：走堆積 defer 路徑
		// ... 處理 f ...
	}
	return nil
}
```

如果 `paths` 有 10000 個檔案，這個函式會**同時開著 10000 個檔案描述子**直到結束，很可能撞到 `ulimit`。

**解法一：包一層函式**

```go
func processGood(paths []string) error {
	for _, p := range paths {
		if err := processOne(p); err != nil {
			return err
		}
	}
	return nil
}

func processOne(p string) error {
	f, err := os.Open(p)
	if err != nil {
		return err
	}
	defer f.Close() // 每次 processOne 回傳時就關，而且是開放編碼路徑

	// ... 處理 f ...
	return nil
}
```

**解法二：用匿名函式**

```go
func processInline(paths []string) error {
	for _, p := range paths {
		err := func() error {
			f, err := os.Open(p)
			if err != nil {
				return err
			}
			defer f.Close()
			// ... 處理 f ...
			return nil
		}()
		if err != nil {
			return err
		}
	}
	return nil
}
```

**解法一更好**——有名字的函式比較好測試也比較好讀。

---

## defer 與具名回傳值

這是 `defer` 最強大也最容易誤用的能力：**`defer` 可以修改具名回傳值**。

```go
package main

import "fmt"

// 具名回傳值：defer 可以改它
func namedReturn() (result int) {
	defer func() { result *= 2 }()
	return 5 // 先把 5 賦給 result，再執行 defer，最後真的回傳
}

// 匿名回傳值：defer 改不到
func anonReturn() int {
	result := 5
	defer func() { result *= 2 }() // 改的是區域變數，不是回傳值
	return result                   // 回傳值已經複製走了
}

func main() {
	fmt.Println(namedReturn()) // 10
	fmt.Println(anonReturn())  // 5
}
```

### `return` 其實是兩步

理解這個差異的關鍵：`return x` 在有 `defer` 的函式裡不是一個原子操作，而是：

```text
1. 把 x 賦值給回傳值變數（具名的話就是那個變數，匿名的話是編譯器產生的隱藏變數）
2. 執行所有 defer
3. 真正跳回呼叫者
```

具名回傳值在步驟 2 還能被改到；匿名回傳值的隱藏變數則不在 `defer` 的作用域裡。

### 實用場景一：統一包裝錯誤

```go
package main

import (
	"errors"
	"fmt"
)

func loadConfig(path string) (err error) {
	defer func() {
		if err != nil {
			err = fmt.Errorf("載入設定 %q: %w", path, err)
		}
	}()

	if path == "" {
		return errors.New("路徑為空")
	}
	return nil
}

func main() {
	fmt.Println(loadConfig(""))
}
```

```text
載入設定 "": 路徑為空
```

不用在每個 `return` 點都寫一次包裝。

### 實用場景二：把 panic 轉成 error

```go
package main

import (
	"fmt"
	"strings"
)

func safeParse(input string) (result []string, err error) {
	defer func() {
		if r := recover(); r != nil {
			err = fmt.Errorf("解析 panic: %v", r)
			result = nil
		}
	}()

	if input == "" {
		panic("空輸入")
	}
	return strings.Split(input, ","), nil
}

func main() {
	fmt.Println(safeParse("a,b,c"))
	fmt.Println(safeParse(""))
}
```

```text
[a b c] <nil>
[] 解析 panic: 空輸入
```

這是函式庫邊界常用的模式——內部用 panic 簡化深層遞迴的錯誤傳遞，對外一律回傳 error。標準庫的 `encoding/json` 內部就是這樣做的。

### 實用場景三：計時

```go
package main

import (
	"fmt"
	"time"
)

func timed(name string) func() {
	start := time.Now()
	return func() {
		fmt.Printf("%s 耗時 %v\n", name, time.Since(start))
	}
}

func slowWork() {
	defer timed("slowWork")() // 注意兩對括號
	time.Sleep(50 * time.Millisecond)
}

func main() { slowWork() }
```

注意 `defer timed("slowWork")()` 的兩對括號：`timed(...)` **立刻執行**（記下開始時間），它回傳的函式才是被延後的。

---

## 常見錯誤

### ① `defer` 遇到會 panic 的接收者

```go
// ✗ 如果 Open 失敗，f 是 nil，defer 會 panic
f, err := os.Open(path)
defer f.Close()
if err != nil {
	return err
}

// ✓ 先檢查錯誤
f, err := os.Open(path)
if err != nil {
	return err
}
defer f.Close()
```

**`defer` 一定要放在錯誤檢查之後。**

### ② 忽略 `Close` 的錯誤

```go
// ✗ 寫入檔案時，Close 的錯誤可能代表資料沒有真的落盤
defer f.Close()

// ✓ 對寫入的檔案要檢查 Close
func writeFile(path string, data []byte) (err error) {
	f, err := os.Create(path)
	if err != nil {
		return err
	}
	defer func() {
		if cerr := f.Close(); cerr != nil && err == nil {
			err = cerr
		}
	}()

	_, err = f.Write(data)
	return err
}
```

唯讀開啟的檔案可以忽略 `Close` 錯誤；**寫入的檔案不行**——緩衝區的資料可能在 `Close` 時才真正寫出，這時候的錯誤是真錯誤。

### ③ 對 mutex 用錯位置

```go
// ✗ 鎖住的範圍太大：整個函式
func (c *Cache) Get(k string) (V, bool) {
	c.mu.Lock()
	defer c.mu.Unlock()

	v, ok := c.m[k]
	if !ok {
		return zero, false
	}
	expensiveLogging(v) // 持鎖做無關的事
	return v, true
}

// ✓ 縮小臨界區
func (c *Cache) Get(k string) (V, bool) {
	c.mu.RLock()
	v, ok := c.m[k]
	c.mu.RUnlock()

	if !ok {
		return zero, false
	}
	expensiveLogging(v)
	return v, true
}
```

`defer unlock` 很方便，但它會把鎖持有到函式結束。如果函式後半段跟共享狀態無關，就別用 `defer`。

### ④ 以為 `os.Exit` 會執行 defer

```go
func main() {
	defer fmt.Println("這行不會印出來")
	os.Exit(1) // 直接結束行程，不執行任何 defer
}
```

`os.Exit`、`log.Fatal`（內部呼叫 `os.Exit`）、以及 goroutine 被 `runtime.Goexit` 以外的方式終止，都不會執行 `defer`。

這也是為什麼**函式庫裡不該用 `log.Fatal`**——它會讓呼叫端的所有清理邏輯失效。

### ⑤ LIFO 順序：這正是資源釋放需要的

`defer` 的後進先出常被當成一個要背的規則，但它其實是**唯一正確的順序**——資源的釋放本來就該跟取得反過來。

```go
package main

import "fmt"

func main() {
	fmt.Println("開啟連線")
	defer fmt.Println("關閉連線") // 最後才關

	fmt.Println("開始交易")
	defer fmt.Println("結束交易") // 交易要在連線關閉前結束

	fmt.Println("取得鎖")
	defer fmt.Println("釋放鎖") // 鎖最先放掉

	fmt.Println("--- 做事 ---")
}
```

```text
開啟連線
開始交易
取得鎖
--- 做事 ---
釋放鎖
結束交易
關閉連線
```

如果是先進先出，就會變成「先關連線，再結束交易」——後者需要前者還活著，直接壞掉。

**所以寫法上只要照著「取得資源就馬上 defer 釋放」，順序自然就對了**，不需要自己想。

### ⑥ 參數立刻求值造成的錯誤

[開頭的第二條規則](#兩條規則決定一切)在實務上最常見的兩種踩法：

```go
package main

import (
	"fmt"
	"time"
)

func wrong() {
	start := time.Now()

	// ✗ time.Since 現在就算了 → 永遠是 0
	// （go vet 會抓到這個錯誤：call to time.Since is not deferred）
	defer fmt.Println("耗時:", time.Since(start))

	time.Sleep(50 * time.Millisecond)
}

func right() {
	start := time.Now()
	defer func() {
		fmt.Println("耗時:", time.Since(start)) // ✓ 到函式結束才算
	}()

	time.Sleep(50 * time.Millisecond)
}

func main() {
	wrong() // 耗時: 0s
	right() // 耗時: 50.1ms
}
```

第二種是「defer 想印最終的錯誤」：

```go
// ✗ err 現在是 nil，印出來永遠是 nil
func bad() (err error) {
	defer fmt.Println("結果:", err)
	err = doWork()
	return err
}

// ✓ 閉包捕捉變數本身
func good() (err error) {
	defer func() { fmt.Println("結果:", err) }()
	err = doWork()
	return err
}
```

**規則**：`defer f(x)` 中的 `x` 現在就求值；要延後求值就包一層 `defer func(){ ... }()`。

!!! tip "`go vet` 抓得到計時這一種"
    ```bash
    go vet ./...
    ```

    ```text
    ./main.go:10:31: call to time.Since is not deferred
    ```

    `go vet` 有專門的檢查針對 `defer` 中直接呼叫 `time.Since`——因為這幾乎必定是錯的。

    但它**只涵蓋這個特例**。上面第二個例子（`defer fmt.Println("結果:", err)`）語法上完全合法，vet 不會有任何意見，只能靠自己記得規則。

### ⑦ 條件式清理：成功之後不該回滾

交易與檔案這類「成功要提交、失敗要還原」的資源，不能無腦 `defer`：

```go
package main

import (
	"context"
	"database/sql"
)

// ✓ 標準寫法：先 defer Rollback，成功後才 Commit
func transfer(ctx context.Context, db *sql.DB, from, to int64, amount int) error {
	tx, err := db.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	// 已經 Commit 的話，Rollback 會回傳 ErrTxDone，忽略即可
	defer tx.Rollback() //nolint:errcheck

	if _, err := tx.ExecContext(ctx,
		`UPDATE accounts SET balance = balance - $1 WHERE id = $2`, amount, from); err != nil {
		return err
	}
	if _, err := tx.ExecContext(ctx,
		`UPDATE accounts SET balance = balance + $1 WHERE id = $2`, amount, to); err != nil {
		return err
	}

	return tx.Commit() // 成功才走到這
}
```

`defer tx.Rollback()` 這個慣用法之所以安全，是因為**`Commit` 之後的 `Rollback` 是無害的 no-op**（回傳 `sql.ErrTxDone`）。所以你不需要在每個錯誤路徑手寫 rollback，也不會誤回滾已提交的交易。

同樣的模式適用於「建到一半失敗要清掉」：

```go
func createFile(path string) (err error) {
	f, err := os.Create(path)
	if err != nil {
		return err
	}

	success := false
	defer func() {
		f.Close()
		if !success {
			os.Remove(path) // 只有失敗時才刪
		}
	}()

	if _, err := f.Write(data); err != nil {
		return err
	}

	success = true
	return nil
}
```

### ⑧ 什麼時候不要用 defer

自從 Go 1.14 的[開放編碼](#三種實作路徑)之後，`defer` 幾乎免費，**不要為了效能而避免使用它**。真正該避開的只有三種情況：

| 情況 | 為什麼 | 替代做法 |
| --- | --- | --- |
| 迴圈中取得資源 | 累積到函式結束才釋放 | [包一層函式](#迴圈中的-defer兩個問題) |
| 鎖只需要保護前半段 | `defer` 會持有到函式結束 | 手動 `Unlock`，縮小臨界區 |
| 每秒數千萬次的極熱路徑 | 仍有微小成本 | 先量測，確認是瓶頸再說 |

第三種請務必**先量測**。`-gcflags="-d=defer"` 會告訴你走的是哪條路徑；如果印出 `open-coded defer`，那它的成本大約是 1 奈秒，幾乎不可能是你的瓶頸。

---

## `defer` 與 goroutine

每個 goroutine 有自己的 `_defer` 鏈結串列（`g._defer`）。goroutine 結束時會執行它自己的 `defer`，但**不會**執行建立它的那個 goroutine 的 `defer`。

```go
package main

import (
	"fmt"
	"sync"
)

func main() {
	var wg sync.WaitGroup

	for i := range 3 {
		wg.Add(1)
		go func() {
			defer wg.Done()          // 這個 defer 屬於這個 goroutine
			defer fmt.Println("完成", i)
			fmt.Println("開始", i)
		}()
	}

	wg.Wait()
	fmt.Println("全部結束")
}
```

`defer wg.Done()` 是最標準的用法——確保不管 goroutine 怎麼結束（正常回傳或 panic），計數都會遞減。**沒有它，一次 panic 就會讓 `wg.Wait()` 永遠卡住。**

---

下一節談 `panic` 與 `recover`——它們跟 `defer` 是同一套機制的三個面向。
