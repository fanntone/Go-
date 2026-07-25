---
title: 網路輪詢器 netpoller
slug: netpoller
part: p5
number: "5.6"
order: 560
summary: Go 如何把非阻塞 I/O 包裝成同步的程式碼、epoll/kqueue/IOCP 的統一抽象，以及為什麼檔案 I/O 不走這條路。
updated: "1.26"
---

## 一個看似矛盾的需求

寫網路程式時，同步的寫法最好讀：

```go
n, err := conn.Read(buf) // 讀不到就等，讀到就繼續
```

但同步 I/O 的傳統實作會**阻塞整條執行緒**。一萬條連線就需要一萬條執行緒——記憶體吃光、切換成本爆炸。

於是 C/C++ 世界發展出 **事件驅動 + 回呼**（epoll + callback、libuv、Netty）。效能好，但程式碼變成一團回呼地獄，錯誤處理與生命週期管理都很痛苦。

**Go 兩者都要**：程式碼寫起來像同步阻塞，底層跑的是非阻塞事件驅動。做到這件事的元件就是 **netpoller**。

---

## 核心機制

```go
n, err := conn.Read(buf)
```

這一行實際發生的事：

<figure class="diagram"><svg viewBox="0 0 700 350" role="img" aria-label="netpoller 的運作流程"><rect class="d-box-a" x="15" y="14" width="670" height="44" rx="6"/><text class="d-t-b" x="30" y="36">① conn.Read(buf) → 對非阻塞 socket 發出 read() 系統呼叫</text><text class="d-t-s" x="30" y="52">socket 在建立時就被設成 O_NONBLOCK，所以 read 會立刻回來，不會卡住</text><path class="d-line" d="M350 58 L350 72" marker-end="url(#ar20)"/><rect class="d-box-o" x="15" y="74" width="325" height="60" rx="6"/><text class="d-t-b" x="30" y="96">② 有資料 → 直接回傳</text><text class="d-t-s" x="30" y="116">完全沒有排程開銷，這是熱路徑</text><rect class="d-box-w" x="360" y="74" width="325" height="60" rx="6"/><text class="d-t-b" x="375" y="96">② 沒資料 → 回傳 EAGAIN</text><text class="d-t-s" x="375" y="116">繼續往下走 ↓</text><path class="d-line-a" d="M522 134 L522 148" marker-end="url(#ar20a)"/><rect class="d-box-w" x="360" y="150" width="325" height="60" rx="6"/><text class="d-t-b" x="375" y="172">③ 把 fd 註冊到 netpoller，然後 gopark</text><text class="d-t-s" x="375" y="192">目前 goroutine 進入 _Gwaiting，M 立刻去跑別的 G</text><path class="d-line-a" d="M522 210 L522 224" marker-end="url(#ar20a)"/><rect class="d-box-w" x="360" y="226" width="325" height="60" rx="6"/><text class="d-t-b" x="375" y="248">④ 資料到了 → netpoll 回報這個 fd 就緒</text><text class="d-t-s" x="375" y="268">把對應的 G 設回 _Grunnable，放進執行佇列</text><path class="d-line-a" d="M360 256 L200 256 L200 300" marker-end="url(#ar20a)"/><rect class="d-box-o" x="15" y="302" width="325" height="44" rx="6"/><text class="d-t-b" x="30" y="324">⑤ G 恢復執行，重試 read → 成功</text><text class="d-t-s" x="30" y="340">從呼叫者的角度，Read 只是「花了一點時間才回來」</text><defs><marker id="ar20" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M0 0 L10 5 L0 10 z" style="fill:var(--line-strong)"/></marker><marker id="ar20a" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M0 0 L10 5 L0 10 z" style="fill:var(--accent)"/></marker></defs></svg><figcaption><b>阻塞的是 goroutine，不是執行緒。</b>關鍵在步驟 ③：goroutine 睡了，但承載它的 M 立刻被釋放去跑別的工作。對使用者程式碼而言，這一切完全透明。</figcaption></figure>

**一句話總結：netpoller 把「阻塞執行緒」換成了「阻塞 goroutine」。** 而 goroutine 阻塞幾乎免費。

---

## 平台抽象

netpoller 對不同作業系統的 I/O 多工機制做了統一封裝。所有平台都實作同一組介面（`runtime/netpoll.go`）：

```go
func netpollinit()                                    // 初始化
func netpollopen(fd uintptr, pd *pollDesc) int32      // 註冊 fd
func netpollclose(fd uintptr) int32                   // 移除 fd
func netpoll(delay int64) (gList, int32)              // 取得就緒的 G
func netpollBreak()                                   // 喚醒阻塞中的 netpoll
```

| 平台 | 實作檔案 | 底層機制 |
| --- | --- | --- |
| Linux | `netpoll_epoll.go` | `epoll`（邊緣觸發 ET） |
| macOS / BSD | `netpoll_kqueue.go` | `kqueue` |
| Windows | `netpoll_windows.go` | **IOCP**（I/O Completion Ports） |
| Solaris | `netpoll_solaris.go` | event ports |
| AIX | `netpoll_aix.go` | `poll` |
| WASM | `netpoll_fake.go` | 假實作（單執行緒環境） |

