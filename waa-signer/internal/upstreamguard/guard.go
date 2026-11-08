// Package upstreamguard 校验 waa-signer 允许访问的上游 URL。
//
// 只允许 https://*.google.com 与 https://*.googleapis.com（含精确域名），
// 阻断任意 URL 与 SSRF；错误信息中不包含任何 Cookie 或认证材料。
package upstreamguard

import (
	"errors"
	"fmt"
	"net/url"
	"strings"
)

// ErrInvalidTargetURL 表示目标 URL 不满足协议或域名白名单。
var ErrInvalidTargetURL = errors.New("targetURL 非法")

var allowedHostSuffixes = []string{"google.com", "googleapis.com"}

// IsAllowedGoogleHost 报告 host 是否为 Google 官方白名单域名（大小写不敏感）。
func IsAllowedGoogleHost(host string) bool {
	host = strings.ToLower(strings.TrimSuffix(strings.TrimSpace(host), "."))
	if host == "" {
		return false
	}
	for _, suffix := range allowedHostSuffixes {
		if host == suffix || strings.HasSuffix(host, "."+suffix) {
			return true
		}
	}
	return false
}

// ValidateTargetURL 校验 RPC 目标 URL，并返回规范化后的 URL。
//
// 规则：
//   - 必须使用 https；
//   - host 必须命中 Google 官方白名单；
//   - 不允许 userinfo、非 443 端口；
//   - 拒绝 IP 字面量与空 host。
func ValidateTargetURL(raw string) (string, error) {
	trimmed := strings.TrimSpace(raw)
	if trimmed == "" {
		return "", fmt.Errorf("%w: 不能为空", ErrInvalidTargetURL)
	}
	parsed, err := url.Parse(trimmed)
	if err != nil {
		return "", fmt.Errorf("%w: 无法解析", ErrInvalidTargetURL)
	}
	if !strings.EqualFold(parsed.Scheme, "https") {
		return "", fmt.Errorf("%w: 必须使用 https", ErrInvalidTargetURL)
	}
	if parsed.User != nil {
		return "", fmt.Errorf("%w: 不得包含 userinfo", ErrInvalidTargetURL)
	}
	host := parsed.Hostname()
	if host == "" {
		return "", fmt.Errorf("%w: 缺少 host", ErrInvalidTargetURL)
	}
	if port := parsed.Port(); port != "" && port != "443" {
		return "", fmt.Errorf("%w: 仅允许 443 端口", ErrInvalidTargetURL)
	}
	if !IsAllowedGoogleHost(host) {
		return "", fmt.Errorf("%w: host %q 不在 Google 白名单", ErrInvalidTargetURL, host)
	}
	return parsed.String(), nil
}
