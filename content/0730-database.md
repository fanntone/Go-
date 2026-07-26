---
title: database/sql
slug: database
part: p7
number: "7.3"
order: 730
summary: 驅動介面與連線池的運作、prepared statement 的隱藏成本、NULL 處理，以及那些忘記 Close 就會出事的地方。
updated: "1.26"
---

## 一層抽象，不是 ORM

`database/sql` 不是 ORM，它是**驅動介面 + 連線池**。它本身不知道 SQL 語法、不做物件映射、也不管方言差異。

```text
你的程式碼
    ↓
database/sql（連線池、交易管理、掃描）
    ↓
database/sql/driver（驅動介面）
    ↓
具體驅動（pgx / mysql / sqlite3 …）
    ↓
資料庫
```

驅動用匿名 import 註冊自己：

```go
import (
	"database/sql"
	_ "github.com/jackc/pgx/v5/stdlib" // 執行它的 init()，呼叫 sql.Register("pgx", ...)
)

db, err := sql.Open("pgx", dsn)
```

---

## `sql.DB` 是連線池，不是連線

這是最常見的誤解。

```go
db, err := sql.Open("pgx", dsn)
```

**`sql.Open` 不會連線到資料庫。** 它只是建立一個 `sql.DB` 物件並驗證驅動名稱。真正的連線在第一次執行查詢時才建立。

```go
package main

import (
	"context"
	"database/sql"
	"log"
	"time"

	_ "github.com/jackc/pgx/v5/stdlib"
)

func main() {
	db, err := sql.Open("pgx", "postgres://user:pass@localhost/mydb")
	if err != nil {
		log.Fatal(err) // 只會在驅動名稱錯誤或 DSN 格式錯誤時發生
	}
	defer db.Close()

	// ✓ 用 Ping 驗證真的連得上
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	if err := db.PingContext(ctx); err != nil {
		log.Fatal("無法連線:", err)
	}

	log.Println("連線正常")
}
```

`sql.DB` **是並行安全的，而且應該在整個程式中共用一份**。不要每個請求都 `sql.Open`。

### 連線池設定

```go
db.SetMaxOpenConns(25)                  // 同時開啟的連線上限（含使用中與閒置）
db.SetMaxIdleConns(25)                  // 保留的閒置連線數
db.SetConnMaxLifetime(5 * time.Minute)  // 連線最長存活時間
db.SetConnMaxIdleTime(1 * time.Minute)  // 連線閒置多久後關閉
```

<figure class="diagram"><svg viewBox="0 0 700 270" role="img" aria-label="database/sql 連線池的運作"><rect class="d-box-a" x="15" y="14" width="200" height="120" rx="6"/><text class="d-t-b d-mid" x="115" y="36">使用中 in-use</text><rect class="d-box-o" x="30" y="46" width="170" height="24" rx="3"/><text class="d-t-s d-mid" x="115" y="63">conn 1 — 執行查詢</text><rect class="d-box-o" x="30" y="74" width="170" height="24" rx="3"/><text class="d-t-s d-mid" x="115" y="91">conn 2 — 交易中</text><text class="d-t-s d-mid" x="115" y="120">Rows 沒關就一直算「使用中」</text><rect class="d-box-w" x="245" y="14" width="200" height="120" rx="6"/><text class="d-t-b d-mid" x="345" y="36">閒置 idle</text><rect class="d-box" x="260" y="46" width="170" height="24" rx="3"/><text class="d-t-s d-mid" x="345" y="63">conn 3</text><rect class="d-box" x="260" y="74" width="170" height="24" rx="3"/><text class="d-t-s d-mid" x="345" y="91">conn 4</text><text class="d-t-s d-mid" x="345" y="120">受 MaxIdleConns 限制</text><rect class="d-box-d" x="475" y="14" width="210" height="120" rx="6"/><text class="d-t-b d-mid" x="580" y="36">等待中 waiting</text><rect class="d-box" x="490" y="46" width="180" height="24" rx="3"/><text class="d-t-s d-mid" x="580" y="63">goroutine A 排隊</text><rect class="d-box" x="490" y="74" width="180" height="24" rx="3"/><text class="d-t-s d-mid" x="580" y="91">goroutine B 排隊</text><text class="d-t-s d-mid" x="580" y="120">達到 MaxOpenConns 時</text><path class="d-line-a" d="M245 60 L219 60" marker-end="url(#ar23)"/><text class="d-t-a d-mid" x="232" y="152">取用</text><path class="d-line-a" d="M215 100 L241 100" marker-end="url(#ar23)"/><text class="d-t-a d-mid" x="228" y="172">歸還</text><defs><marker id="ar23" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M0 0 L10 5 L0 10 z" style="fill:var(--accent)"/></marker></defs><line class="d-dash" x1="15" y1="192" x2="685" y2="212"/><text class="d-t-s" x="15" y="234">db.Stats() 可以取得這些數字。WaitCount 與 WaitDuration 持續成長 = 池子太小或查詢太慢。</text><text class="d-t-s" x="15" y="256">MaxIdleConns 應該設成跟 MaxOpenConns 一樣，否則高峰過後連線會被反覆關閉與重建。</text></svg><figcaption><b>連線池的三種狀態。</b>常見的錯誤設定是 <code>MaxOpenConns=100</code> 但 <code>MaxIdleConns</code> 維持預設的 2——流量下降時 98 條連線被關閉，下一波流量來又要全部重建。</figcaption></figure>

