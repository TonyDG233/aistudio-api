# 08-08 上游 99c38ba 全量 diff 审计（逐文件）

状态：**独立只读审计产物**。本次不修复、不改代码/测试/配置、不提交、不推送、不访问真实账号/IP/Google、不使用 Agent Manager、不派生 subagent。本文不宣告 handoff，不宣告 Gate 0 通过。

本文与 `08-07-upstream-99c38ba-reaudit.md` 的关系：08-07 是领域级初审；本文以 `git diff` 逐文件重做为事实源，并单列 §9 指出 08-07/10/12/13 的漏项与错项。凡本文与 08-07 冲突，以本文引用的上游代码为准，但**裁决权仍归用户**（见 §6）。

---

## 0. 审计范围与可复现方法

### 0.1 审计对象

| 项 | 值 |
|---|---|
| 仓库 | `D:\LLM\self_programming\aistudio-api` |
| 工作分支 / HEAD | `rebuild` @ `41394c1c9a27e58728b1af9518ceb5ac7bf30bcc` |
| 旧基线（仅作 diff 左端） | `4623109b97d14db30e304005fe1d52c29a46a7c2` |
| 新上游（diff 右端） | `99c38baefc84d8cb66b05c28ede8a49ce050bfb2`（= `upstream/master`） |
| 范围 | `git diff 4623109..99c38ba`，76 文件，+4200 / −1190 |
| 工作区 | 审计开始时干净；审计后仅新增本文件（untracked） |

### 0.2 复现命令（全部已在本次会话执行）

```powershell
git rev-parse HEAD
git rev-parse upstream/master
git log --oneline 4623109..99c38ba
git diff --stat   4623109..99c38ba
git diff --numstat 4623109..99c38ba
git diff --name-status 4623109..99c38ba
git show 99c38ba:<path>          # 逐文件取右端全文与行号
git show 4623109:<path>          # 需要对照左端时使用
git grep -n "<symbol>" 99c38ba -- <path>   # 交叉引用核对
```

### 0.3 行号与证据口径（重要）

- 本文所有 `file:line` 均指 **`99c38ba` 树内的行号**（`git show 99c38ba:<path>` 的物理行），不是当前 `rebuild` 工作区行号；两棵树已大幅分叉（fork 多出约 40 个模块）。
- 无法稳定给单行的，给 `@@ -a,b +c,d @@` hunk 范围。
- 复查任一结论的最小方式：`git show 99c38ba:<path>` 定位行号；或 `git diff 4623109..99c38ba -- <path>` 看 hunk。

### 0.4 基线输出（原样记录）

`git rev-parse HEAD` → `41394c1c9a27e58728b1af9518ceb5ac7bf30bcc`
`git rev-parse upstream/master` → `99c38baefc84d8cb66b05c28ede8a49ce050bfb2`

`git log --oneline 4623109..99c38ba`：

```text
99c38ba feat: account grouping, preset management, model quota weighting, and multi-tier preemption
b8af406 feat: port Tier-1 protocol fixes, WAA hash alignment, upstream Gzip, and network resilience
296680f fix(gateway): await session readiness in template capture and remove aggressive watchdogs
```

`git diff --stat 4623109..99c38ba`（尾行）：

```text
76 files changed, 4200 insertions(+), 1190 deletions(-)
```

`git diff --name-status` 汇总：`M` 73 个、`A` 3 个（`defaults/config.yaml`、`tests/unit/test_account_presets.py`、`tests/unit/test_tier_preemption.py`）、`D` 0 个、`R` 0 个；合计 76，与 `git diff --stat` 尾行一致（逐行清单见 §3）。

---

## 1. 结论摘要（先给判断，细节在后续章节）

1. 76/76 文件已逐文件审计，无“其他/杂项”跳过。
2. 上游这次不是“若干补丁”，而是**一次调度/账号域重构 + 协议修复 + UI 扩展**；与本仓 fork 架构（instance/pool/surface/routing_pipeline/event ledger）存在**结构性冲突**，不能按文件 apply。
3. 最危险的三点不在 08-07 的领域矩阵里：
   - `AccountRotator` 删除 `surface` 维度与 `get_keepalive_candidate`/`get_next_account_with_stats`，fork 有 10+ 调用点直接失配；
   - `get_active_account()` 的 enabled-only 回退会在 `toggle`/`apply_preset` 中**把 registry 改成新账号却不重绑浏览器会话**，导致“禁用不生效”；
   - `ensure_active_account()` 对任何付费模型在每次请求 attempt 0 触发**完整浏览器 rebind**（`switch_auth` + BotGuard），与 tier 权重/黏性组合存在**账号振荡**风险。
4. 上游新增 8 个 REST 端点，**零路由级测试**；`test_account_presets.py` 只测 store。08-07 把该文件处置为 `keep` 会掩盖这个空洞。
5. 上游仍保留 blue-green（`session.py:211-259`）与 `_retiring_contexts`；08-07 的 `rewrite` 判断正确，但漏掉了 `_drain_retiring_context` 从“一次 12s 等待”改成“while 无限重试”的新回归。
6. 数量：**NO_FEATURE_ROW = 17**，**冲突 = 20**，**待用户决策 = 14**。

---

## 2. 三个跨文件核心事实（后文冲突表的依据）

### 2.1 fork 与上游的模块面差异（决定“能否按文件合并”）

`git ls-tree -r --name-only 99c38ba src/aistudio_api/application` 只有 5 个模块：
`account_orchestrator.py / account_rotator.py / account_service.py / api_service_gemini.py / chat_service.py`。

fork `HEAD` 同目录有 40+ 模块（`instance_manager.py`、`instance_switch.py`、`routing_pipeline.py`、`surface_orchestrator.py`、`routing_selection.py`、`account_keepalive.py`、`model_catalog.py`、`generation_service.py`、`format_converters/*`、`atg/*`…）。

因此上游对 `account_rotator.py`、`account_orchestrator.py`、`session.py` 的改动，在 fork 中都是**跨模块重构**，不是局部 hunk。后文一律按此口径标注。

### 2.2 fork 对 rotator 的调用面（上游删除/改签名后必然断裂）

`git grep -n "rotator\." HEAD -- src/aistudio_api/application` 的关键结果（fork 侧）：

- `account_orchestrator.py:64,95,120,131,133,140,149,171,194,198,217,251,253,255,257` 全部带 `**sk`（surface keys）；
- `routing_pipeline.py:319` `rotator.is_account_available(...)`、`:403` `rotator.get_account_stats(...)`；
- `account_keepalive.py:388` `self._rotator.get_keepalive_candidate(...)`。

上游 `99c38ba:src/aistudio_api/application/account_rotator.py`：

- `is_account_available(self, account_id, model=None)`（`:529`，无 `surface`）；
- `record_success(self, account_id, model=None, usage=None)`（`:820`）；
- `record_rate_limited(self, account_id, model=None)`（`:831`）；
- `record_error / record_auth_error / clear_cooldown`（`:842/:848/:876`，均无 `surface`）；
- **不存在** `get_keepalive_candidate`、`get_next_account_with_stats`、`_get_available_accounts`（对照 `git show 4623109:...` 的 `def` 清单）。

同时 fork 的 `AccountStats` 用 `_storage_key(surface, model)`（`HEAD:...account_rotator.py` 内 `def _storage_key`，chat 面裸 model、其它面 `"<surface>::<model>"`），上游 `AccountStats.daily_models` 只按模型键（`99c38ba:...:174-181`）。**持久化 schema 不兼容。**

### 2.3 上游新配置块的加载语义（fail-fast 说法需要更正）

`99c38ba:src/aistudio_api/infrastructure/gateway/model_defaults.py:452-470` 的 `_load_yaml_config` 是**浅合并**：

```python
merged = _default_config()
merged.update(loaded)        # 顶层键整体替换
```

- 配置文件**完全没有** `rotator` 块 → 保留 `_default_config()` 硬编码默认（`:330-352`），**静默**，不 fail-fast。
- 配置文件**有但不完整** `rotator` 块 → 整块替换默认 → `_score_candidate` 取 `rot_cfg["tier_weights"]`/`["stickiness_bonus"]`/`["stickiness_threshold"]`/`["drip_penalty"]`（`:713/:727-729`）→ **KeyError**（不是 RuntimeError），且异常发生在**请求路径**，不是启动路径。
- 只有 `tier_weights` 存在但类型不对时才 `RuntimeError("rotator 配置缺少 tier_weights 配置块")`（`:715`）；`cooldowns` 同理（`:271/:315`）。

所以 08-07 §3.4 与 `12-gate0-inputs.md:104` 的“上游缺键直接 RuntimeError / P9-pre fail-fast”表述不准确；真实状态是“**没有启动校验，缺失静默、部分 KeyError**”。

`config.py:71-99` 还表明 `defaults/config.yaml` 是**运行时引导模板**：当目标 `config.yaml` 不存在时会被 `shutil.copyfile` 过去。它不是纯文档。

---

## 3. 76/76 覆盖清单

图例：冲突列引用 §5 的 `Cxx`；`—` 表示本次未发现新冲突（不代表无需改写）。`08-07` 列为该文对应裁决/矩阵行（无则 `未提`）。证据列行号均指 `99c38ba`。

> 本表编号 01–76 连续，物理行见 `docs/dev/rebuild/08-08-upstream-99c38ba-full-diff-audit.md` 第 135–210 行（可用 `Select-String -Pattern '^\| \d{2} \|'` 取前 76 条计数）。

