# 下一阶段转交提示词：WP11 真实 Token 计数（P4.6）

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

## 本阶段目标

实现 WP11：**真实上游 token 计数**。

范围：

- Claude `POST /v1/messages/count_tokens`
- Gemini 原生 `:countTokens`
- OpenAI Responses `POST /v1/responses/input_tokens`（若 Responses 尚未交付，只保留路由占位和显式 404）

硬约束：

- 必须使用上游真实 `totalTokens`，禁止本地字符数估算冒充精确值。
- 计数请求不写模型调用的 token 用量统计，只记录请求次数和格式维度。
- 计数失败显式返回 4xx/5xx，不静默 fallback。
- 纯后端；不改 `web/`。
- 不新增第二套上游传输；复用现有账号/transport/实例池链路。
- 不破坏 WP8 生成链路和 WP10 保活。

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
   - Claude/OpenAI 请求先转入 `GeminiGenerateContentRequest`。
   - 计数只发送 `:countTokens` 所需的 model + contents，不携带生成配置、工具调用降级或 ATG 合成指令。
2. 上游传输：
   - 复用现有 `AIStudioClient`/`instance_pool` lease。
   - 计数请求不应改变生成模板、捕获快照或 ATG 包装。
3. 响应映射：
   - Claude：`{"input_tokens": <totalTokens>}`
   - Gemini：保持原生 `totalTokens` 语义
   - Responses：`{"input_tokens": <totalTokens>}`；未实现时显式 404
4. 统计：
   - 只增加计数请求次数与格式维度。
   - 不写 token usage；不要污染 WP6 的生成统计。
5. 错误：
   - 上游 429 走现有换号链。
   - 所有候选失败时返回明确 `surface_*` / 上游错误。
   - 不允许返回估算值或 0。

## 验收

单元测试：

- Claude 文本 / 工具 / 图片 / thinking 请求归一化正确。
- 计数结果与上游 `totalTokens` 一致。
- 上游失败显式报错。
- 计数不写 token usage 统计。
- 生成链路无回归。

真机（最小额度）：

- Claude `count_tokens` 与上游一致。
- Gemini 原生 `:countTokens` 无回归。
- 429 换号时计数请求也能切换。
- 证据写入 `docs/dev/evidence/wp11-<date>/`。

任何实机测试连续失败三次，立即暂停并请清空上下文的独立 agent 审计，不得继续盲试。

## 质量门

```text
uv run pytest tests/unit -q
uv run ruff check .
uv run ruff format --check .
bun x pyright src tests
```

完成前不要 commit；提交需用户明确批准。禁止 push，除非用户明确要求。

## 禁止事项

- 不改 `web/` 或 UI。
- 不引入本地 tokenizer 估算。
- 不复制第二套 transport。
- 不把计数请求写进生成统计。
- 不破坏 WP5 v3 实例池与 WP10 保活。