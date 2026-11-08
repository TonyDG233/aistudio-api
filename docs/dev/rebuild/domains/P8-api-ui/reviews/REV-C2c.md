# REV-C2c — Dashboard Bento / 四协议 QuickApiExamples / 日志沙盒注入 终审（P2-1 + 视觉微调复审）

- 审查日期：2026-11-07（UTC；P2-1 修复与 5 项首页视觉微调后最终复审）。
- Target worktree：`D:\LLM\self_programming\aistudio-api\.kilo\worktrees\rebuild-upstream`，HEAD `68a65044d783fe8290d576db45ed96177982f2f9`（未变）。
- Main repo：`D:\LLM\self_programming\aistudio-api`，HEAD `4f600c04cfc32d7c9d80faf655b5e99be0b34a75`（未变）。
- Target 工作树：`git status --porcelain` 共 **46** 条目（31 M + 1 D + 14 untracked；含本报告与 2 张 assets）。Main 工作树仅新增本报告与 2 张 assets（共 3 条）。
- Method：只读复核 P2-1 修复与 5 项视觉微调的源码/测试/构建产物；重跑双栈全套门禁；在随机沙盒端口 `127.0.0.1:18623`（`AISTUDIO_UI_DEBUG=1`、`AISTUDIO_WEB_PASSWORD=''`）用 headless Playwright 独立复验 1440/1024/768 的 Dashboard/Logs、实例列表 4 行、未绑定槽位、可用账号副文、重置按钮、四协议 cURL、总 Tokens 口径、Bento、UI Debug 场景/动作；重新生成 Dashboard overview 截图（日志页截图按要求不改）；结束停止服务并清理本轮临时目录。
- 只读声明：本轮**未修改源码/测试/配置/AGENT/static**；仅新增/更新双写本报告与 `c2c-dashboard-overview-1440.png`。**未 stage、未 commit、未 push**。未启动 `ui_debug=false` 服务，未访问真实账号/网络，未启动真实浏览器；未改动 `LogsView.vue` 与日志截图。
- 沙盒停止证据：`127.0.0.1:18623`（`--browser-port 18624` 未监听），`background_process.stop` 后 `NO_LISTEN_18623_18624`，无 `uv/aistudio` 残留进程；本轮 `%TEMP%\aistudio-ui-debug-ru0sjwiy` 及其 `aistudio-ui-debug-paths-*` 等临时目录已删除。

## 0. Verdict

**PASS — 0 个未关闭 P0/P1/P2/Q。**

上一轮 P2-1 继续 CLOSED；本轮 5 项视觉微调逐项 **CLOSED**；仅余 7 条 N 级观察，不构成阻塞。

## 1. 问题清单（完整重列）

