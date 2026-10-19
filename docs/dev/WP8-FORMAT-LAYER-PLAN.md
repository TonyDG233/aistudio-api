# WP8 OpenAI / Claude 格式层实现计划

- 状态：DRAFT（待用户批准，未开始实现）
- 日期：2026-10-19
- 分支：`p1-ark-merge`
- 依据：`docs/dev/PLAN-ark-merge-20261017.md` §4 WP8；`docs/dev/AUDIT-P2P3-20261018.md`
- 约束：本计划只描述实现，不修改 `web/` 与静态产物；不改 WP10 保活机制

---

## 1. 范围

### 1.1 纳入

- OpenAI Chat Completions：`POST /v1/chat/completions`
  - 非流式、流式
  - 文本、图片、思考、工具调用、usage
- OpenAI 模型发现：`GET /v1/models`、`GET /v1/models/{model_id}`
- Claude Messages：`POST /v1/messages`
  - 非流式、流式
  - 文本、图片、思考、工具调用、usage
- 错误码按协议面映射（含鉴权/校验错误）
- ATG per-format 激活（gemini/openai/claude）
- `features.formats.openai` / `features.formats.claude` 开关
- 请求统计记录 `api_format` 维度（为 WP6 formatBreakdown 预留）

### 1.2 不纳入

- OpenAI Responses API（PLAN 标注可选，WP8 首期不交付）
- Claude `POST /v1/messages/count_tokens`（本仓无上游 countTokens 能力，返回显式 404）
- 按协议独立 API Key（首期复用全局 `api_keys`）
- 任何 UI / `web/` / 静态产物改动
- WP10 keepalive / auth 回写 / 元数据刷新改动

### 1.3 不可破坏

- `/v1beta` Gemini 原生接口的响应、SSE 序列、错误形状保持现状
- `config.yaml`（本地，git-ignore）与 `defaults/config.yaml`（tracked）双写
- 未获用户批准不提交、不推送

---

## 2. 现状审计锚点

实现前已确认的代码位置：

| 位置 | 现状 |
|---|---|
| `src/aistudio_api/application/api_service_gemini.py:328` | Gemini 非流式主处理 |
| `src/aistudio_api/application/api_service_gemini.py:641` | Gemini 流式主处理 |
| `src/aistudio_api/application/api_service_gemini.py:486` | Gemini SSE 事件/usage 格式化 |
| `src/aistudio_api/application/api_service_gemini.py:579` | Gemini SSE 错误格式化 |
| `src/aistudio_api/application/chat_service.py:545` | `normalize_gemini_request` |
| `src/aistudio_api/application/chat_service.py:785` | `responseMimeType` / `responseSchema` 已接入 |
| `src/aistudio_api/application/chat_service.py:738` | `toolConfig` 已接入 |
| `src/aistudio_api/infrastructure/gateway/wire_codec.py:287` | 默认启用 thinking（`enable_default_thinking()`） |
| `src/aistudio_api/api/app.py:166` | Gemini / Models 路由注册 |
| `src/aistudio_api/api/app.py:170` | 全局 HTTPException → Gemini 错误形状 |
| `src/aistudio_api/api/app.py:186` | 全局校验异常 → Gemini 错误形状 |
| `src/aistudio_api/api/app.py:338` | SPA 中间件 `is_api` 前缀判断 |
| `src/aistudio_api/api/dependencies.py:69` | `require_api_key`，已支持 Bearer 与 `x-api-key` |
| `src/aistudio_api/api/state.py:126` | `runtime_state.record` |
| `src/aistudio_api/infrastructure/gateway/model_defaults.py:560` | `_FEATURES_DEFAULTS` |
| `src/aistudio_api/infrastructure/gateway/model_defaults.py:751` | `_normalize_features` |
| `src/aistudio_api/infrastructure/gateway/model_defaults.py:852` | `update_configured_features` |
| `src/aistudio_api/application/atg/config.py:95` | `load_atg_config(overrides)` |
| `src/aistudio_api/application/atg/__init__.py:88` | `create_atg_processor(overrides)` |
| `src/aistudio_api/api/routes_models.py:56` | 现有 `/v1beta/models` |
| `tests/unit/test_routes_models.py:45` | 现有测试断言 `/v1/models` 为 404（WP8 需改写） |

---

