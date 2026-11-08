# REV-C4b — 配置 Tooltip 防溢出与 N-1/N-7 修复终审报告

- 审查结论：**PASS — 0 个未关闭 P0/P1/P2/Q**（安全事件 Q-1 已核实并整改闭环）
- 审查日期：2026-11-08（Asia/Shanghai）
- 目标工作区：`D:\LLM\self_programming\aistudio-api\.kilo\worktrees\rebuild-upstream`
- 目标 HEAD：`61a470bfc1be34adf607d8a6cc987dff7cded51b`（C4b 未提交）
- 主仓：`D:\LLM\self_programming\aistudio-api`，HEAD `b85fd6bac3b32e6bc4d41979bb02c7b29df07852`
- 只读声明：本终审未修改源码、测试、配置、AGENT、static；未 stage/commit/push。仅写入本报告与 `reviews/assets/c4b-*.png`、更新 `reviews/assets/c4-dashboard-trend-tooltip-1440.png`（双仓）。
- 安全声明：本次 UI 验证仅在 `AISTUDIO_UI_DEBUG=1`、空密码、随机 `127.0.0.1:18537`（浏览器端口 19357）下进行，交互前已确认 `GET /health` 返回 `ui_debug=true`；服务已停止、端口已关闭、本次临时目录已清理。

## 1. 终审结论

C4b 四项修复均独立复核通过；75 词条审计覆盖真实；viewport clamp 在 1440/1024/768/375 实测完整在视口内；N-1 沙盒 daily/by_model 恒等式在 API 与 UI 两侧成立；N-7 文案语义正确；C4 列表/趋势回归无影响；全量门禁全部通过。Worker 自述的 `ui_debug=false` 运行事故经独立取证为真实事件，账号 tier/refresh 已回到事故前基线，无残留进程/端口/配置/代码污染；未发现隐瞒。剩余 N 项为非阻断观察。

## 2. 方法与只读声明

- 源码/测试：`git diff`、逐文件 `Read`、`git diff --check`、双仓 `git status/HEAD/staged`。
- 静态校验与门禁：`uv run ruff check .`、`uv run pyright src tests`、`uv run pytest -q`、`cd web && bun run lint && bun run type-check && bun test && bun run build`。
- 构建一致性：构建前对 `src/aistudio_api/static` 全量 SHA256 快照，构建后 `Compare-Object` 差异 0（未污染 static）。
- 运行时：`AISTUDIO_UI_DEBUG=1`、`AISTUDIO_WEB_PASSWORD=''`、`uv run python main.py server --port 18537 --browser-port 19357`；`/health` 先确认 `ui_debug=true`；Playwright MCP 做页面断言与截图。
- API 实测：直接请求 `GET /stats?days=7|30|90`、`GET /stats`、`POST /api/debug/dashboard/apply`。
- 未修改任何源码/测试/配置/AGENT/static；未 stage/commit/push。

## 3. C4b 逐项检查表

| 编号 | 修复项 | 结论 | 关键证据 |
| --- | --- | --- | --- |
| 1 | Tooltip `推荐配置: false` 根因 | **PASS** | prop 类型修正 + 格式化 + 无值/复杂隐藏 + 75 词条对齐 |
| 2 | Tooltip 视口防溢出 | **PASS** | 纯函数 clamp + Teleport/fixed + 四宽度实测 + 触屏单触发 |
| 3 | N-1 沙盒 daily/by_model 恒等式 | **PASS** | 归一化/配额分配 + 6 项单测 + API 实测 |
| 4 | N-7 client Tooltip 语义 | **PASS** | 429/救援/最终失败/中断分离 + 实测文案 |

### 3.1 Tooltip `推荐配置: false`

