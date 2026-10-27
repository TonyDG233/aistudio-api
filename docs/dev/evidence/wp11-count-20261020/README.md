# WP11.2 计数传输调查与阻塞（2026-10-20）

## 目标

计数必须包含 `contents + systemInstruction + tools + images + thinking`，且使用上游真实 `totalTokens`，不得估算。

## 已验证：MakerSuite CountTokens RPC（现有 chat 传输）

端点：

```text
https://alkalimakersuite-pa.clients6.google.com/$rpc/
google.internal.alkali.applications.makersuite.v1.MakerSuiteService/CountTokens
```

请求体：`[model, contents]`

结果：

- 文本 `Hello world` → `200` + `[3,...]`，totalTokens = 3。
- systemInstruction 放入 wire index 5 → 仍返回 3，**system 被忽略**。
- tools 放入 index 6 → `400 Invalid value (), Unexpected list for single non-message field.`，**tools 不被接受**。
- 结论：该 RPC 只能计 contents，不能计 system/tools，不满足 WP11 的精确计数目标。

## 已验证：generativelanguage REST `:countTokens`

端点：

```text
POST https://generativelanguage.googleapis.com/v1beta/models/{model}:countTokens
```

尝试：

1. AI Studio web API key（`AIzaSyDdP8...`）：
   - 请求体 `{"contents":..., "systemInstruction":..., "tools":...}` → `400 Unknown name "systemInstruction"/"tools"`。
   - 请求体 `{"generateContentRequest": {...}}` → `403 API_KEY_HTTP_REFERRER_BLOCKED`（key 有 referrer 限制）。
   - 加 `Referer: https://aistudio.google.com/` → `403 API_KEY_SERVICE_BLOCKED`，明确提示 `GenerativeService.CountTokens` 被该 key 禁止。
2. MakerSuite / AI Studio web host 的 `/v1beta/...:countTokens`：
   - 返回 HTML `400`，不是 REST API 网关。

结论：现有 web key 无权调用 REST CountTokens；需要 OAuth access token 或一个启用 `CountTokens` 的 Generative Language API key。

## 最终实现：打包聚合 contents（生产路径）

`application/token_counter.py` 的 `build_count_body()` 把以下内容打包进一个合成 user content：

- `[system]` + systemInstruction 文本
- `[tools]` + 工具声明 JSON（紧凑分隔符、去 null）
- `[conversation]` + 各轮文本、thinking、functionCall/functionResponse
- 图片以 inlineData part 保留

然后调用 MakerSuite `CountTokens` RPC（`[model, packed_contents]`），只取上游 `totalTokens`。

**生产代码不读取、不依赖任何 API key。**

## 误差校验（临时 key，仅离线）

测试 key 只用于对照 REST `:countTokens` 的精确值，未写入代码/配置/证据。

| 场景 | 打包聚合 | REST 精确 | 误差 |
|---|---:|---:|---:|
| `Hello world` | 10 | 3 | +7 tokens（短文本固定开销） |
| system + 2 tools + `Hello world` | 139 | 128 | +8.59% |
| 图片 + system + 2 tools + functionCall/Response | 1292 | 1257 | +2.78% |

结论：打包聚合会略高估（当前样本 +2.8% ~ +8.6%），绝对误差主要来自分段标记和消息 framing；对“大致代表上下文长度”的 agent 上下文条可接受，但不能作为逐 token 精确值对外宣称。

## 真机计数（无 key）

同一 system + tools + 文本请求：

```text
Gemini  :countTokens      -> 200 {"totalTokens":139}
Claude  count_tokens      -> 200 {"input_tokens":138}
Responses input_tokens    -> 200 {"object":"response.input_tokens","input_tokens":139}
```

与 REST 精确值 128 相比，分别 +8.59% / +7.81% / +8.59%。

## 429 换号

`AISTUDIO_TEST_HOOKS=1` + `AISTUDIO_TEST_FORCE_429_ACCOUNT=acc_46223636`：

- 第一次：`acc_46223636` 被强制 429；
- `surface:switch` 切换到 `acc_b1a36ccf`；
- 第二次：`totalTokens=10`，HTTP 200，约 19.4s（含新 Context 装载）。

## 提交

未 commit。测试 key 未进入仓库。