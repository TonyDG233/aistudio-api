package main

import (
	"context"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"log/slog"
	"net"
	"net/http"
	"os"
	"os/signal"
	"path/filepath"
	"runtime"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"syscall"
	"time"
	_ "time/tzdata"
	"unicode"

	"github.com/Mag1cFall/AIStudio2API/internal/aistudio"
	"github.com/Mag1cFall/AIStudio2API/internal/camoufoxnative"
	"github.com/Mag1cFall/AIStudio2API/internal/poolstate"
	"github.com/Mag1cFall/AIStudio2API/internal/signerapi"
	"github.com/Mag1cFall/AIStudio2API/internal/upstreamguard"
)

const (
	defaultModel       = "gemini-3.7-flash"
	generateContentURL = "https://alkalimakersuite-pa.clients6.google.com/$rpc/google.internal.alkali.applications.makersuite.v1.MakerSuiteService/GenerateContent"
)

// signerVersion 是 /health 暴露的服务版本号
const signerVersion = "2.1.0"

const (
	// defaultMaxWorkers 是同时存活的 WAA Worker 数量上限
	defaultMaxWorkers = 8
	// defaultIdleTTL 是空闲 Worker 被后台回收前的保留时长
	defaultIdleTTL = 900 * time.Second
	// maxSetCookieHeaderBytes 限制 X-WAA-Set-Cookies 响应头大小，超限时只返回截断标记
	maxSetCookieHeaderBytes = 32 * 1024
	// maxRPCBodyBytes 限制 /v1/rpc/send 请求体大小，阻断超大请求拖垮进程
	maxRPCBodyBytes = 32 << 20
)

var (
	errPoolClosed    = errors.New("WAA Worker 池已关闭")
	errPoolSaturated = errors.New("WAA Worker 池已达 max-workers 上限且没有可淘汰的空闲 worker")
)

type workerEntry struct {
	mu        sync.Mutex
	key       string
	worker    *aistudio.NativeWorker
	statePath string
	authUser  int
	proxy     string
	createdAt time.Time

	// workerGeneration 记录当前 worker 的本地构建代数，由 entry.mu 保护
	workerGeneration uint64

	// clientRotatingBaseline 是该 Worker 冷启动时客户端传入的滚动凭据快照，
	// 由 entry.mu 保护。仅用于检测客户端是否主动轮换了凭据，绝不在锁内读取
	// Worker 运行期状态。
	clientRotatingBaseline map[string]string

	// 以下字段由 WorkerPool.mu 保护
	slot               bool
	building           bool
	inFlight           int
	pins               int
	lastUsed           time.Time
	recreatePending    bool
	recreateGeneration uint64
}

// closeLocked 在已持有 entry.mu 时关闭并清空底层 worker
func (entry *workerEntry) closeLocked() {
	if entry.worker != nil {
		_ = entry.worker.Close()
		entry.worker = nil
	}
	entry.clientRotatingBaseline = nil
}

// close 在无并发使用者时关闭并清空底层 worker
func (entry *workerEntry) close() {
	entry.mu.Lock()
	defer entry.mu.Unlock()
	entry.closeLocked()
}

type WorkerPool struct {
	mu         sync.Mutex
	entries    map[string]*workerEntry
	stateDir   string
	proxy      string
	locale     string
	timezone   string
	maxWorkers int
	idleTTL    time.Duration
	slots      int
	closed     bool
	stopCh     chan struct{}
}

// PoolStats 是 /health 使用的无 Cookie 池快照
type PoolStats struct {
	Workers    int
	MaxWorkers int
	Idle       int
	// OK 表示服务进程与池仍可接受请求（未进入关闭流程）
	OK bool
	// Ready 表示池还能接收新请求：有剩余名额或至少一个空闲 Worker
	Ready bool
}

func NewWorkerPool(stateDir, proxy, locale, timezone string, maxWorkers int, idleTTL time.Duration) *WorkerPool {
	if maxWorkers < 1 {
		maxWorkers = 1
	}
	if idleTTL <= 0 {
		idleTTL = defaultIdleTTL
	}
	pool := &WorkerPool{
		entries:    make(map[string]*workerEntry),
		stateDir:   stateDir,
		proxy:      proxy,
		locale:     locale,
		timezone:   timezone,
		maxWorkers: maxWorkers,
		idleTTL:    idleTTL,
		stopCh:     make(chan struct{}),
	}
	pool.startJanitor()
	return pool
}

// startJanitor 启动惰性 TTL 回收，只关闭真正空闲的 Worker
func (p *WorkerPool) startJanitor() {
	interval := p.idleTTL / 4
	if interval < 10*time.Second {
		interval = 10 * time.Second
	}
	if interval > time.Minute {
		interval = time.Minute
	}
	go func() {
		ticker := time.NewTicker(interval)
		defer ticker.Stop()
		for {
			select {
			case now := <-ticker.C:
				p.reapIdle(now)
			case <-p.stopCh:
				return
			}
		}
	}()
}

// reapIdle 删除超过 idle-ttl 且没有在途请求的 Worker
func (p *WorkerPool) reapIdle(now time.Time) {
	p.mu.Lock()
	if p.closed {
		p.mu.Unlock()
		return
	}
	var victims []*workerEntry
	for key, entry := range p.entries {
		if entry.pins > 0 || entry.inFlight > 0 || entry.building {
			continue
		}
		if entry.slot {
			if now.Sub(entry.lastUsed) < p.idleTTL {
				continue
			}
			entry.slot = false
			p.slots--
		}
		delete(p.entries, key)
		victims = append(victims, entry)
	}
	p.mu.Unlock()

	for _, entry := range victims {
		entry.close()
	}
}

func (p *WorkerPool) CloseAll() {
	p.mu.Lock()
	if p.closed {
		p.mu.Unlock()
		return
	}
	p.closed = true
	close(p.stopCh)
	entries := make([]*workerEntry, 0, len(p.entries))
	for _, entry := range p.entries {
		entry.slot = false
		entries = append(entries, entry)
	}
	p.entries = make(map[string]*workerEntry)
	p.slots = 0
	p.mu.Unlock()

	for _, entry := range entries {
		entry.close()
	}
}