## 3. 总体架构

```
POST /v1/chat/completions ─┐
POST /v1/messages ─────────┤
                           ▼
              format_converters/*_request.py
                           ▼
          GeminiGenerateContentRequest（dict → pydantic 校验）
                           ▼
                normalize_gemini_request()
                           ▼
              application/generation_service.py
        （ATG wrap、surface 解析、重试、切号、unpack）
                           ▼
        ModelOutput（非流式） / StreamEvent 异步迭代（流式）
                           ▼
              format_converters/*_response.py
                           ▼
              OpenAI / Claude 协议响应
```

原则：

1. 请求统一归一到现有 `GeminiGenerateContentRequest`，不新建第二套内部模型。
2. 响应在格式边界逆变换，Gemini 原生路由保持原样。
3. 非流式与流式共用同一套重试 / ATG / surface / 切号逻辑。
4. 图片 URL 在进入上游前由代理下载并转 base64；上游只接受 `inlineData`。

---

## 4. 文件清单

### 4.1 新增

| 文件 | 职责 |
|---|---|
| `src/aistudio_api/application/format_converters/__init__.py` | 导出 `ConversionError`、converter 入口 |
| `src/aistudio_api/application/format_converters/common.py` | ID、finish_reason、usage、SSE、content buffer、通用校验 |
| `src/aistudio_api/application/format_converters/media.py` | `MediaFetcher`：远程图片下载、SSRF 防护、转 data URL |
| `src/aistudio_api/application/format_converters/openai_request.py` | OpenAI Chat Completions → Gemini 请求 |
| `src/aistudio_api/application/format_converters/openai_response.py` | Gemini 输出 → OpenAI 非流式 / 流式 |
| `src/aistudio_api/application/format_converters/claude_request.py` | Claude Messages → Gemini 请求 |
| `src/aistudio_api/application/format_converters/claude_response.py` | Gemini 输出 → Claude 非流式 / 流式 |
| `src/aistudio_api/application/generation_service.py` | 协议无关的生成管线（非流式 / 流式事件） |
| `src/aistudio_api/application/upstream_errors.py` | 上游错误分类与重试异常处理（从 `api_service_gemini.py` 迁出） |
| `src/aistudio_api/api/schemas_openai.py` | OpenAI 请求模型 |
| `src/aistudio_api/api/schemas_claude.py` | Claude 请求模型 |
| `src/aistudio_api/api/format_errors.py` | 按协议面生成错误响应 |
| `src/aistudio_api/api/routes_openai.py` | `/v1/chat/completions`、`/v1/models` |
| `src/aistudio_api/api/routes_claude.py` | `/v1/messages` |

### 4.2 修改

| 文件 | 改动 |
|---|---|
| `src/aistudio_api/application/api_service_gemini.py` | 改为调用 `generation_service`；保留 Gemini SSE 渲染与公开 handler |
| `src/aistudio_api/api/app.py` | 注册新路由；异常处理按路径分流；`is_api` 增加 `/v1` |
| `src/aistudio_api/application/atg/config.py` | 新增 `resolve_atg_mode(api_format)` |
| `src/aistudio_api/infrastructure/gateway/model_defaults.py` | 新增 `features.formats.*`、`atg.mode_by_format.*` |
| `src/aistudio_api/api/state.py` | `ModelStatsItem.formats` + `record(..., api_format=...)` |
| `defaults/config.yaml` | 同步新增配置键（tracked） |
| `config.yaml` | 本地同步（git-ignore，不提交） |
| `tests/unit/test_routes_models.py` | 删除 `/v1/models` 404 断言，改为 OpenAI 契约 |
| `tests/unit/test_features_config.py` | 新键默认值 / 非法值回退 |

### 4.3 不改

- `web/`、任何静态产物
- WP10 keepalive、auth 回写、元数据刷新
- `/v1beta` Gemini 路由行为

---

## 5. 请求映射

### 5.1 OpenAI → Gemini

