# WP6 独立统计页设计稿（2026-10-20）

> 状态：设计稿已落盘，作为 `/stats` 实现依据。
> 基线：`a10953d`（含内部 Paid 模型标记）。
> 范围：WP6 独立统计页；Paid 模型标记仅用于内部逻辑，不进入任何模型拉取 API。

## 1. 目标与边界

统计页回答四个问题：

1. 用了多少 token，输入/输出/推理/缓存各多少；
2. 哪个模型、哪个时间段在消耗；
3. 缓存命中是否正常；
4. count_tokens 预检查询发生了多少次、用了多少输入 token；
5. 账号当前是否健康（只做状态辅助，不做账号级 token 归因）。

不做：

- 请求级日志/请求记录（未来中转站模式再做）；
- API key 鉴权、租户、计费管理；
- 账号级 token 统计；
- 本地 tokenizer 估算；
- 深色主题、WP7 全局 UI 统一；
- 修改 WP11 Responses/计数链路、WP5 v3 实例池、WP10 保活（除非后续单独授权）。

## 2. 路由与导航

- 页面：`/stats`
- API：`/api/stats`、`/api/stats/timeseries`
- Sidebar 新增「用量统计」，位于「控制面板」与「账号管理」之间。
- 不做 Accept 内容协商。
- 旧的 `GET /stats` JSON 端点退役；Web 客户端改为 `/api/stats`。
- `app.py` 的 `is_api` 移除 `/stats`，Vite 移除 `/stats` 代理，`/api` 代理继续使用。
- 必须验证直接打开和刷新 `/stats` 能返回 SPA 页面。

## 3. 页面信息架构

自上而下：

1. 页面头：标题、时间范围、最后更新时间、刷新。
2. 客户端请求概览：客户端请求/成功/失败/中断 + 上游尝试/429/错误。
3. Token 结构：总 tokens、输入、输出（含推理）、推理、缓存读、缓存写、命中率。
4. 趋势：输入/输出/推理/缓存读，可切换显示预检计数。
5. 模型明细：按模型聚合 token；展开显示 api_format / surface。
6. 账号调度概览：当前状态、档位、chat/build 挂载、429/错误/冷却、模型尝试 chips。
7. 预检查询：count_requests / count_success / count_errors / count_input_tokens。
8. 数据起点/缺字段提示（条件显示）。

### 3.1 客户端层与上游层

一次客户端请求可能触发多次上游尝试：

```text
客户端 1 次请求
  attempt 1：账号 A → 429
  attempt 2：账号 B → 200（真实 usage）
```

展示口径：

```text
客户端请求 1,234（成功 1,198 / 失败 30 / 中断 6）
上游尝试 1,310（429 48 / 错误 28）
```

字段：

```text
client_requests, client_success, client_errors,
client_rate_limited, client_aborted
attempts, attempt_success, attempt_rate_limited, attempt_errors
```

规则：

- 换号后客户端成功：`client_success +1`，`attempt_rate_limited +1`，`client_errors` 不增加。
- 全部失败：`client_errors +1`，同时记录上游尝试失败。
- `client_requests = client_success + client_errors + client_aborted`（允许极少量未决）。
- `attempts = attempt_success + attempt_rate_limited + attempt_errors`。
- 流式请求已返回部分内容后中断：记 `client_aborted`，不记 `client_success`；无法捕获客户端断开时标未知，不硬猜。
- 不做一个混合口径的“成功率”。若展示，必须分别写“客户端成功率”“上游尝试成功率”。

### 3.2 Token 口径

- `prompt_tokens`：上游总输入，含缓存读。
- `completion_tokens`：上游总输出，含推理。
- `reasoning_tokens`：输出子集，不能与输出相加。
- `cache_read_tokens`：输入子集，不能加到总量。
- `cache_write_tokens`：当前上游不提供，返回 `null`，UI 显示 `—`。
- `total_tokens`：以上游 `total_tokens` 为准，不等于各卡片相加。
- `cache_hit_rate = cache_read_tokens / prompt_tokens`；`prompt_tokens = 0` 或无数据时为 `null`。
- 所有 token 必须来自上游 usage/真实计数，禁止本地估算。