// Stats 返回 Worker 数量与就绪状态，绝不包含 Cookie
func (p *WorkerPool) Stats() PoolStats {
	p.mu.Lock()
	defer p.mu.Unlock()
	stats := PoolStats{Workers: p.slots, MaxWorkers: p.maxWorkers, OK: !p.closed}
	for _, entry := range p.entries {
		if entry.slot && !entry.building && entry.inFlight == 0 && entry.pins == 0 {
			stats.Idle++
		}
	}
	stats.Ready = stats.OK && (p.slots < p.maxWorkers || stats.Idle > 0)
	return stats
}

// Release 关闭并移除指定账户或全部空闲 Worker，返回 (释放数, 因在途跳过的数量)
func (p *WorkerPool) Release(accountKey string, all bool) (int, int) {
	p.mu.Lock()
	if p.closed {
		p.mu.Unlock()
		return 0, 0
	}
	var victims []*workerEntry
	released, skipped := 0, 0
	take := func(entry *workerEntry) {
		if entry.pins > 0 || entry.inFlight > 0 || entry.building {
			skipped++
			return
		}
		delete(p.entries, entry.key)
		if entry.slot {
			entry.slot = false
			p.slots--
		}
		released++
		victims = append(victims, entry)
	}
	if all {
		for _, entry := range p.entries {
			take(entry)
		}
	} else if entry, ok := p.entries[accountKey]; ok {
		take(entry)
	}
	p.mu.Unlock()

	for _, entry := range victims {
		entry.close()
	}
	return released, skipped
}

// lruIdleLocked 返回最久未使用且没有任何在途使用的可淘汰 Worker
func (p *WorkerPool) lruIdleLocked(except *workerEntry) *workerEntry {
	var victim *workerEntry
	for _, entry := range p.entries {
		if entry == except || !entry.slot || entry.building || entry.inFlight > 0 || entry.pins > 0 {
			continue
		}
		if victim == nil || entry.lastUsed.Before(victim.lastUsed) {
			victim = entry
		}
	}
	return victim
}

// reserveSlot 为 entry 预留一个 Worker 名额；容量满时淘汰一个 LRU 空闲 Worker
func (p *WorkerPool) reserveSlot(entry *workerEntry) (*workerEntry, error) {
	p.mu.Lock()
	defer p.mu.Unlock()
	if p.closed {
		return nil, errPoolClosed
	}
	if entry.slot {
		entry.building = true
		return nil, nil
	}
	if p.slots < p.maxWorkers {
		p.slots++
		entry.slot = true
		entry.building = true
		return nil, nil
	}
	victim := p.lruIdleLocked(entry)
	if victim == nil {
		return nil, errPoolSaturated
	}
	delete(p.entries, victim.key)
	victim.slot = false
	victim.building = false
	entry.slot = true
	entry.building = true
	return victim, nil
}

// dropSlot 在构建失败时归还 entry 占用的 Worker 名额
func (p *WorkerPool) dropSlot(entry *workerEntry) {
	p.mu.Lock()
	defer p.mu.Unlock()
	if p.closed {
		return
	}
	entry.building = false
	if entry.slot {
		entry.slot = false
		if p.slots > 0 {
			p.slots--
		}
	}
}

// lease 结束获取阶段的 pin，并登记一次在途使用；返回幂等释放函数。
//
// 释放时若发现该 Worker 已标记 recreatePending 且这是最后一个在途 lease，
// 会在不打断其他请求的前提下关闭旧 Worker 并清除 pending；下一次 Acquire
// 会安全地重建。若此时强制重建请求正持有 entry.mu，则交由该请求完成重建。
func (p *WorkerPool) lease(entry *workerEntry) func() {
	var once sync.Once
	p.mu.Lock()
	if entry.pins > 0 {
		entry.pins--
	}
	entry.building = false
	entry.inFlight++
	entry.lastUsed = time.Now()
	p.mu.Unlock()
	return func() {
		once.Do(func() {
			p.mu.Lock()
			if entry.inFlight > 0 {
				entry.inFlight--
			}
			entry.lastUsed = time.Now()
			drained := entry.recreatePending && entry.inFlight == 0 && entry.pins == 0
			p.mu.Unlock()
			if !drained || !entry.mu.TryLock() {
				return
			}
			p.mu.Lock()
			safe := entry.recreatePending && entry.inFlight == 0 && entry.pins == 0
			if safe {
				entry.recreatePending = false
			}
			p.mu.Unlock()
			if safe {
				entry.closeLocked()
			}
			entry.mu.Unlock()
		})
	}
}

func accountKeyFromState(state aistudio.StorageState, authUser int) string {
	now := time.Now()
	seed, ok := state.CookieValue("SAPISID", "https://aistudio.google.com/", now)
	if !ok || seed == "" {
		seed, _ = state.CookieValue("__Secure-3PAPISID", "https://aistudio.google.com/", now)
	}
	if seed == "" {
		seed, _ = state.CookieValue("SID", "https://aistudio.google.com/", now)
	}
	sum := sha256.Sum256([]byte(seed))
	return fmt.Sprintf("acc_%s_u%d", hex.EncodeToString(sum[:6]), authUser)
}

// entryInFlight 读取受 p.mu 保护的当前在途 lease 数
func (p *WorkerPool) entryInFlight(entry *workerEntry) int {
	p.mu.Lock()
	defer p.mu.Unlock()
	return entry.inFlight
}

// markRecreatePending 标记需要重建并推进代数，返回本次代数快照
func (p *WorkerPool) markRecreatePending(entry *workerEntry) uint64 {
	p.mu.Lock()
	defer p.mu.Unlock()
	entry.recreatePending = true
	entry.recreateGeneration = poolstate.NextGeneration(entry.recreateGeneration)
	return entry.recreateGeneration
}

// clearRecreatePending 仅在代数未被后续请求推进时清除 pending
func (p *WorkerPool) clearRecreatePending(entry *workerEntry, generation uint64) {
	p.mu.Lock()
	defer p.mu.Unlock()
	if poolstate.ShouldClearPending(generation, entry.recreateGeneration) {
		entry.recreatePending = false
	}
}