| OpenAI | Gemini |
|---|---|
| `messages[role=system/developer]` | `systemInstruction.parts[].text`（换行拼接） |
| `messages[role=user].content` 文本 | `contents[].parts[].text` |
| `image_url.url = data:image/*;base64,...` | `inlineData {mimeType, data}` |
| `image_url.url = http(s)://...` | `MediaFetcher` 下载 → `inlineData` |
| `messages[role=assistant].content` | model text part |
| `assistant.tool_calls[]` | `functionCall {id, name, args}` |
| `messages[role=tool]` | `functionResponse {id, name, response}` |
| `tools[].function` / 旧 `functions` | `functionDeclarations`，schema 放 `parametersJsonSchema` |
| `tool_choice` | `toolConfig.functionCallingConfig` |
| `max_completion_tokens` / `max_tokens` | `generationConfig.maxOutputTokens` |
| `temperature` / `top_p` / `top_k` | 同名生成配置 |
| `stop`（字符串或数组） | `generationConfig.stopSequences` |
| `reasoning_effort` | `thinkingConfig.thinkingLevel` |
| `response_format.type=json_schema` | `responseMimeType=application/json` + `responseSchema` |
| `response_format.type=json_object` | `responseMimeType=application/json` + 开放 object schema |
| `response_format.type=text` | 不追加约束 |

工具调用历史规则：

- `tool` 消息的 `tool_call_id` 从前序 assistant `tool_calls` 解析函数名。
- 解析不到 → 400 `invalid_request_error`，不生成 `unknown_function`。
- 连续 `tool` 消息合并为同一个 user content。
- 连续 assistant part 合并为同一个 model content，保证角色交替。
- `tool_calls[].function.arguments` 必须是合法 JSON 对象；非法 → 400。
- `n > 1` → 400 显式不支持。

不支持的 part 类型（`input_audio`、`file`、未知类型）→ 400，不静默丢弃。

### 5.2 Claude → Gemini

| Claude | Gemini |
|---|---|
| `system` 字符串 / 文本块数组 | `systemInstruction.parts[].text` |
| `messages[].content[type=text]` | text part |
| `content[type=image].source.type=base64` | `inlineData` |
| `content[type=image].source.type=url` | `MediaFetcher` 下载 → `inlineData` |
| `content[type=tool_use]` | `functionCall {id, name, input}` |
| `content[type=tool_result]` | `functionResponse {id, name, response}` |
| `tool_result.is_error=true` | `response = {error: ...}` |
| `tool_result` 内图片 | `functionResponse.parts[].inlineData` |
| `content[type=thinking]` | `{text, thought: true}` |
| `max_tokens` | `generationConfig.maxOutputTokens`（必填，缺失 → 400） |
| `stop_sequences` / `temperature` / `top_p` / `top_k` | 同名生成配置 |
| `thinking` | 按 `budget_tokens` 映射 `thinkingLevel` |
| `tools[].input_schema` | `functionDeclarations.parametersJsonSchema` |
| `tool_choice` | `toolConfig.functionCallingConfig` |
| `output_format` / `output_config.format` | `responseMimeType` / `responseSchema` |

规则：

- `tool_use_id` 从前序 assistant `tool_use` 解析函数名；解析不到 → 400。
- 连续 `tool_result` 合并为一个 user content。
- `thinking.disabled` 无法关闭上游思考（见 §11 限制），只影响 level。
- `disable_parallel_tool_use` 记录 debug 日志，不强行改变上游。
- `document`、`server_tool_use`、各类 server tool result、未知 block → 400，不静默转文本。
- 内置工具类型（`web_search`、`code_execution` 等）→ 400，首期只支持 function tools。

---

## 6. 响应映射

### 6.1 OpenAI 非流式

```json
{
  "id": "chatcmpl-<epoch_ms>_<hex>",
  "object": "chat.completion",
  "created": 1234567890,
  "model": "<client model>",
  "choices": [
    {
      "index": 0,
      "message": {
        "role": "assistant",
        "content": "<text or empty string>",
        "reasoning_content": "<thinking, optional>",
        "tool_calls": [
          {
            "id": "call_...",
            "type": "function",
            "function": {"name": "...", "arguments": "<complete JSON string>"}
          }
        ]
      },
      "finish_reason": "stop | length | content_filter | tool_calls"
    }
  ],
  "usage": {
    "prompt_tokens": 0,
    "completion_tokens": 0,
    "total_tokens": 0,
    "prompt_tokens_details": {"cached_tokens": 0},
    "completion_tokens_details": {"reasoning_tokens": 0}
  }
}
```

