# WP6 独立统计页设计任务书（2026-10-20）

> 状态：**设计任务书，不是设计稿**。接任 agent 必须先交付设计稿并经用户确认，才能写 UI 代码。
> 适用范围：WP6 独立统计页；WP7 的 UI 统一修改同样遵循本文的视觉验证要求。

## 0. 给接任 agent 的完整转交提示词

你是接续本仓库 WP6 的新 agent。仓库：`D:\LLM\self_programming\aistudio-api`，分支：`p1-ark-merge`，当前基线已推送到 `origin/p1-ark-merge`，HEAD 为 `493b72f`。

开工前按顺序读：

```text
AGENT.md
docs/dev/PLAN-ark-merge-20261017.md 的 WP6 / WP7 段
docs/dev/UI-CHANNEL-DESIGN-20261018.md 的 P1 / P4 段
本文（WP6-STATS-PAGE-DESIGN-BRIEF-20261020.md）
```

### 0.1 当前已完成，不要重做

- WP11 已完成并提交：
  - `2b39b53` OpenAI Responses 面 + usage 透出；
  - `af3f55c` Gemini/Claude/Responses 真实上游计数（打包聚合，误差 +2.8%~+8.6%，用户已接受）；
  - `493b72f` responses 的 ATG 分协议开关 + 根目录文档同步。
- P1 控制面板的设计讨论已落进 `UI-CHANNEL-DESIGN-20261018.md`：
  - 实例数卡灰色小字 `渠道 实际/计划`；
  - P1-O2 格式 tab：Gemini / OpenAI Chat / OpenAI Responses / Claude / Token 计数；
  - 地址用 `window.location.origin` 动态生成；
  - P4 已改为统计页 `/stats` 占位。
- P1 现状截图：`docs/dev/evidence/ui-p1-20261020/`。
- 现有统计底座：
  - `api/state.py` 的 `ModelStatsItem` 只有 `requests/success/rate_limited/errors/prompt_tokens/completion_tokens/total_tokens/formats/last_used`；
  - `RuntimeState.record()` 只累加三个 token 字段；
  - `RuntimeState.record_token_count()` 只记 `requests/formats`，不写生成 token；
  - `wire_parser.py` 能解析上游 usage 明细，但 `state.py` 没保存 cache/reasoning；
  - `web/src/views/DashboardView.vue` 只有账号/请求/429 四张卡；
  - `web/src/components/dashboard/ModelStatsTable.vue` 只有 `Tokens` 列（total）；
  - `web/src/stores/system.ts` 已取 `totalTokens/promptTokens/completionTokens`，但 Dashboard 未使用；
  - 真机 stats 里出现过 `models/gemini-3.8-flash` 与 `gemini-3.8-flash` 同名两行，模型名未归一。

### 0.2 你的任务

实现 WP6：**独立统计页 `/stats`**，让用户和 coding agent 能看到真实 token 消耗与上下文用量参考，而不是只看请求数。

核心指标：

- 生成用量：输入、输出、推理、缓存读、缓存写、总 tokens、缓存命中率；
- 维度：按模型、按 `api_format`（gemini/openai/claude/responses）、按 `surface`（chat/build）；
- 计数请求：`count_requests` / `count_input_tokens` 单独区块，**不得并入生成 token 总量**；
- 时间范围：今日 / 7 天 / 30 天 / 自定义；趋势图；
- 模型名统一去掉 `models/` 前缀，避免同名拆行；
- 空/加载/错误/部分字段缺失状态。

### 0.3 第一步：先交设计稿，禁止直接写 UI

在修改 `web/` 之前，必须先提交设计稿，包含：

1. 页面信息架构与导航位置（P4 `/stats`，从 Sidebar 进入）；
2. 桌面/平板/手机三档线框；
3. 视觉规格：颜色、字号、间距、卡片/表格/图表样式，复用现有设计 token；
4. 数据映射表：每个 UI 数字 → `/stats` 字段 → 上游 usage 字段；
5. 空/加载/错误/缺字段四种状态稿；
6. API 契约变更清单（`/stats`、可选 `/stats/timeseries`）；
7. 浏览器截图验证计划（Playwright 或 agent-browser，1440/1024/390 三档）；
8. 明确“不改哪些现有页面/接口”。

**设计稿未获用户确认前，不得修改 `web/`，也不得先写 UI 再补设计。**

### 0.4 实现顺序（设计确认后）

1. **后端统计扩维**
   - `ModelStatsItem` 增加 `reasoning_tokens`、`cache_read_tokens`、`cache_write_tokens`、`count_requests`、`count_input_tokens`、`surfaces`；
   - `RuntimeState.record()` 从上游 usage 提取并累加 cache/reasoning；
   - `/stats` 透出新字段，保持旧字段兼容；
   - 模型名归一；
   - 计数统计与生成统计分离；
   - 单测覆盖累加、归一、计数不污染生成 token。