- 根因：`web/src/components/config/FieldHelpTip.vue:16` 原 `recommended?: string | number | boolean` 触发 Vue 的 Boolean 类型转换，未传 prop 时被强制为 `false`，从而覆盖 `fieldDef.recommendedValue` 并显示 “推荐配置: false”。
- 修复：`FieldHelpTip.vue:16` 改为 `recommended?: unknown`（无 Boolean cast）；`FieldHelpTip.vue:39-41` 仅在 `recommendedText !== null` 时渲染；模板 `:165-170` 的绿条 `v-if="recommendedText !== null"`。
- 格式化：`web/src/components/config/recommended.ts:8-18`：`boolean` → `开启/关闭`；有限数字 → `String`；非空字符串 → 原值；`null/undefined/空字符串/数组/对象` → `null`（隐藏）。
- 75 词条审计（`CONFIG_SCHEMA` 共 75 项）：
  - 54 项含 `recommendedValue`，测试 `web/tests/configTooltip.test.ts` 的 `defaultRecommended` 覆盖 53 项，`features.formats.gemini` 以单独断言覆盖，合计 54/54；脚本比对 map 与 schema：`missing=0, extra=0`。
  - 21 项无 `recommendedValue`，绿条隐藏；其中 9 项由 `entries without a single default hide the recommended badge` 直接断言，其余源上无值、行为一致。
  - 与 `defaults/config.yaml` 对齐：`rotator.max_retries=5`、`max_contexts=2`、`features.routing.max_switches=2` 均一致；53 项 map 全部取自 `defaults`/`imageProfile`/`geminiProfile` 的实际值，测试全绿。
  - 复杂结构隐藏：`generation.clear_indexes=[7,13,17]`、`safety.settings` 对象、`paid_model_rules` 对象在 tooltip 中不显示绿条（`recommended.ts:9-18` 返回 `null`）。
  - 已移除的“伪推荐”：`profile.drop_unsupported_params`、`system.drop_unsupported_params`、`instances.selector`、`paid_model_rules.match/required_tiers`、`tier_quotas.*` 不再显示与默认值冲突的推荐。
- 浏览器实证（375/1440）：
  - 无值字段 `instances.selector`（实例等级匹配规则）：tooltip 无 “推荐配置” 行。
  - `logging.dump_requests`（请求报文输出到控制台，推荐 false）：显示 `推荐配置: 关闭`，无 `false` 字样。
  - 复杂字段 `generation.clear_indexes`（清空特定字段下标）：无 “推荐配置” 行。
- 残留观察：`SchemaDictionaryModal.vue:149` 仍使用 `JSON.stringify(field.recommendedValue)`（字典弹窗，非 tooltip），见 N-1；prop 类型缺少回归断言，见 N-2。

### 3.2 Tooltip 视口防溢出

- 纯函数：`web/src/components/config/tooltipPosition.ts:39-90`
  - `left = clamp(triggerCenter - width/2, 8, max(8, innerWidth - width - 8))`；
  - 宽度上限 `viewportWidth - 16`（`availableWidth`）；
  - 上方放不下且下方可放 → `bottom`；上方可放 → `top`；上下都放不下按空间较大侧并 clamp `top ∈ [8, innerHeight - height - 8]`；
  - 箭头 `arrowLeft` 同样 clamp，模板以 `-translate-x-1/2` 对准触发点中心。
- 组件：`FieldHelpTip.vue:124` `Teleport to="body"`；`:140` 未定位前 `visibility: hidden`；`:124-145` `position: fixed` + `w-72 max-w-[calc(100vw-16px)] sm:w-80`；`watch(show)`（`:79-86`）在 `nextTick` 后测量并监听 `resize`/`scroll(capture)`，`onBeforeUnmount` 清理（`:88`）。
- 单测：`web/tests/configTooltip.test.ts` 覆盖 1440/1024/768/375 左右边缘、上方无空间翻转、移动端 400px 宽浮窗、箭头对准；断言 `left>=8` 且 `left+renderedWidth<=viewport-8`。
- 浏览器实测（视口 1440x1000 / 1024x900 / 768x900 / 375x812，字段 `rotator.max_retries` 的 `?`，推荐值 5）：