- 只存在工具调用时 `content` 为空字符串，`finish_reason="tool_calls"`。
- 图片输出转 `![Generated Image](data:<mime>;base64,<data>)` 文本。
- 工具参数为上游完整 `functionCall.args` 序列化结果，不伪造分片。

### 6.2 Claude 非流式

```json
{
  "id": "msg_<epoch_ms>_<hex>",
  "type": "message",
  "role": "assistant",
  "model": "<client model>",
  "content": [
    {"type": "thinking", "thinking": "...", "signature": "proxy_thinking_..."},
    {"type": "text", "text": "..."},
    {"type": "tool_use", "id": "toolu_...", "name": "...", "input": {}}
  ],
  "stop_reason": "end_turn | max_tokens | tool_use",
  "stop_sequence": null,
  "usage": {
    "input_tokens": 0,
    "output_tokens": 0,
    "cache_read_input_tokens": 0,
    "cache_creation_input_tokens": 0
  }
}
```

- 空内容返回 `[{"type": "text", "text": ""}]`。
- 有工具调用时 `stop_reason="tool_use"`。
- 图片输出转文本 markdown data URL。

---

## 7. 流式序列

### 7.1 OpenAI

- `Content-Type: text/event-stream`
- 每个事件：`data: <json>\n\n`
- 首个内容 chunk 带 `delta.role="assistant"`。
- 文本 → `delta.content`
- 思考 → `delta.reasoning_content`
- 图片 → `delta.content` 的 markdown data URL
- 工具调用：每个 functionCall 一个 chunk

```json
{
  "choices": [{
    "index": 0,
    "delta": {
      "tool_calls": [{
        "index": 0,
        "id": "call_...",
        "type": "function",
        "function": {"name": "...", "arguments": "<json>"}
      }]
    },
    "finish_reason": null
  }],
  "id": "chatcmpl-...",
  "object": "chat.completion.chunk",
  "created": 1234567890,
  "model": "..."
}
```

- 结束 chunk：`delta={}`，`finish_reason` 为 `tool_calls` 或映射值，附 `usage`。
- 最后输出 `data: [DONE]\n\n`。
- `stream_options.include_usage` 不改变行为：结束 chunk 始终带 usage（对齐 Ark）。

### 7.2 Claude

事件顺序固定：

1. `event: ping` + `data: {}`
2. `event: message_start`
3. 内容块事件（thinking / text / tool_use，按上游顺序）
4. `event: message_delta`（`stop_reason` + usage）
5. `event: message_stop`

thinking 块：

```
content_block_start {type:"thinking", thinking:"", signature:""}
content_block_delta  {type:"thinking_delta", thinking:"..."}
content_block_delta  {type:"signature_delta", signature:"proxy_thinking_..."}
content_block_stop
```

text 块：

```
content_block_start {type:"text", text:""}
content_block_delta  {type:"text_delta", text:"..."}
content_block_stop
```

tool_use 块：

```
content_block_start {type:"tool_use", id:"toolu_...", name:"...", input:{}}
content_block_delta  {type:"input_json_delta", partial_json:"<complete JSON>"}
content_block_stop
```

- 块 `index` 全局递增。
- 有工具调用时 `stop_reason="tool_use"`。

---

## 8. Usage 映射

OpenAI：

| 字段 | 来源 |
|---|---|
| `prompt_tokens` | `prompt_tokens` |
| `completion_tokens` | `completion_tokens` |
| `total_tokens` | `total_tokens` |
| `prompt_tokens_details.cached_tokens` | `prompt_tokens_details.cached_tokens` |
| `completion_tokens_details.reasoning_tokens` | `completion_tokens_details.reasoning_tokens` |

Claude：

| 字段 | 计算 |
|---|---|
| `input_tokens` | `max(0, prompt_tokens - cached_tokens)` |
| `output_tokens` | `completion_tokens` |
| `cache_read_input_tokens` | `cached_tokens` |
| `cache_creation_input_tokens` | `0` |

- 流式取最后一个 `usage` 事件；缺失时全 0，不丢字段。
- 非流式取 `ModelOutput.usage`。

---

## 9. 错误映射

### 9.1 状态码与 type