2. **独立统计页 UI**
   - 新页面 `/stats`，不要塞进 P1；
   - 顶部 token 概览卡、趋势、模型明细表（可展开格式/面）、计数请求区块；
   - P1 只保留摘要或“查看统计”入口。
3. **视觉验证**
   - 用浏览器工具打开实际页面，1440/1024/390 三档截图；
   - 对照设计稿检查；记录控制台/网络错误；
   - 空/加载/错误状态也要截图。
4. **回归**
   - 现有 Dashboard、账号页、配置页不回归；
   - Web 类型检查/构建通过。

### 0.5 质量门

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

### 0.6 验收

- 一次生成请求后，统计页的输入/输出/推理/缓存字段与上游 usage 一致；
- 一次 `count_tokens` 后，`count_requests` 增加，生成 token 总量不变；
- 模型名不再出现 `models/xxx` 与 `xxx` 两行；
- 独立统计页三档截图与确认的设计稿一致；
- 空/加载/错误/缺字段状态有截图证据；
- 证据写入 `docs/dev/evidence/wp6-<date>/`。

### 0.7 禁止

- 设计稿未确认前改 `web/`；
- 只读 Vue 源码就宣称 UI 完成；
- 本地 tokenizer 估算冒充上游用量；
- 把 count 请求混入生成 token 统计；
- 改动 WP11 Responses/计数链路、WP5 v3 实例池、WP10 保活；
- 未获用户批准就 commit/push。

### 0.8 第一轮回复格式

先给设计稿（文字 + 线框/视觉说明 + 数据映射 + API 契约 + 截图计划），不要直接贴代码 diff。等用户确认后再进入后端与 UI 实现。

## 1. 核心目的

反代接入主流 coding agent（Kilo Code / Cline / Roo / Claude Code / Codex CLI / LiteLLM / Gemini SDK）后，用户需要能判断：

1. 当前到底用了多少 token；
2. 输入、输出、推理、缓存各占多少；
3. 哪个模型、哪个 API 格式、哪个 surface 在消耗；
4. 缓存命中是否正常；
5. count_tokens 这类预检查询发生了多少次、消耗了多少输入 token；
6. 随时间变化的趋势，而不是只有一个累计总数。

统计页不能用“字符数/4”或本地 tokenizer 估算；所有 token 数字必须来自上游 `usage` / 真实计数。

## 2. 页面定位

- 独立页面，建议路由 `/stats`（或 `/dashboard/stats`），从 Sidebar 可进入；不挤在现有 Dashboard 的四张卡片里。
- 现有 Dashboard 保留“账号/请求/429”概览；统计页负责 token 与成本分析。
- 页面数据来自后端 `/stats`（必要时新增 `/stats/timeseries`），不直接读浏览器本地状态。

## 3. 必备信息架构

### 3.1 顶部概览卡片
至少包含：

- 总 tokens（生成）
- 输入 tokens（prompt/input）
- 输出 tokens（completion/output，含 reasoning 的展示口径需注明）
- 推理 tokens（reasoning/thoughts）
- 缓存读取 tokens（cache read）
- 缓存写入 tokens（cache write）
- 缓存命中率（派生）
- 计数请求次数 / 计数输入 tokens（单独分组，不与生成混算）

每张卡片必须有：数值、单位、时间范围、数据口径 tooltip；数值为 0 与“无数据”要区分。

### 3.2 趋势区
- 时间范围：今日 / 7 天 / 30 天 / 自定义。
- 至少一条 token 趋势（输入/输出/推理/缓存可叠加或分线）。
- 计数请求趋势可单独开关，避免与生成曲线混淆。
- 无历史数据时显示空状态，不画假曲线。

### 3.3 模型明细表
列建议：

- 模型（归一化，去掉 `models/` 前缀）
- 请求数 / 成功 / 429 / 错误
- 输入 / 输出 / 推理 / 缓存读 / 缓存写 / 总 tokens
- 缓存命中率
- 最后调用
- 可展开行：按 `api_format`（gemini/openai/claude/responses）和 `surface`（chat/build）拆分
- count 请求单独列或单独区块

### 3.4 计数请求区块
- `count_requests`、`count_input_tokens`、按格式拆分；
- 明确文案：“预检查询，不计入生成 token 总量”；
- 若某次 count 失败，展示失败次数/错误维度（不展示估算值）。

### 3.5 账号/配额辅助区（可选）
- 当前激活账号、可用账号、tier、surface cooldown；
- 与 token 统计同页但视觉分组，避免混淆。

## 4. 数据与 API 契约