| 视口 | tooltip left | tooltip right | 上/下 | 箭头中心 | 触发点中心 | 页面 scrollWidth | 页面横向滚动 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1440 | 1112 | 1432 (=1440-8) | 297/483 | 1375 | 1374 | 1440 | 无 |
| 1024 | 696 | 1016 (=1024-8) | 247/433 | 959 | 958 | 1024 | 无 |
| 768 | 440 | 760 (=768-8) | 247/433 | 711 | 710 | 768 | 无 |
| 375 | 79 | 367 (=375-8) | 203/389 | 330 | 329 | 375 | 无 |

  - 浮窗宽度分别为 320/320/320/288，`max-width` 分别为 1424/1008/752/359，均在视口内；375 下 `bottom=389 <= 812-8`。
  - 375 下 `instances.selector` 浮窗 `left=8`（左边缘 clamp）也在视口内。
  - 触屏防双触发：模拟 `(hover: hover)` 为 false 时，`mouseenter` 不打开（no-op），单击打开、再次单击关闭（`false → false → true → false`），未出现双触发。

### 3.3 N-1 沙盒 daily/by_model 恒等式

- `src/aistudio_api/api/ui_sandbox.py`：
  - `_seed_dashboard_stats:1231-1234` 先对每个模型 `_normalize_stats_item` 并派生 `client`，再 `_seed_daily_stats`。
  - `_normalize_stats_item:2001-2023`：`success/rate_limited` clamp，`errors = requests - success - rate_limited`，`prompt/completion` 非负，`cached<=prompt`，`reasoning<=completion`，`total = prompt+completion`。
  - `_allocate:2025-2088`：按权重分配且不超 `target`，溢出桶按 cap 收口，余数按小数部分分配，保证每日合计精确。
  - `_seed_daily_stats:1248-1285`：`attempts=_distribute(requests)`；`success=_allocate(success, attempts)`；`rate_limited=_allocate(rate_limited, remaining)`；`errors=remaining-rate_limited`；`prompt/completion=_distribute`；`cached=_allocate(cached,prompt)`；`reasoning=_allocate(reasoning,completion)`；`total=prompt+completion`；`client=_client_from_attempts`。因此 `attempts=success+rate_limited+errors`、`client.requests=success+rate_limited+errors+aborted`、`total=prompt+completion`（即 `cached+(prompt-cached)+completion`）逐日成立。
  - `_action_simulate_requests:1338` / `_action_simulate_429:1386`：只在模型、daily、totals 上同步增量，`429` 同时 `requests+1, rate_limited+1`，保持恒等式。
- 测试：`tests/unit/test_ui_sandbox_stats_consistency.py` 6 项（`all_states/mass_accounts/narrow_edge/all_429/empty` 参数化 + simulate），对 `daily`、`by_model`、`totals`、模型项逐项断言恒等式；全量 `1279 passed`。
- API 实测（live sandbox）：
  - `GET /stats?days=7|30|90` 与 `GET /stats`：分别 7/30/90/90 个 daily 点，恒等式错误数均为 **0**。
  - `POST /api/debug/dashboard/apply {"action":"simulate_requests"}`：今日 attempts 93→103（+10）、prompt +32000、completion +18000、total +50000，恒等式错误 0。
  - `POST ... {"action":"simulate_429"}`：attempts 103→104（+1）、rate_limited 0→1，恒等式错误 0。
- 残留观察：`_seed_account_daily:1287` 使用 `int()` 截断做账号占比拆分，账号级分项合计可能略小于模型合计（不进入 `/stats` 的 N-1 口径），见 N-9；`_normalize_stats_item` 会以恒等式重写种子 `total_tokens`（如 3,183,000→2,771,000），见 N-10。

### 3.4 N-7 client Tooltip 语义