| HTTP | OpenAI `type` | Claude `type` |
|---|---|---|
| 400 | `invalid_request_error` | `invalid_request_error` |
| 401 | `authentication_error` | `authentication_error` |
| 403 | `permission_error` | `permission_error` |
| 404 | `not_found_error` | `not_found_error` |
| 429 | `rate_limit_error` | `rate_limit_error` |
| 500 | `api_error` | `api_error` |
| 503 | `service_unavailable` | `overloaded_error` |
| 504 | `timeout_error` | `timeout_error` |

### 9.2 非流式 / 首字节前

OpenAI：

```json
{"error": {"message": "...", "type": "...", "code": null, "param": null}}
```

Claude：

```json
{"type": "error", "error": {"type": "...", "message": "..."}}
```

- 鉴权失败、请求校验失败、format 开关关闭、转换错误都按路径返回对应形状。
- 转换错误把字段名放 OpenAI `param`；Claude 在 message 中写 `<field>: ...`。

### 9.3 已开始流式

OpenAI：

```
data: {"error": {"message":"...", "type":"...", "code":null}}
```

Claude：

```
event: error
data: {"type":"error","error":{"type":"...","message":"..."}}
```

### 9.4 首事件预取

格式路由在创建 `StreamingResponse` 前预取第一个 `StreamEvent`：

- 预取阶段异常 → 返回正确 HTTP 状态 + JSON 错误。
- 已预热成功 → 创建 SSE 响应；后续异常写流内错误事件。
- 避免 Starlette 先发 200 再暴露鉴权/账号错误。

---

## 10. 远程图片与多模态

### 10.1 机制

上游只接受 `inlineData`（base64），因此：

- `data:` URL：直接解析，不联网。
- `http(s)` URL：`MediaFetcher` 下载 → 校验 → base64 data URL → `inlineData`。

### 10.2 `MediaFetcher`

新增 `format_converters/media.py`：

```python
class MediaFetcher:
    async def to_data_url(self, url: str) -> str: ...
```

限制：

- 超时 15 秒。
- 最大 20 MiB。
- `Content-Type` 必须以 `image/` 开头；缺失或非图片 → 400。
- 最多 3 次重定向；每次重定向重新做 SSRF 校验。
- 拒绝 loopback、link-local、云元数据地址（169.254.169.254）。
- 默认拒绝私网地址；`features.formats.media.allow_private_urls=true` 时放开。
- 下载失败 / 超限 / 非图片 → 400 `invalid_request_error`，消息包含 URL 与原因。

### 10.3 接入点

- OpenAI `image_url.url` 的 http(s)。
- Claude `image.source.type=url`。
- Claude `tool_result` 内的图片块。

转换器改为 `async`，`MediaFetcher` 通过构造参数注入；单测使用 mock transport，不联网。

### 10.4 不做

- 不下载音频 / 视频 / 文件。
- 不把下载失败降级为 `[System Note]` 文本（除非用户另行批准）。

---

## 11. ATG per-format

### 11.1 配置

```yaml
features:
  atg:
    mode: off
    mode_by_format:
      gemini: inherit
      openai: inherit
      claude: inherit
```

语义：

- `inherit` → 使用 `features.atg.mode`。
- `off` / `prefer` / `required` → 覆盖该协议面。
- 非法值 → `inherit`。

### 11.2 接入

- 每个格式路由在请求归一化前调用 `create_atg_processor({"mode": resolve_atg_mode(fmt)})`。
- ATG 只看到 Gemini 形状请求，与现有逻辑一致。
- 流式 unwrap 继续产出协议无关 `StreamEvent`。
- 日志与统计带 `format=openai|claude`。
- `required` 模式下无合成调用 → 显式失败，不静默放行。

---

## 12. 配置与开关

### 12.1 `features.formats`

```yaml
features:
  formats:
    openai: true
    claude: true
    media:
      allow_private_urls: false
```

- 默认开启：对齐 Ark `ProxyServerSystem.js:675-702` 的无条件端点注册，不新增隐藏面。
- 开关保留作运维手段；关闭时访问对应路由 → 403 `permission_error`，message 明确提示配置键。
- 开启后 `/v1/models` 对 OpenAI 面可见；Claude 无模型列表端点。

### 12.2 双写

- `defaults/config.yaml`：tracked，作为分发默认值。
- `config.yaml`：本地运行时，git-ignore；实现时同步。
- 提交与证据以 `defaults/config.yaml` 为准。

### 12.3 `_FEATURES_DEFAULTS` / 归一化

