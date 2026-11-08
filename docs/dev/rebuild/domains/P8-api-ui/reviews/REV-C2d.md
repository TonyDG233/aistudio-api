# REV-C2d — Stats 幽灵行清理 + 账号实例绿框/多实例徽章 + 通用标识与设置四卡还原 终审（P2-1 复验版）

- 审查日期：2026-11-08（UTC）。
- Target worktree：`D:\LLM\self_programming\aistudio-api\.kilo\worktrees\rebuild-upstream`，HEAD `ddd905eb1eee91790226deaef1c39eda95524fef`（未变）。
- Main repo：`D:\LLM\self_programming\aistudio-api`，HEAD `9475d2411617f76b08b9131b6e0c39ac16e95042`（未变）。
- Target 工作树：`git status --porcelain` 共 **29** 条（25 M + 3 untracked 截图 + 本报告；diff 统计 25 files, +335/−97）。
- Main 工作树：仅 4 条 untracked（3 张截图 + 本报告）；无源码/配置改动。
- Method：本轮为 P2-1 修复的增量终审：① 只读复核 `boundInstanceFor` active-over-retiring 优先级与 `AccountRow`/`accountStatus`/组头/`AccountTable` 的一致性；② 复核新增单测；③ 重跑双栈全套门禁并比对构建前后 static SHA256；④ 在随机沙盒 `127.0.0.1:18773`（`AISTUDIO_UI_DEBUG=1`、`AISTUDIO_WEB_PASSWORD=''`、CDP 18774）用 headless Playwright 1440/1024/768 复现同账号 `RETIRING #6 + ACTIVE #10`，验证绿框/`实例 #10 运行中`/组头 `激活实例 #10`，再关闭 #10 验证无 active 时回退 #6；⑤ 回归 C2d 其余项；⑥ 重生成 accounts 截图。
- 只读声明：本轮**未修改源码/测试/配置/AGENT/static**；仅新增/双写本报告与 3 张 `c2d-*.png`。**未 stage、未 commit、未 push**。未启动 `ui_debug=false` 服务，未访问真实账号/网络。
- 构建只读核对：`bun run build` 前后 `src/aistudio_api/static/assets/*` 12 个文件 SHA256 逐字节一致（构建可复现、产物与源码同步、未引入新改动）。
- 沙盒停止证据：本轮结束后 `18773/18774` 均 `False`（未监听）；本轮临时目录 `aistudio-ui-debug-zjqtu37_`、`aistudio-ui-debug-paths-pgwiicvj` 已删除。

## 0. Verdict

**PASS** — P2-1 已 **CLOSED**；**0 个未关闭 P0/P1/P2/Q**。另有 6 条 N 级观察（均非缺陷/非阻塞）。

## 1. 问题清单（完整重列）