| # | 路径 | A/M | +/− | 改动摘要 | 行为/接口/schema/UI/测试影响 | feature ID | 08-07 裁决 | 冲突 | 证据（file:line / hunk） |
|---|---|---|---|---|---|---|---|---|---|
| 01 | `.env.example` | M | +2/−2 | `AISTUDIO_TIMEOUT_REPLAY/STREAM` 默认 120→600 | 行为：默认超时翻 5 倍；env 覆盖仍可用 | D01-039 | adopt（默认 600） | — | `@@ -43,9 +43,9 @@`；`.env.example:46-49` |
| 02 | `README.md` | M | +0/−1 | 删除 `AISTUDIO_BROWSER_IDLE_TIMEOUT` 行 | 文档：不再承诺 idle 回收键 | D01-038（drop） | adopt 删除 | — | `@@ -223,7 +223,6 @@` |
| 03 | `README_EN.md` | M | +0/−1 | 同 02（英文） | 同上 | D01-038（drop） | adopt 删除 | — | `@@ -222,7 +222,6 @@` |
| 04 | `config.yaml` | M | +162/−32 | 新增 `rotator`、`paid_model_rules`、`tier_quotas`、`account_presets`；`image_models` 增加 `imagen`；新增 `video_models`（veo）；删大量注释；`models: {}` | schema：新增 4 个顶层块 + 1 个 profile；`api_keys: []` 注释明确“留空即免 Key 开放”；`Rotator` 无启动校验 | D01-042 / D05-006 / D08-020 / N04-N07 | adopt（新键） | C09 C21 | `config.yaml:1-3`、`:16-42`、`:44-100`、`:108-232` |
| 05 | `defaults/config.yaml` | A | +205/−0 | 新增引导模板：`api_keys: []` + logging + rotator/paid_model_rules/model_defaults/tier_quotas/account_presets | 运行时：`config.py:71-99` 在 `config.yaml` 缺失时把它复制为正式配置；缺 `browser`/`keepalive`/`features` 键 | N17 | 未提为独立文件（仅“默认模板初始化”） | C21 | `defaults/config.yaml:1-3`；`config.py:71-99` |
| 06 | `docs/BOTGUARD_VERIFICATION_CHAIN.md` | M | +2/−2 | 字段 12 `cached_content`→`session_token`；字段 14 `location`→`request_context` | 文档：仅命名，无行为 | D06-035 | 未单列 | N13 | `@@ -272,8 +272,8 @@` |
| 07 | `docs/WIRE_SPECIFICATION.md` | M | +10/−8 | 同上改名；generationConfig 增 `18 seed`；finish reason 改号（5 OTHER / 6 LANGUAGE；15 IMAGE_OTHER / 16 NO_IMAGE / 17 IMAGE_RECITATION）；增 `18 MISSING_THOUGHT_SIGNATURE`、`22 CONTINUING` | 契约：客户端可见 finish_reason 字符串变化；新增非终止码 | D06-015 / D06-035 / N13 / N14 | 部分（只写 finish 不提前 break） | C18 | `@@ -51,8 +51,8 @@`、`@@ -120,6 +120,7 @@`、`@@ -305,8 +306,8 @@`、`@@ -315,10 +316,11 @@` |
| 08 | `src/aistudio_api/api/app.py` | M | +0/−19 | 删除 `settings` 导入、`idle_task` 创建与取消 | 生命周期：不再有 10s idle 轮询任务 | D01-012（drop）/D03-035（drop） | adopt 移除 | — | `@@ -45,7 +45,6 @@`、`@@ -89,22 +88,6 @@`、`@@ -154,8 +137,6 @@` |
| 09 | `api/response_models.py` | M | +26/−1 | `GeminiUsageMetadata.cachedContentTokenCount`；新增 `AccountBreakdownItem`；`ModelStatsResponse` 增 reasoning/cached/finish_reasons/account_breakdown；`StatsTotals` 增 reasoning/cached | API schema：`/stats` 体积与时延随账号数增长；两个可变默认（Pydantic 会拷贝，但风格风险） | D10-017 / D10-030 / N09 / N10 | adopt（stats 新字段） | C15（UI 无消费） | `response_models.py:15`、`:67-83`、`:86-99`、`:101-109` |
| 10 | `api/responses.py` | M | +7/−0 | `usage["cached_tokens"]` → `cachedContentTokenCount`（None 保持 None） | API 响应：Gemini 客户端可见新字段 | D06-016 | adopt | — | `@@ -47,11 +47,18 @@` |
| 11 | `api/routes_accounts.py` | M | +228/−0 | `AccountResponse` 增 enabled/group/notes；8 个新端点：`POST /{id}/toggle`、`PUT /{id}/group`、`PUT /{id}/notes`、`GET /groups/list`、`POST /groups/{g}/toggle`、`GET /presets/list`、`POST /presets`、`POST /presets/snapshot`、`DELETE /presets/{id}`、`POST /presets/{id}/apply` | API：控制面新增写操作；`toggle`/`apply` 内部调 `try_switch_account`；`Body(default_factory=...)` 依赖 FastAPI≥0.115 | N01-N04 / D02-002 / D02-012 / D11-028 | adopt（账号域能力） | C05 C14 C16 | `routes_accounts.py:39-46`、`:446-460`、`:499-521`、`:524-616`、`:449` |
| 12 | `api/routes_system.py` | M | +54/−4 | `/stats` 增 `account_breakdown`；按账号 tier 调 `resolve_tier_quota`；totals 增 reasoning/cached | API：GET 内遍历 accounts×models；调用 `st._check_date_reset` **在只读端点里改状态** | D10-030 / D11-025 / N09 | adopt（stats 字段） | C15 | `routes_system.py:39-112`（`:73` 状态变异、`:96-110` breakdown） |
| 13 | `api/schemas.py` | M | +1/−0 | `GeminiGenerationConfig.seed` | 接口：仅 Gemini 路由可见；OpenAI/Claude 无 | D06-036 / N11 | 未提 | C20 | `schemas.py:164` |
| 14 | `api/state.py` | M | +32/−8 | `ModelStatsItem` 增 reasoning/cached/finish_reasons；`record()` 增 `finish_reason`；usage 解析 `completion_tokens_details.reasoning_tokens`、`cached_tokens`；total 回退 `pt+ct` | 统计账本：持久化新增 finish_reasons；`int(str(...))` 容错但吞类型错误 | D10-002 / D10-017 / D10-030 / N10 | adopt | — | `state.py:35`、`:66-82`、`:131-170` |
| 15 | `application/account_orchestrator.py` | M | +58/−6 | `try_switch_account` 增 `required_tiers`；自动按模型补 tiers；`ensure_active_account` 对付费模型主动抢切，失败抛 `HTTPException(403)`；`record_rotator_event` 增 usage | 行为：每次请求 attempt 0 可能触发整段会话切换；application 层直接依赖 FastAPI | D02-026 / D05-009 / D05-028 / N08 | adopt（403 fast-fail） | C06 C07 C08 | `account_orchestrator.py:34-48`、`:172-226`、`:228-240` |
| 16 | `application/account_rotator.py` | M | +534/−354 | `AccountStats`→`Account×Model×Date`（`daily_models`）；新增 `AccountModelDailyStats`；`_score_candidate`（配额比+tier 权重+黏性+滴灌+空闲）；`_get_eligible_candidates`（enabled/group/required_tiers/冷却）；删除 surface 维度、`get_next_account_with_stats`、`get_keepalive_candidate`；`get_all_stats` 输出 `today_models` | 行为+schema：rotator_state.json 结构完全变化；surface 隔离丢失；候选集合语义变化 | D02-002 / D05-006 / D08-020 / N05-N08 | rewrite（只作用于现有实例候选） | C01 C02 C09 | `account_rotator.py:63-110`、`:174-202`、`:633-757`、`:759-818`、`:537-631` |
| 17 | `application/account_service.py` | M | +55/−0 | 透传 toggle/group/notes/groups/preset CRUD 到 store | 应用层：无校验、无审计 | D02-002 / N01-N04 | adopt | C16 | `account_service.py:146-193` |
| 18 | `application/api_service_gemini.py` | M | +41/−23 | finish reason 表改号并加 18；`BrowserTransportError`→502/UNAVAILABLE；成功路径传 usage 与 finish_reason；删除三处 info 日志 | 协议：客户端可见 502 与 FINISH_REASON_UNSPECIFIED(22)；日志可观测性下降 | D06-015 / D11-014 / D10-002 / N14 | adopt | C18 | `api_service_gemini.py:47-64`、`:146-148`、`:275-283`、`:352-365` |
| 19 | `application/chat_service.py` | M | +8/−9 | schema `min/max*` 从 `a or b` 改为 `a if present else b`（0 值不再被吞）；`seed` 注入 overrides | 行为：`minLength: 0`/`maxItems: 0` 语义修正；seed 只在 Gemini 面 | D06-036 / N11 | 未提 | C20 | `chat_service.py:235-268`、`:806-812` |
| 20 | `config.py` | M | +2/−16 | 删 `_resolve_default_idle_timeout()` 与 `Settings.browser_idle_timeout`；timeout 默认 600 | 配置：fork 侧 5 处引用该键（app/session/pooled_session/tests）会断 | D01-038 / D01-039 | adopt 删除 | — | `config.py:238-260`（删除 `@@ -235,19 +235,6 @@`） |
| 21 | `domain/errors.py` | M | +17/−2 | 新增 `BrowserTransportError(net_evidence)`；`is_browser_session_hang_error` 识别之；关键词增 `snapshot`/`hook install`；格式化 | 错误域：传输中断与协议错误分流；fork 的 `is_transient_rpc_error` 未变 | D11-014 / D06-063 | adopt | — | `errors.py:30-34`、`:120-140` |
| 22 | `infrastructure/account/account_store.py` | M | +316/−7 | `AccountMeta` 增 enabled/group/notes；新增 `AccountPreset`；`get_active_account` 只在 enabled 中回退（并写 registry）；`save_account` 重导入保留 group/notes/disabled；新增 toggle/group/notes/groups/batch/presets；`presets.json` | 持久化：新增 `presets.json`；`get_active_account` 有副作用；`_load_presets` 在锁内调 `_save_presets`（非重入锁） | D02-001 / D02-002 / D02-010 / N01-N04 | adopt | C05 C13 C22 | `account_store.py:103-133`、`:354-374`、`:396-465`、`:566-633`、`:635-672`、`:725-770` |
| 23 | `infrastructure/browser/browser_engine.py` | M | +0/−79 | 删除 `_spawn_process_watchdog` 与 `ChromiumProcess.watcher/pipe_w` 及关闭逻辑；`launch_chromium_process` 不再起守护 | 进程：POSIX 父进程被 SIGKILL 时不再有孤儿回收（只剩进程组/Job Object/atexit 的“优雅路径”） | D03-004（rewrite） | adopt 移除 | C10 | `@@ -765,63 +765,6 @@`、`@@ -906,16 +849,12 @@`、`@@ -932,21 +871,6 @@`、`@@ -1059,13 +983,10 @@` |
| 24 | `infrastructure/browser/cdp_client.py` | M | +16/−46 | 删 `set_liveness_checker`（两处）与发送/等待轮询；`ping_interval/ping_timeout` 5→None；`CDPPage` 增 `account_auth_file`/`has_net_trace`/`rpc_net_*` 与 `on_loading_failed`；prune 只关 `about:blank` | 可靠性：半开连接仅靠命令超时；fork `pooled_session.py:65` 绑定 liveness 会 AttributeError；多页残留 | D03-005 / D03-006 / D03-009 / N16 | adopt（移除）+rewrite（索引行） | C11 C12 | `cdp_client.py:98-101`、`:219-224`、`:266-274`、`:729-734`、`:836-846` |
| 25 | `infrastructure/browser/js/hooked_request.js` | M | +10/−7 | timeout 默认 60→600；`args.gzip` 时 base64→Uint8Array；区分 AbortError 与 body 读错误；错误消息透传 | 传输：非流式支持 gzip；错误信息更可诊断 | D06-031 / N12 | adopt gzip | — | `hooked_request.js:3-39` |
| 26 | `infrastructure/browser/js/streaming_init.js` | M | +2/−21 | 删除 JS 侧 timeout/`resetTimeout`（`args.timeout` 变成死参数）；gzip body 解码 | 传输：流式超时完全交给 Python deadline；页面内不再自杀 | D06-032 / D06-033 / N12 | adopt | — | `@@ -24,25 +24,7 @@`、`@@ -65,11 +47,11 @@`、`@@ -96,7 +78,6 @@` |
| 27 | `infrastructure/browser/scripts.py` | M | +29/−5 | 新增 `_GZIP_MIN_BYTES=96*1024`；`build_hooked_request_args` 判 `Content-Encoding`、≥96KiB 时 gzip(level 6)+base64 并注入头；`build_streaming_init_args` 复用前者 | 传输：请求体在浏览器内解压；base64 膨胀；已带非 identity 编码时原样放行 | N12 / D06-031 | adopt gzip | — | `scripts.py:11`、`:42-72` |
| 28 | `infrastructure/gateway/botguard_bootstrap.py` | M | +3/−3 | 轮询 25→40 次；`STOP_GENERATION_JS` 从“捕获前”移到“捕获后” | 行为：模板捕获窗口更长；停止生成不早于捕获，避免打断 | D03-020 | adopt | — | `@@ -270,10 +270,8 @@`、`@@ -290,6 +288,8 @@` |
| 29 | `infrastructure/gateway/capture.py` | M | +2/−2 | `_ensure_template` 改 `ctx = await session.ensure_ready()`；模板缓存按 context_id 失效 | 行为：捕获前必须 READY（最多 60s）；context 变化清模板 | D06-028 / D03-001 | adopt | — | `capture.py:104-109` |
| 30 | `infrastructure/gateway/client.py` | M | +12/−8 | 删除公共 `replay()`；`generate_content`/`generate_image` 空 200 抛 `BrowserTransportError`；图片 replay 不再传 120s | API：fork 若外部调用 `client.replay` 会断（当前 src 内无调用）；空 200 不再伪装成功 | D06-001 / D06-069 / D11-014 | adopt | — | `client.py:59-75`、`:161-166`（删除）、`:262-266`、`:340-346` |
| 31 | `infrastructure/gateway/model_defaults.py` | M | +247/−1 | `_default_config` 增 image(imagen)/video/rotator/paid_model_rules/tier_quotas/account_presets；新增 `get_configured_*`、`resolve_tier_quota`；末尾 `return raw_rules` 死代码 | 配置：所有新块都有“缺失静默回退硬编码”；配额匹配支持 exact/prefix/contains | D01-042 / N05-N07 / N15 / N17 | adopt（原生配置面） | C09 | `model_defaults.py:279-435`、`:654-690`、`:669-739`、`:741-769`、`:771-790` |
| 32 | `infrastructure/gateway/model_discovery.py` | M | +110/−4 | `is_model_allowed_for_tier` 改由 `get_required_tiers`（配置规则）优先，tier 比较小写化并识别 `Ultra_20X`；新增 `is_paid_model`/`get_required_tier(s)`（规则 + accessModes + paid/capabilityCodes） | 准入：静态子串规则可把 `*image*` 一律判 Pro+；无规则时 `get_required_tiers` 可能为空 | D02-027 / N07 | adopt | C06 | `model_discovery.py:320-342`、`:344-387`、`:389-441` |
| 33 | `infrastructure/gateway/session.py` | M | +6/−39 | 删 idle 计时/回收与 `_last_activity_time`/`touch_activity`；删 liveness 绑定；`_drain_retiring_context` 改 `while in_flight>0` 重试；snapshot hash 不再纳入 executable_code/code_execution_result；**保留** blue-green 新建页 | 生命周期：无空闲回收；旧页 drain 可能无限重试；模板 hash 语义变化 | D03-017/018/019（drop）/D03-035 / D06-028 | rewrite（blue-green） | C03 C04 C19 | `session.py:82-86`、`:135-147`、`:175-185`（删除）、`:211-259`、`:274-297`、`:345-352` |
| 34 | `infrastructure/gateway/session_auth.py` | M | +5/−14 | `load_cookies_to_page` 绑定 `page.account_auth_file`；meta/auth 写入改 `atomic_write_json` | 持久化：原子写；cookie 回退可精确到“该页账号” | D02-030/031 / D03-022/023 | adopt | — | `session_auth.py:120-123`、`:275-280`、`:310-315`、`:385-390` |
| 35 | `infrastructure/gateway/streaming.py` | M | +27/−12 | finish_reason=22 不记为终态；删除“遇 finish 即 break”；空 chunk 流抛 `BrowserTransportError`；非 200 对 `[,`/`[null,[` 也走 classify | 流式：不再提前截断，可收尾 usage/response_id；截断连接显式失败 | D06-007/017/018 / N14 | adopt | — | `streaming.py:116-140`、`:150-166` |
| 36 | `infrastructure/gateway/transport.py` | M | +105/−9 | 新增 `_ensure_net_trace`（订阅 `$rpc/` request/response/loadingFailed，保留最近 32/8 条）；新增 `_log_rpc_net_evidence`/`_transport_error`；`_get_auth_cookies(auth_file)` 优先页面绑定账号；页面关闭/超时/error 统一 `BrowserTransportError` | 可诊断性：失败带 CDP net 证据；错误类型变化会影响上层重试分类 | D06-031 / D06-033 / D11-014 / N16 | adopt | — | `transport.py:35-95`、`:110-135`、`:190-197`、`:283-300`、`:352-404` |
| 37 | `infrastructure/gateway/wire_codec.py` | M | +71/−86 | `CACHED_CONTENT→SESSION_TOKEN`、`LOCATION→REQUEST_CONTEXT`；`decode` 强制 JSON array 并补位；`_decode_part` 全面改走 `parse_response_part` | 协议：malformed 请求抛 ValueError；part 解析单一来源 | D06-035 / D06-037 / N13 | adopt | — | `wire_codec.py:155-195`、`:218-243`、`:381-451` |
| 38 | `infrastructure/gateway/wire_parser.py` | M | +51/−23 | 角色守卫细化（`[None,"model"]` 视为文本）；functionCall/Response 只接受结构化载荷（防 index10 thought flag 误判）；usage 全 None 返回 `{}`；`parse_text_output` 每 chunk 取 usage/response_id；code 输出只取正文 | 协议：修 false tool_call；非流式 usage 从“最后一 chunk”改“最后一个非空 usage” | D06-021 / D06-027 / D06-038 / N14 | adopt | — | `wire_parser.py:136-150`、`:171-215`、`:270-282`、`:403-410`、`:555-592` |
| 39 | `infrastructure/gateway/wire_spec.py` | M | +6/−2 | `SESSION_TOKEN=11`/`REQUEST_CONTEXT=13`；`GenerationConfigIndex.SEED=18`；新增 `WIRE_FINISH_REASON_CONTINUING=22` | 常量：改名 + 新索引 | D06-035 / N13/N14 | adopt | — | `wire_spec.py:21-25`、`:60-65`、`:122-125` |
| 40 | `infrastructure/gateway/wire_types.py` | M | +16/−22 | `seed` property（index 18）；`AistudioPart.to_wire` 在所有 part 类型上统一写 thought flag/signature（F-01）；`AistudioRequest.cached_content→session_token`（`location` 未改名） | 编码：签名不再只随图片/工具回传；纯文本签名也写回 | D06-036 / D06-020 / N11/N13 | adopt | — | `wire_types.py:222-230`、`:270-321`、`:403-407` |
| 41 | `static/assets/AccountsView.js` | M | +2/−2 | 重建产物（40906B，+8199B）：内嵌“保存当前方案”、预设条、分组 tab、快照弹窗、toggle 调用 | UI 产物：与 `AccountsView.vue`/store 同步 | D11-028 / N02/N04 | 未提 static 明细 | — | 产物含 `保存当前方案`/`账号预设`/`presets`（`git show 99c38ba:...` 单行） |
| 42 | `static/assets/DashboardView.js` | M | +2/−2 | 重建产物（13166B，+5616B）：思考/缓存卡片、`account_breakdown` 折叠明细 | UI 产物：与 `DashboardView.vue`/`ModelStatsTable.vue` 同步 | D11-024/038 / N09/N10 | 未提 static 明细 | — | 产物含 `思考`/`缓存`/`account_breakdown`×6 |
| 43 | `static/assets/SettingsView.js` | M | +1/−1 | 仅 chunk 引用/哈希变化（+55B）；**不含** rotator/tier_quotas/paid_model_rules/account_presets/video_models 字符串 | UI：确认新配置块不可视化编辑 | D11-033 | 未提 | C15 | `SettingsView.js` 全文 grep 上述键为 0 |
| 44 | `static/assets/index.css` | M | +1/−1 | 重建产物（+2746B）：新增 `translate-x-4`、`xl:grid-cols-6`、`opacity-60`、`w-9` 等工具类 | UI：支撑 toggle/分组/6 卡片布局 | D11-022/024 | 未提 | — | 产物 grep 命中上述类 |
| 45 | `static/assets/index.js` | M | +1/−1 | 重建入口（+2456B）：accounts API 新方法、store 预设/分组、AppLayout | UI 产物：与 `api/accounts.ts`/`stores/accounts.ts` 同步 | D11-022/028 | 未提 | — | 产物含 `presets`×9、`applyPreset`×3、`listGroups`×2 |
| 46 | `static/assets/vendor-icons.js` | M | +72/−57 | 打包图标集扩充（+1634B）：Brain/Database/BookmarkPlus/SlidersHorizontal/ChevronRight/Tag/Folder/Trash2/Users 等 | UI 产物：新图标依赖 | D11-028 | 未提 | — | 产物逐符号命中（Brain/Database/…=1） |
| 47 | `static/assets/vendor-vue.js` | M | +1/−1 | 仅导出别名重排（+8B，lib 未变） | 产物：无功能变化 | D11-022 | 未提 | — | 首个差异偏移 111681，仅 `K/L/M/N/O` 别名换位 |
| 48 | `tests/conftest.py` | M | +96/−0 | fixture `config.yaml` 增 paid_model_rules/rotator/tier_quotas/account_presets | 测试：仅 Free/Pro/Ultra + 4 预设；**缺 Plus/Ultra_20X/video_models**，Plus 用例会静默落到 Free 配额 | D12-002/D12-016 | rewrite | C14 | `conftest.py:53-148` |
| 49 | `tests/unit/test_account_orchestrator.py` | M | +2/−4 | `record_success(..., usage=None)`；`_check_date_reset` 单参；删除跨天“不可用”断言 | 测试：跟随新签名；session_expired 永久性更明确 | D02-029..031 / D05-041 | rewrite | C14 | `@@ -130,7 +130,7 @@`、`@@ -312,8 +312,7 @@` |
| 50 | `tests/unit/test_account_presets.py` | A | +131/−0 | 3 个 store 级用例：enabled/group 持久化、preset CRUD/apply、重导入保留元数据 | 测试：**无路由级**（toggle/group/notes/presets 8 个端点 0 覆盖）；active 回退断言依赖 dict 顺序 | D02-002/D02-012 / N04 | keep（CSV:82） | C14 | `test_account_presets.py:11-131` |
| 51 | `tests/unit/test_account_rotation_concurrency.py` | M | +13/−16 | 迁到 `daily_models`/`daily_date_la`；`record_success` 50→15 次 | 测试：`test_rotator_prioritizes_least_used_account` 现依赖 sticky 阈值，名称与断言不再对应 | D05-036/037/041 | rewrite | C14 | `@@ -131,12 +131,12 @@`、`@@ -492,7 +492,7 @@`、`@@ -512,25 +512,23 @@` |
| 52 | `tests/unit/test_api_responses.py` | M | +45/−3 | 新增 cachedContentTokenCount、502 映射、`to_gemini_finish_reason` 改号断言 | 测试：覆盖 P5 内容，但 CSV 把它归到 P8 Responses 后置 | D06-058/059 / D06-015 / D11-014 | rewrite（P8） | C14 C18 | `test_api_responses.py:33-55`、`:127-147` |
| 53 | `tests/unit/test_browser_engine.py` | M | +0/−16 | 删除 `test_spawn_process_watchdog_creates_pipe_and_reaps` | 测试：watchdog 保护完全失去回归；若保留 watchdog 需恢复该用例 | D03-004 | rewrite | C10 | `@@ -14,7 +14,6 @@`、`@@ -166,21 +165,6 @@` |
| 54 | `tests/unit/test_browser_session.py` | M | +4/−0 | snapshot hash 期望把 executable_code/code_execution_result 映射为 `""` | 测试：证明 hash 不再感知代码 part | D03-016/019/034 / D06-028 | rewrite | C19 | `@@ -252,6 +252,8 @@`、`@@ -269,6 +271,8 @@` |
| 55 | `tests/unit/test_cdp_client.py` | M | +20/−22 | 删 liveness 用例；prune 用例改为 3 target 且只关 about:blank | 测试：锁定“只关空白页”新语义；半开连接无测试 | D03-009 / D03-006 | rewrite | C11 C12 | `@@ -213,24 +212,6 @@`、`@@ -238,12 +219,28 @@` |
| 56 | `tests/unit/test_cookie_import.py` | M | +10/−1 | `import_bundle` 测试 monkeypatch `probe_account_benefit_tier`/`probe_account_email` | 测试：避免真实探测；CSV 要求已被上游满足 | D02-003..013 | rewrite | C14 | `@@ -160,11 +160,20 @@` |
| 57 | `tests/unit/test_runtime_persistence.py` | M | +4/−4 | 断言改 `total_requests/total_rate_limited/daily_models`；删 `auth_errors==1` 断言 | 测试：跟随新 schema，但对 auth_errors 的覆盖下降 | D10-016/020/025 | rewrite | C14 | `@@ -75,10 +75,10 @@` |
| 58 | `tests/unit/test_session_lifecycle.py` | M | +0/−49 | 删 idle 回收用例与冷启动刷新时间戳用例；**未补** ensure_ready/drain/无 idle 的新用例 | 测试：生命周期覆盖净减少；08-07 的“重写”尚无落点 | D03-034/D04-031/D08-022 | rewrite | C14 | `@@ -123,23 +122,6 @@`、`@@ -156,34 +138,3 @@` |
| 59 | `tests/unit/test_stream_transport.py` | M | +168/−0 | 新增 gzip 阈值/往返、CDP net evidence、空 200 流、CONTINUING=22 后 usage 保留 | 测试：质量较好；仅 args 级 gzip，无真实 transport gzip 回放 | D06-007/008 / N12/N16 | rewrite | C14 | `test_stream_transport.py:199-358` |
| 60 | `tests/unit/test_tier_preemption.py` | A | +174/−0 | 4 个用例：Free→Pro 抢占、无 Pro 403、Plus→Pro、Pro→Ultra(veo) | 测试：只覆盖“配置规则命中”的 paid 模型；未覆盖 paid 但无规则→Ultra-only 的 bug；`clear_cooldown` mock 未被断言 | D05-019/021 / N08 | rewrite | C06 C07 C14 | `test_tier_preemption.py:17-174` |
| 61 | `tests/unit/test_wire_codec.py` | M | +9/−4 | `cached_content→session_token`；新增 seed→index 18 | 测试：覆盖改名与 seed 编码；未覆盖 `[None,"model"]` 文本、finish 22 | D06-028..038 / N11/N13 | rewrite | C14 C18 | `@@ -344,9 +344,7 @@`、`@@ -393,6 +391,13 @@` |
| 62 | `tests/unit/test_wire_parser.py` | M | +7/−3 | 空 usage→{}；thought 块 function_call=None；角色守卫边界（`[None,"model"]`→文本） | 测试：关键协议回归有锁；未覆盖 22/CONTINUING | D06-028..038 / N14 | rewrite | C14 | `@@ -59,7 +60,7 @@`、`@@ -140,11 +141,12 @@` |
| 63 | `web/bun.lock` | M | +2/−2 | `@rollup/rollup-android-arm64` 4.63.4→4.65.0 | 构建：仅 Android arm64 可选依赖；与功能无关 | NO_FEATURE_ROW（tooling） | 未提 | — | `bun.lock:13`、`:142` |
| 64 | `web/package.json` | M | +1/−1 | 同上 devDependency 版本 | 构建：同上 | NO_FEATURE_ROW（tooling） | 未提 | — | `package.json:22` |
| 65 | `web/src/api/accounts.ts` | M | +66/−0 | 新增 toggle/updateGroup/updateNotes/listGroups/batchToggleGroup/listPresets/createPreset/createSnapshotPreset/deletePreset/applyPreset | API 客户端：`updateNotes`/`batchToggleGroup` **无 UI 调用者** | D11-028 / N01-N04 | 未提 | C16 | `accounts.ts:79-140` |
| 66 | `web/src/components/accounts/AccountRow.vue` | M | +108/−15 | 新增启停 switch、分组标签内联编辑、停用态整体灰化、激活按钮仅在 enabled 显示 | UI：启停/分组可操作；备注不可编辑不可见 | D11-028/030 / N01/N02 | 未提 | C05 C16 | `AccountRow.vue:130`、`:181-213`、`:265-275` |
| 67 | `web/src/components/accounts/ModelQuotaGrid.vue` | M | +92/−39 | 每模型显示 quota 进度条、429/滴灌/就绪、reasoning/cached token 药丸 | UI：软配额可视化；读 `today_models` | D11-030/039 / N05 | 未提 | — | `ModelQuotaGrid.vue:16-20`、`:52-70`、`:115-133` |
| 68 | `web/src/components/config/ProfileCard.vue` | M | +2/−1 | 规则分组名称输入补 `:model-value`（修不受控）；media_resolution 事件 `as any` | UI bugfix：profile.name 编辑现在真的受控；类型放宽 | D11-033 | 未提 | — | `ProfileCard.vue:140`、`:263` |
| 69 | `web/src/components/dashboard/ModelStatsTable.vue` | M | +252/−45 | 行可展开：按账号明细（邮箱/索引/分组/订阅/请求/成功429/进度/状态）；模型行加 reasoning/cached 药丸 | UI：`account_breakdown` 首个消费点；`finish_reasons` 仍无展示 | D11-038 / N09/N10 | 未提 | — | `ModelStatsTable.vue:49-56`、`:177-300` |
| 70 | `web/src/stores/accounts.ts` | M | +104/−1 | presets/groups/applyingPresetId 状态与 6 个动作；toggle/group 后 `fetchAll` | 状态管理：`fetchAll` 未加入 presets/groups；失败静默 catch | D11-028 / N01/N02/N04 | 未提 | C14 | `stores/accounts.ts:26-29`、`:70-165`、`:41-68` |
| 71 | `web/src/types/accounts.ts` | M | +45/−2 | `Account.enabled/group/notes`；`AccountWithStats` 重定义（email 可空）；新增 preset 类型 | 类型：`enabled/group` 变必填，所有 fixture/测试需补 | D11-028 / N01-N04 | 未提 | — | `types/accounts.ts:12-14`、`:15-52` |
| 72 | `web/src/types/rotation.ts` | M | +26/−1 | 新增 `ModelDailyDetail`、`today_models`、today_total_*；删除 `model_rate_limited_dates` | 类型：旧字段删除，外部消费者需同步 | D11-030 / N05 | 未提 | — | `types/rotation.ts:1-42` |
| 73 | `web/src/types/stats.ts` | M | +25/−0 | `AccountBreakdownItem`；ModelStatItem 增 reasoning/cached/finish_reasons/account_breakdown；Totals 增 reasoning/cached | 类型：`finish_reasons` 无消费方 | D10-017 / N09/N10 | 未提 | C15 | `types/stats.ts:1-44` |
| 74 | `web/src/views/AccountsView.vue` | M | +197/−10 | 预设条（应用/删除/保存快照弹窗）、分组 tab 过滤、`onMounted(loadData)` + 8s 轮询 | UI：确认弹窗用原生 `confirm`；`usePolling` 默认 immediate，首屏会重复请求一次 | D11-028 / N02/N04 | 未提 | — | `AccountsView.vue:28-40`、`:147-205`、`:240-285` |
| 75 | `web/src/views/DashboardView.vue` | M | +38/−7 | 6 列卡片布局；新增“思考 Tokens”“缓存 Tokens”；可用账号数排除 disabled | UI：totals 新字段展示；不展示 finish_reasons | D11-024 / N10 | 未提 | — | `DashboardView.vue:33-52`、`:60`、`:108-128` |
| 76 | `web/tsconfig.tsbuildinfo` | M | +1/−1 | 增量构建信息补全所有 .vue 模块（旧文件只含 .ts） | 构建产物：无运行时影响；说明上游提交了不一致的增量缓存 | NO_FEATURE_ROW（build artifact） | 未提 | — | `tsconfig.tsbuildinfo:1` |