## 4. 三档线框

### 4.1 桌面 1440

```text
┌ Sidebar ┬ 用量统计                         [今日] [7天] [30天] [自定义]  ⟳ 16:42:10 ┐
│         │ 统计范围 10/14–10/20（7天） · 数据自 10/20 起 · 口径 ⓘ                       │
│         │ ┌ 客户端请求 ─────────────────────┐ ┌ Token 概览 ──────────────────────────┐
│         │ │ 1,234（成功 1,198 / 失败 30 / 中断 6）│ │ 总 Tokens 1,234,567   命中率 18.5%   │
│         │ │ 上游尝试 1,310（429 48 / 错误 28）│ │ 输入 900,000  输出 334,567（推理 34,567）│
│         │ └──────────────────────────────────┘ │ 缓存读 166,500  缓存写 —             │
│         │                                      └──────────────────────────────────────┘
│         │ ┌ 趋势  [输入][输出][推理][缓存读] [显示预检计数] ───────────────────────┐
│         │ │                         SVG 折线/面积图                                  │
│         │ └──────────────────────────────────────────────────────────────────────────┘
│         │ ┌ 模型明细 ───────────────────────────────────────────────────────────────┐
│         │ │ 模型 | 客户端请求/成功 | 输入 | 输出(推理) | 缓存读写 | 总 | 命中率 | 最后 | > │
│         │ │ 展开：API 格式 / Surface                                                  │
│         │ └──────────────────────────────────────────────────────────────────────────┘
│         │ ┌ 账号调度概览（辅助检查） [只看异常] [搜索账号] ⓘ 当前状态，非所选范围 ─┐
│         │ │ 账号/档位 | 状态 | chat | build | 429/错误 | 冷却 | 模型尝试 | 最后      │
│         │ └──────────────────────────────────────────────────────────────────────────┘
│         │ ┌ 预检查询（count_tokens） ───────────────────────────────────────────────┐
│         │ │ 计数请求 128（成功 126 / 失败 2） 计数输入 45,678                       │
│         │ │ 格式 gemini 80 · openai 30 · claude 12 · responses 6                    │
│         │ │ 预检查询不计入生成 token 总量；不是生成请求记录。                       │
│         │ └──────────────────────────────────────────────────────────────────────────┘
```

### 4.2 平板 1024

- Sidebar 保留；内容区约 768px。
- 客户端请求与 Token 概览上下排列。
- 趋势全宽。
- 模型表横向滚动，最小宽度约 980px，首列模型名粘性。
- 账号表横向滚动或压缩为两行；禁止竖排文字。

### 4.3 手机 390

- 单列。
- 时间范围横向滚动。
- 客户端请求、Token 概览按 2 列小网格排列；总 tokens 单独大数字。
- 趋势全宽，必要时图表最小宽度 520px 横向滚动。
- 模型表改为卡片列表：模型名 + 总 tokens + 输入/输出/缓存/命中率小网格；点击展开格式/面。
- 账号表改为卡片：账号/档位 + 状态 + chat/build + 429/错误 + 模型 chips。
- 禁止内容溢出。

## 5. 视觉规格

复用现有设计 token：