!!! note "Windows 的模型不太一樣"
    epoll 與 kqueue 是**就緒通知（readiness notification）**：「這個 fd 現在可以讀了，你自己去讀」。

    IOCP 是**完成通知（completion notification）**：「你要的讀取操作已經完成，資料在這裡」。

    runtime 在 Windows 上做了額外的轉換工作，讓上層看到一致的行為。這也是為什麼 Windows 上的 Go 網路程式碼路徑跟 Unix 稍有不同，但你完全感覺不到。

---

## 誰在呼叫 netpoll

netpoller **沒有專屬的執行緒**。檢查就緒事件的工作分散在三個地方：

1. **排程器的 `findRunnable()`** —— 找不到 G 的時候，順便呼叫 `netpoll(0)`（非阻塞）看有沒有 I/O 完成。
2. **`sysmon` 監控執行緒** —— 每次巡邏時呼叫 `netpoll(0)`。
3. **M 準備休眠前** —— 如果沒有其他 P 在做這件事，這個 M 會呼叫 `netpoll(阻塞)`，直到有 I/O 事件或計時器到期才醒來。

第 3 點很重要：**它讓 Go 程式在完全閒置時不會空轉燒 CPU**。至少會有一個 M 阻塞在 `epoll_wait` 上，其餘的都在睡覺。

---

## 實際驗證

看看一萬條連線用了多少執行緒：

```go
package main

import (
	"fmt"
	"io"
	"net"
	"runtime"
	"sync"
	"time"
)

func main() {
	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		panic(err)
	}
	defer ln.Close()

	const n = 5000
	var wg sync.WaitGroup

	// 伺服器：每條連線一個 goroutine
	go func() {
		for {
			c, err := ln.Accept()
			if err != nil {
				return
			}
			go func(c net.Conn) {
				defer c.Close()
				io.Copy(io.Discard, c) // 阻塞在讀取，直到對方關閉
			}(c)
		}
	}()

	// 客戶端：建立 n 條連線但不送資料
	conns := make([]net.Conn, 0, n)
	for range n {
		c, err := net.Dial("tcp", ln.Addr().String())
		if err != nil {
			break
		}
		conns = append(conns, c)
	}

	time.Sleep(500 * time.Millisecond)

	fmt.Printf("連線數:      %d\n", len(conns))
	fmt.Printf("goroutine 數: %d\n", runtime.NumGoroutine())
	fmt.Printf("GOMAXPROCS:  %d\n", runtime.GOMAXPROCS(0))

	for _, c := range conns {
		c.Close()
	}
	wg.Wait()
}
```

典型輸出：

```text
連線數:      5000
goroutine 數: 10003
GOMAXPROCS:  8
```

**一萬個 goroutine 阻塞在讀取，但作業系統執行緒只有十幾條。** 每個 goroutine 佔 2 KB 起跳的堆疊，總共約 20 MB——換成一萬條執行緒的話，光是堆疊虛擬位址空間就要 8–80 GB。

---

## 逾時是怎麼實作的

```go
conn.SetReadDeadline(time.Now().Add(5 * time.Second))
n, err := conn.Read(buf)
```

逾時**不是**用另一個 goroutine 監控。實作是：

1. `SetReadDeadline` 在 `pollDesc` 上設定一個計時器（走 [計時器](timer.html) 那套 P 本地堆積）。
2. 計時器到期時，把等待中的 G 喚醒，並在 `pollDesc` 上標記逾時。
3. G 醒來發現是逾時而非資料就緒，回傳 `os.ErrDeadlineExceeded`。

所以逾時幾乎是零成本的——沒有額外的 goroutine，沒有額外的執行緒。

```go
package main

import (
	"errors"
	"fmt"
	"net"
	"os"
	"time"
)

func main() {
	ln, _ := net.Listen("tcp", "127.0.0.1:0")
	defer ln.Close()

	go func() {
		c, _ := ln.Accept()
		defer c.Close()
		time.Sleep(time.Second) // 故意不送資料
	}()

	c, _ := net.Dial("tcp", ln.Addr().String())
	defer c.Close()

	c.SetReadDeadline(time.Now().Add(100 * time.Millisecond))

	buf := make([]byte, 16)
	start := time.Now()
	_, err := c.Read(buf)

	fmt.Printf("耗時 %v\n", time.Since(start).Round(10*time.Millisecond))
	fmt.Println("是逾時嗎:", errors.Is(err, os.ErrDeadlineExceeded))

	var ne net.Error
	if errors.As(err, &ne) {
		fmt.Println("net.Error.Timeout():", ne.Timeout())
	}
}
```

```text
耗時 100ms
是逾時嗎: true
net.Error.Timeout(): true
```

