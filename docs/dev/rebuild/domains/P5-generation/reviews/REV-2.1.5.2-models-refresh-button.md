# REV-2.1.5.2 — 设置页「刷新模型列表」按钮 + 模型发现 P2 打包返工

- **审查角色**：100% 独立只读 Reviewer（与 Worker 零上下文共享，全部结论以亲眼核验为准）
- **审查对象**：主工作区未提交改动（`git diff`，基线 `8daaffd`）
- **审查环境**：Windows / pwsh 7 / Python 3.13 / Ruff 0.17.1；前端 Vite + Vue 3 + bun
- **审查时间**：2026-11-20
- **结论**：**PASS**（0 × P0 / 0 × P1 / 8 × P2 / 4 × Q / 7 × N；无破坏性、无风控安全回归、无测试弱化）

范围摘要：前端设置页新增「刷新模型列表」标题栏按钮、`refreshModels()` 与 `ModelsRefreshResponse` 类型、static 产物重建；后端 P2-2/P2-4/P2-5/P2-6/P2-7/P2-8 与 Q-④ 闭环；Worker 三处自主改动（`_ensure_fallback_cache`、`ui_sandbox.models_refresh_payload`、`routes_system` 鸭子类型）均经独立审计。本轮未发现需要返工的 P0/P1；8 项 P2 全部为可跟踪的文案一致性、超时预算口径、测试缺口与既有遗留，均附 `file:line` 与证据。UI 现状经本人三分辨率 Sandbox 实测与源码逐行核对，**未发现 Worker 编造 UI 事实**。

---

## 1. 审查范围与零触碰核验

`git diff --name-only 8daaffd`（共 12 个文件，与 Worker 声称集合完全一致）：

```
src/aistudio_api/api/app.py
src/aistudio_api/api/routes_system.py
src/aistudio_api/api/ui_sandbox.py
src/aistudio_api/infrastructure/gateway/model_discovery.py
src/aistudio_api/static/assets/SettingsView.js
src/aistudio_api/static/assets/index.js
tests/unit/test_model_discovery_browserless.py
tests/unit/test_model_warmup_refresh.py
tests/unit/test_routes_system.py
web/src/api/system.ts
web/src/types/system.ts
web/src/views/SettingsView.vue
```

- **零触碰红线全部通过**：`REV-*.md`、`web/src/components/settings/SchedulingCard.vue`、`web/src/components/ui/Button.vue`、`web/src/assets/main.css`、`defaults/**`、`docs/changelog.md`、`AGENT.md` 的 `git status` 均为空。
- **无调试残留**：对全部新增行做 `console.log|print(|breakpoint|pdb|TODO|FIXME|XXX|debugger` 扫描，0 命中。
- **`git diff --check 8daaffd`**：退出码 0，无空白错误。
- **无隐私/密钥**：diff 与新增沙盒 mock 全部为虚构模型与固定中文字符串，无 Cookie/token/密钥。

---

## 2. UI 现状独立核验（不信任 Worker 核验表）

### 2.1 卡片标题栏结构与按钮落点（源码）

`web/src/views/SettingsView.vue:620-655`：

```vue
<div class="flex flex-col sm:flex-row sm:items-center justify-between gap-3 border-b border-gray-100 pb-4">
  <div class="flex items-center gap-2.5">           <!-- 标题区：Puzzle 图标 + h3「模型规则与安全策略」 -->
  <div class="flex items-center gap-2 flex-wrap">   <!-- 动作区（新增容器） -->
    <Button variant="secondary" size="sm" :loading="refreshingModels"
            title="立即重新拉取上游模型目录并清除失败退避（最长约 30 秒）" @click="refreshModels">
      <RefreshCw v-if="!refreshingModels" class="w-3.5 h-3.5" />
      <span>{{ refreshingModels ? '刷新中...' : '刷新模型列表' }}</span>
    </Button>
    <Button variant="primary" size="sm" :loading="isSaving('model')" @click="saveSection('model')"> 保存模型规则 </Button>
  </div>
</div>
```