- 页面 `max-w-7xl`，`p-4 sm:p-6 lg:p-8`，区块间距 `space-y-6`。
- 卡片：`bg-white border border-gray-200/80 rounded-2xl shadow-xs`。
- 卡片头：`px-6 py-4 border-b border-gray-100`；标题 `text-sm font-semibold text-gray-900`。
- 表头：`bg-gray-50/70 text-xs font-semibold text-gray-500 uppercase tracking-wider`。
- 行：`py-3.5`，`divide-y divide-gray-100`。
- 数字：`font-mono tabular-nums`，右对齐，千分位；K/M/B 时 tooltip 给精确值。
- 指标值 24–30px；标签 12px；表格 12–14px；辅助文案 12px。
- 颜色：
  - 输入 brand `#10a37f`
  - 输出 blue `#3b82f6`
  - 推理 amber `#f59e0b`
  - 缓存读 violet `#8b5cf6`
  - 缓存写 gray
  - 预检查询 gray + 虚线边框
  - 429/错误 rose
  - Paid/Pro-only badge：amber 或 violet，和 429/错误区分
- `0` 是真实数值；`null` 显示 `—`。
- 趋势图使用轻量 SVG 折线/面积图，不新增图表依赖；无数据不画假曲线。
- 深色模式本期不做。

## 6. 数据映射

| UI 数字 | API 字段 | 上游来源 |
|---|---|---|
| 总 Tokens | `total_tokens` | Gemini `totalTokenCount`；OpenAI `total_tokens`；Claude 统一后总量 |
| 输入 | `prompt_tokens` | Gemini `promptTokenCount`；OpenAI `prompt_tokens`；Claude `input_tokens` |
| 输出（含推理） | `completion_tokens` | Gemini `candidatesTokenCount + thoughtsTokenCount`；OpenAI `completion_tokens` |
| 推理 | `reasoning_tokens` | Gemini `thoughtsTokenCount`；OpenAI `completion_tokens_details.reasoning_tokens` |
| 缓存读 | `cache_read_tokens` | Gemini `cachedContentTokenCount`；OpenAI `cached_tokens`；Claude `cache_read_input_tokens` |
| 缓存写 | `cache_write_tokens` | Claude `cache_creation_input_tokens`；当前 Gemini 上游无 → `null` |
| 命中率 | `cache_hit_rate` | `cache_read_tokens / prompt_tokens` |
| 客户端请求 | `client_requests` | API 入口记一次 |
| 客户端成功 | `client_success` | 最终完整响应返回客户端 |
| 客户端失败 | `client_errors` | 最终向客户端返回错误 |
| 客户端中断 | `client_aborted` | 流式/客户端断开，可捕获时 |
| 上游尝试 | `attempts` | 每次真实上游调用，含重试/换号 |
| 上游 429/错误 | `attempt_rate_limited/attempt_errors` | 上游尝试结果 |
| 计数请求 | `count_requests` | count_tokens API 调用 |
| 计数输入 | `count_input_tokens` | 上游 CountTokens 真实结果 |
| 计数失败 | `count_errors` | 计数调用最终失败 |
| 格式 | `formats` | `record(api_format=...)` |
| Surface | `surfaces` | `ctx.surface`（chat/build） |
| Paid/Pro-only | `paid`（内部） | model discovery 的 `access_tier == 2`，仅内部使用 |
| 最后调用 | `last_used` | UTC+8 |

## 7. Paid 模型标记（内部）

- `model_discovery` 已解析 ListModels 的 `item[77]`（protobuf field 78）：`2 = Paid`。
- 模型字典新增 `paid: bool`、`access_tier: int | None`。
- **不在** `/v1beta/models`、`/v1/models` 等 API 暴露 `paid`/`access_tier`。
- 用途：
  - 内部路由：Paid 模型应走 chat/pg 的 pro 模型实例；
  - 统计页展示 Paid/Pro-only badge；
  - 账号异常：free 账号出现 Paid 模型调用时标红。
- 不使用“名称含 `-pro`”作为 Paid 判断。已知 `gemini-3.1-pro-preview` 不是 Paid，`gemini-3-flash-preview` 是 Paid。
- 当前 paid 路由接入是后续内部逻辑项；统计页只消费该字段，不改 WP5 v3 实例池。

## 8. Stats 数据模型与持久化

沿用 `data/stats.json`，升级 schema v2：