### 設定建議

| 設定 | 建議值 | 理由 |
| --- | --- | --- |
| `MaxOpenConns` | 依資料庫的 `max_connections` 除以應用實例數 | 不能超過資料庫能承受的總量 |
| `MaxIdleConns` | **等於 `MaxOpenConns`** | 避免反覆建立與關閉連線 |
| `ConnMaxLifetime` | 5–30 分鐘 | 讓連線輪替，配合負載平衡器與資料庫的 failover |
| `ConnMaxIdleTime` | 1–5 分鐘 | 低流量時釋放資源 |

!!! warning "`ConnMaxLifetime` 一定要設"
    沒設的話連線會永久存在。這會造成幾個問題：

    - 資料庫做 failover 或滾動重啟時，舊連線指向已下線的節點
    - 負載平衡器（例如 PgBouncer、雲端的 RDS Proxy）無法重新分配流量
    - 某些防火牆會靜默切斷長時間的閒置連線，你的程式卻不知道

    設一個小於資料庫端 `idle_timeout` 的值。

### 監控連線池

```go
package main

import (
	"database/sql"
	"log/slog"
	"time"
)

func monitorPool(db *sql.DB) {
	for range time.Tick(30 * time.Second) {
		s := db.Stats()
		slog.Info("db pool",
			"open", s.OpenConnections,
			"in_use", s.InUse,
			"idle", s.Idle,
			"wait_count", s.WaitCount,
			"wait_duration", s.WaitDuration,
			"max_idle_closed", s.MaxIdleClosed,
			"max_lifetime_closed", s.MaxLifetimeClosed,
		)
	}
}
```

**要盯的兩個指標：**

- **`WaitCount` 持續成長** = 連線池不夠用。要嘛調大 `MaxOpenConns`，要嘛優化慢查詢。
- **`MaxIdleClosed` 很高** = `MaxIdleConns` 太小，連線在反覆建立與關閉。

---

## 四種查詢方法