| 编号 | 级别 | 状态 | 位置 | 结论与证据 |
|---|---|---|---|---|
| P2-1 | P2 | **CLOSED** | `dashboardMetrics.ts:26-28`；`DashboardView.vue:59,62-65`；`dashboardHelpers.test.ts:100-121` | 总 Tokens 直接取 `totals.total_tokens`，不再相加 reasoning/cached。实测卡片 `24.7M` == 模型表合计 `24,663,000` == `/stats`；+50K 后三者同步 `24,713,000`。 |
| V-1 | 视觉微调 | **CLOSED** | `DashboardView.vue:141,146` | 实例列表 `max-h-[14rem]`（224px）+ 行 `h-14`（56px）；1440/1024/768 实测 `clientHeight=224`、`scrollHeight=504`、单行 56px、9 槽位、恰好 4 行可见；`scrollTop` 可到 280，滚到底 #9 可见；右侧 4 张 StatCard 均 363×153、左卡 363×322、右 2×2 网格 741×322，无大片留白。 |
| V-2 | 视觉微调 | **CLOSED** | `DashboardView.vue:170-181` | 未绑定 #2/#9 行文本 `Free │ 未绑定账号 │ 未启动 │ 0 在途`，无 `u/0` 幽灵内容；已绑定 #1/#3…#8 仍显示账号 ID + `u/N`。 |
| V-3 | 视觉微调 | **CLOSED** | `dashboardMetrics.ts:42-43,66-73`；`DashboardView.vue:222`；`dashboardHelpers.test.ts:160-185` | 副文改为 `实例中 X · 空闲就绪 Y · 失效 Z`，且 `X+Y=available`。实测：all_states `4+4=8/12`；mass `4+32=36/48`；all_429 `0+0=0/12`；empty `0+0=0/0`；失效/冷却/disabled 不计入 available。 |
| V-4 | 视觉微调 | **CLOSED** | `UiDebugMenu.vue:164-172` | 重置按钮仅 `<RotateCcw/>` + `重置`；DOM `innerText==='重置'`、含 SVG、无 🔄/↻（码点逐字检查）；源码与构建产物 🔄/↻ 均 0 命中。 |
| V-5 | 视觉微调 | **CLOSED** | `quickApiExamples.ts:68,102,141,177`；`dashboardHelpers.test.ts:221-229` | 四协议 cURL 的 body 均为 `-d '{\n…\n}'` 多行（7/8/9/7 行）；四协议 Base/Endpoint/Auth/示例映射无回归；代码块 `overflow-x-auto`（可横向滚动，非隐藏截断）。 |
| P0 | P0 | 无 | — | 无。 |
| P1 | P1 | 无 | — | 无。 |
| Q | Q | 无 | — | 无。 |
| N-1 | N | 已知 | `/api/accounts/active` | 无 active 账号的场景（empty、全 429）会记录 1 条预期网络 404；不是 JS 异常，C2c 未改账号 store；all_states 主态 console error = 0。 |
| N-2 | N | 可接受 | `routes_debug.py:176-182`；`LogsDebugMenu.vue:65-76` | 清空 ring 后前端 `location.reload()`；重载后仅剩清空/重载自身 4 条 HTTP 元数据，注入样例为 0；归档保留（清空后导出仍含样例且无原始假密钥）。 |
| N-3 | N | 可接受 | `LogsView.vue:831`（未改动）；详情面板 `pre-wrap break-words` | 列表行沿用既有 `truncate`；超长报文在详情面板完整换行（`sw=cw`），满足“long payload 换行”。 |
| N-4 | N | 已核实 | `src/aistudio_api/static/assets/*` | static 全部由 `bun run build` 生成；本轮构建后 `DashboardView.js` 70 行变更（较上轮 56 行增加的 14 行为 5 项微调），文件集与产物一致，无手工编辑痕迹。 |
| N-5 | N | 低风险 | `state.py:234-273` | v2 判定要求 `schema_version==2` 或存在 `daily`；仅含 `models` 的中间态文件会被当作 v1 读取并跳过 `models`。现有测试与真实形态未触发。 |
| N-6 | N | 预期行为 | `ui_sandbox.py:1163-1180` | 切换 Dashboard 场景会 `reset()` 并把 `_formats_all_on` 归零，因此“协议全开”会回到默认开关状态；与场景重置语义一致。 |
| N-7 | N | 可接受 | `QuickApiExamples.vue:123` | 代码块 `pre` 使用 `overflow-x-auto`：≤1024 双栏布局下 Gemini 首行长 URL 在代码块内横向滚动（1440 时 `sw=515/cw=495`，1024 时 `sw=515/cw=287`），JSON body 已多行且完整；属代码块内滚动，不是页面溢出或隐藏截断，768 单列时 `sw=cw=638` 完全放下。 |

## 2. 视觉微调逐项复审（本轮重点）

| 项 | 状态 | file:line | 独立 DOM / 截图证据 |
|---|---|---|---|
| 1 实例列表 4 行 | **CLOSED** | `DashboardView.vue:141,146` | 三宽度 `clientHeight=224`、`scrollHeight=504`、行高 56px、9 槽位、`visibleRows=4`；滚到底 #9 可见；右侧 2×2 卡片 4×363×153、左卡 363×322、无大片留白。截图可见 #1–#4 与右 2×2。 |
| 2 未绑定无 u/0 | **CLOSED** | `DashboardView.vue:170-181` | #2、#9 文本无 `u/`；#1/#3/#4/#5/#6/#7/#8 均显示 `acc_*` + `u/0`。 |
| 3 可用账号副文 | **CLOSED** | `dashboardMetrics.ts:42-43,66-73`；`DashboardView.vue:222` | `实例中 4 · 空闲就绪 4 · 失效 2`，4+4=8；mass/all_429/empty 均满足 `X+Y=available`；单测 160-185 覆盖 5 绑定+3 就绪+2 失效+1 disabled+1 cooldown。 |
| 4 重置按钮无 Emoji | **CLOSED** | `UiDebugMenu.vue:164-172` | DOM `innerText='重置'`、有 `svg`、无 🔄/↻；源码/构建产物全文检索 🔄/↻ = 0。 |
| 5 四协议 cURL 多行 | **CLOSED** | `quickApiExamples.ts:68,102,141,177`；`dashboardHelpers.test.ts:221-229` | 四 Tab 均实测 `-d '{` + 换行 + `}'`；行数 7/8/9/7；OpenAI `…/v1/chat/completions`、Gemini `…/v1beta/...`、Claude `…/v1/messages`、Responses `…/v1/responses` 无回归；`pre` 可横向滚动，非截断（N-7）。 |