// isRecreatePending 报告 entry 是否仍有一个待处理的重建请求
func (p *WorkerPool) isRecreatePending(entry *workerEntry) bool {
	p.mu.Lock()
	defer p.mu.Unlock()
	return entry.recreatePending
}

// waitForDrain 有界等待同账号在途 lease 全部释放；调用方必须已持有 entry.mu
func (p *WorkerPool) waitForDrain(ctx context.Context, entry *workerEntry) error {
	ticker := time.NewTicker(20 * time.Millisecond)
	defer ticker.Stop()
	for {
		p.mu.Lock()
		inFlight := entry.inFlight
		closed := p.closed
		p.mu.Unlock()
		if closed {
			return errPoolClosed
		}
		if inFlight == 0 {
			return nil
		}
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-ticker.C:
		}
	}
}

// clientCookieValues 把 storage state 的 Cookie 列表压平成 name->value 快照。
//
// 同名 Cookie 以最后一个非空值为准，只用于客户端滚动凭据对比，不参与写回。
func clientCookieValues(state aistudio.StorageState) map[string]string {
	values := make(map[string]string, len(state.Cookies))
	for _, cookie := range state.Cookies {
		if cookie.Value == "" {
			continue
		}
		values[cookie.Name] = cookie.Value
	}
	return values
}

// AcquireWorker 取得账户 Worker 并登记一次在途使用；返回的释放函数必须调用一次。
//
// accountKeyOverride 为调用方（Python account.id）提供的稳定键；为空时回退到
// 基于 Cookie 的哈希键。返回的 key 是本请求实际使用的键，供 mint/rpc/release 对齐。
//
// forceRecreate 仅在 inFlight == 0 时立即关闭旧 Worker；存在在途 proof/rpc 时
// 设置 recreatePending/generation，等待最后一个 lease 释放后再安全重建，
// 不会中断同账号正在执行的请求。
func (p *WorkerPool) AcquireWorker(
	ctx context.Context,
	rawCookies string,
	authUser int,
	model string,
	proxyOverride string,
	accountKeyOverride string,
	forceRecreate bool,
) (*aistudio.NativeWorker, string, string, func(), error) {
	state, err := aistudio.ParseCookieText(rawCookies)
	if err != nil {
		return nil, "", "", nil, fmt.Errorf("解析 Cookie 失败: %w", err)
	}

	key, err := poolstate.ResolveAccountKey(accountKeyOverride, accountKeyFromState(state, authUser))
	if err != nil {
		return nil, "", "", nil, err
	}
	effectiveProxy := strings.TrimSpace(proxyOverride)
	if effectiveProxy == "" {
		effectiveProxy = p.proxy
	}

	p.mu.Lock()
	if p.closed {
		p.mu.Unlock()
		return nil, "", "", nil, errPoolClosed
	}
	entry := p.entries[key]
	if entry == nil {
		entry = &workerEntry{key: key}
		p.entries[key] = entry
	}
	entry.pins++
	p.mu.Unlock()

	pinned := true
	defer func() {
		if pinned {
			p.mu.Lock()
			if entry.pins > 0 {
				entry.pins--
			}
			p.mu.Unlock()
		}
	}()

	entry.mu.Lock()

	reuseAllowed := !forceRecreate
	if reuseAllowed && entry.worker != nil && p.isRecreatePending(entry) && p.entryInFlight(entry) == 0 {
		// pending 已排空：本次请求负责重建，不能继续复用旧 Worker
		reuseAllowed = false
	}
	if reuseAllowed && entry.worker != nil && entry.proxy == effectiveProxy {
		st := entry.worker.State()
		if st.Phase == aistudio.WorkerReady || st.Phase == aistudio.WorkerBusy {
			worker := entry.worker
			incoming := clientCookieValues(state)
			if poolstate.HasClientRotatedCredentials(entry.clientRotatingBaseline, incoming) {
				// Python 侧已轮换凭据：旧 Worker 的 Cookie 罐已过期，必须重建。
				slog.Info("客户端滚动凭据已更新，按最新凭据重建热 Worker", "account", key)
			} else {
				entry.mu.Unlock()
				release := p.lease(entry)
				pinned = false
				return worker, "hot", key, release, nil
			}
		}
	}

	if entry.worker != nil {
		decision := poolstate.DecideForceRecreate(true, p.entryInFlight(entry))
		if decision == poolstate.CloseImmediately {
			entry.closeLocked()
			p.clearRecreatePending(entry, 0)
		} else {
			generation := p.markRecreatePending(entry)
			slog.Info("存在同账号在途请求，延迟重建 Worker", "account", key, "generation", generation)
			if err := p.waitForDrain(ctx, entry); err != nil {
				entry.mu.Unlock()
				return nil, "", "", nil, fmt.Errorf("等待同账号在途请求结束失败: %w", err)
			}
			entry.closeLocked()
			p.clearRecreatePending(entry, generation)
		}
	}

	accDir := filepath.Join(p.stateDir, key)
	if err := os.MkdirAll(accDir, 0o755); err != nil {
		p.dropSlot(entry)
		entry.mu.Unlock()
		return nil, "", "", nil, fmt.Errorf("创建账户状态目录失败: %w", err)
	}
	statePath := filepath.Join(accDir, "storage-state.json")
	if err := aistudio.WriteStorageState(statePath, state); err != nil {
		p.dropSlot(entry)
		entry.mu.Unlock()
		return nil, "", "", nil, fmt.Errorf("写入 storage-state.json 失败: %w", err)
	}

	cleanModel := strings.TrimPrefix(strings.TrimSpace(model), "models/")
	if cleanModel == "" {
		cleanModel = defaultModel
	}

	opts := camoufoxnative.Options{
		StorageStatePath: statePath,
		Model:            cleanModel,
		BootstrapPrompt:  camoufoxnative.DefaultBootstrapPrompt,
		Locale:           p.locale,
		Timezone:         p.timezone,
		Proxy:            effectiveProxy,
		AuthUser:         authUser,
	}

	victim, err := p.reserveSlot(entry)
	if err != nil {
		entry.mu.Unlock()
		return nil, "", "", nil, err
	}
	if victim != nil {
		slog.Info("Worker 池已满，淘汰最久未使用的空闲 Worker", "account", victim.key, "last_used", victim.lastUsed)
		victim.close()
	}

	slog.Info("正在初始化 SpiderMonkey WAA Worker", "account", key, "auth_user", authUser, "model", cleanModel, "proxy", effectiveProxy != "")
	t0 := time.Now()
	worker, err := aistudio.NewSpiderMonkeyWorker(ctx, key, opts)
	if err != nil {
		p.dropSlot(entry)
		entry.mu.Unlock()
		return nil, "", "", nil, fmt.Errorf("初始化 SpiderMonkey WAA Worker 失败: %w", err)
	}
	slog.Info("SpiderMonkey WAA Worker 初始化就绪", "account", key, "elapsed_ms", time.Since(t0).Milliseconds())

	now := time.Now()
	entry.worker = worker
	entry.workerGeneration++
	entry.clientRotatingBaseline = poolstate.ExtractRotatingCredentials(clientCookieValues(state))
	entry.statePath = statePath
	entry.authUser = authUser
	entry.proxy = effectiveProxy
	entry.createdAt = now
	p.mu.Lock()
	entry.lastUsed = now
	p.mu.Unlock()
	entry.mu.Unlock()

	release := p.lease(entry)
	pinned = false
	return worker, "cold", key, release, nil
}