```go
package main

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
)

type User struct {
	ID    int64
	Name  string
	Email string
}

// ① QueryRowContext：預期恰好一列
func getUser(ctx context.Context, db *sql.DB, id int64) (*User, error) {
	var u User
	err := db.QueryRowContext(ctx,
		`SELECT id, name, email FROM users WHERE id = $1`, id,
	).Scan(&u.ID, &u.Name, &u.Email)

	if errors.Is(err, sql.ErrNoRows) {
		return nil, fmt.Errorf("找不到使用者 %d", id)
	}
	if err != nil {
		return nil, err
	}
	return &u, nil
}

// ② QueryContext：多列
func listUsers(ctx context.Context, db *sql.DB, limit int) ([]User, error) {
	rows, err := db.QueryContext(ctx,
		`SELECT id, name, email FROM users ORDER BY id LIMIT $1`, limit)
	if err != nil {
		return nil, err
	}
	defer rows.Close() // ✓ 絕對不能忘

	var out []User
	for rows.Next() {
		var u User
		if err := rows.Scan(&u.ID, &u.Name, &u.Email); err != nil {
			return nil, err
		}
		out = append(out, u)
	}

	// ✓ 迴圈結束後一定要檢查 rows.Err()
	if err := rows.Err(); err != nil {
		return nil, err
	}
	return out, nil
}

// ③ ExecContext：不回傳列的操作
func deleteUser(ctx context.Context, db *sql.DB, id int64) error {
	res, err := db.ExecContext(ctx, `DELETE FROM users WHERE id = $1`, id)
	if err != nil {
		return err
	}
	n, err := res.RowsAffected()
	if err != nil {
		return err
	}
	if n == 0 {
		return fmt.Errorf("找不到使用者 %d", id)
	}
	return nil
}
```

!!! danger "三個必做的事"
    **① `defer rows.Close()`。** 沒關的 `Rows` 會**一直佔著一條連線**。幾百次之後連線池就空了，整個服務卡死。這是 Go 資料庫程式碼最致命的 bug。

    **② 迴圈後檢查 `rows.Err()`。** `rows.Next()` 回傳 `false` 有兩種可能：資料讀完了，或**發生錯誤**。不檢查 `rows.Err()` 的話，網路中斷造成的部分讀取會被當成正常結束。

    **③ 用 `errors.Is(err, sql.ErrNoRows)` 判斷「查無資料」。** 不要用 `err == sql.ErrNoRows`——有些驅動會包裝錯誤。

!!! tip "Go 1.25：`Rows` 沒關會被偵測到"
    Go 1.25 起，`database/sql` 會在 `Rows` 被 GC 而未關閉時記錄警告（透過 finalizer）。這讓漏掉 `Close` 的問題更早被發現。不過它是「事後偵測」，還是要靠 `defer` 從源頭避免。

---

## NULL 處理

Go 的基本型別不能表示 NULL。掃描一個 NULL 到 `string` 會出錯：

```text
sql: Scan error on column index 2, name "email": converting NULL to string is unsupported
```

### 三種解法

```go
package main

import (
	"database/sql"
	"fmt"
)

type User struct {
	ID    int64
	Name  string
	Email *string        // 解法一：指標
	Bio   sql.NullString // 解法二：sql.NullXxx
	Age   sql.Null[int]  // 解法三：泛型（Go 1.22+）
}

func scan(rows *sql.Rows) (*User, error) {
	var u User
	if err := rows.Scan(&u.ID, &u.Name, &u.Email, &u.Bio, &u.Age); err != nil {
		return nil, err
	}
	return &u, nil
}

func show(u *User) {
	if u.Email != nil {
		fmt.Println("email:", *u.Email)
	}
	if u.Bio.Valid {
		fmt.Println("bio:", u.Bio.String)
	}
	if u.Age.Valid {
		fmt.Println("age:", u.Age.V)
	}
}
```

!!! version "Go 1.22：泛型的 `sql.Null[T]`"
    以前要為每種型別記一個名字：`NullString`、`NullInt64`、`NullFloat64`、`NullBool`、`NullTime`、`NullInt32`、`NullInt16`、`NullByte`。

    Go 1.22 加入泛型版本：

    ```go
    var v sql.Null[time.Time]
    var n sql.Null[int]
    var s sql.Null[MyCustomType] // 任何實作了 Scanner/Valuer 的型別都行
    ```

    新程式碼建議用這個。

**解法四：在 SQL 層處理。** 通常最乾淨：