- `model_defaults.py:560` 增加 `formats` 与 `atg.mode_by_format`。
- `model_defaults.py:751` 增加 bool/choice 归一化。
- `update_configured_features` 自动接受新顶层键 `formats`。

---

## 13. 统计

- `ModelStatsItem` 增加 `formats: dict[str, int]`。
- `record(model, result, usage=None, api_format="gemini")` 累加对应格式计数。
- `load_stats` 兼容旧 `stats.json`（无 `formats` 字段）。
- WP8 只记录计数；`formatBreakdown` 展示留给 WP6。

---

## 14. 生成管线抽取

### 14.1 `generation_service.py`

```python
@dataclass(frozen=True)
class StreamEvent:
    kind: str
    payload: object


@dataclass
class GenerationOutcome:
    output: ModelOutput
    model: str
    finish_reason: str
    response_id: str | None
    surface: str
```

- `generate(...) -> GenerationOutcome`：迁移 `api_service_gemini.py:367-483`。
- `stream_generation(...) -> AsyncIterator[StreamEvent]`：迁移 `api_service_gemini.py:703-886`。
- 事件 kind：`body/thinking/tool_calls/images/reasoning_images/usage/finish_reason/response_id/thought_signature/done`。
- 统一处理：ATG wrap/unwrap、empty retries、`ensure_active_account`、`try_switch_account`、surface 解析、stats。

### 14.2 `upstream_errors.py`

从 `api_service_gemini.py` 迁出：

- `clean_upstream_error_message`
- `classify_gemini_error_payload`
- `handle_attempt_exception`
- `to_gemini_finish_reason` + WIRE map

`api_service_gemini.py` 保留 re-export，避免现有 import 断裂。

### 14.3 Gemini 回归

- `handle_gemini_generate_content` 保持公开签名与返回类型。
- Gemini SSE 渲染仍由 `format_sse_event` / `format_sse_usage` / `format_sse_error` 完成。
- 抽取阶段只允许行为等价重构；任何输出差异视为失败。

---

## 15. 测试计划（行为契约，不镜像实现）

### 15.1 `tests/unit/test_format_openai.py`

- system + developer 合并。
- user/assistant 文本与角色交替。
- 连续工具结果合并。
- assistant tool_calls → functionCall；tool 消息按 id 解析名称。
- 找不到函数名 → 400。
- data URL 图片；http(s) 图片走 `MediaFetcher` mock。
- 未知 part / `input_audio` / `file` → 400。
- `n>1` → 400。
- tools / 旧 functions → `parametersJsonSchema`。
- `tool_choice`：auto / none / required / 指定函数。
- `response_format`：json_schema / json_object / text。
- 非流式响应字段、finish_reason、usage、工具调用。
- 流式顺序：role → content/thinking → tool_calls(index) → finish+usage → `[DONE]`。

### 15.2 `tests/unit/test_format_claude.py`

- system 字符串 / 数组。
- text / image(base64) / image(url mock) / tool_use / tool_result / thinking。
- tool_result 名称解析、`is_error`、图片进 `functionResponse.parts`。
- `max_tokens` 缺失 → 400。
- 未知 block / 内置工具 → 400。
- `tool_choice`：auto / any / tool / none。
- thinking budget → level。
- 非流式 content 顺序、stop_reason、usage。
- 流式顺序：ping → message_start → thinking(signature) → text → tool_use → message_delta → message_stop。

### 15.3 `tests/unit/test_format_media.py`

- data URL 不联网。
- public URL 成功转 base64。
- 重定向 3 次内成功；超过 → 400。
- 非 image Content-Type → 400。
- 超过 20 MiB → 400。
- 超时 → 400。
- loopback / link-local / 元数据地址 → 400。
- 私网默认拒绝；`allow_private_urls=true` 时允许。

### 15.4 `tests/unit/test_format_routes.py`

- format 关闭 → 403 且形状正确。
- 鉴权失败 → 对应 401 形状。
- 转换错误 → 对应 400 形状。
- `/v1/models` OpenAI 形状；未知模型 404 形状。
- 流式首事件前异常 → 正确 HTTP 状态。
- 流式首事件后异常 → SSE 错误事件。
- 不依赖真实上游：monkeypatch `generation_service`。

### 15.5 `tests/unit/test_generation_service.py`