- 按钮**位于 primary「保存模型规则」左侧**，DOM 顺序经浏览器实测为 `["刷新模型列表", "保存模型规则"]`。
- 卡片整体位于 `<template v-if="activeMode === 'visual'">` 内（`SettingsView.vue:584` 起），YAML 模式整张卡片（含按钮）不渲染 —— 实测确认。

### 2.2 先例样式比对（独立核验，非 Worker 口述）

- **同页先例**：`SettingsView.vue:507-513` 动作区使用完全相同的 `flex items-center gap-2 flex-wrap` 容器，内含 `variant="secondary" size="sm"` + `BookOpen w-3.5 h-3.5` 的「参数规范字典」按钮。
- **loading 先例**：`SettingsView.vue:547-557`「重置默认」为 `variant="secondary" size="sm" :loading="resetting"` + `<RotateCcw v-if="!resetting">` + `{{ resetting ? '重置中...' : '重置默认' }}`，与本次新增按钮的模式**逐字同构**；`Button.vue:37-40` 的 loading 态自带 `Loader2 w-4 h-4 animate-spin`，因此 `v-if="!refreshingModels"` 隐藏 `RefreshCw` 后只会存在 1 个 spinner，不会双图标。
- **标题栏容器先例**：`ApiKeyManagerCard.vue:143`、`LogConfigCard.vue:54`、`ProtocolAtgCard.vue:117`、`SchedulingCard.vue:662`、`SecurityBrowserCard.vue:55` 全部使用同一套 `flex flex-col sm:flex-row sm:items-center justify-between gap-3 border-b border-gray-100 pb-4`。

结论：新增按钮的落点、variant/size、图标尺寸、loading 文案、容器类名均与项目既有先例一致，**无自创样式**。

### 2.3 交互状态机与三态文案（源码 + 实测双证）

`SettingsView.vue:263-291`：

| 状态 | 判定 | 文案（逐字） | 实测结果 |
|---|---|---|---|
| 成功 | `res.error` 空 且 `source ∈ {live_page, live_signer}` | `模型列表已刷新：来源 {中文映射}，共 {N} 个模型` | 触发 `toast.success`，实测 `模型列表已刷新：来源 实时接口，共 10 个模型` |
| 降级 | `res.error` 空 且其他来源 | `模型列表已更新，但未拿到实时数据：当前来源 {中文映射}，共 {N} 个模型` | 触发 `toast.warning`，实测 `模型列表已更新，但未拿到实时数据：当前来源 本地快照，共 10 个模型` |
| 失败 | `res.error` 非空 或 请求抛错 | `模型列表刷新失败：{error}` | 触发 `toast.error`（红色边框），实测 `模型列表刷新失败：sandbox scenario: 无可用账号候选` |

- **来源中文映射**（`SettingsView.vue:263-269`）：`live_page=真实页面`、`live_signer=实时接口`、`live_http=实时接口（直连兜底）`、`snapshot=本地快照`、`fallback=内置兜底`；未知来源回退原始字符串，无 `undefined` 风险。
- **无确认弹窗**：`refreshModels()` 内无 `confirm()`；实测点击后请求立即发出。
- **并发保护**：`refreshModels.value` 为真时直接 return（`:272`），且按钮 `disabled`（`Button.vue:17` 的 `disabled || loading`）。
- **loading 实测**：点击后 250ms 抓取 DOM 得到 `btnText="刷新中..."`, `disabled=true`, `svg.animate-spin=1`, `svg=1`（`RefreshCw` 已隐藏），符合设计。

### 2.4 API/类型契约一致性

- `web/src/api/system.ts:57-61`：`refreshModels(): Promise<ModelsRefreshResponse>` → `POST /api/models/refresh`，无 body。
- `web/src/types/system.ts:32-36`：`{ source: string; model_count: number; error?: string }`。
- 后端 `routes_system.py:399-405` 返回 `{source, model_count}`，仅当 `error` 为真时附加 `error`，**与前端可选字段完全对齐**；前端只消费 `source / model_count / error`，未使用响应中不存在的字段。
- 鉴权：路由挂 `protected_router`（`routes_system.py:356`），`app.py:651` 统一注入 `require_web_auth`；缺失密码时 `dependencies.py:199-212` 返回 **401**，前端 `request()` 自动跳转登录。