```sql
SELECT id, name, COALESCE(email, '') AS email FROM users
```

如果「空字串」與「NULL」在你的領域模型裡沒有差別，這樣可以讓 Go 端的程式碼簡單很多。

---

## Prepared Statement 的隱藏成本

```go
stmt, err := db.PrepareContext(ctx, `SELECT name FROM users WHERE id = $1`)
if err != nil {
	return err
}
defer stmt.Close() // ✓ 一定要關

for _, id := range ids {
	var name string
	if err := stmt.QueryRowContext(ctx, id).Scan(&name); err != nil {
		return err
	}
}
```

### 一個容易忽略的行為

`db.Prepare` 回傳的 `*sql.Stmt` 是**綁定到連線池而非單一連線**的。當你在不同連線上使用它時，`database/sql` 會**在每條連線上各自準備一次**。

這表示：

- 連線池有 25 條連線 → 這個 statement 最多會在資料庫端被準備 25 次
- 如果連線因為 `ConnMaxLifetime` 被輪替，還要重新準備

!!! warning "單次查詢不要用 Prepare"
    ```go
    // ✗ 三次來回：Prepare → Execute → Close
    stmt, _ := db.Prepare(query)
    defer stmt.Close()
    stmt.QueryRow(arg).Scan(&v)

    // ✓ 一次來回（多數驅動支援）
    db.QueryRow(query, arg).Scan(&v)
    ```

    **直接用 `db.Query`／`db.Exec` 傳參數，一樣有 SQL 注入防護。** `database/sql` 內部會處理參數綁定。

    只有在**同一個 statement 要執行很多次**（例如批次匯入）時，明確 `Prepare` 才划算。

!!! note "pgx 的差異"
    PostgreSQL 的 `pgx` 驅動預設會**自動快取** prepared statement，所以你用 `db.Query` 也能享受到準備的好處，不需要手動 `Prepare`。

    但如果你的架構中間有 **PgBouncer 且使用 transaction pooling 模式**，prepared statement 會出問題（連線會被切換）。這時要設定 `default_query_exec_mode=simple_protocol` 或使用 PgBouncer 1.21+ 的 prepared statement 支援。

---

## 交易

```go
package main

import (
	"context"
	"database/sql"
	"fmt"
)

func transfer(ctx context.Context, db *sql.DB, from, to int64, amount int) (err error) {
	tx, err := db.BeginTx(ctx, &sql.TxOptions{
		Isolation: sql.LevelReadCommitted,
	})
	if err != nil {
		return err
	}

	// ✓ 用 defer 統一處理 commit / rollback
	defer func() {
		if p := recover(); p != nil {
			tx.Rollback()
			panic(p) // 重新拋出
		}
		if err != nil {
			tx.Rollback() // 已經 Commit 的話這裡回傳 ErrTxDone，可以忽略
			return
		}
		err = tx.Commit()
	}()

	var balance int
	err = tx.QueryRowContext(ctx,
		`SELECT balance FROM accounts WHERE id = $1 FOR UPDATE`, from,
	).Scan(&balance)
	if err != nil {
		return err
	}

	if balance < amount {
		return fmt.Errorf("餘額不足：有 %d，需要 %d", balance, amount)
	}

	if _, err = tx.ExecContext(ctx,
		`UPDATE accounts SET balance = balance - $1 WHERE id = $2`, amount, from); err != nil {
		return err
	}

	if _, err = tx.ExecContext(ctx,
		`UPDATE accounts SET balance = balance + $1 WHERE id = $2`, amount, to); err != nil {
		return err
	}

	return nil // defer 會執行 Commit
}
```

三個要點：

