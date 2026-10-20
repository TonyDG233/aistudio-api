# 下一阶段转交提示词：WP11.1 Responses 面 → WP11.2 真实 Token 计数（P4.6）

你是接续本仓库工作的新 agent。仓库：

```text
D:\LLM\self_programming\aistudio-api
分支：p1-ark-merge
```

## 当前基线

WP5 v3 后端收尾已完成并提交：

```text
b59d6f3 fix(wp5v3): 完成实例池并发与预算收尾
0ced898 docs: 同步 WP5 v3 架构与实例池说明
8ad9a63 fix(accounts): 兼容旧项目 auth JSON 导入
```

WP5 v3 已完成：

- 统一 Context 实例池、chat/build surface、页面预算
- 请求期派发前换号/装载
- 429 换号、候选容量回退、同页并发
- 4 账号轮转、强制 429、2/4 并发真机验证
- 旧项目 auth JSON 直接导入兼容

根 `config.yaml` 已恢复，临时账号已删除，服务已停止。

## 核心目的（先对齐，再动手）

反代接入主流 coding agent（Kilo Code / Cline / Roo / Claude Code / Codex CLI / LiteLLM / Gemini SDK）后，必须满足两类真实用量反馈：

1. **生成响应 usage 全量透出**：agent 页面（如 Kilo Code 的 `259.0K / 1.0M` 上下文条）主要从响应 usage 取数，必须包含输入、输出、推理、缓存读/写；不能为零、不能丢明细。
2. **preflight 真实计数**：Claude Code / LiteLLM / Gemini SDK 这类显式调用 `count_tokens` / `input_tokens` 的客户端，必须拿到上游真实计数，且计数必须包含 system/tools/images/thinking，不能只数 contents。

## 当前工作区状态（重要）

工作区已有一版未提交的 WP11 计数实现（`application/token_counter.py`、`routes_*`、`client.count_tokens`、相关测试）。**不要丢弃**：它应并入提交二，但必须先修正“只发 model+contents、漏算 system/tools”的问题；提交一（Responses 面）不得夹带这些计数改动。

## 本阶段两个独立提交（顺序固定）

### 提交一：WP11.1 OpenAI Responses 面 + 全格式 usage 明细
- 新增 `POST /v1/responses`（非流式 + 真流式），请求归一复用现有 Gemini 链路；
- 响应 `usage` 必须含 `input_tokens / input_tokens_details.cached_tokens / output_tokens / output_tokens_details.reasoning_tokens / total_tokens`；
- 同时校准 Chat Completions / Claude / Gemini 的 cache/reasoning 映射，确保 agent 从响应 usage 取数时显示真实值；
- 本提交不实现 preflight count；`/v1/responses/input_tokens` 暂保持显式 404。

### 提交二：WP11.2 真实上游 token 计数
- Claude `POST /v1/messages/count_tokens`：完整请求归一（system/tools/images/thinking）→ 上游真实计数 → `{"input_tokens": totalTokens}`；
- Gemini 原生 `:countTokens`：发送完整 `generateContentRequest`（含 systemInstruction/tools），不能只发 model+contents；
- OpenAI Responses `POST /v1/responses/input_tokens`：完整 input/tools/instructions；
- 复用现有账号/transport/实例池；不改变生成模板、不捕获快照、不应用 ATG；
- 计数统计单独记 `count_requests` / `count_input_tokens`，不写生成 token usage；
- 上游失败显式 4xx/5xx，禁止静默 fallback / 0 / 估算。

硬约束：

- 必须使用上游真实计数，禁止本地字符数/tokenizer 估算冒充精确值。
- 不新增第二套上游传输；复用现有账号/transport/实例池链路。
- 不破坏 WP8 生成链路、WP5 v3 实例池、WP10 保活。
- 纯后端；本阶段不改 `web/`。

## WP6 独立统计页设计门禁

WP6 的独立统计页目前没有既有设计稿。接任 agent 必须先按 `docs/dev/WP6-STATS-PAGE-DESIGN-BRIEF-20261020.md` 提交设计稿（信息架构、三档线框、视觉规格、数据映射、API 契约、截图验证计划），经用户确认后才能写 UI 代码。

WP6/WP7 所有 UI 验收必须用浏览器工具（Playwright/agent-browser）打开实际页面截图，对照设计稿检查桌面/平板/手机；**禁止只读 Vue 源码就宣称 UI 完成**。

## 必读文档

按顺序：