### 2.5 YAML 模式

实测切到「YAML 源码」后 `refreshButtonVisible=false`、`modelCardPresent=false`、页面仅剩 1 个 textarea，符合「YAML 模式不渲染按钮」的要求。

---

## 3. P2 逐项闭环核验

| 项 | 要求 | 实测证据 | 判定 |
|---|---|---|---|
| **P2-2** | signer 路细化 `live_signer` 并入 `LIVE_SOURCES`、`DISCOVERY_TRUTH_SOURCES` 同步、磁盘路保持 `live_http` | `model_discovery.py:648` `LIVE_SOURCES={live_page,live_signer,live_http}`；`:658-660` `DISCOVERY_TRUTH_SOURCES` 含 `live_signer`；`:965` `_accept_live(..., source="live_signer")`；磁盘路仍 `:992` `source="live_http"`；单测 `test_model_discovery_browserless.py:185` 改为 `live_signer`，`:234` 保持 `live_http` | ✅ |
| **P2-4** | `_fetch_via_signer` 加 30s `asyncio.wait_for`，超时按非 403 瞬态继续候选 | `model_discovery.py:49` 常量 30.0；`:1338-1349` `wait_for` + `TimeoutError` → `RuntimeError`；signer 循环 `:969-980` 对 `ListModelsHttpError`/`Exception` 分流，`RuntimeError` 落入通用 `Exception` 继续下一候选，`abort_round` 仅在 403 置位 | ✅（预算口径见 P2-2） |
| **P2-6** | `reset_backoff` keyword、**锁内**清退避、删除公开 `reset_backoff()`、零 Shim | `model_discovery.py:808` keyword-only 参数；`:846-849` 在 `async with self._lock` **内**清 `_failure_streak`/`_next_retry_at`；原方法已删除；全库 `grep reset_backoff` 仅剩参数、docstring、`routes_system.py:385` 传参与测试，**0 残留调用** | ✅ |
| **P2-7** | 零候选轮记失败退避 | `model_discovery.py:1024` 无条件 `self._record_failure()`（删除 `attempted_any` 守卫）；单测 `test_model_discovery_browserless.py:644-660` 断言首轮 `streak==1`、`next_retry_at` 非空，且第二轮命中「退避期」且 streak 不再增长 | ✅ |
| **P2-8** | docstring 失实修正 | `model_discovery.py:244` 改为 `"Browserless sessions have no Page; discovery uses their signer transport."`，与实现一致 | ✅ |
| **Q-④** | signer 成功进入每日长间隔 | `model_discovery.py:653` `LIVE_REFRESH_SOURCES={live_page,live_signer}`；`app.py:150-159` `if source in LIVE_REFRESH_SOURCES: live_refresh_interval_sec`；`test_model_warmup_refresh.py:22-30` 参数化验证 `live_page`/`live_signer` 均为 86400，`:33` 验证 `fallback/snapshot/live_http` 为 600 | ✅（文档滞后见 P2-7） |
| **P2-5** | 路由级 4 项 + 循环异常兜底单测 | `test_routes_system.py:217`（未鉴权 401）、`:229`（成功结构）、`:262`（超时 error）、`:292`（异常 error 非 500）；`test_model_warmup_refresh.py:187-213`（CancelledError 透传 + 告警 + 600s 续跑） | ✅ |

---

## 4. Worker 自主改动安全审计

### 4.1 `_ensure_fallback_cache`（`model_discovery.py:733-743`）

