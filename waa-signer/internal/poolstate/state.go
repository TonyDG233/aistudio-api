// Package poolstate 汇集 Worker 池的纯状态判定。
//
// 该包只依赖标准库，不引入 cgo、mozjs 或任何上游传输依赖，因此可以在
// 没有 SpiderMonkey 静态库的环境中独立单元测试。
package poolstate

import (
	"errors"
	"fmt"
	"regexp"
	"strings"
)

// RecreateDecision 描述给定时刻对旧 Worker 应采取的动作。
type RecreateDecision int

const (
	// ReuseWorker 表示当前没有旧 Worker 需要关闭（或无需重建）。
	ReuseWorker RecreateDecision = iota
	// CloseImmediately 表示没有在途 lease，可立即关闭旧 Worker 并重建。
	CloseImmediately
	// DeferUntilIdle 表示仍有在途 lease，必须标记 pending，待最后一个 lease 释放后重建。
	DeferUntilIdle
)

// DecideForceRecreate 根据旧 Worker 是否存在与同账号在途 lease 数给出重建决策。
//
// 只有 inFlight == 0 时才允许立即关闭旧 Worker；存在在途 proof/rpc 时必须延迟，
// 避免强制重建中断同账号正在执行的请求。
func DecideForceRecreate(hasWorker bool, inFlight int) RecreateDecision {
	if !hasWorker {
		return ReuseWorker
	}
	if inFlight > 0 {
		return DeferUntilIdle
	}
	return CloseImmediately
}

// ShouldRebuildAfterDrain 报告 pending 状态是否已经安全排空。
func ShouldRebuildAfterDrain(pending bool, inFlight int) bool {
	return pending && inFlight <= 0
}

// NextGeneration 推进一次重建代数。
func NextGeneration(current uint64) uint64 {
	return current + 1
}

// ShouldClearPending 报告刚完成的重建代数是否仍是最新请求。
//
// requestedGeneration == 0 表示调用方显式要求无条件清除（例如已在无在途
// 请求时立即关闭旧 Worker）。
func ShouldClearPending(requestedGeneration, currentGeneration uint64) bool {
	return requestedGeneration == 0 || requestedGeneration == currentGeneration
}

var accountKeyPattern = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$`)

// ErrInvalidAccountKey 表示显式 accountKey 不符合稳定键契约。
var ErrInvalidAccountKey = errors.New("accountKey 非法")

// ValidateAccountKey 校验显式 accountKey。
//
// 该键同时用作 Worker 状态目录名，因此只允许安全字符，且禁止 "." / ".."
// 这类可能造成目录穿越的取值。
func ValidateAccountKey(key string) error {
	if !accountKeyPattern.MatchString(key) {
		return fmt.Errorf("%w: 只允许 1-128 位字母、数字、点、下划线与连字符，且必须以字母或数字开头", ErrInvalidAccountKey)
	}
	return nil
}

// rotatingCredentialNames 是 Google 每次响应都会轮换的短效凭据名。
//
// 这些凭据由客户端（Python）在活跃使用时通过 /RotateCookies 或保活刷新，
// 因此“客户端是否换过凭据”只需比较客户端自己两次传入的快照。
var rotatingCredentialNames = []string{
	"__Secure-1PSIDTS",
	"__Secure-3PSIDTS",
	"SIDCC",
	"__Secure-1PSIDCC",
	"__Secure-3PSIDCC",
}

// ExtractRotatingCredentials 从客户端 Cookie 快照中提取滚动凭据的非空值。
//
// 返回值是轻量快照，用作该 Worker 冷启动创建时的基线；没有有效滚动凭据时
// 返回 nil，调用方据此判断“无法轮换检测”。
func ExtractRotatingCredentials(cookies map[string]string) map[string]string {
	if len(cookies) == 0 {
		return nil
	}
	snapshot := make(map[string]string, len(rotatingCredentialNames))
	for _, name := range rotatingCredentialNames {
		if value := strings.TrimSpace(cookies[name]); value != "" {
			snapshot[name] = value
		}
	}
	if len(snapshot) == 0 {
		return nil
	}
	return snapshot
}

// HasClientRotatedCredentials 报告客户端传入的滚动凭据是否已偏离冷启动基线。
//
// baseline 是该 Worker 创建时客户端传入的初始滚动凭据快照，incoming 是本次
// 请求携带的同一份快照。只有客户端主动更新了凭据（例如 403 刷新后先
// /RotateCookies 再下发新值）才返回 true；Worker 运行期自行从上游响应更新的
// Cookie 不参与比较，因此并发请求携带同一份旧基线时不会误判回退。
//
// incoming 缺失或为空的键不参与比较，避免不完整快照触发无谓重建。
func HasClientRotatedCredentials(baseline map[string]string, incoming map[string]string) bool {
	if len(baseline) == 0 {
		return false
	}
	for _, name := range rotatingCredentialNames {
		previous := baseline[name]
		if previous == "" {
			continue
		}
		value := strings.TrimSpace(incoming[name])
		if value == "" {
			continue
		}
		if value != previous {
			return true
		}
	}
	return false
}

// ResolveAccountKey 优先使用调用方提供的稳定 accountKey，否则回退到 Cookie 哈希。
//
// 显式 key 会先做安全校验；回退值由调用方基于 Cookie 计算，保证 mint/rpc/release
// 在同一账号上使用同一个键。
func ResolveAccountKey(explicit, fallback string) (string, error) {
	explicit = strings.TrimSpace(explicit)
	if explicit == "" {
		fallback = strings.TrimSpace(fallback)
		if fallback == "" {
			return "", errors.New("accountKey 为空且无法从 Cookie 推导")
		}
		return fallback, nil
	}
	if err := ValidateAccountKey(explicit); err != nil {
		return "", err
	}
	return explicit, nil
}