```text
AGENT.md
docs/dev/PLAN-ark-merge-20261017.md §4 WP11
docs/dev/WP5-V3-DESIGN-20261019.md
docs/dev/UI-CHANNEL-DESIGN-20261018.md（只读，UI 不在本阶段）
docs/dev/evidence/wp5v3-20261019/README.md
```

## 现有代码入口

- `src/aistudio_api/api/`：Claude/OpenAI/Gemini 路由
- `src/aistudio_api/application/generation_service.py`：请求归一、lease 生命周期
- `src/aistudio_api/application/routing_pipeline.py`：候选选择与实例切换
- `src/aistudio_api/infrastructure/gateway/instance_pool.py`：统一 Context 池
- `src/aistudio_api/infrastructure/gateway/client.py`：浏览器传输与 lease
- `src/aistudio_api/infrastructure/gateway/wire_codec.py`：Gemini wire 编解码
- `src/aistudio_api/application/formats/`：OpenAI/Claude 格式转换

建议新增：

```text
src/aistudio_api/application/token_counter.py   # 唯一计数入口
src/aistudio_api/api/routes_formats.py           # 对应端点（若已有路由文件则接入）
tests/unit/test_token_counter.py
tests/unit/test_token_count_routes.py
```

## 实现要求

1. 请求归一化：
   - Claude/OpenAI Responses 请求先转入 `GeminiGenerateContentRequest`。
   - 计数必须携带完整 prompt：contents、systemInstruction、tools、图片、thinking 历史；不得只发 model + contents，也不携带 generationConfig/toolConfig/ATG 合成指令。
2. 上游传输：
   - 复用现有 `AIStudioClient`/`instance_pool` lease。
   - 计数请求不应改变生成模板、捕获快照或 ATG 包装。
   - 当前已验证可用的上游是 MakerSuite `CountTokens` RPC；如它不能携带 system/tools，必须换到能携带完整 `generateContentRequest` 的路径，不能继续漏算。
3. 响应映射：
   - Claude：`{"input_tokens": <totalTokens>}`
   - Gemini：保持原生 `totalTokens` 语义
   - Responses：`{"input_tokens": <totalTokens>}`
4. 统计：
   - 单独增加 `count_requests` / `count_input_tokens` 与格式维度。
   - 不写生成 token usage；不要污染 WP6 的生成统计。
5. 错误：
   - 上游 429 走现有换号链。
   - 所有候选失败时返回明确 `surface_*` / 上游错误。
   - 不允许返回估算值或 0。
6. Responses 生成面（WP11.1）：
   - `POST /v1/responses` 非流式 + 真流式，usage 必须含 input/output/reasoning/cache 明细；
   - 不实现 Responses 时不得用 Chat Completions 假响应冒充。

## 验收

### 提交一（WP11.1 Responses 面）
- OpenAI Responses 非流式/流式/工具调用与 usage 映射通过单测；
- usage 的 input/output/reasoning/cache 明细与上游一致；
- Chat Completions / Claude / Gemini usage 无回归；
- 证据：`docs/dev/evidence/wp11-responses-<date>/`。

### 提交二（WP11.2 计数）
单元测试：

- Claude 文本 / 工具 / 图片 / thinking 请求归一化正确，且 system/tools 计入。
- 计数结果与上游真实计数一致。
- 上游失败显式报错。
- 计数只写 count_requests/count_input_tokens，不写生成 token usage。
- 生成链路无回归。

真机（最小额度）：

- Claude `count_tokens`（含 system/tools/images/thinking）与上游一致。
- Gemini 原生 `:countTokens` 含 systemInstruction/tools，与生成 `promptTokenCount` 对照一致。
- Responses `input_tokens` 与上游一致。
- 429 换号时计数请求也能切换。
- 证据写入 `docs/dev/evidence/wp11-count-<date>/`。

任何实机测试连续失败三次，立即暂停并请清空上下文的独立 agent 审计，不得继续盲试。

## 质量门

```text
uv run pytest tests/unit -q
uv run ruff check .
uv run ruff format --check .
bun x pyright src tests
```

两个提交必须分开：先 Responses 面，后计数补齐；每个提交完成前跑四项质量门。提交需用户明确批准；禁止 push，除非用户明确要求。

## 禁止事项

- 不改 `web/` 或 UI。
- 不引入本地 tokenizer 估算。
- 不复制第二套 transport。
- 不把计数请求写进生成统计。
- 不破坏 WP5 v3 实例池与 WP10 保活。