**覆盖计数：76 行 / 76 文件。** 与 `git diff --numstat` 逐行一一对应（含 3 个 A：05、50、60）。

---

## 4. NO_FEATURE_ROW 清单（17 项）

判定口径：`06-feature-index.csv`（518 数据行）中**没有**能表达该能力的行；仅“沾边”的不算映射，标 `partial` 并说明。

| ID | 上游新能力 | 证据（99c38ba） | 最接近的既有行 | 为何不能直接映射 |
|---|---|---|---|---|
| N01 | 账号 enabled/disabled + 候选过滤 + active 只在 enabled 回退 | `account_store.py:103-133`、`:354-374`；`account_rotator.py:645-663` | D02-002（元数据扩展）partial | D02-002 是“字段集合”记账，不含“禁用即退出候选/活跃”的调度语义 |
| N02 | 账号分组 CRUD + 批量分组启停 + 分组过滤 UI | `routes_accounts.py:499-521`；`AccountsView.vue:190-210` | D05-011（分组匹配，已 drop） | D05-011 是旧 groups/tag 策略组，已 drop；本能力不同且仍存在 |
| N03 | 账号 notes 字段/API | `account_store.py:604-609`；`routes_accounts.py:486-497` | D02-002 partial | 无 notes 的 UI、无审计、无测试 |
| N04 | 账号预设（CRUD/snapshot/apply/system presets） | `account_store.py:635-770`；`routes_accounts.py:524-616`；`config.yaml:214-232` | NO_FEATURE_ROW（最近 D05-011 drop） | 全新控制面对象，feature 集无行 |
| N05 | 订阅层级每日软配额（tier_quotas + resolve_tier_quota + 打分/展示） | `model_defaults.py:654-739`；`account_rotator.py:700-706`；`routes_system.py:96-110` | D05-006（tier→quota pool）partial | D05-006 是旧 pool 路由映射；这里是“账号×模型×日”的软额度，不拒绝、只加权 |
| N06 | rotator 打分配置（strategy/stickiness/drip_penalty/tier_weights） | `model_defaults.py:756-769`；`account_rotator.py:707-757` | D08-020 partial | D08-020 只覆盖 429 冷却分层；不含打分权重 |
| N07 | paid_model_rules 静态准入规则（contains/prefixes/exact → required_tiers） | `config.yaml:34-42`；`model_discovery.py:344-441` | D02-027 partial | D02-027 是 accessModes 位过滤；静态子串规则是新判定源 |
| N08 | tier 抢占（required_tiers + attempt 0 主动切号 + 本地 403） | `account_orchestrator.py:172-226` | D05-009/D02-026 partial | 既有行只说“paid 模型需 paid 账号”，不含“主动抢占/本地 fast-fail” |
| N09 | `/stats` 按账号明细 `account_breakdown` | `response_models.py:67-83`；`routes_system.py:56-112` | D10-030 partial | D10-030 是 totals 聚合，不含按账号×模型视图 |
| N10 | finish_reasons 聚合与展示 | `state.py:35/152`；`response_models.py:93`；`types/stats.ts:30` | NO_FEATURE_ROW | 无任何行；且 UI 也没有消费（死字段） |
| N11 | 生成参数 seed（wire index 18 / Field 19） | `wire_spec.py:63`；`wire_types.py:222-230`；`schemas.py:164`；`chat_service.py:809` | D06-036 partial | D06-036 是请求编码总行，未列 seed；且只 Gemini 面支持 |
| N12 | 请求体 ≥96KiB 自动 gzip + base64（浏览器内解压） | `scripts.py:11/42-72`；`hooked_request.js:17`；`streaming_init.js:50` | NO_FEATURE_ROW | feature 集无 gzip/压缩行 |
| N13 | wire 字段 12/14 语义改名（cached_content→session_token；location→request_context） | `wire_spec.py:24-25`；`wire_types.py:406`；`wire_codec.py:189/218` | D06-035 partial | 常量行不含“语义改名”与文档同步 |
| N14 | finish reason 22 CONTINUING（非终止）+ 18 MISSING_THOUGHT_SIGNATURE | `wire_spec.py:125`；`streaming.py:128`；`docs/WIRE_SPECIFICATION.md:320` | D06-015 partial | 映射行只列 0..17，无 18/22 |
| N15 | video_models（Veo）profile + image profile 纳入 imagen | `model_defaults.py:291-296`；`config.yaml:76-84` | NO_FEATURE_ROW | 无视频模型默认配置行 |
| N16 | CDP `$rpc/` Network.loadingFailed 证据链（rpc_net_requests/failures） | `cdp_client.py:266-274`；`transport.py:35-95`；`errors.py:30-34` | NO_FEATURE_ROW | 无“传输证据”行 |
| N17 | `defaults/config.yaml` 作为运行时引导模板（缺失即复制） | `config.py:71-99`；`defaults/config.yaml:1-3`；`Dockerfile:92-93` | NO_FEATURE_ROW | feature 集无“首次运行配置初始化”行 |