- 非流式：正常 / 重试 / 切号 / ATG wrap+unwrap。
- 流式：事件顺序、ATG 解包、空响应重试、required 失败。
- Gemini 回归：固定 fake client 下 SSE 序列与重构前一致。

### 15.6 修改现有测试

- `tests/unit/test_routes_models.py:45`：删除 `/v1/models` 404 断言。
- `tests/unit/test_features_config.py`：新增键默认值与非法值回退。
- `tests/unit/test_api_responses.py`：仅在错误分类迁出后补 re-export 兼容断言。

---

## 16. 真机最小验证

前置：`features.formats.openai/claude` 默认开启；若被运维关闭需先打开。代理 `http://127.0.0.1:10809` 生效。

1. OpenAI 非流式最小 prompt：校验 `object/id/choices/usage`。
2. OpenAI 流式 `curl -N`：校验 chunk、finish、usage、`[DONE]`。
3. OpenAI 工具调用 + 多轮 tool 结果：校验函数名/参数往返。
4. OpenAI 远程图片 URL：校验下载并识别图片内容。
5. Claude 非流式：校验 `content/stop_reason/usage`。
6. Claude 流式：校验 `ping → message_start → ... → message_stop`。
7. Claude 工具调用 + 多轮 `tool_result`。
8. Claude 远程图片 URL。
9. ATG：
   - `prefer`：两协议面各命中一次合成通道。
   - `required`：无合成调用时显式失败。
10. Gemini 回归：`/v1beta` 非流式 + 流式与基线一致。

证据归档：`docs/dev/evidence/wp8-20261019/README.md` + curl 原始输出 + 日志片段 + commit hash。不得包含 API Key / Cookie。

---

## 17. 明确偏离与限制

1. **不交付 Responses API**：PLAN 标注可选，首期不做。
2. **不交付 count_tokens**：无上游能力，不返回假的估算值。
3. **`include_thoughts=false` 不能关闭上游思考**：`wire_codec.py:287` 恒启用 thinking；只能控制 level。
4. **OpenAI 流式始终带 usage**：对齐 Ark，不严格遵循 `stream_options.include_usage`。
5. **Claude `disable_parallel_tool_use` 不强制生效**：只记录日志。
6. **Claude 内置工具 / document block 首期拒绝**：显式 400，不静默转文本。
7. **工具参数不分片**：上游 functionCall 为完整对象，输出完整 JSON 字符串。

---

## 18. 待用户确认

1. 远程图片下载失败时：
   - A（建议）：显式 400，请求失败。
   - B：按 Ark 写 `[System Note]` 文本并继续。
2. 私网图片默认拒绝、用 `features.formats.media.allow_private_urls` 放开：
   - A（建议）：默认拒绝。
   - B：默认允许（本机/局域网更宽松，但 SSRF 风险更高）。
3. format 关闭时状态码：
   - A（建议）：403 `permission_error`，明确提示配置键。
   - B：404，伪装端点不存在。
4. 是否同意首期不交付 Responses API 与 count_tokens。

---

## 19. 实施顺序与质量门

| 阶段 | 内容 | 完成标准 |
|---|---|---|
| A | `upstream_errors.py` + `generation_service.py` 抽取；Gemini 改为调用 | 现有单测全绿，Gemini SSE 行为不变 |
| B | OpenAI converter + routes + `/v1/models` + 错误分流 | OpenAI 单测 + 本地 curl 通过 |
| C | Claude converter + routes | Claude 单测 + 本地 curl 通过 |
| D | `features.formats`、ATG per-format、stats `api_format` | 配置/单测通过 |
| E | 质量门 + 真机 + 证据 | 四道质量门全绿，证据齐全，等待提交批准 |

质量门：

```powershell
uv run ruff check .
uv run ruff format --check .
bun x pyright src tests
uv run pytest tests/unit -q
```

每个阶段独立小提交；提交前申请用户批准；不自行 push。

---

## 20. 审计方式

- 每个实现提交的 message 引用本计划章节（例如 `feat(formats): WP8-B OpenAI chat completions (§5.1/§6.1/§7.1)`）。
- 变更行数用 `git diff --numstat` 或 `git show --stat` 统计并汇报。
- 测试输出原样归档到证据目录。
- 计划变更必须先改本文件，再改代码。