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