补充：`BrowserTransportError → HTTP 502/UNAVAILABLE` 归入 `D11-014`（应用级异常映射）与 `D06-063`（错误分类），不单列 NO_FEATURE_ROW，但见 C06/C08 的取舍。

---

## 5. 冲突登记表（20 项）

状态口径：`08-07 错误`（既有结论与上游代码不符）/ `旧决策失效`（现有 feature/CSV/12 的口径不再成立）/ `缺少决策`（上游能力无裁决）/ `需要用户取舍`（真实权衡，不能由审计单方面定）。

| ID | 现有 feature/决策 | 上游变化（证据） | 影响 | 候选选项 | 状态 |
|---|---|---|---|---|---|
| C01 | D01-006/D05-006/D08-022、`08-07 §3.1`、`10 §61` | 上游 `AccountRotator` 删除 `surface` 参数、`_get_available_accounts`、`get_next_account_with_stats`、`get_keepalive_candidate`；`record_*` 增 `usage` 去 `surface`（`account_rotator.py:529-535/820-882`） | fork 15 处调用点 + keepalive + routing_pipeline 失配；rotator_state.json schema 不兼容 | A 保留 surface 维度、只移植打分/配额；B 全面改成上游单维；C 适配层同时接受 `surface=` 但内部忽略 | 旧决策失效 + 需要用户取舍 |
| C02 | D05-005/006/014（surface 隔离）、D03-016（drop 多页） | `AccountStats.daily_models` 只按 model 键（`account_rotator.py:130/174-181`） | chat 的 429 会连带封 build（fork 要求分面隔离） | A 存储键继续 `surface::model`；B 放弃分面隔离；C 只对 build 面单独记账 | 需要用户取舍 |
| C03 | D03-017/018/019（均已 drop）、`08-07 §3.6` | 上游仍 blue-green：先 `create_new_page` 再 drain 旧 context（`session.py:211-259`）；`switch_auth` 直接走该路径（`:178-180`） | 与“1 账号 1 Context 1 Page”直接冲突 | A 按 08-07 rewrite（先关旧再建新）；B 采用上游双页；C 仅手动 rebind 用单页，抢占不复用该函数 | 08-07 判断正确；待确认实现选项 A |
| C04 | 无（新回归） | `_drain_retiring_context` 由“一次 12s 等待”改为 `while in_flight>0` 无限重试（`session.py:280-285`） | 旧页可能永不释放（长流/卡死），后台 task 常驻；与“无后台任务”约束冲突 | A 恢复一次性超时+强制关；B 保留重试但设总上限；C 不采用 drain | 缺少决策 |
| C05 | D02-012、`08-07 §3.1`（禁用账号不入候选） | `get_active_account` 在 store 内把 registry 改指向首个 enabled（`account_store.py:354-374`）；`toggle`/`apply_preset` 再基于“已回退后”的 active 判断（`routes_accounts.py:447-470/585-616`） | 禁用当前账号后浏览器会话仍绑旧账号（不重绑），registry/UI 却显示新账号 | A 控制面禁用/应用预设时显式 rebind；B registry 不自动改写，active=None 时拒绝服务；C 只允许“禁用非活跃账号” | 缺少决策（且为上游缺陷） |
| C06 | D02-026/D02-027、`08-07 §2` | `is_paid_model` 由静态子串规则判定；`get_required_tiers` 无规则且无 accessModes 时返回 `[]`；`ensure_active_account` 的 fallback 把空 tiers 当 `req_tier="Free"` → 只允许 Ultra（`model_discovery.py:344-441`；`account_orchestrator.py:186-190`） | paid=True 但无规则的模型会对 Pro 账号误报“需 Ultra”403；`*image*` 规则会把所有含 image 的模型判 Pro+ | A 先判 required_tiers 空就按 403 但仍尝试上游；B 补全规则/回退 Pro；C 不用静态规则，只信 accessModes | 需要用户取舍（含上游逻辑缺陷） |
| C07 | D05-009/D05-026、`08-07 §3.1` | `ensure_active_account` 在 attempt 0 对不满足模型的当前账号直接 `try_switch_account` → `activate_account` → `switch_auth`+`ensure_botguard`（`account_orchestrator.py:200-226`；`account_service.py:45-65`） | 每个付费请求可能触发完整 rebind（blue-green 双页）；sticky 阈值 0.8/tier_weights 可致“image→Pro、flash→Free”振荡 | A 仅准入拒绝（403）不抢占；B 抢占但要求“账号稳定窗口”；C 保留抢占、把 rebind 改为池内实例切换 | 需要用户取舍 |
| C08 | D11-013/014、D06-062（协议感知错误） | application 层直接 `raise HTTPException(403, detail={...})`（`account_orchestrator.py:212-226`） | 绕过 fork 的 protocol-aware 错误映射；OpenAI/Claude 客户端拿到非标准体 | A 抛领域错误由 API 层映射；B 保留 HTTPException 但在各协议面兜底；C 返回 503 语义 | 缺少决策 |
| C09 | `08-07 §3.4`、`12:104`、`10:41`（rotator fail-fast） | 见 §2.3：缺整块=静默默认；缺键=请求期 KeyError；无启动校验 | “P9-pre fail-fast”若照字面实现，会与上游默认模板策略打架；当前迁移若照抄会在运行期 500 | A 启动时严格校验 rotator 必填键；B 允许缺省但深合并默认；C 完全照上游（缺失静默/部分 500） | 08-07 错误 + 缺少决策 |
| C10 | D03-004（rewrite）、`10 §72`（D03-004 删 pipe watchdog） | 上游删除 `_spawn_process_watchdog`（`browser_engine.py` `@@ -765,63`），仅余进程组/Job Object/atexit | POSIX 父进程被 SIGKILL/OOM 后 Chromium 变孤儿；“watchdog”本是**孤儿回收**，不是“自动 stale 复活” | A 保留 watchdog（推荐：与无自动调度不矛盾）；B 按上游删除，接受孤儿风险；C 改为外部 supervisor | 旧决策失效（理由错误）+ 需要用户取舍 |
| C11 | D03-009（rewrite）、`10 §61/73`（disable liveness/CDP ping） | 删 `set_liveness_checker` 与轮询；`ping_interval/ping_timeout=None`（`cdp_client.py:98-101/219-224`） | 半开连接只能靠命令超时；fork `pooled_session.py:65` 直接 AttributeError | A 保留 liveness/ping；B 按上游删除并改 pool；C 只保留 ping 不保留轮询 | 需要用户取舍 + 旧决策需补 fork 影响 |
| C12 | D03-006（keep）、D03-033（单实例单页） | prune 从“关掉所有多余 page”收窄为“只关 about:blank”（`cdp_client.py:836-846`） | 失败切换/其他账号残留页会累积，违反 fork 页预算 | A 保持只关空白（上游）；B chat 面仍收紧、build 面放宽；C 关闭所有非活跃 target | 需要用户取舍 |
| C13 | D02-001/D02-031（原子持久化） | `_load_presets` 在持有 `threading.Lock` 时调用同样加锁的 `_save_presets`（`account_store.py:642-672`）；`__init__` 无条件写 `presets.json`（`:635-640`） | presets.json 被删/写失败时可能死锁；只读目录无法启动 | A 改 RLock 或去嵌套；B 懒加载不写盘；C 保留并文档化 | 缺少决策（缺陷） |
| C14 | `06-test-disposition.csv`（82 行）、`13 §G0.5`（28/28） | 上游新增 8 端点 0 路由测试；新测试只到 store/args 级（`test_account_presets.py`、`test_stream_transport.py:199+`） | “28/28 文件覆盖”被误读为行为覆盖；fork 若不补路由测试会漏 C05/C06 | A 新增 `test_routes_account_presets.py` 等；B 在既有 rewrite 行内补断言；C 接受缺口 | 08-07 错误（keep 定性）+ 缺少决策 |
| C15 | D11-024/D11-033/D11-038 | 新配置块未进 `web/src/components/config/schema.ts`（全文件无 rotator/tier_quotas/paid_model_rules/account_presets）；`SettingsView.js` 也无这些字符串 | 只能 YAML 模式编辑；可视化编辑器会“看不见”新键 | A 扩展 schema（含 tier_quotas 嵌套编辑器）；B 明确只支持 YAML；C 只读展示 | 缺少决策 |
| C16 | D11-028（账号页） | `updateNotes`/`batchToggleGroup` 有 API 无 UI；`notes` 无任何展示（`api/accounts.ts:93-108`；grep `notes` 仅类型/API） | 死接口 + 半成品能力 | A 补 notes/批量 UI；B 删除未用 API；C 保留 API 标注实验 | 缺少决策 |
| C17 | D10-017 / D11-038 | `finish_reasons` 被写入统计与 `/stats`，但无任何前端消费（`types/stats.ts:30` 之后无引用） | 死字段；增加 stats 体积 | A Dashboard 增 finish reason 分布；B 移除字段；C 保留仅 API | 缺少决策 |
| C18 | D06-015（finish 映射） | 5/6 互换、15/16/17 重排、新增 18（`api_service_gemini.py:47-64`；`docs/WIRE_SPECIFICATION.md:306-320`） | 客户端可见字符串改变；fork 的协议转换/测试/fixture 需同步 | A 采用上游新表；B 兼容映射旧值；C 仅内部改、对外保持旧名 | 旧决策失效 |
| C19 | D06-028/D06-031（模板缓存与签名） | snapshot hash 移除 executable_code/code_execution_result（`session.py:345-352`；`test_browser_session.py:271-277`） | 仅代码/工具结果变化时不再刷新模板 → 可能复用旧模板 | A 接受（减少重捕获）；B 保留代码 part 入 hash；C 只对 code execution 模型保留 | 需要用户取舍 |
| C20 | D06-036 / D11-006（OpenAI 兼容） | `seed` 仅 Gemini 归一化路径注入（`chat_service.py:809`）；OpenAI/Claude/Responses 无 seed | OpenAI 客户端传 seed 被静默丢弃 | A 四协议都支持；B 仅 Gemini（文档注明）；C 显式 400 | 缺少决策 |