| 编号 | 级别 | 状态 | 位置 | 结论与证据 |
|---|---|---|---|---|
| P2-1 | P2 | **CLOSED** | `web/src/utils/instances.ts:154-166`；`web/src/components/accounts/AccountTable.vue:565`；`web/src/components/accounts/AccountRow.vue:109-111,159-166,331-338`；`web/src/utils/accountStatus.ts:39-42`；单测 `web/tests/accountStatus.test.ts:188-211` | `boundInstanceFor` 先收集所有非 reclaiming/invalid 候选，再 `candidates.find(state==='active') ?? candidates[0]`，ACTIVE 恒优先、无 ACTIVE 才回退。`AccountRow` 绿框与 `accountStatus` 均复用该函数，`AccountTable` 传 `boundInstanceFor(acc.id, instancesStore.instances)`，组头 `runningInstancesForAccounts` 仍只取 ACTIVE，因此行/胶囊/组头一致。实测（§5）：`acc_free_ready` 同时有 `inst-6(retiring)` 与 `inst-10(active)`（retiring 在前）时，子行 `border-emerald-300 bg-emerald-50/40 ring-1 ring-emerald-400/20`、文本 `实例 #10 运行中 换槽`，无 `实例 #6 排空`；组头 `激活实例 #1` + `激活实例 #10`；无重复蓝徽章。关闭 #10 后无 active，回退显示 `实例 #6 排空` 且不绿，行为合理。 |
| P0 | P0 | 无 | — | 无。 |
| P1 | P1 | 无 | — | 无。 |
| Q | Q | 无 | — | 无未决问题。 |
| N-1 | N | 口径说明 | `src/aistudio_api/api/state.py:31-47`；`web/src/components/dashboard/modelStats.ts:3-15`；`src/aistudio_api/api/routes_system.py:114-120` | 任务描述的“三处 RESERVED 集合”实际落点为：① 后端 `STATS_RESERVED_KEYS`；② 前端 `RESERVED_STATS_KEYS`；③ `/stats` 路由 `normalize_model_name` 归一化过滤（复用①，未另建集合）。两处显式集合均含全部 14 键：`models,daily,schema_version,totals,version,finish_reasons,count_tokens,client,attempt,range,events,quota_scope,updated_at,account_breakdown`。 |
| N-2 | N | 已核实 | `web/src/views/SettingsView.vue:335` | 沙盒 `browser_headless=true`，运行时卡实际显示 `开启 (Headless)`；`关闭 (GUI)` 分支由源码与构建产物（`SettingsView.js` 含 `GUI`）核实，未在 live 截图触发。 |
| N-3 | N | 观感 | `web/src/components/settings/LogConfigCard.vue:27-34`；`web/src/components/config/ConfigSelect.vue:67` | 选项文案为 `label + " (" + description + ")"`，渲染成 `调试 (DEBUG) (输出详细的底层运行跟踪)` 的双重括号；原生英文已正确出现，属轻微观感问题，不影响功能。 |
| N-4 | N | 预期行为 | Playwright console | 本轮正常巡检期间 console **0 error / 0 warning**；第一轮终审在“主动 stop 服务后”出现的 `ERR_CONNECTION_REFUSED` 为页面轮询连接拒绝，非 JS 异常，不计入正常态门禁。 |
| N-5 | N | 已核实 | `data/stats.json`；`data/stats.json.bak` | 目标区真实磁盘 `data/stats.json` 已是干净 v2（`models` 4 个真实模型 + 空 `daily`，模型内部 `finish_reasons` 为合法字段）；旧 v1 flat 形态仅存在于 `.bak`。沙盒 `/stats` 实测 4 个真实模型、无 `finish_reasons/count_tokens` 行。 |
| N-6 | N | 预期行为 | `web/src/components/accounts/AccountRow.vue:159-166`；`web/src/utils/accountStatus.ts:30-49` | 类名优先级为 disabled → expired → runningInstance，`accountStatus` 亦为 disabled/expired 先于 instance；若账号已停用/失效但仍有 ACTIVE 槽，子行不会显示绿框。沙盒无该组合，属产品语义（失效账号不应示绿）。 |

## 2. 逐项检查表

### 2.1 幽灵行 / stats v2