- `web/src/components/dashboard/UsageRequestChart.vue:124-125` 使用 `point.client.rate_limited`、`point.client.rescued_requests`，并分别取 `errors`/`aborted`；模板 `:277` `429 限流失败`、`:281` `重试/切号救援成功`、`:285` `最终失败`、`:289` `中断`。
- 语义核对：`generation_service._terminal_result` 将 `SurfaceSwitchExhaustedError`/`UsageLimitExceeded` → `rate_limited`；`Cancelled`/`GeneratorExit` → `aborted`；其余终态失败 → `errors`；救援成功计入 `rescued_requests`（`success` 的子集）。因此四类终态互不重叠，`429 限流失败` 是最终限流失败而非重试次数。
- 测试：`web/tests/dashboardStatsUi.test.ts` 新增断言旧文案 `429 重试` 不再出现，且 `UsageRequestChart` 使用分离字段。
- 浏览器实测：client 模式 tooltip 文本为 `客户端请求 / 成功 / 429 限流失败 / 重试/切号救援成功 / 最终失败 / 中断 / 成功率`；attempt 模式为 `尝试 / 成功 / 429 / 错误 / 成功率`。语义正确。

## 4. C4 回归

- 列表/趋势切换正常；“近 7/30/90 天”分别渲染 7/30/90 个数据点；模型下拉仅含 4 个真实模型（`gemini-3.7-flash`、`gemini-3.8-flash`、`gemini-4-argon`、`gemma-4-31b-it`），无 `models/daily/finish_reasons/...` 保留键。
- client/attempt 切换正常；Tokens 柱状图每柱仅 3 段（`#14b8a6/#3b82f6/#6366f1`），无 reasoning 段；hover 浮窗正常。
- 首页 Tokens hover（7 天，全部模型，11-08）：总 763,654；命中缓存 169,324 + 未命中输入 369,310 + 输出 225,020 = **763,654**，三项和等于总 Tokens；截图已更新。
- Bento 顶栏（当前实例/可用账号/API 请求/429/总 Tokens）渲染正常；C4b diff 未触及 C2c/C2d 组件，源码无回归。
- 干净交互后 console error = 0、warning = 0；页面 `scrollWidth === innerWidth`（1440）。

## 5. 全量门禁实跑

| 门禁 | 结果 |
| --- | --- |
| `uv run ruff check .` | `All checks passed!`，exit 0 |
| `uv run pyright src tests` | `0 errors, 0 warnings, 0 informations`，exit 0 |
| `uv run pytest -q` | `1279 passed in 26.43s`，exit 0 |
| `cd web && bun run lint` | exit 0 |
| `cd web && bun run type-check` | exit 0 |
| `cd web && bun test` | `69 pass / 0 fail / 604 expect() / 8 files`，exit 0 |
| `cd web && bun run build` | `✓ built in 2.77s`，exit 0 |
| static 构建一致性 | 构建前后全量 SHA256 `Compare-Object` 差异 **0**（13 文件） |
| `git diff --check` | 目标区/主仓均 exit 0（仅 schema.ts CRLF 警告） |
| staged | 双仓均为空 |
| `config.yaml` | 双仓均未 tracked（`/config.yaml` 被 `.gitignore:15` 忽略） |
| HEAD | 目标区 `61a470b`、主仓 `b85fd6b` 均未变化 |

## 6. 安全事件审计（独立核实）

Worker 自述：一次误以 `ui_debug=false` 启动，42 个账号外部页面请求 302，随后回滚账号 tier/refresh 字段并备份 `%TEMP%\c4b-accounts-backup-20261108202251`。独立核实结果：

- **事件真实存在**：
  - `data/accounts/registry.json` 备份 mtime `2026-11-08 20:09:54`（事故写盘），回滚后 mtime `20:23:12`。
  - `data/logs/aistudio-20261108.log` 于 `20:09:04` 打印 `账号初始状态`（47 账号），随后 `20:09:07-20:09:50` 对 42 个账号执行 `[Refresh] ... valid`。
  - 备份 meta 中恰好 **42** 个账号带 `last_refresh_error="page fetch HTTP 302"` 及同一时间戳的 `tier_checked_at/last_refresh_attempt_at`。