## 3. P2-1 关闭核对（保留）

| 检查 | 结果 | 证据 |
|---|---|---|
| 主数值来源 | PASS | `dashboardMetrics.ts:21-28` 注释明确不要重加四桶；`totalTokenCount` 仅 `Number(totals?.total_tokens) || 0`。 |
| Dashboard 使用权威值 | PASS | `DashboardView.vue:59` 使用 `totalTokenCount`；tooltip `:62-65` 为「输入/输出 Tokens 汇总（含思考/缓存）：24,663,000」。 |
| 卡片 == 模型表 == `/stats` | PASS | 24.7M（24,663,000）== 3,183,000+7,950,000+12,300,000+1,230,000 == `/stats`；+50K 后 24,713,000 三者一致。 |
| 不重复计入 | PASS | `sum4=24,991,000` 未出现在 UI/测试期望；reasoning/cached 作为子集只出现在副文。 |
| 单测/构建 | PASS | `dashboardHelpers.test.ts:100-121`（`==total_tokens`、`!=simpleSum`、`null→0`）；`DashboardView.js` 含 `total_tokens` 与修复后 tooltip，无旧四项相加。 |

## 4. 边界确认

| 边界 | 结果 | 证据 |
|---|---|---|
| `LogsView.vue` 仅 import + 顶部挂载一行 | **PASS** | diff 仅 `LogsView.vue:20` 与 `:542`；本轮未改日志页与日志截图。 |
| Worker B 未改 Dashboard 等 | **PASS** | Dashboard 改动均在 Worker A 文件域；日志侧仅新增 `LogsDebugMenu.vue`、`api/logs.ts`、`routes_debug.py` 端点、`log_broadcast.py`、`logger.py`。 |
| Worker A 未改日志后端 | **PASS** | Worker A 改动集中在 Dashboard/QuickApi/stats/`ui_sandbox`；未改 `LogsView.vue`/`LogsDebugMenu.vue`/广播与日志 sink。 |
| 无 static 手工编辑 | **PASS** | 构建后 static 文件集与 diff 统计一致（`DashboardView.js` 70、其余 8 项同前）；`DashboardView.js` 含 `max-h-[14rem]`、无 🔄/↻。 |
| 未 commit/stage/push | **PASS** | 双仓 `git diff --cached --name-only` 均为空；HEAD 保持 `68a6504` / `4f600c0`。 |

## 5. Stats（v1 flat / v2 envelope / 幽灵行）

| 检查项 | 结果 | 证据 |
|---|---|---|
| v1 flat → v2 迁移 | PASS | `state.py:215-289`；`test_v1_flat_stats_loads_and_migrates_to_v2` 通过。 |
| v2 envelope 读取 | PASS | `state.py:234-273`；`test_v2_envelope_loads_models_and_daily` 通过（`models/` 归一、`daily` 载入）。 |
| 保留键过滤 | PASS | 后端 `is_reserved_stats_key`（`state.py:31-44`）+ `routes_system.py:101-112`；前端 `modelStats.ts:3-22,93-118`。 |
| 真实模型保留 | PASS | 沙盒 4 个真实模型正常；`test_ghost_keys_are_skipped_and_clean_structure_written` 通过。 |
| daily 迁移 | PASS | `state.py:246-265,320-328`；`test_runtime_persistence` 断言 daily 非空。 |
| 备份 / 原子写回 | PASS | `state.py:290-302,329`；`test_migration_write_failure_keeps_loaded_history` 通过。 |
| save 不写幽灵键 | PASS | `state.py:310-330` 双侧过滤；实测迁移后无 `totals/version`。 |
| `/stats` 过滤 | PASS | 实测 4 个真实模型，无 `models/daily`；`test_stats_response_filters_reserved_keys` 通过。 |
| 前端防御 / 无幽灵行 | PASS | `normalizeModelStats` 丢弃保留键；实测模型表 4 行、无幽灵行。 |