---

## 6. 待用户决策清单（14 项，全部“待确认”）

> 以下每项都**未裁决**。推荐仅供参考，不构成最终决定；不得据此直接写业务代码。

| # | 决策点 | 选项 | 影响 | 推荐 | 状态 |
|---|---|---|---|---|---|
| D-01 | rotator 是否保留 surface 维度（C01/C02） | A 保留 `surface::model` 键并移植打分；B 全量改上游单维；C 仅 build 面独立 | A 改动小、保住分面隔离；B 最省事但 429 跨面污染；C 折中 | A | 待确认 |
| D-02 | 是否保留 pipe watchdog（C10） | A 保留；B 删除；C 外部 supervisor | A 保住 SIGKILL 孤儿回收；B 与上游一致但增加孤儿风险；C 需部署改造 | A（需用户确认与“无后台自动”不冲突） | 待确认 |
| D-03 | CDP liveness/ping 处置（C11） | A 全保留；B 全删；C 只保留 ping | A 半开检测最好、与上游分叉；B 需改 pool 并接受慢超时；C 折中 | C | 待确认 |
| D-04 | 付费模型抢占策略（C07） | A 只做 403 准入；B 请求期抢占；C 抢占+稳定窗口 | A 无 rebind、可能多 403；B 体验好但振荡/双页；C 复杂 | A（P5 最小可用） | 待确认 |
| D-05 | 控制面禁用/应用预设是否自动 rebind（C05） | A 自动 rebind；B active=None 时拒绝；C 只允许禁用非活跃 | A 语义正确但要处理在途；B 简单但影响可用性；C 限制操作 | A | 待确认 |
| D-06 | 403 的产生层（C08） | A 领域错误→API 映射；B 保留 HTTPException；C 503 | A 四协议一致；B 快但 OpenAI/Claude 体不标准；C 语义混淆 | A | 待确认 |
| D-07 | rotator 配置校验（C09） | A 启动严格校验；B 深合并默认；C 照上游 | A 最早失败；B 最稳；C 会请求期 500 | A 或 B（需定一个） | 待确认 |
| D-08 | 预设持久化位置/形态（N04/C13） | A `data/accounts/presets.json`（上游）；B 只存 `config.yaml`；C 双写 | A 支持运行时 CRUD；B 无运行时增删；C 复杂 | A，但需修锁 | 待确认 |
| D-09 | 模板 hash 是否纳入代码 part（C19） | A 不纳入（上游）；B 纳入；C 按模型 | A 少重捕获、可能脏模板；B 安全但慢；C 折中 | B（若 F-01 签名敏感） | 待确认 |
| D-10 | drain 旧页策略（C04） | A 一次超时强制关；B 有界重试；C 不 drain | A 与旧行为一致；B 更温和但后台常驻；C 可能打断流 | A | 待确认 |
| D-11 | 页面 prune 范围（C12） | A 只关 about:blank；B 分面策略；C 全关非活跃 | A 安全但泄漏；B 复杂；C 违反“另一账号 drain 中”场景 | A（配合 1 页硬约束的显式检查） | 待确认 |
| D-12 | 新配置块是否进可视化编辑器（C15） | A 扩展 schema；B 仅 YAML；C 只读 | A 成本高；B 最省；C 折中 | B（P5 前），A 后置 | 待确认 |
| D-13 | notes / 批量分组 API 去留（C16） | A 补 UI；B 删 API；C 保留实验 | A 完整；B 收敛；C 留债 | B 或 A | 待确认 |
| D-14 | `api_keys: []` 在 `config.yaml`/`defaults/config.yaml` 的语义（C21 相关） | A 保持上游“空即开放”；B 改为“空即拒绝，需显式 opt-in”；C 引入 `allow_anonymous: true` | A 与 fork 安全默认冲突；B 安全但要改默认模板与文档；C 最明确 | B（与既有 08-07 方向一致，但需用户确认具体开关名） | 待确认 |