- 仅在 `not self._cached_models` 时物化内置表；已有缓存（snapshot/live/fallback）时**原样返回**，不改变正常缓存语义。
- 物化时同步 `set_paid_models(...)`、`_last_fetched_at=now`、`last_source="fallback"`，与 `__init__` 脏快照回退（`:759-771`）与第 4 步冷启动兜底（`:1027-1036`）完全同构。
- 两个调用点均在「闸门命中」分支（`:844`、`:858`），此前该分支返回可能为空的 `_cached_models`（调用方拿到空列表）；改动是显式修正，不是语义漂移。
- 唯一瑕疵：`:836-843` 的锁外闸门段在「缓存为空」这一异常态下会无锁写共享状态；写入幂等、内容等价，且该状态在生产中不可达（失败轮的第 4 步必定填充缓存）。记为 **N-1**，不阻塞。

### 4.2 `ui_sandbox.models_refresh_payload`（`ui_sandbox.py:1331-1348`）

- 方法挂在 `UISandbox` 实例上；`RuntimeState.ui_sandbox` 默认 `None`（`state.py:600`），仅在 `lifespan` 的 `if ui_debug:` 分支赋值（`app.py:377-388`），退出时清空（`:414-415`）。生产路径 `ui_debug=False` → 该方法**不可达**。
- 场景映射：`empty` → `{source:"fallback", error:...}`（错误 toast 可复现）；`narrow_edge` → `{source:"snapshot"}`（降级 toast）；其余 → `{source:"live_signer"}`（成功 toast）。三态均可在离线沙盒复现，实测通过。
- 双保险：即使该 mock 缺失，`model_discovery.get_models` 在 ui_debug 下也会 `_ui_debug_active()` 直接返回 `sandbox_model_catalog()`（`model_discovery.py:820-823`），不会触网。

### 4.3 `routes_system.py` 鸭子类型接 mock（`:368-376`）

- `getattr(runtime_state, "ui_debug", False)` 先短路，且 `RuntimeState` 恒有该字段（`state.py:599`），`getattr` 默认值仅服务于测试里的 `SimpleNamespace`。
- **同文件先例**：`routes_system.py:296-311`（`/api/engine/health-check`）早已用相同的 `getattr(sandbox, ..., None)` + `callable` + `dict` 归一模式，本次只是沿用既有约定，非新范式。
- 生产路径 `ui_debug=False` 时 `sandbox=None`，`callable(None)` 为 False，直接落到真实 `model_discovery` 调用，**100% 不受影响**。

### 4.4 `_model_warmup_loop` 抽取（`app.py:187-202`，调用点 `:636`）

- 与旧内联循环（基线 `app.py:615-624`）逐句等价：`CancelledError` 直接 `raise`；其他异常 `warning` 后 `sleep(_model_warmup_interval_seconds("fallback"))`；无「绕过退避强刷」——异常后续跑的是 `_model_warmup_cycle → get_models(force_refresh=True)`，而 `get_models` 的闸门检查（`:836`）仍然生效（`force_refresh` 不越过闸门，只有 `reset_backoff` 越过，且仅管理端点传 True）。
- 单测 `test_model_warmup_refresh.py:187-213` 用 `pytest.raises(asyncio.CancelledError)` 坐实透传，断言 `sleeps==[600]` 与告警文本。

---

## 5. 测试真实性

- 新增用例逐条阅读，断言均有牙齿：
  - `test_models_refresh_requires_web_auth`（`:217`）：`settings.web_password` 置真后无凭据请求必须 401；路由不存在只会 404，**401 证明路由挂载 + 鉴权生效**。
  - `test_models_refresh_returns_source_and_count`（`:229`）：断言 `source/model_count` 结构、无 `error`、且透传 `force_refresh=True`、`reset_backoff=True`。
  - `test_models_refresh_timeout_returns_error_field`（`:262`）：把路由预算压到 0.01s、让 `get_models` 挂 5s，断言 200 + `超时` + `model_count` 为 int（**不是 500**）。
  - `test_models_refresh_failure_returns_error_field_not_500`（`:292`）：`RuntimeError` 断言 200 + `RuntimeError: upstream exploded` 落进 `error`。
  - `test_reset_backoff_bypasses_gate_and_recounts_failed_round`（`:621`）：预置 `streak=4`、`retry=+3600s`，若 `reset_backoff` 被忽略会命中闸门 → 断言 `streak==1` 失败；同时断言跑完一轮后重新进入退避（`streak=1`、`retry_at` 非空）。
  - `test_no_candidate_round_records_failure_and_gates_next_round`（`:644`）：无候选首轮 `streak==1`，次轮 `退避期` 且 `streak` 不变；恢复 `attempted_any` 守卫会立即失败。
  - `test_signer_timeout_falls_through_to_next_candidate`（`:664`）：首个 sender 挂 60s 并断言「超时必须被取消」（若未被取消会抛该断言），第二个 sender 200；断言 `last_source="live_signer"`、`last_error is None`、告警文本存在。
  - `test_warmup_loop_survives_cycle_exception`（`:187`）：首轮抛 `RuntimeError`、次轮抛 `CancelledError`，断言调用 2 次、只 sleep 一次 600s、告警出现；吞掉 `CancelledError` 或漏掉告警都会失败。