## 6. Dashboard Bento

| 检查项 | 结果 | 证据 |
|---|---|---|
| 4 指标 2×2 | PASS | `DashboardView.vue:212-270`；1440 截图为右侧 2×2。 |
| 实例卡（本轮微调） | PASS | `:141,146`；4 行可见、可滚动、未绑定无 u/0、已绑定显示 ID/u/N；右 2×2 无大片留白。 |
| 可用账号副文（本轮微调） | PASS | `:222` + `dashboardMetrics.ts:42-43,66-73`；多场景 `X+Y=available`。 |
| 429 冷却账号 | PASS | 卡片 footer `当前冷却中: N 个账号`；all_429 冷却 9。 |
| 总 Tokens | PASS | 24.7M == 模型表合计 == `/stats`；K/M 正确；无重复计入。 |
| Tokens 英文 | PASS | 可见「总 Tokens / 思考 / 缓存」，源码与构建产物无「令牌」。 |
| 1440/1024/768 | PASS | 三宽度 `scrollWidth==clientWidth`；1024/768 无页面级右溢出/重叠；长邮箱 `truncate`+`title`。 |

## 7. QuickApiExamples（四协议）

| 检查项 | 结果 | 证据 |
|---|---|---|
| 4 Tab 可点 | PASS | 实测 4 Tab 全部可切换。 |
| 未启用标记/提示 | PASS | 默认 Claude/Responses `(未启用)` + 温和提示；全开后标记消失。 |
| 四协议映射 | PASS | OpenAI `/v1` + `/v1/chat/completions`；Gemini 根 + `/v1beta/models/{model}:generateContent`；Claude 根 + `/v1/messages`；Responses `/v1` + `/v1/responses`；Auth/cURL/Python 对应正确。 |
| cURL 多行（本轮微调） | PASS | 四协议 body `-d '{\n…\n}'`；`dashboardHelpers.test.ts:221-229`；代码块 `overflow-x-auto`（N-7）。 |
| 默认模型/复制/对齐/双栏 URL | PASS | 默认 `gemini-3.7-flash`；复制反馈「✓ 已复制」；两代码框顶部同高（1440/1024）或上下堆叠（768）；底部双栏复制正常。 |

## 8. UI Debug（全站）

| 检查项 | 结果 | 证据 |
|---|---|---|
| 名称还原 | PASS | `UiDebugMenu.vue:132`、`SettingsView.vue:321`、`LogConfigCard.vue:137`、`schema.ts:362-367`；「界面调试」0 命中。 |
| 重置按钮（本轮微调） | PASS | `UiDebugMenu.vue:164-172` RotateCcw + `重置`，无 🔄/↻。 |
| 预设场景 | PASS | all_states（12/9、24.7M）、mass（48/6、277.7M）、all_429（1,046、success=0、冷却 9）、empty（0/0、暂无实例槽位）。 |
| 快捷动作 | PASS | +10/+50K、+1 429、协议全开/默认、重置；`/api/system/status` 实时同步。 |
| 刷新与 404 | PASS | `UiDebugMenu.vue:86-99` 并行刷新；`routes_debug_dashboard.py:22-33,45-49` ui_debug=false 404（单测）。 |

## 9. 日志页（纯增量，未改动）

| 检查项 | 结果 | 证据 |
|---|---|---|
| LogsView 仅 2 处改动 | PASS | `LogsView.vue:20,542`；本轮未改 LogsView 与日志截图。 |
| 菜单/4 按钮 | PASS | ui_debug=true 才显示；3 注入 + 1 清空。 |
| `/inject` 三类型 | PASS | all_levels=5、sanitize_test=1、long_payload=1。 |
| `/clear` 清 ring | PASS | 注入样例 0；latest_id 不重置、订阅者保留、归档保留。 |
| SSE/脱敏/导出 | PASS | SSE 实时；UI/SSE/导出均无原始假密钥（Bearer/Cookie/x-goog-api-key 为 `***`）。 |
| 长报文/原逻辑 | PASS | 详情 `pre-wrap` 换行；搜索/暂停/继续/导出/清屏正常；三宽度无页面溢出。 |

## 10. 门禁结果（目标区独立执行）