---

## 7. 测试处置修正表（只给建议，**不修改 CSV**）

现有 CSV：82 数据行（`tests/**/*.py` 主 rebuild 80 + 上游新增 2）。以下为本次审计发现需要变更/补充的行。

### 7.1 需要改处置或改理由的行

| CSV 行（test_file） | 现处置 | 建议 | 理由 |
|---|---|---|---|
| `tests/unit/test_account_presets.py` | keep | **rewrite** | 上游只有 store 级 3 例；fork 需要补 toggle/group/notes/groups/presets 8 端点的路由测试，并验证 C05 的“禁用不重绑”缺陷 |
| `tests/unit/test_tier_preemption.py` | rewrite | rewrite（理由补全） | 必须补：paid 但无规则→Ultra-only 的 fallback（C06）；抢占是否允许触发 `switch_auth`（C07）；403 的协议体（C08） |
| `tests/unit/test_api_responses.py` | rewrite（理由写 P8 Responses 后置） | rewrite（理由改为 P5 协议/统计，不含 Responses 端到端） | 本次新增断言是 `cachedContentTokenCount`、502、finish 表，属 P5；与 Responses 后置无关 |
| `tests/unit/test_browser_engine.py` | rewrite（删 watchdog 用例） | **条件化 keep** | 若 D-02 选“保留 watchdog”，该用例必须恢复；只有选“删除”才成立 |
| `tests/unit/test_cdp_client.py` | rewrite（删 liveness 用例） | rewrite（条件化） | 同 D-03；另需新增“只关 about:blank”与“非空白页保留”的显式断言（上游已加一半） |
| `tests/unit/test_session_lifecycle.py` | rewrite | rewrite（补用例） | 上游净删 49 行、零新增；必须补：无 idle 回收、ensure_ready 门、drain 有界、rebind 状态序列 |
| `tests/unit/test_cookie_import.py` | rewrite | **keep + 迁移** | 上游已按 CSV 要求加 monkeypatch，能力未变；只需把该做法带进 fork |
| `tests/unit/test_runtime_persistence.py` | rewrite | rewrite（补断言） | 上游删掉了 `auth_errors == 1` 断言，覆盖下降；需补 rotator_state schema 迁移（旧 `model_cooldowns` → `daily_models`） |
| `tests/unit/test_wire_codec.py` | rewrite（理由 F-11 toolConfig/Struct） | rewrite（理由加 seed/session_token/malformed） | 本次还改了字段改名、decode 严格性、`_decode_part` 单一来源 |
| `tests/unit/test_wire_parser.py` | rewrite（理由 finish/toolcall） | rewrite（理由加 CONTINUING/role guard/structural payload） | 新增 22 语义与 `[None,"model"]` 边界 |
| `tests/unit/test_stream_transport.py` | rewrite（理由 gzip/空 200） | rewrite（补 CDP net evidence、CONTINUING） | 上游新增 168 行，理由需覆盖全部 3 个新维度 |
| `tests/unit/test_account_rotation_concurrency.py` | rewrite | rewrite（补 surface 键迁移） | 若 D-01 选 A，必须补 `build::model` 键的跨日/冷却用例 |
| `tests/conftest.py` | rewrite | rewrite（补 fixture） | 现 fixture 缺 Plus/Ultra_20X/video_models；Plus 用例会静默落到 Free 配额，属“测试骗绿”风险 |