```text
{
  "schema_version": 2,
  "models": { "<normalized-model>": StatsItem },
  "daily": { "YYYY-MM-DD": { "<normalized-model>": StatsItem } }
}
```

`StatsItem`：

```text
requests, success, rate_limited, errors
client_requests, client_success, client_errors,
client_rate_limited, client_aborted
prompt_tokens, completion_tokens, reasoning_tokens,
cache_read_tokens, cache_write_tokens, total_tokens
count_requests, count_success, count_errors, count_input_tokens
formats: { gemini: StatsBucket, openai: ..., claude: ..., responses: ... }
surfaces: { chat: StatsBucket, build: StatsBucket }
last_used
```

`StatsBucket` 包含除 `formats/surfaces` 外的同名标量字段。

迁移：

- 旧 v1 平铺文件自动识别。
- `models/xxx` 与 `xxx` 合并求和。
- 旧记录缺 reasoning/cache/count 字段 → `null`。
- 旧 `formats` 整数请求数转 bucket；token 明细 `null`。
- 旧 count 请求已混入 `requests`，无法回溯拆分；新数据严格分离。
- 每日聚合从 v2 上线日起；更早历史只有累计值。
- 每日保留 90 天；超出范围返回 `partial` + `effective_start`。

## 9. API 契约

### 9.1 `GET /api/stats`

查询：

```text
range=today|7d|30d|custom|all   默认 all（兼容旧调用）
start=YYYY-MM-DD                 custom 必填
end=YYYY-MM-DD                   custom 必填
```

响应：

```text
schema_version
generated_at
range: { key, start, end, timezone, data_start, retention_days, partial }
totals: { ...StatsItem... }
models: { normalizedName: { ...StatsItem..., last_used } }
```

`null` 语义：

- `reasoning_tokens`、`cache_read_tokens`、`cache_write_tokens`、`count_*`：`int | null`。
- 旧记录/上游无字段 → `null`，不是 0。
- 总量聚合只加有数据的模型；全部无数据 → `null`。
- 部分缺字段时 `range.partial=true`，UI 显示提示。

旧字段 `requests/success/rate_limited/errors/prompt_tokens/completion_tokens/total_tokens/last_used` 保留。

### 9.2 `GET /api/stats/timeseries`

```text
range=today|7d|30d|custom|all
start=YYYY-MM-DD
end=YYYY-MM-DD
bucket=day
```

返回：

```text
generated_at
range: {...}
bucket: "day"
points: [{
  date,
  totals: { prompt_tokens, completion_tokens, reasoning_tokens,
            cache_read_tokens, cache_write_tokens, total_tokens },
  count_requests, count_input_tokens, count_errors
}]
```

缺失日期返回 `null` 点，前端断线/留空，不补 0。

### 9.3 路由迁移

- 页面 `/stats`，API `/api/stats`。
- 旧 `GET /stats` JSON 退役；Web 客户端全部改 `/api/stats`。
- `app.py` 的 `is_api` 移除 `/stats`。
- `vite.config.ts` 移除 `/stats` 代理。
- 直接访问/刷新 `/stats` 必须返回页面。

## 10. 状态与交互

- 加载：骨架屏覆盖概览、趋势、表格；切范围时保留旧数据 + 轻量加载遮罩。
- 空：
  - 无记录：“该时间范围没有生成记录”，提供「查看 7 天」。
  - 有记录但 token 0：显示 0，并标“无 usage 数据”。
- 错误：卡片内错误面板 + 重试；轮询失败保留上次数据并标“数据可能过期”。
- 部分缺字段：单元格 `—` + tooltip；顶部提示“部分历史记录缺少推理/缓存字段”。
- 401：沿用现有登录跳转；403/5xx：错误面板。
- 轮询：10 秒，页面不可见时暂停；手动刷新更新时间；范围切换立即请求，防止旧响应覆盖。
- 账号面板：当前调度状态，不随时间范围变化；计数来自调度器，标“尝试级/当前状态”。
- 预检查询：明确“不计入生成 token；不是请求记录”。