1. **回傳值用具名的 `err`**，這樣 `defer` 才能檢查與修改它（見 [defer](defer.html#defer-與具名回傳值)）。
2. **交易期間只用 `tx.*`，不要混用 `db.*`。** 用 `db` 會拿到另一條連線，不在這個交易裡。
3. **一個交易佔用一條連線直到結束。** 長交易會耗盡連線池。

!!! danger "交易中不要做外部呼叫"
    ```go
    // ✗ HTTP 呼叫可能要好幾秒，這段時間連線與資料庫的鎖都被佔著
    tx.Exec(`UPDATE orders SET status = 'paid' WHERE id = $1`, id)
    callPaymentAPI()   // ← 危險
    tx.Commit()
    ```

    交易應該只包含資料庫操作，而且越短越好。外部呼叫放到交易外面，用冪等性或 saga 模式處理一致性。

---

## 批次操作

一列一列 insert 非常慢——每次都是一輪網路來回。

```go
package main

import (
	"context"
	"database/sql"
	"fmt"
	"strings"
)

type Row struct {
	Name  string
	Email string
}

// 把多列組成一個 INSERT
func bulkInsert(ctx context.Context, db *sql.DB, rows []Row) error {
	if len(rows) == 0 {
		return nil
	}

	const batchSize = 1000
	for start := 0; start < len(rows); start += batchSize {
		end := min(start+batchSize, len(rows))
		batch := rows[start:end]

		placeholders := make([]string, 0, len(batch))
		args := make([]any, 0, len(batch)*2)

		for i, r := range batch {
			placeholders = append(placeholders,
				fmt.Sprintf("($%d, $%d)", i*2+1, i*2+2))
			args = append(args, r.Name, r.Email)
		}

		query := `INSERT INTO users (name, email) VALUES ` +
			strings.Join(placeholders, ", ")

		if _, err := db.ExecContext(ctx, query, args...); err != nil {
			return fmt.Errorf("批次 %d-%d: %w", start, end, err)
		}
	}
	return nil
}
```

!!! warning "參數數量有上限"
    PostgreSQL 的每個查詢最多 **65535** 個參數。上面的例子每列 2 個參數，所以單批最多約 32767 列。批次大小要依欄位數量調整。

    PostgreSQL 上，**`COPY` 協定比批次 INSERT 快一個數量級**。`pgx` 提供 `CopyFrom`：

    ```go
    conn.CopyFrom(ctx,
        pgx.Identifier{"users"},
        []string{"name", "email"},
        pgx.CopyFromSlice(len(rows), func(i int) ([]any, error) {
            return []any{rows[i].Name, rows[i].Email}, nil
        }),
    )
    ```

---

## SQL 注入

```go
// ✗ 字串拼接 —— 絕對不要
query := "SELECT * FROM users WHERE name = '" + name + "'"

// ✓ 參數化查詢
db.Query("SELECT * FROM users WHERE name = $1", name)
```

參數化查詢是**唯一**可靠的防護。它不是「跳脫特殊字元」，而是把查詢與資料**分開傳送**給資料庫——資料永遠不會被當成 SQL 解析。

### 動態欄位名怎麼辦

參數只能用於**值**，不能用於欄位名或表名：

```go
// ✗ 不能這樣
db.Query("SELECT * FROM users ORDER BY $1", column)
```

必須用白名單：

```go
package main

import "fmt"

var allowedSort = map[string]string{
	"name":       "name",
	"created":    "created_at",
	"created_at": "created_at",
}

func buildQuery(sortBy, dir string) (string, error) {
	col, ok := allowedSort[sortBy]
	if !ok {
		return "", fmt.Errorf("不允許的排序欄位: %q", sortBy)
	}

	order := "ASC"
	if dir == "desc" {
		order = "DESC"
	}

	return fmt.Sprintf("SELECT id, name FROM users ORDER BY %s %s", col, order), nil
}
```

**白名單，不是黑名單。** 只允許你明確列出的值。

---

## 生態系選擇

`database/sql` 很低階，實務上通常會搭配其他工具：

| 工具 | 定位 | 適合 |
| --- | --- | --- |
| `database/sql` | 標準庫 | 簡單查詢、完全掌控 |
| `sqlx` | 薄封裝，加上 struct 掃描 | 想少寫 `Scan` 但保留 SQL 掌控權 |
| `sqlc` | 從 SQL **生成** Go 程式碼 | 型別安全、零反射、SQL 是唯一真相來源 |
| `pgx`（原生模式） | PostgreSQL 專用驅動 | 需要 PG 特有功能與最佳效能 |
| `ent` / `gorm` | 完整 ORM | 快速開發、CRUD 為主的應用 |

**個人建議**：`sqlc` 是目前最好的平衡點——你寫 SQL，它產生型別安全的 Go 函式，沒有反射、沒有 DSL 要學、編譯期就能抓到欄位錯誤。

```sql
-- query.sql
-- name: GetUser :one
SELECT id, name, email FROM users WHERE id = $1;
```

```go
// sqlc 產生的程式碼
user, err := queries.GetUser(ctx, 42) // 型別完全正確
```

---

## 常見錯誤與解法

`database/sql` 的問題有個共通特徵：**症狀出現的地方跟原因發生的地方相隔很遠**。連線漏了不會立刻報錯，要等到池子耗盡、整個服務同時卡死才爆出來。這張表把症狀對回原因。

| 症狀 | 真正的原因 | 解法 |
| --- | --- | --- |
| 服務跑一陣子後所有查詢一起卡住 | `Rows` 沒 `Close`，連線被佔光 | `defer rows.Close()` |
| `db.Stats().WaitCount` 持續成長 | 池子太小或查詢太慢 | 調大 `MaxOpenConns` / 優化查詢 |
| 連線一直重建、`MaxIdleClosed` 很高 | `MaxIdleConns` 太小 | 設成等於 `MaxOpenConns` |
| 資料庫 failover 後仍連舊節點 | 沒設 `ConnMaxLifetime` | 設 5–30 分鐘 |
| 啟動時沒報錯，第一次查詢才失敗 | `sql.Open` 不會真的連線 | 啟動時 `PingContext` |
| 讀到一半網路斷了卻當成正常結束 | 沒檢查 `rows.Err()` | 迴圈後一定要檢查 |
| `converting NULL to string is unsupported` | 欄位可為 NULL | `sql.Null[T]` / 指標 / SQL 端 `COALESCE` |
| 交易一直卡住、鎖等待暴增 | 交易中做了外部呼叫 | 交易只包資料庫操作 |
| timeout 設了卻沒作用 | 用了非 `Context` 版本的 API | 一律用 `QueryContext` 等 |

### ① `Rows` 沒關：最致命的一個

值得單獨拿出來講，因為它的後果最嚴重而且最難查。

```go
// ✗ 每次呼叫漏一條連線
func listBad(db *sql.DB) ([]string, error) {
	rows, err := db.Query(`SELECT name FROM users`)
	if err != nil {
		return nil, err
	}

	var out []string
	for rows.Next() {
		var n string
		if err := rows.Scan(&n); err != nil {
			return nil, err // ← 提早 return，rows 永遠不會被關
		}
		out = append(out, n)
	}
	return out, nil
}
```

**注意這裡有兩個漏洞**：完全沒有 `Close`，而且就算你在最後補一行 `rows.Close()`，中間那個錯誤路徑仍然會漏。

```go
// ✓
func listGood(ctx context.Context, db *sql.DB) ([]string, error) {
	rows, err := db.QueryContext(ctx, `SELECT name FROM users`)
	if err != nil {
		return nil, err
	}
	defer rows.Close() // ← 不管從哪條路徑離開都會關

	var out []string
	for rows.Next() {
		var n string
		if err := rows.Scan(&n); err != nil {
			return nil, err
		}
		out = append(out, n)
	}
	return out, rows.Err() // ← 順便處理迴圈中斷的錯誤
}
```

!!! note "`Rows` 什麼時候會自動關？"
    `rows.Next()` 讀到最後一筆並回傳 `false` 時，`Rows` 會自動關閉。所以**正常讀完的路徑其實不會漏**——會漏的永遠是**提早離開**：`return`、`break`、`panic`。

    這正是為什麼 `defer` 是唯一可靠的做法，也是為什麼這個 bug 在測試時看不出來（測試資料通常都讀完了），上線後遇到錯誤路徑才爆。

### ② 症狀的傳播方式

理解這個有助於在監控上及早發現：

```text
某個 handler 漏了 rows.Close()
   ↓ 每次請求佔住一條連線
連線池的閒置連線逐漸歸零
   ↓ 新請求開始排隊等連線
db.Stats().WaitCount / WaitDuration 開始上升
   ↓ 排隊時間超過 context timeout
所有查詢同時回傳 "context deadline exceeded"
   ↓
看起來像「資料庫掛了」，但資料庫其實很閒
```

**關鍵指標是 `WaitCount`**——它會在災難發生前幾分鐘就開始上升。把 `db.Stats()` 推到監控系統（做法見[監控連線池](#監控連線池)），比等 500 錯誤有用得多。

### ③ 交易中做外部呼叫

```go
// ✗ HTTP 呼叫可能要好幾秒，這段時間連線與資料庫的列鎖都被佔著
tx, _ := db.BeginTx(ctx, nil)
tx.ExecContext(ctx, `UPDATE orders SET status='paying' WHERE id=$1`, id)
resp, _ := callPaymentAPI(ctx, id) // ← 危險
tx.ExecContext(ctx, `UPDATE orders SET status=$1 WHERE id=$2`, resp.Status, id)
tx.Commit()
```

一個交易 = 一條連線被獨佔到結束。外部呼叫慢 3 秒，這條連線就被佔 3 秒，同時資料庫端的列鎖也拿著不放——其他要改同一列的交易全部排隊。

**解法是把外部呼叫移出交易**，用兩段短交易加上冪等性處理：

```go
// 第一段交易：標記狀態
if err := markPaying(ctx, db, id); err != nil {
	return err
}

// 交易外：呼叫外部服務
resp, err := callPaymentAPI(ctx, id)
if err != nil {
	return err
}

// 第二段交易：寫回結果（要能重複執行而不出錯）
return finalizePayment(ctx, db, id, resp.Status)
```

代價是中間狀態要能被復原——這就是為什麼分散式流程需要 saga 或對帳機制。**但把外部呼叫塞進交易並不會讓問題消失，只是把它換成更難處理的連線池耗盡。**

### ④ 用 `Prepare` 做單次查詢

```go
// ✗ 三次來回：Prepare → Execute → Close
stmt, _ := db.Prepare(query)
defer stmt.Close()
stmt.QueryRow(arg).Scan(&v)

// ✓ 一次來回，一樣有 SQL 注入防護
db.QueryRow(query, arg).Scan(&v)
```

參數化查詢的防注入效果來自「查詢與資料分開傳送」，**不需要你手動 `Prepare` 就已經生效**。明確 `Prepare` 只在「同一個 statement 要執行很多次」時才划算。

---

## 檢查清單

| 項目 | 為什麼 |
| --- | --- |
| ✓ `sql.DB` 全域共用一份 | 它是連線池，不是連線 |
| ✓ 啟動時 `PingContext` | `sql.Open` 不會真的連線 |
| ✓ `SetMaxOpenConns` 有設 | 不要打爆資料庫 |
| ✓ `SetMaxIdleConns` = `SetMaxOpenConns` | 避免連線反覆重建 |
| ✓ `SetConnMaxLifetime` 有設 | 支援 failover 與負載平衡 |
| ✓ `defer rows.Close()` | 否則連線洩漏 |
| ✓ 檢查 `rows.Err()` | 區分「讀完」與「出錯」 |
| ✓ 所有查詢都用 `*Context` 版本 | 可取消、可逾時 |
| ✓ 一律用參數化查詢 | 防 SQL 注入 |
| ✓ 交易中不做外部呼叫 | 避免長時間持鎖 |
| ✓ 監控 `db.Stats()` | 及早發現連線池問題 |

---

下一節談 cgo：跟 C 世界互通的代價。