- **既有断言改写审计（未弱化）**：`test_manual_refresh_endpoint_resets_backoff_and_forces_refresh` 删除了 `seen["streak"] / seen["next_retry_at"]` 断言，原因是路由不再直接改服务状态，改为透传 `reset_backoff=True`；对应的「真的清零」改由服务级 `test_reset_backoff_bypasses_gate_and_recounts_failed_round` 用真实 `ModelDiscoveryService` 断言。**覆盖合起来等价且更贴近真实实现**，不是为过测试而删除断言。
- 统计：全量 **1713 passed**（基线记忆 1704，净增 9 = 路由 4 + discovery 3 + 循环 1 + `live_sources` 参数化净增 1）；三个受影响模块 55 passed。

---

## 6. 门禁真实输出

| 门禁 | 命令 | 实测输出 | 判定 |
|---|---|---|---|
| 依赖同步 | `uv sync --extra dev` | `Resolved 47 packages / Audited 47 packages` | ✅ |
| Ruff Lint | `uv run ruff check .` | `All checks passed!` | ✅ 0 error |
| Ruff 格式 | `uv run ruff format --check .` | `62 files would be reformatted, 329 files already formatted` | ⚠️ **存量红**（见 P2-8）；本 diff 未新增 |
| Pyright | `uv run pyright src tests` | `0 errors, 0 warnings, 0 informations` | ✅ |
| 全量测试 | `$env:PYTHONUTF8='1'; uv run pytest -q` | `1713 passed in 45.13s` | ✅ |
| 前端 Lint | `cd web; bun run lint`（`--max-warnings 0`） | 退出码 0，无告警输出 | ✅ 0 error / 0 warning |
| 前端类型 | `cd web; bun run type-check`（`vue-tsc -b`） | 退出码 0 | ✅ |
| 前端构建 | `cd web; bun run build` | `✓ built in 2.82s` | ✅ |
| Diff 空白 | `git diff --check 8daaffd` | 无输出，退出码 0 | ✅ |

**关于「ruff format 63 存量文件」的说法**：实测为 **62** 个未格式化文件（4 个 `.md` + 58 个 `.py`），且全部为**存量**——本 diff 触碰的文件中只有 `ui_sandbox.py` 在名单内，我用 `git show 8daaffd:...ui_sandbox.py` 单独跑 `ruff format --check` 确认**基线同样未格式化**；其格式差异位于 `:220-224`、`:1441-1500` 等与本轮改动无关的区域。Worker 说的「存量」成立，数字差 1（63 vs 62）记为 **N-3**。

**构建产物一致性**：`bun run build` 后 `src/aistudio_api/static/assets/SettingsView.js` 与 `index.js` 相对基线的 numstat 仍为 `3/3`、`1/1`（与 Worker 产物一致），产物中可见 `刷新模型列表`、`live_signer`、`refreshModels`。Windows 构建同时把 `static/index.html` 重写为 CRLF（内容逐字节等价），我已用 `git checkout HEAD -- src/aistudio_api/static/index.html` 还原为 Worker 的 LF 版本，工作树未引入额外改动（见 N-4）。