### 7.2 缺行（CSV 无对应文件，需新增行）

| 建议新文件 | 覆盖 | 关联 |
|---|---|---|
| `tests/unit/test_routes_account_presets.py` | `POST /{id}/toggle`、`PUT /{id}/group`、`PUT /{id}/notes`、`GET /groups/list`、`POST /groups/{g}/toggle`、`GET/POST/DELETE /presets*`、`POST /presets/{id}/apply` | C05 C14 N01-N04 |
| `tests/unit/test_model_tier_admission.py`（或并入 `test_model_access.py`） | `is_paid_model` / `get_required_tiers` / `is_model_allowed_for_tier` 的规则、accessModes、空规则 fallback | C06 N07 |
| `tests/unit/test_stats_account_breakdown.py`（或并入 `test_stats_routes.py`） | `/stats` breakdown 字段、disabled/cooldown/quota 计算、只读端点不应变异状态 | N09 C15 |
| `tests/unit/test_gzip_transport.py`（或并入 `test_stream_transport.py`） | 96KiB 边界、已有 Content-Encoding、base64 往返、真实 replay 路径 | N12 |

### 7.3 与 `13 §G0.5` 的差异

`13-gate0-execution-record.md:58-61` 的 “28/28” 是**文件级**覆盖，成立；但它不等于**行为级**覆盖。上游 `routes_accounts.py` 新增 228 行/8 端点没有任何路由测试，这一点未被 13 记录。

---

## 8. UI / 静态资源专项（08-07 漏项最集中处）

### 8.1 源码 → 产物映射（已逐项核对）

| 产物 | 大小变化 | 对应源码 | 结论 |
|---|---|---|---|
| `AccountsView.js` | 32707→40906（+8199） | `views/AccountsView.vue`、`stores/accounts.ts`、`api/accounts.ts`、`AccountRow.vue` | 同步；含预设/分组/快照/toggle 字符串 |
| `DashboardView.js` | 7550→13166（+5616） | `views/DashboardView.vue`、`ModelStatsTable.vue`、`types/stats.ts` | 同步；含思考/缓存卡片与 `account_breakdown`×6 |
| `SettingsView.js` | 71106→71161（+55） | `views/SettingsView.vue`、`components/config/*`（**均未改**） | 仅 chunk 重排；**新配置块不在可视编辑器** |
| `index.js` | 28488→30944（+2456） | `api/*`、`stores/*`、`AppLayout` | 同步；`presets`×9、`applyPreset`×3 |
| `index.css` | 49697→52443（+2746） | Tailwind 扫描新模板类 | 同步；新增 toggle/6 列/灰度类 |
| `vendor-icons.js` | 24717→26351（+1634） | lucide 新图标 | 同步；Brain/Database/Tag/Folder/Trash2/Users 等 |
| `vendor-vue.js` | 111933→111941（+8） | 无源码变化 | 仅导出别名重排（`or/xa/ka` 等），**不是依赖升级** |

> 结论：不存在“bundle changed 但源码没变”的欺骗性提交；7 个产物都能对应到 13 个 web 源码文件中的具体改动（SettingsView 仅重排）。

### 8.2 08-07 漏掉的 UI 事实

1. **新配置块不可视化编辑**：`web/src/components/config/schema.ts`（272 行）无 `rotator`/`tier_quotas`/`paid_model_rules`/`account_presets`；`SettingsView.js` 亦无这些字符串。用户只能在 YAML 模式编辑。08-07 写“原生配置面 adopt”，未提这一点。
2. **`ProfileCard.vue` 修了一个真实 bug**：`规则分组名称` 输入此前没有绑定 `:model-value`（不受控），本次补上（`:140`）；`mediaResolution` 事件改为 `as any`（`:263`）。08-07 未提。
3. **notes 完全无 UI**：`AccountRow.vue` 只做了启停与分组；`account.notes` 无展示/编辑；`updateNotes` API 无调用者。
4. **批量分组启停无 UI**：`batchToggleGroup` 仅存在于 `api/accounts.ts`。
5. **`finish_reasons` 无 UI**：类型与后端都有，`ModelStatsTable.vue`/`DashboardView.vue` 未消费。
6. **`AccountRow.vue` 的 enabled switch 没有二次确认**，点击即生效，且后端会触发 `try_switch_account`（见 C05）——UI 上像是“纯本地开关”，实际可能动会话。
7. **`AccountsView.vue` 双首屏请求**：`onMounted(loadData)` + `usePolling(loadData, 8000)`（`usePolling` 默认 `immediate !== false`）→ 首次渲染两次 `fetchAll+fetchPresets+fetchGroups`。
8. **分组 tab 的计数只显示“全部分组”数量**，各分组 tab 无计数；`allGroups` 合并了 API groups 与账号 group，可能显示空分组。
9. **预设应用无 loading 逐项禁用以外的反馈**：删除自定义预设用原生 `confirm`，与其余 Modal 风格不一致。
10. **`AccountWithStats` 类型收紧为 `enabled/group` 必填**，但 `activeAccount` 是 `Account`；若后端旧数据缺字段（迁移前），前端会在运行时得到 `undefined`，`isEnabled` 用 `!== false` 兜底，但过滤 `(a.group || 'default')` 也兜底——这一点前端比后端稳。

### 8.3 后端 API 契约变化（UI 直接依赖）

- `AccountResponse` 新增 `enabled/group/notes`：`routes_accounts.py:39-46`。
- `GET /api/accounts/active` 在“全部禁用”时返回 **404**（`routes_accounts.py:178-186`），而 `stores/accounts.ts:58-63` 会回退到 `accounts[0]`（可能是 disabled）→ Dashboard “当前激活账号”可能显示一个已停用账号。
- `/stats` 新增 `account_breakdown`（`response_models.py:67-83`），前端 `ModelStatsTable.vue:216-300` 直接渲染 `acc.today_*`。

---

## 9. 对 08-07 / 10 / 12 / 13 的具体纠错清单