- **账号当前状态可解释且与事故前基线一致**：
  - 事故前基线来自 `20:09:04` 启动日志：Pro=2（`acc_99bb393f`、`acc_a30d1cc4`），Free=45。
  - 备份（事故后）：Pro=4（多出 `acc_aeb28c13`、`acc_66ca0e98`）。日志 `20:09:49` 显示这两个账号探测返回 `tier=Pro`（同时 email page fetch 302）。
  - 回滚后（当前）：Pro=2（`acc_99bb393f`、`acc_a30d1cc4`）；与 `20:09:04` 基线逐账号比对 **tier_diffs=0**。
  - 42 个账号的 `tier_checked_at/last_refresh_attempt_at/last_refresh_error` 已清空；另有 5 个账号保留事故前既有的 `HTTP 401` 刷新错误（非本次 302，未受影响）。
  - 47 个账号 `auth.json` 与备份逐字节比对 **0 差异**；`presets.json` SHA256 完全一致。
- **无残留进程/端口**：无 `python/uv/chrome` 残留；本次沙盒端口 18537/19357 已关闭，PID 27332 不存在；本次 `%TEMP%\aistudio-ui-debug-*` 目录已清理。
- **无真实 config.yaml 被跟踪/污染**：根 `config.yaml` 被 `.gitignore` 忽略（未 tracked）；mtime 为事故时刻 `20:09:04`，但其内容与 `defaults/config.yaml` 语义比对 **DIFF_COUNT=0**，`api_keys: []`、`ui_debug: false`，无敏感信息进入 git；无 tracked diff。
- **无其它事故产物**：`data/dumps` 为空；仅存在 `acc_4bbddd38/profile`（mtime 10-31，事故前既有）；`stats.json` mtime 11-07（事故前），无事故写入。
- **代码修复未受污染**：`git diff` 仅含 C4b 的 `ui_sandbox.py`、`FieldHelpTip.vue`、`schema.ts`、`UsageRequestChart.vue`、测试与 static；无账号/配置数据进入 tracked 变更。
- **后续 UI 验证全部 `ui_debug=true`**：本次 20:30 启动后先查 `/health`（`ui_debug=true`）再交互；所有页面操作均在确认后进行。
- **结论**：事件属流程红线（违反“不得以 ui_debug=false 启动”），但未隐瞒；账号数据已回到事故前基线，无持久数据损坏。Q-1 记为 CLOSED。残留外部影响见 N-8。

## 7. 问题清单（完整）

### P0

无。

### P1

无。

### P2

无（未发现未关闭 P2）。

### Q

- **Q-1 流程红线事件（CLOSED）**：Worker 曾以 `ui_debug=false` 启动，触发 42 个账号真实外部页面请求（302）及 2 个账号探测 tier 更新；随后回滚到事故前基线并留备份。独立核实：当前 tier 与 `20:09:04` 启动日志逐账号一致（0 差异），42 个 302 refresh 字段已清空，`auth.json` 0 差异，无残留进程/端口，config.yaml 未 tracked 且语义等于 defaults，代码未污染。事件已闭环，不再计为未关闭 Q。

### N（非阻断观察）