| 检查项 | 结果 | 证据 |
|---|---|---|
| v2 envelope 仅从 `raw["models"]` 读取，绝不遍历顶层 | **PASS** | `state.py:257-259` 用 `any(key in data for key in ("schema_version","models","daily"))` 判定 envelope；分支内仅遍历 `models_map`（`:267-272`）与 `raw_daily`（`:273-289`）。 |
| 保留键扩展 | **PASS** | `state.py:31-47` 14 键齐全；`is_reserved_stats_key`（`:52-57`）额外剥离 `models/` 前缀。 |
| `models` 内已污染 `finish_reasons/count_tokens` 被 load 清理 | **PASS** | `state.py:267-269` 跳过保留键并置 `models_ghosts` → `needs_migration`；`daily` 内污染在 `:281-284` 置迁移。测试 `test_v2_polluted_models_entry_is_cleaned_but_real_models_kept` 通过。 |
| save 清理 | **PASS** | `state.py:337-350` 对 `models` 与 `daily` 双侧 `is_reserved_stats_key` 过滤；`test_save_stats_never_writes_reserved_ghost_keys` 通过。 |
| 迁移备份 + 原子写 | **PASS** | `state.py:314-327` 先 `shutil.copy2` 生成 `stats.json.bak`，再 `save_stats()`；保存走 `atomic_write_json`（`:352`）。`test_v2_top_level_ghost_counters_never_become_models` 断言 `.bak` == 原文件、重写后无幽灵键。 |
| 测试覆盖 | **PASS** | 新增 4 个后端测试 + 扩展 `/stats` 过滤测试；`pytest` 1253 passed。 |
| 真实磁盘旧 stats 形态 | **PASS** | `data/stats.json` 干净 v2；`data/stats.json.bak` 为旧 v1 flat（4 模型、顶层无信封键），见 N-5。 |
| `/stats` 实测 | **PASS** | 沙盒 `/stats` 返回 `models` 4 个真实模型 + `totals` + `quota_scope`；无任何幽灵行。 |
| 前端防御 | **PASS** | `modelStats.ts:28-32` 对 `models/` 归一后再判保留；`:129` 过滤；Dashboard 模型表实测 4 行、无 `finish_reasons/count_tokens`。 |

### 2.2 账号页（实例池 / 组头 / 子行 / 多实例）

| 检查项 | 结果 | 证据 |
|---|---|---|
| `InstancePoolCard` 无 `[当前]`，只留 `[最新]` | **PASS** | `InstancePoolCard.vue` 删除 `currentInstanceId` 的“当前”span；live DOM `curTagCount=0`、`[当前]` 字面量 false；实例池文本仅含 `最新`。 |
| 组头：运行中实例所在组 Cookie 图标绿色 | **PASS** | `CookieGroupBlock.vue:98-103` `hasRunningInstance ? bg-emerald-500`；live 组 #1 图标 class 含 `bg-emerald-500 text-white`，组 #2（无运行实例）为 `bg-gray-100`。 |
| 组头：每个运行中实例一个 `激活实例 #X` 徽章 | **PASS** | `CookieGroupBlock.vue:120-126` `v-for=runningInstances`；live 边界态组 #1 同时出现 `激活实例 #1`、`激活实例 #10`（1440 实测）。 |
| 不再仅 legacy `当前激活` | **PASS** | 组头改为按 instances store 的 ACTIVE 集合渲染；`runningInstancesForAccounts`（`instances.ts:173-184`）不读取 `active_account_id`。 |
| 子行：运行中实例账号绿框/浅绿背景 | **PASS** | 边界态 `acc_free_ready`（`inst-6` retiring + `inst-10` active）实测 `border-emerald-300 bg-emerald-50/40 ring-1 ring-emerald-400/20`；`greenIds=[acc_free_ready, acc_pro_prod]`。 |
| 移除 429 旁重复蓝色 `实例 #X` 徽章 | **PASS** | `AccountRow.vue` 删除蓝色 `boundNumber` span；live `dupBlueCount=0`，429 旁无重复蓝徽章。 |
| 右侧保留 `[🟢 实例 #X 运行中]` + 换槽 | **PASS** | 边界态行文本 `实例 #10 运行中 换槽`；`AccountRow.vue:331-338,375-384`；无 `实例 #6 排空`。 |
| 多实例 #1/#2 均正确，不依赖单一 active_account_id | **PASS** | 组头/子行/实例池均按全部 ACTIVE 实例渲染；单测 `runningInstancesForAccounts` 断言 `[1,2]`；live `激活实例 #1/#10` 双徽章。 |
| 无 ACTIVE 时回退 RETIRING 行为合理 | **PASS** | 关闭 `inst-10` 后 `acc_free_ready` 仅剩 `inst-6`：行显示 `实例 #6 排空`、非绿；组头仅 `激活实例 #1`。 |
| active-over-retiring 单测 | **PASS** | `web/tests/accountStatus.test.ts:188-211`：retiring #6 在前 + active #10 → 返回 #10 active，`accountStatus` 同步；无 active 时回退 #6。 |