| # | 文档位置 | 原文/含义 | 实证 | 影响 |
|---|---|---|---|---|
| 1 | 08-07 §2 行“新增 rotator…”；§3.4 | “rotator 缺键时上游直接 `RuntimeError`，必须纳入 P9-pre fail-fast” | 缺整块 → `_default_config()` 静默；缺键 → `KeyError`（`model_defaults.py:452-470`；`account_rotator.py:713-729`） | P9-pre 若按“RuntimeError”写测试会测不到；按“fail-fast”实现需先补校验 |
| 2 | 08-07 §2 行“AccountStats 改为…” | 只描述打分/软配额，未提 API 面删除 | `surface` 维度、`get_keepalive_candidate`、`get_next_account_with_stats` 均消失（§2.2） | 迁移会低估工作量，且漏掉 fork 调用点断裂 |
| 3 | 08-07 §3.2 | 把“进程 watchdog”与“无后台自动 stale 复活”归为一类并 adopt 删除 | watchdog 是父进程死亡后的**孤儿回收**（`browser_engine.py` 被删块注释原文 “companion process monitors parent via pipe EOF”） | 删除会削弱崩溃清理；理由不成立，需重新取舍（C10） |
| 4 | 08-07 §2 行“移除 browser_idle_timeout、idle 回收、进程 watchdog、liveness checker、CDP ping” | 一句话带过 CDP ping | `ping_interval/ping_timeout` 5→None（`cdp_client.py:98-101`），且 fork `pooled_session.py:65` 绑定 liveness | 半开连接检测与 fork 兼容性未评估（C11） |
| 5 | 08-07 §2 行“close_context 等待释放；只清理 about:blank 备用页” | 视为 adopt 的纯收益 | prune 收窄意味着**非空白多余页保留**（`cdp_client.py:836-846`） | 与“1 页/账号预算”冲突（C12） |
| 6 | 08-07 §5 + CSV 第 82 行 | `test_account_presets.py` = keep | 只有 store 级 3 例；8 个新端点 0 测试（`tests/unit/test_account_presets.py`） | 会误判“账号域已验收”（C14） |
| 7 | 08-07 §2 行“Stats 新字段” | 只说字段 adopt | 未提 `routes_system.stats_response` 在 GET 内 `st._check_date_reset`（`routes_system.py:73`）与 O(models×accounts) | 只读端点副作用与时延风险未记录 |
| 8 | 08-07 §2 行“流式不再遇 finish_reason 提前 break” | 只说这一点 | 同时还改了 finish 表编号（5/6、15/16/17、+18）、加 22 过滤、空流 502（`api_service_gemini.py:47-64`；`streaming.py:128/165`） | 客户端可见字符串变化未纳入影响面（C18） |
| 9 | 08-07 全文 | 未提 `seed` | `schemas.py:164`、`wire_spec.py:63`、`chat_service.py:809` | 新生成参数被漏审（N11/C20） |
| 10 | 08-07 全文 | 未提 snapshot hash 变化 | `session.py:345-352` 删除 code part；`test_browser_session.py:271-277` 期望 `""` | 模板复用语义变化（C19） |
| 11 | 08-07 §2 行“get_active_account 只回退 enabled 账号” | 视为纯收益 | 同时会在 store 内**改写并持久化** active id（`account_store.py:354-374`），并在全禁用时返回 None（`/active` 404） | 引出 C05 的“禁用不重绑”缺陷 |
| 12 | 08-07 §2 行“传输 >=96KB 自动 gzip” | 未说实现位置/边界 | 浏览器内 `atob`→`Uint8Array`；base64 膨胀；已有 `Content-Encoding` 时跳过（`scripts.py:42-72`；`hooked_request.js:17`；`streaming_init.js:50`） | 复现与排障需要知道是 JS 侧 |
| 13 | 12-gate0-inputs.md:104、10 §41/§61 | “rotator 缺键 fail-fast” | 同 #1 | P9-pre 测试口径需改 |
| 14 | 13-gate0-execution-record.md:81-84 | “G0.4 通过（无 pending）”“G0.5 28/28” | 行为级缺口（§7.3）；本文新增 14 项待决策 | “无 pending”不成立；应改为“有 14 项待用户确认” |
| 15 | 08-07 §2 行“上游 API Key 语义” | “config.yaml 与 env 均无 Key 时免密开放” | 实际是 `api_keys: []` **显式**表示开放（`config.yaml:1-3`、`defaults/config.yaml:1-2`），并作为运行时引导模板（`config.py:71-99`） | 安全默认必须改的是“默认文件内容”，不只是解析逻辑 |
| 16 | 08-07 全文 | 未提 `defaults/config.yaml` 的运行时作用 | 同上 | 误以为是文档模板，实际是首启配置来源 |

---

## 10. 修复阶段建议（只给顺序与依赖，不实施）

> 依赖方向：先“事实/契约”，再“核心账本”，最后“UI/统计”。任何一步都不应在 D-01…D-14 未确认前动业务代码。

| 阶段 | 内容 | 依赖 | 产出/验收 |
|---|---|---|---|
| F0 | 用户确认 §6 的 D-01…D-14（尤其 D-01/D-02/D-04/D-05） | 本文 | 决策记录（新增文档，不改既有 CSV 语义） |
| F1 | 定义 fork 侧 rotator 契约：是否保留 `surface`、`get_keepalive_candidate`、`record_*(usage=...)`、state schema 迁移 | F0 D-01/D-02/D-03 | 一页接口契约 + 迁移映射表（旧 `model_cooldowns`→`daily_models`） |
| F2 | 移植“纯协议/传输”低耦合修复：wire 改名+seed+CONTINUING+role guard+structural payload；transport net evidence；gzip；finish 表；空 200/空流 | 无（可与 F1 并行） | `test_wire_*`、`test_stream_transport`、`test_api_responses` 绿 |
| F3 | 账号域：enabled/group/notes + presets store + 8 路由 + **修正 C05 的 rebind 语义** | F1（active/candidate 语义） | 新 `test_routes_account_presets.py`；禁用生效测试 |
| F4 | 准入/抢占：`is_paid_model`/`required_tiers` 去歧义（C06）、403 产生层（C08）、抢占策略（D-04） | F0 D-04/D-06；F1 | `test_model_tier_admission.py`；无“Ultra-only 误 403” |
| F5 | 会话/生命周期：blue-green 改写、drain 有界、无 idle、ensure_ready、prune 策略、liveness/ping 取舍 | F0 D-02/D-03/D-09/D-10/D-11 | `test_session_lifecycle`、`test_cdp_client`、`test_browser_session` 重写 |
| F6 | 统计：`/stats` breakdown、finish_reasons、reasoning/cached；只读端点去副作用 | F3（账号元数据）、F2（usage） | `test_stats_routes`/新 breakdown 测试 |
| F7 | Web：Accounts 预设/分组/启停、Dashboard 卡片与 breakdown、ProfileCard 修复；决定 C15/C16/C17 的 UI 面 | F3/F6 API 稳定后 | 重新 build，`static/assets` 与源码一致（7 产物可复现） |
| F8 | 测试处置落地（§7），更新 `06-test-disposition.csv` 与 13 记录 | F1-F7 | 行为级覆盖，而非仅文件数 |

顺序红线：F2 与 F3 不可同时改 `account_rotator.py`/`session.py`（同文件冲突）；F5 必须在 F3 之后（rebind 语义依赖 active 规则）。

---

## 11. 覆盖率声明

- **本次审计覆盖 76/76 文件**（§3 表 76 行，逐行对应 `git diff --numstat 4623109..99c38ba`）。
- 其中：`.env/README` 3、配置/模板 2、docs 2、`src/api` 7、`src/application` 5、`src/config+domain` 2、`src/infrastructure` 19、`static/assets` 7、`tests` 15、`web` 14。
- 无“其他/杂项”跳过项；无未覆盖文件。
- 未做的事（明确声明，不算覆盖）：未运行上游测试套件（只读审计，且不安装依赖）；未访问真实账号/IP/Google；未验证 `static/assets` 可否在本地复现构建（只核对了产物↔源码字符串与大小）；未对 fork 现状做运行时回归（只做静态调用面核对）。
- 证据可独立复核：所有结论均给 `99c38ba` 行号或 hunk；`git show 99c38ba:<path>` 即可复验。

---

## 12. 附录：关键证据索引（便于快速抽查）

| 主题 | 命令 | 关键行 |
|---|---|---|
| rotator 单维/评分 | `git show 99c38ba:src/aistudio_api/application/account_rotator.py` | `:633-686`（候选）、`:688-757`（打分）、`:759-818`（选择） |
| 软配额 | `git show 99c38ba:src/aistudio_api/infrastructure/gateway/model_defaults.py` | `:654-739` |
| 抢占/403 | `git show 99c38ba:src/aistudio_api/application/account_orchestrator.py` | `:172-226` |
| active 回退/预设 | `git show 99c38ba:src/aistudio_api/infrastructure/account/account_store.py` | `:354-374`、`:635-770` |
| 新路由 | `git show 99c38ba:src/aistudio_api/api/routes_accounts.py` | `:446-616` |
| blue-green/drain | `git show 99c38ba:src/aistudio_api/infrastructure/gateway/session.py` | `:211-297` |
| gzip | `git show 99c38ba:src/aistudio_api/infrastructure/browser/scripts.py` | `:42-72` |
| wire 改名/seed | `git show 99c38ba:src/aistudio_api/infrastructure/gateway/wire_spec.py` | `:21-25`、`:60-65`、`:122-125` |
| finish 22/empty stream | `git show 99c38ba:src/aistudio_api/infrastructure/gateway/streaming.py` | `:116-166` |
| CDP 移除项 | `git show 99c38ba:src/aistudio_api/infrastructure/browser/cdp_client.py` | `:98-101`、`:219-224`、`:836-846` |
| 统计 breakdown | `git show 99c38ba:src/aistudio_api/api/routes_system.py` | `:39-112` |
| 配置加载语义 | `git show 99c38ba:src/aistudio_api/infrastructure/gateway/model_defaults.py` | `:452-470` |
| 配置引导模板 | `git show 99c38ba:src/aistudio_api/config.py` | `:68-99` |

---

（完）
