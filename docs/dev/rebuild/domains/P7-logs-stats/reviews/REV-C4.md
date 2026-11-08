# REV-C4 — 统计台账与趋势图终审报告

- 审查结论：**PASS — 0 个未关闭 P0/P1/P2/Q**
- 审查日期：2026-11-08（Asia/Shanghai）
- 目标工作区：`D:\LLM\self_programming\aistudio-api\.kilo\worktrees\rebuild-upstream`
- 目标 HEAD：`0f5eac9e616b2143bb5602153edabd491bf8e20f`（C4 未提交）
- 主仓：`D:\LLM\self_programming\aistudio-api`，HEAD `840731b83ae65ad282c6c15ab766679e9269abc6`
- 只读声明：本终审未修改源码、测试、配置、AGENT、static；未 stage/commit/push。仅写入本报告与 `reviews/assets/c4-*.png`（目标区与主仓双写）。
- 路径说明：按任务指令写入 `domains/P7-logs-stats/reviews/`；仓库当前另有 `P7-logs` 域，本报告未改动其内容。

## 1. 终审结论

首轮 4 个 P2 均已独立复核关闭；C4 全量核心、全量门禁、双仓状态均通过。剩余 N 项为非阻断观察，不构成 P0/P1/P2/Q。

| 编号 | 首轮问题 | 终审状态 | 关键证据 |
| --- | --- | --- | --- |
| P2-1 | Tokens 堆叠图/Tooltip 重复计算思考 Tokens | **CLOSED** | 堆叠仅 3 段；reasoning 仅在 Tooltip 标注“已包含于输出” |
| P2-2 | 204/404 重试与 AuthError+tier 漏记 attempt | **CLOSED** | 重试/抛错前先 `_record_error`；新增 4 项单测 |
| P2-3 | 1440/1024 下 x 轴最后日期被裁切 | **CLOSED** | 动态 viewBox，首/尾 anchor；1440/1024/768 最后日期完整可见 |
| P2-4 | `topK: int -> float` 意外契约变更 | **CLOSED** | 恢复 `int`，JSON 序列化 `"topK":40` |

## 2. P2 关闭复核

### 2.1 P2-1 Tokens 堆叠与 Tooltip

- `web/src/components/dashboard/modelUsage.ts:386-403`：`tokenStack()` 的 `output = completion_tokens`，`reasoning` 单独返回，`stackTotal = cached + uncached + output`；注释明确 completion 已含 reasoning。
- `web/src/components/dashboard/UsageTokenChart.vue:55-57`：堆叠段只有 `cached/uncached/output` 三段，没有 reasoning 段。
- `UsageTokenChart.vue:245`：Tooltip 仍保留 4 个分项行，思考行明确标注“思考 Tokens（已包含于输出）”；总 Tokens 使用 `total_tokens`，为权威值。
- 浏览器实证（1440，30 天，全部模型）：每个柱仅 3 个 `<rect>`，fill 依次为 `#14b8a6`、`#3b82f6`、`#6366f1`；Tooltip 文本为日期 + `当日总 Tokens` + 命中缓存 + 未命中输入 + 输出 + 思考（已包含于输出），无重复堆叠。
- 语义复核：Gemini UsageMetadata 的 `totalTokenCount` 为 prompt + candidates + thoughts；本项目 `completion_tokens = visible + reasoning`，故生产数据的 `cached + uncached + completion = prompt + completion = total`。沙盒假数据的分项独立分布问题见 N-1（非阻断）。

### 2.2 P2-2 attempt 每执行一次

- `src/aistudio_api/application/routing_pipeline.py:779-785`：AuthError 且 tier 不满足时，先 `_record_error("auth_error")`，再抛 `TierRequiredError`。
- `routing_pipeline.py:867-884`：204/404 同号重试前先 `_record_error("error")`，随后返回 `retry_same`；重试成功由 `record_success` 再记一次，成功/失败均独立。
- `routing_pipeline.py:860/885`：非重试的 404/204 终态仍只记录一次，未发现重复。
- 单测：`tests/unit/test_stats_ledger.py:248`（204 重试 + 成功两次 attempt）、`:278`（transient 404 重试）、`:306`（AuthError+tier）、`:336`（204 重试成功 client 仍单次 success）；定向运行 `20 passed`。
- client 回归：204 重试成功仍为 `[(MODEL, "success", True)]`；429/切换/取消/空响应路径既有测试全部通过；`1273 passed`。

### 2.3 P2-3 日期裁切与固定宽度