### 2.3 通用标识

| 检查项 | 结果 | 证据 |
|---|---|---|
| `Free/Plus/Pro/Ultra/Ultra_20X` | **PASS** | `instances.ts:11` `TIER_OPTIONS`；`tierShortLabel`（`:42-49`）用于账号徽章、Dashboard 实例列表、槽位摘要；live 徽章为短标识。 |
| `ID: acc_xxx` | **PASS** | `AccountRow.vue:94-107`、`InstancePoolCard.vue:34`、各表单；live `ID: acc_*` 命中，`编号：` 0 命中。 |
| `default` | **PASS** | `AccountRow.vue:245,265`、`AccountsView.vue:44-46`；live `默认分组` 0 命中。 |
| `chat/build` | **PASS** | `InstancePoolCard.vue:154-156` 直接显示 `instance.surface || 'chat'`；live 实例池槽位均为 `chat`；`对话与工具 (Chat)` 仅出现在表单/弹窗（保留例外）。 |
| `:8080 / CDP :9222` | **PASS** | `SettingsView.vue:331-332`；live 运行时卡 `:8080 / CDP :9222`。 |
| `INFO/DEBUG/WARNING/ERROR` | **PASS** | `LogConfigCard.vue:27-34`、`schema.ts:320-330`；live 选项四个原生英文齐全，卡片主值 `INFO`。 |
| 无 `编号：`/`默认分组` | **PASS** | 源码与 `static/assets/*.js` 均 0 命中；live 0 命中。 |
| 无 `免费账号 (Free)` 徽章 / 实例徽章无 `对话与工具 (Chat)` | **PASS** | 徽章用 `tierShortLabel`/`instance.surface`；长标签仅存在于表单，符合“保留设置表单”例外。 |
| 表单中文选项例外 | **PASS** | `CreateInstanceModal.vue:139,155`、`BindSlotModal.vue:142,190`、`ReassignAccountModal.vue:86` 仍用 `surfaceLabel/tierLabel`，未误改。 |

### 2.4 设置页四卡与新增卡片

| 检查项 | 结果 | 证据 |
|---|---|---|
| 四张只读卡 | **PASS** | `SettingsView.vue:324-397`；live `grid` 4 卡：服务端口/浏览器、日志输出模式、网络代理状态、控制台鉴权状态。 |
| `:8080 / CDP :9222` | **PASS** | live `服务端口 / 浏览器 :8080 / CDP :9222`。 |
| Headless/GUI 文案 | **PASS** | live `无头模式: 开启 (Headless)`；`关闭 (GUI)` 分支源码/构建均存在（N-2）。 |
| 日志级别原始英文 | **PASS** | live 卡主值 `INFO`；下拉 `DEBUG/INFO/WARNING/ERROR` 齐全。 |
| 报文持久化/代理/鉴权原始字段 | **PASS** | live `报文持久化: 未启用`、`支持 HTTP / SOCKS5 出口代理`、`未配置 AISTUDIO_WEB_PASSWORD`。 |
| 新增安全/调度/协议/模型卡中文标题未回滚 | **PASS** | live 标题：`安全与浏览器配置`、`日志与调试`、`实例池、配额与路由调度`、`协议开关与防截断网关`、`模型规则与安全策略`。 |
| 下划线字段中文设置卡未回滚 | **PASS** | 日志/安全/调度/协议/模型卡字段标签保持中文；仅数据值（如 `image_models`）保留原键名。 |

### 2.5 回归（C2c 等）