// toSignerCookies 把内部 Cookie 结构转换为对外 JSON 契约
func toSignerCookies(cookies []aistudio.StateCookie) []signerapi.Cookie {
	out := make([]signerapi.Cookie, 0, len(cookies))
	for _, cookie := range cookies {
		out = append(out, signerapi.Cookie{
			Name:         cookie.Name,
			Value:        cookie.Value,
			Domain:       cookie.Domain,
			Path:         cookie.Path,
			Expires:      cookie.Expires,
			HTTPOnly:     cookie.HTTPOnly,
			Secure:       cookie.Secure,
			SameSite:     cookie.SameSite,
			PartitionKey: cookie.PartitionKey,
		})
	}
	return out
}

// writeSetCookieHeader 在流式响应写入状态码前回传 Set-Cookie 摘要，不记录到日志。
//
// 数组同时包含更新项与删除墓碑项（value 为空、expires=0），保持既有数组格式。
func writeSetCookieHeader(w http.ResponseWriter, cookies []signerapi.Cookie) {
	if len(cookies) == 0 {
		return
	}
	encoded, err := json.Marshal(cookies)
	if err != nil {
		return
	}
	value := base64.RawURLEncoding.EncodeToString(encoded)
	if len(value) > maxSetCookieHeaderBytes {
		w.Header().Set("X-WAA-Set-Cookies-Truncated", "true")
		return
	}
	w.Header().Set("X-WAA-Set-Cookies", value)
}

// envInt 读取整数环境变量，缺失或非法时回退默认值
func envInt(name string, fallback int) int {
	value := strings.TrimSpace(os.Getenv(name))
	if value == "" {
		return fallback
	}
	parsed, err := strconv.Atoi(value)
	if err != nil || parsed < 1 {
		slog.Warn("环境变量不是合法的正整数，使用默认值", "name", name, "value", value, "default", fallback)
		return fallback
	}
	return parsed
}

// envDuration 支持 "900" 秒或 "15m"/"900s" 形式的环境变量，缺失或非法时回退默认值
func envDuration(name string, fallback time.Duration) time.Duration {
	value := strings.TrimSpace(os.Getenv(name))
	if value == "" {
		return fallback
	}
	if seconds, err := strconv.Atoi(value); err == nil {
		if seconds > 0 {
			return time.Duration(seconds) * time.Second
		}
	} else if parsed, err := time.ParseDuration(value); err == nil && parsed > 0 {
		return parsed
	}
	slog.Warn("环境变量不是合法的时长，使用默认值", "name", name, "value", value, "default", fallback)
	return fallback
}

// validateExplicitAccountKey 在进入 Worker 池前校验显式稳定键，空值表示回退哈希
func validateExplicitAccountKey(key string) error {
	trimmed := strings.TrimSpace(key)
	if trimmed == "" {
		return nil
	}
	return poolstate.ValidateAccountKey(trimmed)
}

func parseAuthUser(val any) int {
	switch v := val.(type) {
	case float64:
		return int(v)
	case int:
		return v
	case string:
		n, err := strconv.Atoi(strings.TrimSpace(v))
		if err == nil && n >= 0 {
			return n
		}
	}
	return 0
}

func normalizeTierHeader(tier string) string {
	switch strings.ToUpper(strings.TrimSpace(tier)) {
	case "TIER1", "PRO":
		return "TIER1"
	case "TIER2", "ULTRA", "ULTRA_20X":
		return "TIER2"
	case "TIER0", "PLUS":
		return "TIER0"
	default:
		return ""
	}
}