- `web/src/components/dashboard/useChartWidth.ts:3-20`：ResizeObserver 测量容器 `clientWidth`，最小 280，fallback 720。
- `UsageRequestChart.vue:14/29/55-59/147/218` 与 `UsageTokenChart.vue:6/20/34-39/121/174`：动态 viewBox、首尾 `start/end` anchor，`min-w-[520px]` 已移除。
- 1440 实测：两图 viewBox 均为 `0 0 493 260`，容器 `scrollWidth == clientWidth == 493`；最后一个日期 `11-08` 文本矩形 `[779,933,28,13]` 完整位于容器 `[330,691,493,260]` 内（Tokens 图同理，`lastFullyInside=true`）。
- 1024 实测：两图 viewBox `0 0 285 260`，`scrollWidth == clientWidth == 285`，最后日期完整可见；页面 `scrollWidth=1024`。
- 768 实测：单列 670px，图表 viewBox `0 0 636 260`，无内部滚动、无页面溢出；页面 `scrollWidth=768`。
- Tooltip 裁切：1440 与 1024 实测均完整位于图表容器水平范围内；1440 截图可见浮窗未越界。
- 回归测试：`web/tests/dashboardStatsUi.test.ts:43-55` 断言不存在 `min-w-[520px]`、存在 `useChartWidth`、首/尾 anchor。

### 2.4 P2-4 topK 契约

- `src/aistudio_api/api/response_models.py:196`：`topK: int | None`。
- 实测 `GeminiModelResponse(... topK=40).model_dump_json()` 输出 `"topK":40`，`model_dump(mode="json")["topK"]` 为 `int`，非 `40.0`。
- `tests/unit/test_routes_models.py:275-280` 已断言 JSON 为整数且包含 `"topK":40`；models 路由既有测试随全量 1273 项通过。

## 3. C4 全量核心复核

- **Client / Attempt**：客户端终态幂等；429 同号/切号/空响应成功仍 1 success + rescued；最终 429 映射 rate_limited；取消映射 aborted；容量/tier/其它映射 errors；client 与 attempt 成功率独立。首轮 P2-2 修复后 204/404 与 AuthError+tier 也不漏记，且不重复。
- **CountTokens**：`record_attempts=False`，只写独立 `count_tokens` 账本；成功/失败记录；不进入生成 client/attempt/tokens；不增加生成配额；Gemini/Claude/Responses 入口独立。
- **90 天 daily/by_model**：Asia/Shanghai 连续 90 天，超出裁剪；daily 含 `client/attempt/tokens/by_model`；`?days=7|30|90` 返回窗口 summary + N 天；无参数 all-time + 90 天；v2 兼容、迁移备份、原子写、真实模型保留、幽灵键过滤。
- **沙盒**：21 天多模型起伏；`simulate_requests` 实测今日 daily attempts/client/tokens 各 +10；`simulate_429` 实测 attempts/client/429 各 +1；daily 与 `/stats` 同源一致。
- **前端**：列表/趋势 segmented slider、7/30/90、全部/具体模型、client/attempt、独立 `/stats?days=`、Bento 全局 `/stats` 轮询不污染、双图 SVG/hover/毛玻璃、空/加载/错误、1440/1024 双栏/768 堆叠、无页面级溢出；Bento/QuickApi 结构未改；无独立 StatsView/侧栏/路由；N-2 “默认开启”与 N-6 死函数删除保持关闭。

## 4. 全量门禁实跑

| 门禁 | 结果 |
| --- | --- |
| `uv run ruff check .` | `All checks passed!`，exit 0 |
| `uv run pyright src tests` | `0 errors, 0 warnings, 0 informations`，exit 0 |
| `uv run pytest -q` | `1273 passed in 26.55s`，exit 0 |
| `uv run pytest -q tests/unit/test_stats_ledger.py` | `20 passed in 1.03s` |
| `cd web && bun run lint` | exit 0 |
| `cd web && bun run type-check` | exit 0 |
| `cd web && bun test` | `61 pass / 0 fail / 243 expect() calls / 7 files`，exit 0 |
| `cd web && bun run build` | `✓ built in 3.41s`，exit 0 |
| static 构建一致性 | 构建前后 static 全量 SHA256 清单 `Compare-Object` 差异数 **0** |
| `git diff --check` | exit 0（仅 CRLF 警告） |
| staged | 空 |
| `config.yaml` | 双仓均未 tracked（`git ls-files config.yaml` 空） |
| HEAD | 目标区 `0f5eac9`、主仓 `840731b` 均未变化 |

## 5. UI Debug 实测

- 启动：`AISTUDIO_UI_DEBUG=1`、`AISTUDIO_WEB_PASSWORD=''`、`uv run python main.py server --port 18371 --browser-port 19223`，绑定 `127.0.0.1:18371`。
- 1440：列表↔趋势、7/30/90、全部模型/`gemini-3.8-flash`、client/attempt、两图 hover 均正常；最后日期 `11-08` 完整可见；页面 `scrollWidth=1440`。
- 1024：双栏（各 285px 图表），最后日期完整可见，页面 `scrollWidth=1024`，Tooltip 水平位于容器内。
- 768：单列 670px，图表 636px，最后日期完整可见，页面 `scrollWidth=768`，无页面溢出。
- 请求图：hover 竖虚线存在；attempt Tooltip 显示尝试/成功/429/错误/成功率；client Tooltip 显示请求/成功/429/救援/最终失败/成功率。
- Tokens 图：3 段堆叠；Tooltip 显示日期/总 Tokens/命中缓存/未命中输入/输出/思考（已包含于输出）；`backdrop-filter: blur(12px)` 深色毛玻璃。
- 网络：`/stats` 轮询与 `/stats?days=7|30|90` 独立，未污染 Bento 全局 stats。
- 干净重载后 console error = 0。
- 沙盒动作：`POST /api/debug/dashboard/apply` 的 `simulate_requests` 与 `simulate_429` 均返回 200，并同步更新今日 daily。
- 清理：服务已停止，`18371/19223` 均不可连接，PID 4692 不存在；本次临时目录 `%TEMP%\aistudio-ui-debug-t0ll_y8r` 已清理。