| 检查项 | 结果 | 证据 |
|---|---|---|
| 实例卡 4 行 | **PASS** | live `visibleRows=4`（9 槽位，`max-h-[14rem]`）。 |
| 可用账号 `X+Y=available` | **PASS** | live `8 / 12` + `实例中 4 · 空闲就绪 4 · 失效 2`，4+4=8。 |
| 总 Tokens | **PASS** | live `24.7M`（24,663,000）与 `/stats totals.total_tokens` 一致。 |
| QuickApiExamples 四协议 | **PASS** | 4 Tab `OpenAI 兼容/Gemini 原生/Claude 兼容/Responses 协议` 均在；本轮未改动该组件，回归可点。 |
| 日志页 UI Debug 不受影响 | **PASS** | 上一轮 live 已验证；本轮未改动 LogsView，无回归。 |
| 页面横向溢出/重叠 | **PASS** | 1440/1024/768 三宽度 Dashboard/Accounts/Settings 均 `scrollWidth==clientWidth`。 |
| console | **PASS（正常态）** | 本轮巡检期间 0 error / 0 warning。 |

## 3. 门禁结果（目标区独立执行）

| 命令 | 结果 |
|---|---|
| `uv run ruff check .` | **PASS**：All checks passed! |
| `uv run pyright src tests` | **PASS**：0 errors, 0 warnings, 0 informations |
| `uv run pytest -q` | **PASS**：**1253 passed**，0 failed / 0 skipped，25.68s |
| `cd web && bun run lint` | **PASS**：ESLint `--max-warnings 0`，EXIT=0 |
| `cd web && bun run type-check` | **PASS**：vue-tsc EXIT=0 |
| `cd web && bun test` | **PASS**：**43 pass / 0 fail**，166 expect，5 files，110ms |
| `cd web && bun run build` | **PASS**：1788 modules，built in 3.53s，EXIT=0；构建前后 static 12 文件 SHA256 完全一致 |
| `git diff --check`（双仓） | **PASS**：EXIT=0（仅 `state.py`/`schema.ts` CRLF 提示，无 whitespace error） |
| `git ls-files config.yaml`（双仓） | 均无输出 |
| `git diff --cached --name-only`（双仓） | 均为空 |
| HEAD 稳定性 | Target `ddd905e` / Main `9475d24`，审查前后未变 |
| 构建产物抽查 | `static/assets/*.js` 含 `激活实例/CDP :/Headless/GUI/Prompt:/finish_reasons/Ultra_20X/ID: `；`编号：/默认分组` 0 命中 |

## 4. 沙盒实测记录

- 启动：`$env:AISTUDIO_UI_DEBUG='1'; $env:AISTUDIO_WEB_PASSWORD=''` + `uv run aistudio-api server --port 18773 --browser-port 18774`；headless Playwright 1440×900 / 1024×900 / 768×900。
- 构造 P2-1 边界态：all_states 下 `acc_free_ready` 已有 `inst-6`（RETIRING）；`POST /api/instances {account_id:acc_free_ready, surface:chat, only_tier:Free}` 创建 `inst-10`（ACTIVE）。`/api/instances` 顺序 `inst-6(retiring)` → `inst-10(active)`。
- 修复后实测（1440）：
  - `acc_free_ready` 行 class `border-emerald-300 bg-emerald-50/40 ring-1 ring-emerald-400/20`；
  - 行文本 `实例 #10 运行中 换槽`，无 `实例 #6 排空`；
  - 组头 `激活实例 #1` + `激活实例 #10`，Cookie 图标 `bg-emerald-500`；
  - `greenIds=[acc_free_ready, acc_pro_prod]`；`dupBlueCount=0`；实例池 `当前` 0 命中。