// extractPromptsFromWire 从 wireContents ([[parts, role], ...]) 提取 bindingPrompt 与最后一轮文本 pagePrompt
func extractPromptsFromWire(wireContents json.RawMessage, fallbackPrompt string) (string, string) {
	bindingPrompt := fallbackPrompt
	if len(wireContents) == 0 {
		return bindingPrompt, bindingPrompt
	}

	var turns [][]json.RawMessage
	if err := json.Unmarshal(wireContents, &turns); err != nil || len(turns) == 0 {
		return bindingPrompt, bindingPrompt
	}

	var bindingParts []string
	var lastTurnTextParts []string

	for turnIdx, turn := range turns {
		if len(turn) == 0 {
			continue
		}
		var parts [][]json.RawMessage
		if err := json.Unmarshal(turn[0], &parts); err != nil {
			continue
		}
		isLastTurn := turnIdx == len(turns)-1
		for _, part := range parts {
			// Check thought flags: index 0 (bool), index 10 (bool), index 12 (int 1 or bool true)
			isThought := false
			if len(part) > 0 {
				var b0 bool
				if json.Unmarshal(part[0], &b0) == nil && b0 {
					isThought = true
				}
			}
			if len(part) > 10 {
				var b10 bool
				if json.Unmarshal(part[10], &b10) == nil && b10 {
					isThought = true
				}
			}
			if len(part) > 12 {
				var b12 bool
				var n12 int
				if (json.Unmarshal(part[12], &b12) == nil && b12) || (json.Unmarshal(part[12], &n12) == nil && n12 == 1) {
					isThought = true
				}
			}

			// Text at index 1
			if len(part) > 1 && len(part[1]) > 0 && string(part[1]) != "null" {
				var textVal string
				if err := json.Unmarshal(part[1], &textVal); err == nil {
					bindingParts = append(bindingParts, textVal)
					if isLastTurn && !isThought && textVal != "" {
						lastTurnTextParts = append(lastTurnTextParts, textVal)
					}
					continue
				}
			}
			// InlineData at index 2 ([mime, base64])
			if len(part) > 2 && len(part[2]) > 0 && string(part[2]) != "null" {
				var inline []string
				if err := json.Unmarshal(part[2], &inline); err == nil && len(inline) >= 2 {
					bindingParts = append(bindingParts, inline[1])
					continue
				}
			}
			// FileData at index 5 ([file_id])
			if len(part) > 5 && len(part[5]) > 0 && string(part[5]) != "null" {
				var fileData []string
				if err := json.Unmarshal(part[5], &fileData); err == nil && len(fileData) >= 1 {
					bindingParts = append(bindingParts, fileData[0])
					continue
				}
			}
			bindingParts = append(bindingParts, "")
		}
	}

	if bindingPrompt == "" && len(bindingParts) > 0 {
		bindingPrompt = strings.Join(bindingParts, " ")
	}
	pagePrompt := strings.Join(lastTurnTextParts, " ")
	return bindingPrompt, pagePrompt
}

func buildSessionHeaders(ctx context.Context, worker *aistudio.NativeWorker, protoHeaders http.Header, g1Tier string) map[string]string {
	out := make(map[string]string)
	for k, vals := range protoHeaders {
		if len(vals) > 0 && vals[0] != "" {
			out[http.CanonicalHeaderKey(k)] = vals[0]
		}
	}
	if tierVal := normalizeTierHeader(g1Tier); tierVal != "" {
		out["X-AIStudio-G1-Tier"] = tierVal
	}
	if state, err := worker.BrowserStorageState(ctx); err == nil {
		if auth, err := aistudio.NewSigner().Authorization(state); err == nil && auth != "" {
			out["Authorization"] = auth
		}
		if cookieHdr, err := state.CookieHeader(generateContentURL, time.Now()); err == nil && cookieHdr != "" {
			out["Cookie"] = cookieHdr
		}
	}
	return out
}

func writeJSON(w http.ResponseWriter, status int, payload any) {
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(payload)
}

// mintError 构造始终带空数组字段的 mint 错误响应
func mintError(message string) signerapi.MintResponse {
	return signerapi.MintResponse{
		OK:             false,
		SetCookies:     []signerapi.Cookie{},
		DeletedCookies: []signerapi.Cookie{},
		Error:          message,
	}
}

func discoverProxy() string {
	for _, envName := range []string{"AISTUDIO_PROXY", "HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"} {
		if val := strings.TrimSpace(os.Getenv(envName)); val != "" {
			return val
		}
	}
	for _, port := range []int{7890, 10808} {
		addr := fmt.Sprintf("127.0.0.1:%d", port)
		conn, err := net.DialTimeout("tcp", addr, 50*time.Millisecond)
		if err == nil {
			_ = conn.Close()
			return "http://" + addr
		}
	}
	return ""
}

const defaultFallbackDNSServers = "8.8.8.8:53,1.1.1.1:53,223.5.5.5:53"

var androidCACertDirs = []string{
	"/apex/com.android.conscrypt/cacerts",
	"/system/etc/security/cacerts",
}

func firstNonEmptyEnv(names ...string) string {
	for _, name := range names {
		if val := strings.TrimSpace(os.Getenv(name)); val != "" {
			return val
		}
	}
	return ""
}

func parseDNSPort(port string) (string, bool) {
	if port == "" {
		return "", false
	}
	for i := 0; i < len(port); i++ {
		if port[i] < '0' || port[i] > '9' {
			return "", false
		}
	}
	n, err := strconv.Atoi(port)
	if err != nil || n < 1 || n > 65535 {
		return "", false
	}
	return strconv.Itoa(n), true
}