## 6. 问题清单（完整）

### P0

无。

### P1

无。

### P2

无（首轮 P2-1..P2-4 全部 CLOSED）。

### Q

无。

### N（非阻断观察）

- **N-1 沙盒 daily 分项仍是独立分布**：`ui_sandbox.py:1247-1257` 对 attempts/success/rate_limited/errors 及 prompt/completion/reasoning/cached/total 独立 `_distribute`，导致假数据每日 `success + rate_limited + errors` 可大于 attempts（实测曾有 49/49/1），且 `total_tokens` 与 `cached+uncached+completion` 可不相等（实测总 1,637,214 vs 分项 1,401,544）。生产 wire 语义下 total = prompt + candidates + thoughts = prompt + completion，故不影响真实账本；仅 UI debug 演示数据的算术自洽性。
- **N-2 tool-use prompt tokens 未单列**：Gemini total 还包含 `toolUsePromptTokenCount`，当前 stats/图表没有该分项；发生工具调用的请求可能出现 stack < total 的边界情况。C4 图表验收未要求该分项。
- **N-3 `?days=` 下 `models` 仍为 all-time**：`state.build_stats_payload` 的 `models` 取全量，仅 totals/client/attempt/daily 按窗口；当前 UI 只用 daily，无功能影响。
- **N-4 countTokens 最终 429 归 errors**：`token_counter.py` 统一失败落 `count_tokens.errors`，账本无 rate_limited 字段；符合“成功/失败记录”要求。
- **N-5 `_acquire` 返回 None 时归 rate_limited**：稀有“候选全部不可用”路径抛 `SurfaceSwitchExhaustedError`；常规容量路径由 `SurfaceCapacityError` 归 errors。
- **N-6 趋势视图状态不持久**：列表↔趋势切换会卸载 `ModelUsageChart`，重新进入时 days/model/mode 重置为 7 天/全部/client。
- **N-7 client Tooltip“429 重试”语义**：实际使用 `client.rate_limited`（最终 429 终态数），并非重试次数；建议后续改文案。
- **N-8 时区回退**：`modelUsage.todayIso()` 使用浏览器本地时区，仅在 payload 无 range/无 daily 点时回退；后端 Shanghai 口径为主。
- **N-9 保留键集中化**：`routes_system.py` 不再自带 `RESERVED_STATS_KEYS`，统一由 `state.build_stats_payload/_normalized_models` 过滤；行为保持，但与旧的三处同步约定不同。
- **N-10 static 重建顺带对齐历史陈旧产物**：rebuild 更新 `DashboardView.js` 的 QuickApi 片段并移除 `accountStatus.js`，对应源码在 HEAD 已存在，属构建对齐；构建前后 static 哈希一致。

## 7. 双仓与工作树状态

- 目标区：C4 未提交改动 + 本报告/assets；无 stage；HEAD 仍 `0f5eac9`。
- 主仓：仅本报告与 2 张截图；HEAD 仍 `840731b`。
- 本审查未修改源码/测试/配置/AGENT/static；未执行 stage/commit/push。
- 本次沙盒临时目录已清理；其它 `%TEMP%\aistudio-ui-debug-*` 为历史会话残留，非本次产生。

## 8. 截图与 SHA256

- `docs/dev/rebuild/domains/P7-logs-stats/reviews/assets/c4-dashboard-trend-charts-1440.png`
  - SHA256 `420251347DCBB5A74200D847DA36A0363EA0FB593A656D0B8E0C34CC01AC742B`
  - 1440x1632，30 天，client 模式，全部模型；最后日期 `11-08` 完整可见
- `docs/dev/rebuild/domains/P7-logs-stats/reviews/assets/c4-dashboard-trend-tooltip-1440.png`
  - SHA256 `64F204191E1A4458EBBD961855395191CE34118758FC1910D359630857837854`
  - 1440x1632，Tokens hover 深色毛玻璃浮窗（4 分项 + 总 Tokens）
- 两张截图已双写目标区与主仓，SHA256 一致。

## 9. 未解决项

无未关闭 P0/P1/P2/Q。N-1..N-10 为非阻断观察，不影响 PASS；若后续需要“沙盒假数据完全算术自洽”或“tool-use prompt 单列”，可作为独立改进项。

最终 Verdict：**PASS**。