- **N-1**：`SchemaDictionaryModal.vue:149` 仍以 `JSON.stringify(field.recommendedValue)` 展示 `false`/对象；这是字段字典弹窗，不是本次 Tooltip 修复范围，但建议后续统一用 `formatRecommendedValue`。
- **N-2**：`FieldHelpTip` 的根因（`recommended?: unknown`）没有单测/源级断言守护；若未来改回 Boolean 联合类型，`configTooltip.test.ts` 不会失败，可能复发 “推荐配置: false”。本次已用浏览器实证，但建议补一条源级断言。
- **N-3**：`profile.is_image_model`（推荐 true）、`safety.disable_safety_settings`（true）、`generation.thinking_level`（MINIMAL）是生图 profile 的推荐值，却按全局 schema 显示在每个 profile 卡片；在文本类 profile 上可能构成“场景相关伪推荐”。与 defaults 的 image profile 对齐，非本次回归。
- **N-4**：`schema.defaultValue` 目前未被 UI 使用；`paid_model_rules.defaultValue={}` 与 defaults 的已填充规则不一致（无运行时影响）。
- **N-5**：`clampTooltipPosition` 只 clamp `top`，不限制 tooltip 高度；若内容高度超过视口-16 会发生垂直溢出。当前 375x812 实测高度 186，未触发。
- **N-6**：桌面 hover 打开后点击会把 tooltip 关闭（沿用既有 hover+click 逻辑）；触屏已实测单击单触发，无双击问题。
- **N-7**：事故中 2 个账号的探测返回 `tier=Pro`（`20:09:49` 日志），回滚按“恢复事故前状态”策略将其改回 Free，属有意的整体回退；下次合法的真实刷新会重新探测。当前显示 tier 可能暂时滞后，非持久损坏。
- **N-8**：42 次外部页面请求（302）已真实发生，无法撤回；可能对上游产生审计/限流副作用，本地无法验证。建议后续以真实服务运行时留意账号状态。
- **N-9**：`_seed_account_daily` 用 `int()` 截断做账号占比拆分，账号级分项合计可能略小于模型合计；不影响 `/stats` 的 daily/by_model 恒等式。
- **N-10**：`_normalize_stats_item` 会以 `prompt+completion` 重写种子 `total_tokens`（例如 3,183,000→2,771,000），属恒等式要求，但会改变演示数据的展示总量。
- **N-11**：`_allocate` 的“上下都放不下”垂直分支未被单测覆盖（纯函数逻辑正确，当前内容未触发）。

## 8. 双仓与工作树状态

- 目标区：C4b 未提交改动 + 本报告/assets；无 stage；HEAD 仍 `61a470b`；无临时文件写入仓库。
- 主仓：仅本报告与 5 张 assets；无 stage；HEAD 仍 `b85fd6b`。
- 本审查未修改源码/测试/配置/AGENT/static；未执行 stage/commit/push。
- 本次沙盒服务已停止、端口关闭、临时目录 `aistudio-ui-debug-*`（本次产生）已清理。

## 9. 截图与 SHA256

- `docs/dev/rebuild/domains/P7-logs-stats/reviews/assets/c4b-settings-tooltip-1440.png`
  - SHA256 `AF978272831887F99E5E60C1416F32A48320BA0DE021262B6BEE9F3A96A27D04`，1440x1000，`调度重试上限` 浮窗，`推荐配置: 5`，left 1112 / right 1432。
- `.../c4b-settings-tooltip-1024.png`
  - SHA256 `B0223C16E203D471C405FB8AAC4BFC07E2C850D5BAD2EA4DAD9A5D3F3432939B`，1024x900，left 696 / right 1016。
- `.../c4b-settings-tooltip-768.png`
  - SHA256 `A4690667F006B0A6CED0E7C260213F2362B800470C62A07C2665B5B97FFA04A1`，768x900，left 440 / right 760。
- `.../c4b-settings-tooltip-375.png`
  - SHA256 `280A70EFD7094E13D40B019B1E9A6101C51E5D2FEDE4988BCE093C3BB84F4737`，375x812，left 79 / right 367。
- `.../c4-dashboard-trend-tooltip-1440.png`
  - SHA256 `730F7F334D75CBD1E3713BC3FA18094FDC433EF386B1F45F3698018219A61721`，1440x1000，Tokens hover：763,654 = 169,324 + 369,310 + 225,020。
- 以上 5 张已双写目标区与主仓，逐文件 SHA256 一致（`match=True`）。

## 10. 未解决项

无未关闭 P0/P1/P2/Q。Q-1 已核实并整改闭环；N-1..N-11 为非阻断观察，不影响 PASS。

最终 Verdict：**PASS**。
