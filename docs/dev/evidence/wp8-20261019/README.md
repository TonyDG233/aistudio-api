# WP8 OpenAI / Claude 格式层证据

- 日期：2026-10-19
- 分支：`p1-ark-merge`
- 计划：`docs/dev/WP8-FORMAT-LAYER-PLAN.md`（提交 `c2a9f06`）
- 实现状态：已完成，未提交（等待用户批准）
- 修改范围：后端与测试；未改 `web/`、静态产物、WP10 保活

## 1. 质量门

最终运行结果：

```text
uv run ruff check .
All checks passed!

uv run ruff format --check .
155 files already formatted

bun x pyright src tests
0 errors, 0 warnings, 0 informations

uv run pytest tests/unit -q
571 passed in 21.49s
```

## 2. 单元测试覆盖

新增/修改的测试文件：

- `tests/unit/test_format_openai.py`
- `tests/unit/test_format_claude.py`
- `tests/unit/test_format_media.py`（由 `test_format_openai.py` / `test_format_claude.py` 覆盖媒体路径，未单独建文件）
- `tests/unit/test_format_error_routing.py`
- `tests/unit/test_routes_models.py`（`/v1/models` 404 断言改为 OpenAI 403 形状）

覆盖点：

- OpenAI 请求映射：system/developer、连续角色合并、工具调用、工具结果、data URL/远程图片、response_format、tool_choice、`n>1` 拒绝、未知 part 拒绝。
- Claude 请求映射：system、text/image/tool_use/tool_result/thinking、工具结果图片、max_tokens 必填、内置工具拒绝、tool_choice、thinking budget。
- 非流式响应：字段、finish_reason、usage、工具调用、图片 markdown。
- 流式响应：OpenAI chunk 顺序与 `[DONE]`；Claude `ping → message_start → content_block_* → message_delta → message_stop`。
- thoughtSignature：非流式输出写入缓存，历史工具调用转换时回填。
- 错误形状：OpenAI/Claude/Gemini 的 401、403、400 分流。
- 媒体安全：私网默认拒绝、允许开关、非图片、超限、重定向、超时。

## 3. 真机环境

- 服务：`python main.py server --port 8099 --browser-port 9333`
- 代理：`HTTP_PROXY/HTTPS_PROXY=http://127.0.0.1:10809`
- 模型：`gemini-flash-latest`
- 账号：2 个已导入账号，活跃账号 `acc_46223636`
- `features.formats.openai` / `features.formats.claude` 默认开启（对齐 Ark `ProxyServerSystem.js:675-702` 的无条件端点注册）；开关保留作运维手段。
- ATG `required` / `prefer` 分别验证。
- 验证结束后 `atg.mode_by_format.*` 恢复为 `inherit`。

## 4. 真机结果

| 场景 | 结果 | 证据 |
|---|---|---|
| `GET /v1/models` | 200，OpenAI list/model 形状 | 运行日志 |
| OpenAI 非流式 | 200，返回 `OK-WP8` | 运行日志 |
| OpenAI 流式 | `role`、`reasoning_content`、`finish_reason`、usage、`[DONE]` | `openai-stream.txt` |
| OpenAI 单轮工具调用 | 200，`get_weather`，`finish_reason=tool_calls` | 运行日志 |
| OpenAI 多轮工具结果 | 200，返回天气结论 | 运行日志 |
| OpenAI 冷缓存工具历史 | 200，占位签名生效 | 运行日志 |
| OpenAI 重启后续接工具会话 | 200，持久化签名生效 | `openai-multiturn-request.json` |
| Claude 非流式 | 200，`content/text`、`stop_reason`、usage | 运行日志 |
| Claude 流式 | 完整 Anthropic 事件序列 | `claude-stream.txt` |
| Claude 工具多轮 | 200，`tool_use` → `tool_result` → 文本 | 运行日志 |
| Claude 冷缓存工具历史 | 200，占位签名生效 | 运行日志 |
| Claude 重启后续接工具会话 | 200，持久化签名生效 | `claude-multiturn-request.json` |
| Claude thinking 历史回放 | 200，无 400 | 运行日志 |
| OpenAI 远程图片 URL | 200，图片被识别（prompt_tokens=1078） | 运行日志 |
| Claude 远程图片 URL | 200，返回 `White` | 运行日志 |
| Gemini 非流式回归 | 200，`candidates/usageMetadata/responseId` | 运行日志 |
| Gemini 流式回归 | 200，SSE 文本 + 终态 usage | `gemini-stream.txt` |
| ATG `required` OpenAI | 200，返回 `4`，解包成功 | 运行日志 |
| ATG `required` Claude | 200，返回 `4`，解包成功 | 运行日志 |
| ATG `prefer` OpenAI/Claude | `syntheticHit=true` 日志 | 见下方摘录 |

