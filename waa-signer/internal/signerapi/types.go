// Package signerapi 定义 waa-signer 的 JSON 传输契约。
//
// 该包只依赖标准库，便于在无 cgo/mozjs 依赖的环境中校验字段名与序列化行为。
package signerapi

import "encoding/json"

// Cookie 是回传给 Python 侧的 Cookie 更新项。
//
// 删除/失效语义：value 为空且 expires 为 0（Unix 纪元）表示该 Cookie 已被删除；
// 调用方可按空值或过期时间任一规则清理本地副本。
type Cookie struct {
	Name         string  `json:"name"`
	Value        string  `json:"value"`
	Domain       string  `json:"domain"`
	Path         string  `json:"path"`
	Expires      float64 `json:"expires"`
	HTTPOnly     bool    `json:"httpOnly"`
	Secure       bool    `json:"secure"`
	SameSite     string  `json:"sameSite"`
	PartitionKey string  `json:"partitionKey,omitempty"`
}

// MintRequest 是 POST /v1/proof/mint 的请求体。
type MintRequest struct {
	Cookies      string          `json:"cookies"`
	AuthUser     any             `json:"authUser"`
	G1Tier       string          `json:"g1Tier"`
	Model        string          `json:"model"`
	Prompt       string          `json:"prompt"`
	PagePrompt   string          `json:"pagePrompt"`
	Digest       string          `json:"digest"`
	WireContents json.RawMessage `json:"wireContents"`
	Proxy        string          `json:"proxy"`
	AccountKey   string          `json:"accountKey"`
}

// MintResponse 是 POST /v1/proof/mint 的响应体。
type MintResponse struct {
	OK             bool              `json:"ok"`
	Proof          string            `json:"proof,omitempty"`
	Digest         string            `json:"digest,omitempty"`
	PoolState      string            `json:"poolState,omitempty"`
	AccountKey     string            `json:"accountKey,omitempty"`
	Headers        map[string]string `json:"headers,omitempty"`
	SetCookies     []Cookie          `json:"setCookies"`
	DeletedCookies []Cookie          `json:"deletedCookies"`
	Error          string            `json:"error,omitempty"`
}

// SendRPCRequest 是 POST /v1/rpc/send 的请求体。
type SendRPCRequest struct {
	Cookies    string            `json:"cookies"`
	AuthUser   any               `json:"authUser"`
	G1Tier     string            `json:"g1Tier"`
	Model      string            `json:"model"`
	URL        string            `json:"url"`
	Headers    map[string]string `json:"headers"`
	Body       string            `json:"body"`
	PagePrompt string            `json:"pagePrompt"`
	Proxy      string            `json:"proxy"`
	AccountKey string            `json:"accountKey"`
}

// ReleaseRequest 支持按账户或全量显式释放 Worker。
type ReleaseRequest struct {
	AccountKey string `json:"accountKey"`
	All        bool   `json:"all"`
}

// ReleaseResponse 是 POST /v1/worker/release 的响应体。
type ReleaseResponse struct {
	OK       bool   `json:"ok"`
	Released int    `json:"released"`
	Skipped  int    `json:"skipped"`
	Error    string `json:"error,omitempty"`
}

// HealthResponse 是 GET /health 的响应体，绝不包含 Cookie。
//
// ready 表示"服务进程健康且 Worker 池还能接收新请求"；当池已满且没有空闲
// Worker 时 ready=false，但 ok/status 仍表示服务进程本身正常。
type HealthResponse struct {
	OK          bool   `json:"ok"`
	Status      string `json:"status"`
	Engine      string `json:"engine"`
	Workers     int    `json:"workers"`
	IdleWorkers int    `json:"idleWorkers"`
	MaxWorkers  int    `json:"maxWorkers"`
	Version     string `json:"version"`
	Ready       bool   `json:"ready"`
}