## 11. 账号调度概览

数据源：

- `/accounts`：email、tier、expired。
- `/rotation`：尝试/成功/429/错误、`model_requests`、冷却、auth_cooldown、session_expired、is_available。
- `/routing/state`：`instances.contexts[]` 的 `account_id`、`chat_ready`、`build_ready`、`state`、`in_flight`。

列：

| 列 | 说明 |
|---|---|
| 账号/档位 | 邮箱截断 + tooltip；pro/free/ultra/plus/unknown |
| 状态 | active / available / cooldown / auth cooldown / session expired / credential expired |
| chat/build | 已挂载页数；`—` 未挂载或未知 |
| 429/错误 | 调度器累计，标“当前状态” |
| 冷却 | 全局/模型冷却摘要 |
| 模型尝试 | chip，带 surface 前缀；Paid 模型加 Paid badge |
| 最后调用 | last_used |

异常规则：

- 过期/登录失效 → 红色“需重导 Cookie”。
- auth_cooldown/全局冷却 → 琥珀。
- Paid 模型出现在非 pro 账号 → 红色异常。
- pro 账号出现 build 尝试 → 琥珀提示（非必然错误）。
- 不显示账号级 token；不把调度器累计数当所选时间范围。

## 12. 截图验证计划

- 工具：Playwright MCP 或 agent-browser。
- 视口：1440×900、1024×768、390×844。
- 数据 fixture：临时 `AISTUDIO_STATS_FILE`，覆盖：
  - 正常多模型、多格式、chat/build；
  - 旧记录缺 cache/reasoning；
  - 空范围；
  - count 成功/失败；
  - Paid/Free 模型与 pro/free 账号。
- 状态截图：正常、加载、空、错误、部分缺字段。
- 深链：直接打开并刷新 `/stats`。
- 记录 console error、网络失败。
- 证据目录：`docs/dev/evidence/wp6-20261020/`。
- 对照设计稿逐项检查；只读 Vue 代码不算完成。

## 13. 实现顺序

1. Stats schema v2 + 迁移 + 模型名归一。
2. `record()` 扩展 usage/cache/reasoning/surface；`record_token_count()` 扩展 count 输入/失败/surface；count 失败不写生成错误。
3. 客户端/上游两层计数与 `usage_missing`。
4. `/api/stats` + `/api/stats/timeseries` + 路由迁移。
5. UI：fixture 静态数据 → 真实 API。
6. 三档截图、状态截图、深链验证。
7. 回归 Dashboard/账号/配置；质量门；证据入库。

## 14. 质量门

```text
uv run pytest tests/unit -q
uv run ruff check .
uv run ruff format --check .
bun x pyright src tests
cd web
bun run type-check
bun run lint
bun run build
```

## 15. 验收

- 一次生成请求后，输入/输出/推理/缓存字段与上游 usage 一致。
- 一次 count_tokens 后，`count_requests` 增加，生成 token 总量不变。
- 换号后成功：客户端成功 +1，上游 429 +1，客户端失败不增加。
- 模型名不再出现 `models/xxx` 与 `xxx` 两行。
- Paid 模型在统计页有 Paid/Pro-only 标记；free 账号使用 Paid 模型标红。
- 三档真实浏览器截图与设计稿一致。
- 空/加载/错误/缺字段状态有证据。
- 未引入请求记录、本地 tokenizer、账号级 token、API key 鉴权。

## 16. 非目标

- 请求记录、状态码、IP、耗时、流式模式明细；
- API key/租户/中转站管理页；
- 账号级 token 归因；
- 深色主题与 WP7 全局 UI；
- 修改 WP11/WP5 v3/WP10 行为；
- 在模型拉取 API 暴露 `paid` 字段。