func isValidHostname(host string) bool {
	if len(host) == 0 || len(host) > 253 {
		return false
	}
	labels := strings.Split(host, ".")
	allLastDigits := true
	for i, label := range labels {
		if len(label) == 0 || len(label) > 63 {
			return false
		}
		isLast := i == len(labels)-1
		for j := 0; j < len(label); j++ {
			c := label[j]
			isDigit := c >= '0' && c <= '9'
			isAlpha := (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z')
			if isLast && !isDigit {
				allLastDigits = false
			}
			if j == 0 || j == len(label)-1 {
				if !isAlpha && !isDigit {
					return false
				}
			} else if !isAlpha && !isDigit && c != '-' {
				return false
			}
		}
	}
	return !allLastDigits
}

func normalizeDNSHost(host string) (string, bool) {
	host = strings.TrimSpace(host)
	if host == "" {
		return "", false
	}
	if idx := strings.IndexByte(host, '%'); idx >= 0 {
		host = host[:idx]
		ip := net.ParseIP(host)
		if ip == nil || ip.To4() != nil {
			return "", false
		}
		return host, true
	}
	if net.ParseIP(host) != nil {
		return host, true
	}
	if isValidHostname(host) {
		return host, true
	}
	return "", false
}

func parseDNSServers(raw string) []string {
	fields := strings.FieldsFunc(raw, func(r rune) bool {
		return r == ',' || r == ';' || unicode.IsSpace(r)
	})
	if len(fields) == 0 {
		return nil
	}
	seen := make(map[string]struct{}, len(fields))
	out := make([]string, 0, len(fields))
	for _, item := range fields {
		item = strings.TrimSpace(item)
		if item == "" {
			continue
		}
		if strings.IndexFunc(item, func(r rune) bool {
			return r == '/' || unicode.IsSpace(r) || unicode.IsControl(r)
		}) >= 0 {
			continue
		}
		var rawHost, rawPort string
		if host, port, err := net.SplitHostPort(item); err == nil {
			rawHost = strings.TrimSpace(host)
			rawPort = strings.TrimSpace(port)
			if rawPort == "" {
				rawPort = "53"
			}
		} else {
			rawHost = item
			if strings.HasPrefix(rawHost, "[") && strings.HasSuffix(rawHost, "]") {
				rawHost = rawHost[1 : len(rawHost)-1]
			}
			rawHost = strings.TrimSpace(rawHost)
			rawPort = "53"
		}
		port, ok := parseDNSPort(rawPort)
		if !ok {
			continue
		}
		host, ok := normalizeDNSHost(rawHost)
		if !ok {
			continue
		}
		normalized := net.JoinHostPort(host, port)
		if _, exists := seen[normalized]; exists {
			continue
		}
		seen[normalized] = struct{}{}
		out = append(out, normalized)
	}
	if len(out) == 0 {
		return nil
	}
	return out
}

func hasNonEmptyFile(path string) bool {
	info, err := os.Stat(path)
	return err == nil && !info.IsDir() && info.Size() > 0
}

func resolveRuntimeDNSServers(goos, dnsRaw, resolvConfPath string) []string {
	raw := strings.TrimSpace(dnsRaw)
	if raw == "" {
		raw = firstNonEmptyEnv("WAA_DNS_SERVERS", "AISTUDIO_DNS_SERVERS")
	}
	if raw == "" && goos != "windows" && goos != "darwin" {
		if !hasNonEmptyFile(resolvConfPath) {
			raw = defaultFallbackDNSServers
		}
	}
	if raw == "" {
		return nil
	}
	return parseDNSServers(raw)
}

func newCustomDNSResolver(servers []string) *net.Resolver {
	var rr atomic.Uint64
	return &net.Resolver{
		PreferGo: true,
		Dial: func(ctx context.Context, network, _ string) (net.Conn, error) {
			if len(servers) == 0 {
				return nil, errors.New("no DNS servers configured")
			}
			dialNetwork := "udp"
			if strings.HasPrefix(strings.ToLower(network), "tcp") {
				dialNetwork = "tcp"
			}
			start := int((rr.Add(1) - 1) % uint64(len(servers)))
			dialer := net.Dialer{Timeout: 3 * time.Second}
			var lastErr error
			for i := range servers {
				target := servers[(start+i)%len(servers)]
				conn, err := dialer.DialContext(ctx, dialNetwork, target)
				if err == nil {
					return conn, nil
				}
				lastErr = err
			}
			if lastErr != nil {
				return nil, lastErr
			}
			return nil, errors.New("no DNS servers configured")
		},
	}
}

func resolveAndroidCertDir(goos, sslCertDir, sslCertFile, bundlePath string, candidateDirs []string) string {
	if goos == "windows" || goos == "darwin" {
		return ""
	}
	if strings.TrimSpace(sslCertDir) != "" || strings.TrimSpace(sslCertFile) != "" {
		return ""
	}
	if _, err := os.Stat(bundlePath); err == nil {
		return ""
	}
	existingDirs := make([]string, 0, len(candidateDirs))
	for _, dir := range candidateDirs {
		if info, err := os.Stat(dir); err == nil && info.IsDir() {
			existingDirs = append(existingDirs, dir)
		}
	}
	if len(existingDirs) == 0 {
		return ""
	}
	return strings.Join(existingDirs, ":")
}

func configureRuntimeNetwork(dnsRaw string) {
	if servers := resolveRuntimeDNSServers(runtime.GOOS, dnsRaw, "/etc/resolv.conf"); len(servers) > 0 {
		net.DefaultResolver = newCustomDNSResolver(servers)
		slog.Info("已配置纯 Go 自定义 DNS 解析器", "servers", servers)
	}
	if certDir := resolveAndroidCertDir(
		runtime.GOOS,
		os.Getenv("SSL_CERT_DIR"),
		os.Getenv("SSL_CERT_FILE"),
		"/etc/ssl/certs/ca-certificates.crt",
		androidCACertDirs,
	); certDir != "" {
		if err := os.Setenv("SSL_CERT_DIR", certDir); err != nil {
			slog.Warn("设置 SSL_CERT_DIR 失败", "ssl_cert_dir", certDir, "error", err)
		} else {
			slog.Info("已自动注入 Android 系统 CA 证书目录", "ssl_cert_dir", certDir)
		}
	}
}

func main() {
	defaultListen := os.Getenv("LISTEN_ADDR")
	if defaultListen == "" {
		if port := os.Getenv("PORT"); port != "" {
			defaultListen = "0.0.0.0:" + port
		} else {
			defaultListen = "127.0.0.1:8081"
		}
	}
	defaultStateDir := os.Getenv("WAA_STATE_DIR")
	if defaultStateDir == "" {
		defaultStateDir = filepath.Join(os.TempDir(), "aistudio_waa_workers")
	}
	defaultLocale := os.Getenv("WAA_LOCALE")
	if defaultLocale == "" {
		defaultLocale = "en-US"
	}
	defaultTZ := os.Getenv("AISTUDIO_TIMEZONE")
	if defaultTZ == "" {
		defaultTZ = "America/Los_Angeles"
	}
	defaultDNS := firstNonEmptyEnv("WAA_DNS_SERVERS", "AISTUDIO_DNS_SERVERS")

	listenAddr := flag.String("listen", defaultListen, "HTTP listen address")
	proxyURL := flag.String("proxy", discoverProxy(), "Upstream HTTP/HTTPS/SOCKS5 proxy URL")
	stateDir := flag.String("state-dir", defaultStateDir, "Directory for worker fingerprints and interpreter cache")
	locale := flag.String("locale", defaultLocale, "Browser profile locale")
	timezone := flag.String("timezone", defaultTZ, "Browser profile IANA timezone")
	dnsFlag := flag.String("dns", defaultDNS, "Comma/whitespace-separated DNS servers (host or host:port)")
	maxWorkers := flag.Int("max-workers", envInt("WAA_MAX_WORKERS", defaultMaxWorkers), "Maximum concurrent WAA workers")
	idleTTL := flag.Duration("idle-ttl", envDuration("WAA_IDLE_TTL", defaultIdleTTL), "Idle worker TTL before reclamation (e.g. 900s)")
	flag.Parse()

	configureRuntimeNetwork(*dnsFlag)

	if *maxWorkers < 1 {
		slog.Error("max-workers 必须为正整数", "max_workers", *maxWorkers)
		os.Exit(2)
	}
	if *idleTTL <= 0 {
		slog.Error("idle-ttl 必须为正时长", "idle_ttl", *idleTTL)
		os.Exit(2)
	}

	pool := NewWorkerPool(*stateDir, *proxyURL, *locale, *timezone, *maxWorkers, *idleTTL)
	defer pool.CloseAll()

	mux := http.NewServeMux()

	mux.HandleFunc("GET /health", func(w http.ResponseWriter, r *http.Request) {
		stats := pool.Stats()
		status := "ok"
		if !stats.OK {
			status = "closed"
		}
		writeJSON(w, http.StatusOK, signerapi.HealthResponse{
			OK:          stats.OK,
			Status:      status,
			Engine:      "spidermonkey-152",
			Workers:     stats.Workers,
			IdleWorkers: stats.Idle,
			MaxWorkers:  stats.MaxWorkers,
			Version:     signerVersion,
			Ready:       stats.Ready,
		})
	})

	mux.HandleFunc("POST /v1/proof/mint", func(w http.ResponseWriter, r *http.Request) {
		var req signerapi.MintRequest
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			writeJSON(w, http.StatusBadRequest, mintError("invalid JSON body: "+err.Error()))
			return
		}
		if strings.TrimSpace(req.Cookies) == "" {
			writeJSON(w, http.StatusBadRequest, mintError("cookies are required"))
			return
		}
		if err := validateExplicitAccountKey(req.AccountKey); err != nil {
			writeJSON(w, http.StatusBadRequest, mintError(err.Error()))
			return
		}

		authUser := parseAuthUser(req.AuthUser)
		bindingPrompt, pagePrompt := extractPromptsFromWire(req.WireContents, req.Prompt)
		if req.PagePrompt != "" {
			pagePrompt = req.PagePrompt
		}

		digest := strings.TrimSpace(req.Digest)
		if digest == "" {
			sum := sha256.Sum256([]byte(bindingPrompt))
			digest = hex.EncodeToString(sum[:])
		}

		ctx, cancel := context.WithTimeout(r.Context(), 90*time.Second)
		defer cancel()

		beforeState, beforeErr := aistudio.ParseCookieText(req.Cookies)

		worker, poolState, accountKey, release, err := pool.AcquireWorker(ctx, req.Cookies, authUser, req.Model, req.Proxy, req.AccountKey, false)
		if err != nil {
			slog.Warn("获取 WAA Worker 失败", "auth_user", authUser, "error", err)
			status := http.StatusBadGateway
			if errors.Is(err, poolstate.ErrInvalidAccountKey) {
				status = http.StatusBadRequest
			}
			writeJSON(w, status, mintError(err.Error()))
			return
		}
		defer func() {
			if release != nil {
				release()
			}
		}()

		t0 := time.Now()
		proof, protoHeaders, err := worker.ProofByDigest(ctx, digest, pagePrompt)
		if err != nil && ctx.Err() == nil && poolState == "hot" {
			slog.Warn("热 Worker 签名失败，正在重建 Worker 重试", "auth_user", authUser, "error", err)
			release()
			worker, poolState, accountKey, release, err = pool.AcquireWorker(ctx, req.Cookies, authUser, req.Model, req.Proxy, req.AccountKey, true)
			if err == nil {
				proof, protoHeaders, err = worker.ProofByDigest(ctx, digest, pagePrompt)
			}
		}
		if err != nil {
			slog.Warn("生成 WAA Proof 失败", "auth_user", authUser, "error", err)
			writeJSON(w, http.StatusBadGateway, mintError(err.Error()))
			return
		}

		setCookies := []signerapi.Cookie{}
		deletedCookies := []signerapi.Cookie{}
		if beforeErr == nil {
			if afterState, stateErr := worker.BrowserStorageState(ctx); stateErr == nil {
				setCookies, deletedCookies = signerapi.DiffCookies(
					toSignerCookies(beforeState.Cookies),
					toSignerCookies(afterState.Cookies),
				)
			}
		}

		headers := buildSessionHeaders(ctx, worker, protoHeaders, req.G1Tier)
		slog.Info(
			"生成 WAA Proof 成功",
			"auth_user", authUser,
			"pool_state", poolState,
			"digest", digest[:min(8, len(digest))],
			"page_prompt_len", len(pagePrompt),
			"proof_len", len(proof),
			"set_cookie_count", len(setCookies),
			"deleted_cookie_count", len(deletedCookies),
			"elapsed_ms", time.Since(t0).Milliseconds(),
		)

		writeJSON(w, http.StatusOK, signerapi.MintResponse{
			OK:             true,
			Proof:          proof,
			Digest:         digest,
			PoolState:      poolState,
			AccountKey:     accountKey,
			Headers:        headers,
			SetCookies:     setCookies,
			DeletedCookies: deletedCookies,
		})
	})

	// POST /v1/rpc/send: 使用同一个 SpiderMonkey Worker 的 Firefox 152 uTLS/HTTP2/HTTP3 连接池直接发送受保护 RPC
	mux.HandleFunc("POST /v1/rpc/send", func(w http.ResponseWriter, r *http.Request) {
		r.Body = http.MaxBytesReader(w, r.Body, maxRPCBodyBytes)
		var req signerapi.SendRPCRequest
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			status := http.StatusBadRequest
			message := "invalid JSON body: " + err.Error()
			var tooLarge *http.MaxBytesError
			if errors.As(err, &tooLarge) {
				status = http.StatusRequestEntityTooLarge
				message = "request body exceeds 32 MiB limit"
			}
			writeJSON(w, status, map[string]any{"ok": false, "error": message})
			return
		}
		if strings.TrimSpace(req.Cookies) == "" || strings.TrimSpace(req.Body) == "" {
			writeJSON(w, http.StatusBadRequest, map[string]any{"ok": false, "error": "cookies and body are required"})
			return
		}
		if err := validateExplicitAccountKey(req.AccountKey); err != nil {
			writeJSON(w, http.StatusBadRequest, map[string]any{"ok": false, "error": err.Error()})
			return
		}
		targetURL := strings.TrimSpace(req.URL)
		if targetURL == "" {
			targetURL = generateContentURL
		}
		validatedURL, err := upstreamguard.ValidateTargetURL(targetURL)
		if err != nil {
			writeJSON(w, http.StatusBadRequest, map[string]any{"ok": false, "error": err.Error()})
			return
		}
		targetURL = validatedURL

		authUser := parseAuthUser(req.AuthUser)
		beforeState, beforeErr := aistudio.ParseCookieText(req.Cookies)

		worker, _, accountKey, release, err := pool.AcquireWorker(r.Context(), req.Cookies, authUser, req.Model, req.Proxy, req.AccountKey, false)
		if err != nil {
			status := http.StatusBadGateway
			if errors.Is(err, poolstate.ErrInvalidAccountKey) {
				status = http.StatusBadRequest
			}
			writeJSON(w, status, map[string]any{"ok": false, "error": err.Error()})
			return
		}
		defer release()

		protoHeaders, err := worker.ProtocolHeaders(r.Context(), "")
		if err != nil {
			writeJSON(w, http.StatusBadGateway, map[string]any{"ok": false, "error": err.Error()})
			return
		}
		state, err := worker.BrowserStorageState(r.Context())
		if err != nil {
			writeJSON(w, http.StatusBadGateway, map[string]any{"ok": false, "error": err.Error()})
			return
		}
		authHeader, err := aistudio.NewSigner().Authorization(state)
		if err != nil {
			writeJSON(w, http.StatusBadGateway, map[string]any{"ok": false, "error": err.Error()})
			return
		}

		hdrs := protoHeaders.Clone()
		hdrs.Set("Content-Type", "application/json+protobuf")
		hdrs.Set("Authorization", authHeader)
		if tierVal := normalizeTierHeader(req.G1Tier); tierVal != "" {
			hdrs.Set("X-AIStudio-G1-Tier", tierVal)
		}
		for k, v := range req.Headers {
			if strings.EqualFold(k, "host") || strings.EqualFold(k, "content-length") || strings.EqualFold(k, "cookie") {
				continue
			}
			hdrs.Set(k, v)
		}

		resp, err := worker.SendProtected(r.Context(), aistudio.ProtectedRequest{
			URL:        targetURL,
			Headers:    hdrs,
			Body:       []byte(req.Body),
			PagePrompt: req.PagePrompt,
		})
		if err != nil {
			writeJSON(w, http.StatusBadGateway, map[string]any{"ok": false, "error": err.Error()})
			return
		}
		defer resp.Body.Close()

		if beforeErr == nil {
			if afterState, stateErr := worker.BrowserStorageState(r.Context()); stateErr == nil {
				updated, deleted := signerapi.DiffCookies(
					toSignerCookies(beforeState.Cookies),
					toSignerCookies(afterState.Cookies),
				)
				payload := make([]signerapi.Cookie, 0, len(updated)+len(deleted))
				payload = append(payload, updated...)
				payload = append(payload, deleted...)
				writeSetCookieHeader(w, payload)
			}
		}
		if accountKey != "" {
			w.Header().Set("X-WAA-Account-Key", accountKey)
		}

		if contentType := resp.Header.Get("Content-Type"); contentType != "" {
			w.Header().Set("Content-Type", contentType)
		}
		w.Header().Set("X-Upstream-Status", strconv.Itoa(resp.StatusCode))
		w.WriteHeader(resp.StatusCode)

		flusher, canFlush := w.(http.Flusher)
		buf := make([]byte, 16*1024)
		for {
			n, readErr := resp.Body.Read(buf)
			if n > 0 {
				_, _ = w.Write(buf[:n])
				if canFlush {
					flusher.Flush()
				}
			}
			if readErr != nil {
				break
			}
		}
	})

	// POST /v1/worker/release: 显式关闭并释放指定账户或全部空闲 Worker（幂等）
	mux.HandleFunc("POST /v1/worker/release", func(w http.ResponseWriter, r *http.Request) {
		var req signerapi.ReleaseRequest
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			writeJSON(w, http.StatusBadRequest, signerapi.ReleaseResponse{OK: false, Error: "invalid JSON body: " + err.Error()})
			return
		}
		accountKey := strings.TrimSpace(req.AccountKey)
		if !req.All && accountKey == "" {
			writeJSON(w, http.StatusBadRequest, signerapi.ReleaseResponse{OK: false, Error: "accountKey or all=true is required"})
			return
		}
		if !req.All {
			if err := validateExplicitAccountKey(accountKey); err != nil {
				writeJSON(w, http.StatusBadRequest, signerapi.ReleaseResponse{OK: false, Error: err.Error()})
				return
			}
		}
		released, skipped := pool.Release(accountKey, req.All)
		slog.Info("显式释放 WAA Worker", "all", req.All, "account", accountKey, "released", released, "skipped", skipped)
		writeJSON(w, http.StatusOK, signerapi.ReleaseResponse{OK: true, Released: released, Skipped: skipped})
	})

	server := &http.Server{
		Addr:              *listenAddr,
		Handler:           mux,
		ReadHeaderTimeout: 15 * time.Second,
	}

	stop := make(chan os.Signal, 1)
	signal.Notify(stop, syscall.SIGINT, syscall.SIGTERM)
	go func() {
		<-stop
		slog.Info("正在关闭 WAA Signer 服务...")
		ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		_ = server.Shutdown(ctx)
		pool.CloseAll()
	}()

	slog.Info("WAA Signer 服务已启动 (Mag1cFall/AIStudio2API SpiderMonkey 152 内核)",
		"version", signerVersion,
		"listen", *listenAddr,
		"proxy", *proxyURL,
		"state_dir", *stateDir,
		"max_workers", *maxWorkers,
		"idle_ttl", idleTTL.String(),
	)
	if err := server.ListenAndServe(); err != nil && err != http.ErrServerClosed {
		slog.Error("服务异常退出", "error", err)
		os.Exit(1)
	}
}