---

## 7. UI 沙盒实测（AGENT.md §10.2 强制）

启动方式：`uv run python main.py server --port 18371 --ui-debug`（`background_process` 托管，另以 18372 复跑取全页截图），仅访问 `127.0.0.1`，结束后 `stop`，两端口 `Test-NetConnection` 均失败、无 python 进程残留。

### 7.1 三分辨率几何实测

| 视口 | 按钮尺寸 | 动作区尺寸 | 与「保存模型规则」同排 | 页面横向溢出 | 结论 |
|---|---|---|---|---|---|
| 1440×900 | 118×30，`x=1141` | 242×30（`flex-wrap`） | 是（前者在左） | `scrollWidth==clientWidth==1440` | ✅ |
| 1024×900 | 118×30，`x=725` | 242×30 | 是 | `1024==1024` | ✅ |
| 768×900 | 118×30，`x=477` | 242×30（容器内未换行） | 是；标题栏仍为 `row`（`sm:` 640px 断点以上） | `768==768`，`right=595 < 768` | ✅ |

1440 实测计算样式：`background-color: rgb(255,255,255)`、`border: 1px solid …`、`padding: 6px 12px`、`font-size: 12px`、`gap: 6px` → 证实为 `variant=secondary / size=sm`；`title="立即重新拉取上游模型目录并清除失败退避（最长约 30 秒）"`；`svg` 数量 1（`RefreshCw`）。

### 7.2 状态与文案实测

- **loading**：临时在页面内把 `/api/models/refresh` 的 fetch 延迟 6s，点击后 250ms 读取 DOM：`刷新中...` / `disabled=true` / `svg.animate-spin=1` / 总 `svg=1`（无双图标）。
- **成功**：默认场景 `source=live_signer` → `模型列表已刷新：来源 实时接口，共 10 个模型`（success toast）。
- **降级**：`POST /api/debug/ui-scenario {"scenario":"narrow_edge"}` → `模型列表已更新，但未拿到实时数据：当前来源 本地快照，共 10 个模型`（warning toast）。
- **失败**：`{"scenario":"empty"}` → `模型列表刷新失败：sandbox scenario: 无可用账号候选`，toast class 为 `border-red-200 text-red-800`（error）。
- **控制台**：`playwright_browser_console_messages(level=error)` → `Total messages: 0 (Errors: 0, Warnings: 0)`。

### 7.3 截图归档（本人实测，未被 Worker 影响）

完整页面（全页，主证据）：

![1440 全页](assets/rev-2.1.5.2-fullpage-1440.png)
![768 全页](assets/rev-2.1.5.2-fullpage-768.png)

补充视口证据（卡片定位与三态）：

![1440 视口](assets/rev-2.1.5.2-settings-1440x900.png)
![1024 视口](assets/rev-2.1.5.2-settings-1024x900.png)
![768 视口](assets/rev-2.1.5.2-settings-768x900.png)
![成功 toast](assets/rev-2.1.5.2-success-toast-1440x900.png)
![失败 toast](assets/rev-2.1.5.2-error-toast-1440x900.png)

> 说明：Worker 已归档的 `ui-refresh-models-1440x900.png` 实际是 `1440×7852` 全页图，`ui-refresh-models-375x900.png` 实际是 `375×13125`，命名与真实尺寸不符（内容真实，非编造），记 **N-2**。

---

## 8. 遗留项登记

本轮之后仍未关闭的 P2/Q（逐项已确认仍存在于代码）：