!!! tip "Deadline 是絕對時間，不是持續時間"
    `SetReadDeadline(t)` 設定的是「到 `t` 這個時刻為止」，不是「從現在起 N 秒」。在迴圈裡讀取時，每一輪都要重新設定：

    ```go
    for {
        conn.SetReadDeadline(time.Now().Add(30 * time.Second)) // 每輪更新
        n, err := conn.Read(buf)
        // ...
    }
    ```

    設 `time.Time{}`（零值）表示取消逾時。

---

## 為什麼檔案 I/O 不走 netpoller

這是實務上很重要的一個限制。

**在 Linux 上，一般檔案（regular file）不能用 epoll 做非阻塞 I/O。** epoll 對常規檔案永遠回報「就緒」，因為從核心的角度，檔案「總是可以讀」——只是可能要等磁碟。

所以 `os.File` 的 `Read`／`Write` 會**真的阻塞 M**。排程器會偵測到（`sysmon` 發現系統呼叫超過 20 微秒），把 P 交接給別的 M，但**那條 M 就是被佔住了**。

```go
// 這 1000 個 goroutine 會造成大量執行緒建立
for range 1000 {
	go func() {
		data, _ := os.ReadFile("/some/large/file")
		process(data)
	}()
}
```

### 影響與對策

**觀察執行緒數量：**

```go
package main

import (
	"fmt"
	"runtime"
	"runtime/pprof"
)

func main() {
	// ... 你的工作 ...

	p := pprof.Lookup("threadcreate")
	fmt.Println("建立過的執行緒數:", p.Count())
	fmt.Println("goroutine 數:", runtime.NumGoroutine())
}
```

或用 `GODEBUG=schedtrace=1000` 看 `threads=` 那個欄位。

**對策一：限制並行度。**

```go
sem := make(chan struct{}, 32) // 最多 32 個並行檔案操作

for _, path := range paths {
	sem <- struct{}{}
	go func(p string) {
		defer func() { <-sem }()
		processFile(p)
	}(path)
}
```

**對策二：設定執行緒上限。**

```go
runtime.SetMaxThreads(1000) // 預設 10000
```

注意這是**硬性上限，超過會直接 crash**。設它的目的是「早點爆炸」而不是「防止爆炸」——讓你在測試環境就發現問題，而不是在正式環境慢慢耗盡系統資源。

**對策三：管線與非阻塞 fd 例外。**

有趣的是，**pipe、FIFO、terminal、socket** 這些「字元裝置」是可以用 epoll 的。所以 `os/exec` 的 stdout/stderr 管線、`net.Conn` 都走 netpoller。只有常規檔案不行。

---

## 常見誤解

**「netpoller 是一個獨立的執行緒。」** 不是。它沒有專屬執行緒，檢查工作搭排程器與 sysmon 的便車。

**「Go 的網路 I/O 是非同步的。」** 從程式碼的角度是同步的（`Read` 會等到有資料）。非同步的是**底層機制**，不是 API。這正是 Go 的設計價值——同步的心智模型，非同步的效能。

**「用了 goroutine 就不用管連線數。」** 每條連線至少一個 goroutine（2 KB 堆疊）加上讀寫緩衝區（通常各 4 KB）。十萬條連線大約需要 1 GB 記憶體。仍然需要限流與資源規劃。

**「檔案讀寫也是非阻塞的。」** 不是。見上一節。

---

## 相關的實務建議

### 一定要設逾時

```go
// ✗ 沒有任何逾時的伺服器，一個慢客戶端就能耗住資源
srv := &http.Server{Addr: ":8080", Handler: mux}

// ✓
srv := &http.Server{
	Addr:              ":8080",
	Handler:           mux,
	ReadHeaderTimeout: 5 * time.Second,   // 防 Slowloris 攻擊
	ReadTimeout:       30 * time.Second,
	WriteTimeout:      30 * time.Second,
	IdleTimeout:       120 * time.Second,
}
```

`ReadHeaderTimeout` 特別重要——沒有它，攻擊者可以用極慢的速度送標頭，長期佔住連線與 goroutine。

### 客戶端要重用連線

```go
// ✗ 每次都建新的 Transport → 連線無法重用，還可能耗盡 port
func badRequest(url string) { 
	client := &http.Client{}
	client.Get(url)
}

// ✓ 共用一個 Client
var client = &http.Client{
	Timeout: 10 * time.Second,
	Transport: &http.Transport{
		MaxIdleConns:        100,
		MaxIdleConnsPerHost: 10,
		IdleConnTimeout:     90 * time.Second,
	},
}
```

!!! danger "務必讀完並關閉 response body"
    ```go
    resp, err := client.Get(url)
    if err != nil {
        return err
    }
    defer resp.Body.Close()

    // ✓ 即使不需要內容，也要讀完，否則連線無法回到連線池
    io.Copy(io.Discard, resp.Body)
    ```

    只 `Close` 而不讀完，連線會被直接丟棄而非重用。在高頻呼叫下，這會導致大量 TIME_WAIT 連線並可能耗盡本地 port。

---

下一節是 Part 5 的最後一節：sysmon，那條在背景默默巡邏的執行緒。