### 4.1 后端必须补齐的统计字段

`ModelStatsItem` 建议扩展：

```text
requests, success, rate_limited, errors
prompt_tokens, completion_tokens, reasoning_tokens
cache_read_tokens, cache_write_tokens, total_tokens
count_requests, count_input_tokens
formats: { gemini, openai, claude, responses }
surfaces: { chat, build }
last_used
```

`totals` 提供同名字段聚合；派生 `cache_hit_rate = cache_read / (prompt + cache_read)` 的口径必须在 API 文档和 UI tooltip 中写清楚。

### 4.2 数据来源

- 生成 usage：`wire_parser` 解析上游 usage → `RuntimeState.record()`；
- 必须保留：OpenAI `prompt_tokens_details.cached_tokens` / `completion_tokens_details.reasoning_tokens`；Claude `cache_read_input_tokens` / `cache_creation_input_tokens`；Gemini `cachedContentTokenCount` / `thoughtsTokenCount`；
- 计数：`RuntimeState.record_token_count()` 只写 `count_requests` / `count_input_tokens` / format，不写生成 token；
- 模型名统一归一，避免 `models/xxx` 与 `xxx` 两行。

### 4.3 API 契约

- `GET /stats`：返回 `models`、`totals`、`ranges`（如支持）、`generated_at`；
- 如需趋势：`GET /stats/timeseries?range=7d&bucket=day`；
- 字段缺失时返回 0 或 null 必须明确，前端不得把 null 当 0 画图；
- 保持向后兼容：旧字段（`prompt_tokens/completion_tokens/total_tokens`）继续存在。

## 5. 状态与交互

必须设计：

- 加载态（骨架屏，不是空白）
- 空状态（无生成记录 / 无 count 记录 / 无历史）
- 错误态（后端 4xx/5xx、鉴权失败、超时）
- 部分数据缺失态（如旧记录没有 cache 字段）
- 刷新/自动轮询（建议 10s，可手动刷新）
- 时间范围切换后的 loading 与空数据
- 移动端折叠/横向滚动策略

## 6. 视觉与响应式

- 桌面 ≥1280：概览卡片 + 趋势 + 明细表同屏可读；
- 平板 768–1279：卡片两列，趋势与表格纵向；
- 手机 <768：卡片单列，表格横向滚动或卡片化，禁止内容溢出；
- 深色/浅色（若现有设计系统支持）需给出两套截图；
- 数字使用等宽字体，千分位分隔；大数用 K/M/B 时必须在 tooltip 给精确值。

## 7. 设计稿交付物（接任 agent 必须先交）

设计稿至少包含：

1. 页面信息架构与导航位置；
2. 桌面/平板/手机三档线框；
3. 视觉规格：颜色、字号、间距、卡片/表格/图表样式，复用现有设计 token；
4. 数据映射表：每个 UI 数字 → API 字段 → 上游 usage 字段；
5. 空/加载/错误/缺字段四种状态稿；
6. API 契约变更清单（`/stats`、`/stats/timeseries`）；
7. 浏览器截图验证计划：用 Playwright 或 agent-browser 打开实际页面，在 1440/1024/390 三档截图，并与设计稿逐项对照；
8. 明确“不改哪些现有页面/接口”，避免 WP7 范围膨胀。

**未提交设计稿或未获用户确认前，不得修改 `web/`。**

## 8. 实现阶段（设计确认后）

1. **后端统计扩维**：`state.py` 字段、usage 解析、`/stats` 契约、模型名归一、count 独立统计；单测覆盖。
2. **独立统计页 UI**：先静态数据/契约 mock，再接真实 API；组件拆分遵循现有 `web/src/components` 风格。
3. **视觉验证**：浏览器工具实际打开页面，三档截图；记录控制台错误、网络失败、空状态。
4. **回归**：现有 Dashboard、账号页、配置页不回归；`bun` 构建/类型检查通过。
5. **证据**：截图与设计稿对照写入 `docs/dev/evidence/wp6-<date>/`。

## 9. 验收

- token 数字与上游 usage 一致：一次生成请求后，输入/输出/推理/缓存字段可逐项核对；
- 一次 `count_tokens` 后，`count_requests` 增加，生成 token 总量不变；
- 独立统计页在三种视口下有真实浏览器截图，且与确认的设计稿一致；
- 空/加载/错误/缺字段状态均有截图或录屏证据；
- 不允许只读 Vue 源码就宣称 UI 完成。

## 10. 非目标

- 不在本任务书阶段写任何 UI 代码；
- 不引入本地 tokenizer 估算；
- 不把 count 请求混入生成 token 统计；
- 不重做现有 Dashboard 的账号/请求概览；
- 不在 WP6 阶段做 WP7 的全局 UI 统一修改。