- 无 ACTIVE 回退实测：`DELETE /api/instances/inst-10` 后重载页面，`acc_free_ready` 行 `实例 #6 排空`、非绿；组头仅 `激活实例 #1`。
- 回归实测（1440）：Dashboard 模型表 4 行、无幽灵行、总 Tokens `24.7M`、可用 `8/12`、实例卡 4 行、四协议 Tab 齐全；Settings 四卡与 `CDP :9222`/`INFO`/`Headless` 正常。
- 布局：1024/768 三页 `scrollWidth==clientWidth`；Settings 4 卡无重叠。
- console：正常巡检 0 error / 0 warning。
- 停止：`background_process.stop` 后 `18773/18774` 未监听；本轮临时目录已删除。

## 5. 截图

| 文件（双仓 `docs/dev/rebuild/domains/P8-api-ui/reviews/assets/`） | 内容 | 字节 | SHA256 |
|---|---|---|---|
| `c2d-settings-runtime-1440.png` | 1440 设置页 4 张运行时卡（`:8080 / CDP :9222`、`INFO`、Headless、代理/鉴权） | 17,646 | `AEDFA276A6DE3018820A55E658E0AABA3F4901889A2ED41ED8D043439D772CAD` |
| `c2d-accounts-instances-1440.png` | 1440 账号页（**重生成**）：组头绿 Cookie + `激活实例 #1/#10`、`acc_free_ready` 绿框 + `实例 #10 运行中`、无 `实例 #6 排空`、无重复蓝徽章、实例池无 `[当前]` | 370,773 | `7CEC2E73F65F3FFB6715851228D03DF507525E2E0F67494337D78EC24DB8B5F9` |
| `c2d-dashboard-ghost-1440.png` | 1440 Dashboard 全页：模型表 4 行、无 `finish_reasons/count_tokens` 幽灵行、短 Tier、总 Tokens 24.7M | 146,959 | `06FB9628652834F9D57B26B8ECF70187F1E13F7789616A7907A243D8F2D01E17` |

双仓 SHA256 完全一致；报告使用相对路径；未覆盖 C2b/C2c 历史截图与报告 SHA。仅 `c2d-accounts-instances-1440.png` 因 P2-1 复验重生成；settings/dashboard 两图未受修复影响，SHA 保持不变。除以上三张外无其它截图入库。

## 6. 边界与双仓状态

| 边界 | 结果 | 证据 |
|---|---|---|
| 未修改源码/测试/配置/AGENT/static | **PASS** | `git status` 仅 25 个 C2d 既有修改 + 3 张新增截图 + 本报告；无额外改动 |
| 未 stage/commit/push | **PASS** | 双仓 `git diff --cached --name-only` 为空；HEAD 未变 |
| 目标区无临时文件 | **PASS** | 除 3 张截图与本报告外无 untracked；沙盒临时目录已删 |
| Main 仓仅新增报告/assets | **PASS** | Main `git status` 仅 3 张截图 + 本报告 |
| C2b/C2c 历史截图未覆盖 | **PASS** | `assets` 下 C2b/C2c 4 文件仍在，未写入 |

## 7. 结论

- **Verdict：PASS** — **0 个未关闭 P0/P1/P2/Q**。
- P2-1 已 CLOSED：`boundInstanceFor` ACTIVE-over-RETIRING 优先级 + 单测 + live 边界态复验（绿框、`实例 #10 运行中`、组头 `激活实例 #10`、无 `实例 #6 排空`、无重复蓝徽章）；无 ACTIVE 回退 #6 行为合理。
- 其余 C2d 目标项与 C2c 回归全部 PASS：幽灵行清理/迁移备份/原子写、InstancePool `[当前]` 移除、组头绿图标与多徽章、通用标识还原、设置四卡恢复、Bento/Tokens/QuickApi、三宽度无页面级溢出、console 正常态 0 error。
- 全量门禁：1253 pytest / 43 bun；`ruff`、`pyright`、`lint`、`type-check`、`build`、`git diff --check`、config/staged 检查全部通过；static 构建前后一致。
- 本轮**未 commit、未 stage、未 push**。