### thoughtSignature 修复

修复前多轮工具调用：

```text
HTTP 400: [original: beyond::dependency::INVALID_ARGUMENT]
Function call is missing a thought signature.
```

原因：OpenAI/Claude 协议没有 `thoughtSignature` 字段，历史工具调用回放时丢失了上游要求的签名。

参考 Ark 的最终方案：

- `CommonConverter.js:18-19`：固定占位签名
  `DUMMY_THOUGHT_SIGNATURE = "context_engineering_is_the_way_to_go"`
- `ChatCompletionsConverter.js:200-210`：缺少签名的 `functionCall` 注入占位签名
- `ClaudeRequestConverter.js:277-289`：Claude `tool_use` 同样注入
- `CommonConverter.js:382-411`：只给 `functionCall` 补签名，`functionResponse` 不需要
- `ClaudeRequestConverter.js:292-296`：thinking block 转 `{text, thought:true}`，不携带外部签名

本仓实现：

- 真实签名优先：响应侧按 tool call id 缓存上游真实签名，持久化到
  `data/signature_cache.json`，30 天 TTL、4096 条、原子写入、非消费式读取。
- 占位签名兜底：缓存缺失时使用 Ark 同款占位签名，不报错、不降级为纯文本。
- Claude thinking：忽略客户端外部签名，按 `{text, thought:true}` 回放。
- 真机验证：冷缓存、代理重启、Claude thinking 历史三种场景均无 400。

### ATG 日志摘录

```text
[ATG] 已注入合成通道: tool=atg_emit_..., model=gemini-flash-latest, contents=2, tools=1
[ATG] 非流式解包: tool=atg_emit_..., stats={"syntheticHit": true, "syntheticCount": 1, "realToolCount": 0, "repaired": false, "conflict": false}
```

OpenAI 与 Claude 均出现 `syntheticHit: true`。

## 5. 明确限制

- 不交付 OpenAI Responses API。
- 不交付 Claude `count_tokens`；显式返回 404，不伪造估算。
- 远程图片只支持 http(s) 下载与 data URL；私网默认拒绝，需 `features.formats.media.allow_private_urls=true`。
- 上游始终启用 thinking；`thinking.disabled` 只能不设置 level，不能保证关闭上游思考。
- OpenAI 流式结束 chunk 始终携带 usage（对齐 Ark），不严格遵循 `stream_options.include_usage`。
- Claude 内置工具（web_search / code_execution 等）与 document block 首期显式 400。
- 工具参数为上游完整 JSON，不做伪造分片。

## 6. 变更统计

实现代码（未提交）：

- 新增协议/管线源码：2921 行
- 新增测试：1472 行
- 修改 tracked 文件：`+203 / -729`（主要是 `api_service_gemini.py` 抽取后净减少）
- 证据文件：138 行（README + 请求/响应样本）
- 合计触达行数：5463 行（新增 4531 + 删除 729，含测试与证据）
- 净变更：+4005 行

## 7. 复现命令

```text
uv run ruff check .
uv run ruff format --check .
bun x pyright src tests
uv run pytest tests/unit -q
```

真机启动：

```text
$env:HTTPS_PROXY='http://127.0.0.1:10809'
$env:HTTP_PROXY='http://127.0.0.1:10809'
uv run python main.py server --port 8099 --browser-port 9333
```

## 8. 安全

- 证据文件不包含 API Key、Cookie、账号密码。
- 签名缓存只在进程内保存，TTL 24h，最多 4096 条。
- 远程图片下载有 SSRF 防护、超时、大小与 Content-Type 限制。