| 编号 | 遗留项 | 代码现状（file:line） | 状态 |
|---|---|---|---|
| 旧 P2-1 → 本报告 P2-5 | discovery 与在途生成共用 signer worker，busy 槽位不排除 | `instance_manager.py:791-805` 仅做排序偏好，仍返回 busy 槽；`model_discovery.py:958-980` 不占 `active_requests` | 仍在 |
| 旧 P2-3 → 本报告 P2-6 | 磁盘兜底旧指纹 + 伪造 visit-id | `model_discovery.py:1394-1409`（`X-AIStudio-Visit-Id: v1_<uuid4>`、静态 `EXT_BIN`、裸 `httpx.AsyncClient`） | 仍在，待 VPS 确诊 |
| Q-① | 退避不持久化 | `_next_retry_at` 仅内存（`:1101`），`_save_snapshot` 只写 `version/fetched_at/models`（`:787-800`） | 仍在 |
| Q-② | 闸门连带挡 browser `live_page` | 闸门位于候选选择之前（`:836-844`），对全部来源一视同仁 | 仍在 |
| Q-③ | signer 并发语义未审计 | `waa-signer/cmd/waa-signer/main.go:1263-1387` 不在本仓审查范围 | 仍在 |

**偏离 6（长间隔注释）核验为真**：`model_defaults.py:2102-2104` 仍写「`live_refresh_interval_sec` 只用于 `last_source == "live_page"`…其他来源（fallback/snapshot/live_http）」，与 Q-④ 后的实现不符。作为对照，另有三处**同类滞后文案**因本轮零触碰红线不可修改，登记待后续步骤：`defaults/config.schema.json:353`、`defaults/config.yaml:148-149`、`web/src/components/settings/SchedulingCard.vue:1493`（「真实页面发现成功后的刷新间隔」）。

---

## 9. P0 / P1 / P2 / Q / N 清单

### P0（破坏性 / 风控安全，必须返工）
无。

### P1（提交前应修）
无。

### P2（可跟踪改进）

- **P2-1（新增）** `SettingsView.vue:278-284` 与 `model_discovery.py:648` 口径不一致：`live_http` 属于后端 `LIVE_SOURCES`（真实 live 成功、写入快照），但前端成功分支只认 `live_page/live_signer`，会落入「**未拿到实时数据**」的 warning；而 `MODEL_SOURCE_LABELS` 又把 `live_http` 标注为「实时接口（直连兜底）」，同一条 toast 自相矛盾。建议把 `live_http` 纳入成功分支（成功文案本身已能表达「直连兜底」来源），或改写降级文案。无数据/功能影响，属文案准确性。
- **P2-2（新增）** `model_discovery.py:49` 的 signer 单次超时 `30.0s` 与 `routes_system.py:48` 的整轮预算 `30.0s` **等值**；`:45-48` 注释声称该超时「不占用整段预算、可回落到下一候选」，但在管理端点上首个卡死候选就会吃满 30s 预算，健康候选来不及尝试（预热循环无外层预算，行为符合注释）。建议单次超时降到 10s 或按候选数分配预算，并同步注释。
- **P2-3（新增）** 两处新增分支零单测：`model_discovery.py:733-743` 的 `_ensure_fallback_cache` **物化分支**（缓存为空 + 闸门命中）无用例（现有用例只覆盖「缓存非空原样返回」）；`ui_sandbox.py:1331-1348` 的 `models_refresh_payload` 与其路由分支 `routes_system.py:367-376` 无用例。风险低（均只在异常态/沙盒态可达），建议补 1-2 个用例防回归。
- **P2-4（新增，风控加固）** 手动刷新端点无服务端最小间隔：`routes_system.py:357-405` 每次调用都 `reset_backoff=True` 强制整轮 fan-out（signer 各会话 + 磁盘候选），按钮把「强制清退避」变成一键操作（`SettingsView.vue:272` 仅做前端防抖）。在 Google 风控敏感场景下，运维连点会把已累积的 24h 退避反复打回 600s。建议增加服务端短冷却（如 60s 内重复调用直接返回上次结果/`retry_in`）。
- **P2-5（遗留 P2-1）** discovery 与在途生成共享 signer worker，busy 槽位不排除：`instance_manager.py:791-805`、`model_discovery.py:958-980`。建议 `active_requests == 0` 过滤或全 busy 时跳过本轮。
- **P2-6（遗留 P2-3）** 磁盘 `auth.json` 兜底仍是裸 `httpx` + 伪造 visit-id + 静态 ext-bin：`model_discovery.py:1394-1409`，待 VPS 实测 403 根因确诊。
- **P2-7（口径滞后）** `model_defaults.py:2102-2104` docstring 未随 Q-④ 更新（可改未改）；`defaults/config.schema.json:353`、`defaults/config.yaml:148-149`、`SchedulingCard.vue:1493` 同类文案受本轮零触碰约束，需后续单独步骤统一为「live_page / live_signer 成功用长间隔」。
- **P2-8（存量）** 全仓 `uv run ruff format --check .` 红：62 个文件（含此前即未格式化的 `ui_sandbox.py`），本 diff 未新增；建议单开一个纯格式化提交收口，避免与功能改动混杂。