| 命令 | 结果 |
|---|---|
| `uv run ruff check .` | **PASS**：All checks passed! |
| `uv run pyright src tests` | **PASS**：0 errors, 0 warnings, 0 informations |
| `uv run pytest -q` | **PASS**：**1109 passed**，0 failed / 0 skipped，30.19s |
| `cd web && bun run lint` | **PASS**：eslint 0 warnings（`--max-warnings 0`） |
| `cd web && bun run type-check` | **PASS**：vue-tsc 0 错误 |
| `cd web && bun test` | **PASS**：**39 pass / 0 fail**，155 expect，5 files |
| `cd web && bun run build` | **PASS**：1788 modules，vite 4.52s；无构建错误 |
| `git diff --check` | **PASS**：exit 0（仅 CRLF 提示，无 whitespace error） |
| `git ls-files config.yaml`（双仓） | 均无输出；`defaults/config.yaml` 双仓均已跟踪 |
| `git diff --cached --name-only`（双仓） | 均为空 |
| HEAD 稳定性 | Target `68a6504` / Main `4f600c0`，审查前后未变 |
| 构建产物 | `DashboardView.js` 含 `max-h-[14rem]`、`total_tokens`、修复后 tooltip；无 🔄/↻；旧四项相加 0 命中 |

## 11. 沙盒实测记录

- 启动：`$env:AISTUDIO_UI_DEBUG='1'; $env:AISTUDIO_WEB_PASSWORD=''` + `uv run aistudio-api server --port 18623 --browser-port 18624`。
- 实例列表：三宽度 `clientH=224 / scrollH=504 / 行高 56 / 9 槽位 / 4 行可见`，`scrollTop` 280 后 #9 可见；未绑定 #2/#9 无 `u/0`；已绑定显示 ID+`u/N`。
- 可用账号：all_states `4+4=8/12`；mass `4+32=36/48`；all_429 `0+0=0/12`；empty `0+0=0/0`。
- 重置按钮：`重置` + SVG，无 🔄/↻（DOM 与源码/构建产物三重核对）。
- 四协议：4 Tab 可点；cURL body 多行（7/8/9/7 行）；Base/Endpoint/Auth/示例无回归；`pre` 为 `overflow-x-auto`（N-7）。
- 布局：1440/1024/768 Dashboard 与 Logs 均 `scrollWidth==clientWidth`，无页面级横向溢出/文字重叠；右侧 2×2 卡片无大片留白。
- Console：all_states 主态 0 error；仅 empty/全 429 等无 active 账号场景出现既有 `/api/accounts/active` 404（N-1）。
- 停止：`background_process.stop` 后 `NO_LISTEN_18623_18624`、无 `uv/aistudio` 进程；本轮临时目录已清理。

## 12. 截图

| 文件（双仓 `docs/dev/rebuild/domains/P8-api-ui/reviews/assets/`） | 内容 | SHA256 |
|---|---|---|
| `c2c-dashboard-overview-1440.png` | 1440×1495 全页（本轮重生成）：实例列表 4 行（#2 未绑定无 u/0）、可用账号 `8/12` + `实例中 4 · 空闲就绪 4 · 失效 2`、总 TOKENS 24.7M、Bento、四协议 Tab 与反代 URL、cURL 多行 body；147,635 bytes | `B108398694BC5220C01BC8885742C3EA56D5A4E092DA12856E2EB9AAD1956265` |
| `c2c-logs-debug-1440.png` | 1440 日志页（本轮未改）：顶部 UI Debug 菜单（4 按钮）、全级别注入、脱敏详情、超长报文行；101,502 bytes | `EE7F3E9BEF0444FF1E2851FF54E9C361D6812932C59BAFDCABC379676669C01F` |

双仓 SHA256 完全一致；报告使用相对路径；除以上两张外无其它截图入库。

## 13. 结论

- **Verdict：PASS** — P2-1 与此轮 5 项视觉微调全部 CLOSED；0 个未关闭 P0/P1/P2/Q；仅余 N 级观察。
- 总 Tokens 主数值 == `/stats` `totals.total_tokens` == 模型表 Tokens 汇总；实例列表恰好 4 行且可滚动；未绑定无幽灵 u/0；可用账号 `X+Y=available`；重置按钮无重复 Emoji；四协议 cURL 多行且映射无回归。
- 本轮**未 commit、未 stage、未 push**，等待用户审阅。