### Q（疑问）

- **Q-1** 退避状态不持久化（`model_discovery.py:1101`、`:787-800`）：崩溃重启会清空退避，是否需要在快照里持久化 `failure_streak/next_retry_at`？
- **Q-2** 退避闸门对全部来源生效（`:836-844`），会连带挡住 browser 引擎的 `live_page` 发现；混合引擎部署是否接受该耦合？
- **Q-3** signer 单 worker 对「ListModels 与 GenerateContent 并发」的语义（排队/拒绝/复用）未审计（`waa-signer/.../main.go:1263-1387`），P2-5 严重度取决于此。
- **Q-4** `live_http`（磁盘直连成功）在产品口径上算「实时成功」还是「降级兜底」？直接决定 P2-1 的修法；建议与 Q-④ 的 `LIVE_REFRESH_SOURCES` 口径一并确认。

### N（备注）

- **N-1** `_ensure_fallback_cache` 的锁外闸门段在「缓存为空」异常态下无锁写共享状态，写入幂等且生产不可达（失败轮第 4 步必填充缓存），不影响正确性。
- **N-2** Worker 截图命名与实际尺寸不符（`ui-refresh-models-1440x900.png` 实为 1440×7852；`375x900` 实为 375×13125），内容真实非编造。
- **N-3** Worker 称「ruff format 63 存量文件」，实测 62（4 md + 58 py），差异可能来自统计时刻；「存量」判断成立。
- **N-4** Windows `bun run build` 会把 `static/index.html` 写成 CRLF（内容等价）；我已还原为基线 LF 版本，工作树无额外改动。
- **N-5** `test_warmup_loop_survives_cycle_exception` 的 `sleeps == [600]` 依赖运行期 `config.yaml` 的 `fallback_refresh_interval_sec`（本机默认 600 通过）；若某环境自定义该值会误红，建议注入 `settings` 或 mock 配置读取。
- **N-6** `routes_system.py:367-376` 的鸭子类型接 mock 与同文件 `:299-313`（engine health）既有先例一致，且 `ui_debug=False` 短路，生产路径安全。
- **N-7** 旧断言改写经逐条比对为「职责迁移」而非弱化：路由不再直接改服务状态，服务级用例以真实 `ModelDiscoveryService` 断言清零与重计退避，覆盖等价且更真实。

---

## 10. 只读声明

本次审查为 100% 只读。除本报告 `docs/dev/rebuild/domains/P5-generation/reviews/REV-2.1.5.2-models-refresh-button.md` 与该目录 `assets/` 下 `rev-2.1.5.2-*.png` 截图外，未修改任何源码、测试、配置或文档。

- 实跑 `bun run build` 会重写 `src/aistudio_api/static/`；其中 `SettingsView.js` 与 `index.js` 产物与 Worker 版本一致（numstat 不变），`index.html` 因 Windows 换行被重写为 CRLF，已用 `git checkout HEAD -- src/aistudio_api/static/index.html` 还原，最终 `git diff` 与审查开始时一致。
- 实机服务仅以 `--ui-debug` 沙盒启动（端口 18371/18372），仅访问 `127.0.0.1`；结束时均已 `stop`，端口 `Test-NetConnection` 失败、无进程残留，未访问真实 Google / 真实账号 / 外网。
- 报告未预先填入任何证据：上述门禁输出、测试统计、几何尺寸与 toast 文案均为本轮实跑/实采结果。
