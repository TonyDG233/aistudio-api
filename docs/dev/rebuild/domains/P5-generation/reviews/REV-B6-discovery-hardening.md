# REV-B6-discovery-hardening 独立只读离线审计

- 审计日期：2026-10-31
- 审计对象：`.kilo/worktrees/rebuild-upstream` 当前未提交改动（B6 模型发现/动态 Paid/证据更正/启动防双预热）
- 审计方式：只读文件与离线测试；未启动 `main.py server`，未启动 Chromium/CloakBrowser，未访问真实 Google/上游，未修改源码/测试/配置，未 stage/commit
- 结论：**PASS（审计范围内无 blocker）**
- 重要限制：`docs/dev/evidence/B6/model_discovery_fix.json` 的历史 live 运行无法在本离线审计中重放；本报告只核对其与当前代码/快照的内部一致性，不把“历史记录”冒充“本轮 live 复验”

---

## 0. 结论摘要

| 项目 | 结果 |
|---|---|
| 启动/双重预热 | PASS：pool 启用时 `client.warmup()` 不自动启动；`InstanceManager.startup()` 仅启动一次；`ensure_cdp_client` 不建 Page/不导航/不跑 BotGuard；shutdown 取消任务后关闭 |
| Pool-aware discovery | PASS：`discovery_sessions()` 只取 READY slot，paid 先 Free 后；不增 `active_requests`、不 retire；三个模型路由已改为 pool-aware |
| 动态 Paid | PASS：live `paid/access_tier/accessModes` 写入快照并驱动路由；未命中静态规则的动态 Paid 仍走 Pro；Free-only 场景本地 `TierRequiredError` |
| Paid rules/历史 | PASS（有 Q）：`defaults/config.yaml:87-93`、`model_defaults.py` 校验/getter、`99c38ba`/`a9e7dc3` 均核对一致；静态 `image` 规则与 live `paid=false` 的冲突属既定优先级，交用户裁决 |
| B6 证据更正 | PASS：原始 14 条明确标为 fallback；later live-fix 单独隔离；B6 维持 FAIL/PARTIAL；未发现虚假 live 声明 |
| AGENT.md | PASS：§4.6/§10.4 已包含实机防风控铁律；§4.1–§4.8 编号连续；与 §4.4 不矛盾 |
| Tests/hygiene | PASS：746 passed / 2 既有 Windows 失败 / 1 skipped；ruff 0；pyright 0；diff check 0；无 service/browser/network；根 `config.yaml` 仍 untracked |
| Blockers | **无**（Q/N 见第 7、8 节；均为非阻塞裁决项/说明项） |

---

## 1. 审计边界、仓库状态与门禁

### 1.1 审计前后仓库状态

审计开始时 worktree 未提交改动：

```text
 M AGENT.md
 M src/aistudio_api/api/app.py
 M src/aistudio_api/api/routes_models.py
 M src/aistudio_api/api/routes_openai.py
 M src/aistudio_api/api/state.py
 M src/aistudio_api/application/instance_manager.py
 M src/aistudio_api/infrastructure/gateway/model_discovery.py
 M tests/conftest.py
?? docs/dev/evidence/B6/
?? docs/dev/rebuild/domains/P5-generation/reviews/REV-B6-live-verification.md
?? tests/unit/test_app_startup_warmup.py
?? tests/unit/test_model_discovery_live_wiring.py
```

- 分支：`rebuild-upstream`
- HEAD：`9bffda1 feat(rebuild): 完成 OpenAI/ATG 最小闭环与生成主链`
- 根仓库审计前 `git status --porcelain` 为空；根仓库 `git ls-files config.yaml` 为空
- `git check-ignore -v config.yaml` → `.gitignore:15:/config.yaml`
- `data/models_cache.json` 存在（31 条 live 快照），被 `.gitignore:12:/data/` 忽略，未入库
- 本审计只新增两份同名报告；未 stage、未 commit、未改源码/测试/配置

### 1.2 精确门禁

```text
$env:PYTHONUTF8='1'; $env:PYTHONIOENCODING='utf-8'; uv run pytest -q
=> 2 failed, 746 passed, 1 skipped in 8.93s

uv run ruff check src tests
=> All checks passed!  (exit 0)

uv run pyright src tests
=> 0 errors, 0 warnings, 0 informations  (exit 0)

git diff --check
=> exit 0（仅 LF→CRLF warning）

git diff --cached --check
=> exit 0；且 git diff --cached --stat 为空（无 staged 文件）
```

两个失败均为既有 Windows 平台差异，未在本步改动：

1. `tests/unit/test_browser_engine.py::test_find_chromium_executable_strictly_requires_cloakbrowser`
   - Windows 上 `/opt/cloakbrowser` 的 `Path` 字符串化为 `\opt\cloakbrowser`，测试的 `fake_opt_is_dir` 只匹配 POSIX 字符串；属路径语义问题。
2. `tests/unit/test_browser_scripts_syntax.py::test_evaluate_wrapper_expressions_parse_cleanly`
   - `bun.CMD -e` 经 `cmd.exe` 包装后报 `\n was unexpected at this time`；属 Windows 命令 shim 问题，不是 JS 语法错误。

1 skipped 为 POSIX-only watchdog 用例。上述失败/跳过在 `REV-B2-1-p1-account.md`、`REV-B2-4-p2-routing.md`、`REV-B5a-toolcall.md`、`REV-B5b-openai-atg-seam.md` 等历史报告中已记录，计数演进与本轮 746 passed 一致。

### 1.3 无 service / 无 browser / 无 network 声明

审计期间：

```text
port 8080 closed
port 9222 closed
chrome_count=0
main_server_processes=0
```

- 未启动 `main.py server`；未启动 Chromium/CloakBrowser；未调用任何真实 Google/上游接口。
- `pytest` 使用本地 fixture/mock；唯一外部子进程是 `bun.CMD -e` 的本地 JS 语法检查（因 Windows shim 失败），不是网络/浏览器/服务。
- `uv run` 使用本地既有环境；审计未主动发起外部网络请求。

---

## 2. 启动与双重预热审计

### 2.1 lifespan 实际分流

`src/aistudio_api/api/app.py`：

- `_warmup_control_session()`：`72-86`，仅 legacy 控制面预热，重试 3 次。
- `_start_control_warmup(client, pool_enabled=...)`：`89-95`，`pool_enabled=True` 返回 `None`，不创建 task。
- `_startup_instances()`：`98-114`，仅当 `warmup_task` 存在才 await；随后调用 `manager.startup()` 一次。
- `_refresh_model_catalog_once()`：`117-128`，只取 `state.discovery_sessions()`，不取 `client._session`。
- `lifespan`：
  - `pool_specs = get_configured_instance_specs()` / `pool_enabled = bool(pool_specs)`：`216-217`
  - pool 启用日志与跳过控制面预热：`218-226`
  - `InstancePool(client.session.ensure_cdp_client, ...)`：`228-235`
  - `startup_task = asyncio.create_task(_startup_instances(...))`：`255-256`
  - `_model_warmup()` 先 await `startup_task`，再循环刷新模型目录：`262-290`
  - shutdown 传入三个 task：`293-297`
- `_shutdown_runtime()`：`131-154`，先 cancel 未完成任务并 `gather(return_exceptions=True)`，再 keepalive → instance_manager → client。

结论：

- pool 启用时 `warmup_task is None`，不会自动启动 `client.warmup()`。
- `manager.startup()` 只由 `_startup_instances()` 调用一次；`RoutingPipeline._ensure_started()`（`routing_pipeline.py:477-486`）在 `configured_surfaces` 非空时直接返回，且 `InstanceManager.startup()` 自身有 `_lifecycle_lock` + `if self._records: return` 幂等保护（`instance_manager.py:150-154`），不会第二次启动。
- 模型预热等待 `startup_task` 完成，再取 READY pool sessions；不会在控制面 Page 上跑 ListModels。
- shutdown 只取消实际存在的 task；pool 启用时不存在控制面 warmup task，无泄漏。

### 2.2 `ensure_cdp_client` 不建控制 Page / 不导航 / 不跑 BotGuard

`src/aistudio_api/infrastructure/gateway/session.py`：

- `ensure_cdp_client()`：`567-572`，调用 `_ensure_browser_cdp(connect_page=False)`，只保证进程/CDP endpoint。
- `_ensure_browser_cdp(connect_page=False)`：`574-597`，若进程/CDP 已存在直接返回；否则 `launch_chromium_process` + `wait_until_ready`，在 `connect_page=False` 时 `return None`，不 `connect_page`、不 `navigate_to_aistudio`、不跑 `bootstrap_botguard_and_template`。
- `ensure_botguard_service()`：`365-376`，只在 `client.warmup()`（`client.py:99-103`）或手动 `activate_account`（`account_service.py:51-71`）路径调用；pool 启动路径不调用。

测试：

- `tests/unit/test_app_startup_warmup.py:57-66`：mock `_ensure_browser_cdp`，断言 `ensure_cdp_client()` 以 `connect_page=False` 调用，且 `session.page is None`。
- `tests/unit/test_app_startup_warmup.py:18-36`：pool 启用时 `warmup_task is None`、`client.warmup.await_count == 0`、`manager.startup.await_count == 1`。

### 2.3 启动双预热/同账号双页结论

- 启动路径：**未发现**控制面 + pool 双重预热，也未发现同账号双页。
- `InstanceManager.startup()` 用 `used` 集合为每个 slot 选不同账号（`instance_manager.py:183-187`），且 `_normalize_instance_specs` 会合并同 surface/min_tier（`model_defaults.py:257-311`），因此同一 lane 不会为同账号重复开页。
- `InstanceManager.close(None)` → `close_all()`（`instance_manager.py:299-302,651-663`），shutdown 会关闭 pool slots 并把 browser 进程交给 `client.close()` 终止。
- 残留 manual 路径：`POST /accounts/{id}/activate` 使用控制面 `ensure_botguard_service`（`routes_accounts.py:363-382` + `account_service.py:51-71`），且未检查该账号是否已有 pool 实例；这是**既有、手动、非启动**路径，见 N12/Q5，不由本 diff 引入。

---

## 3. Pool-aware discovery 与动态 Paid

### 3.1 `discovery_sessions()` 语义

`src/aistudio_api/application/instance_manager.py:607-625`：

- 调 `self._sync_records()`；
- 只遍历 `SlotState.READY`，跳过 `session is None` 或 `page_closed()`；
- 按 `slot.lane == "paid"` 分到 paid 列表，其余到 free 列表；
- 返回 `paid + free`；
- 只读 `slot.state/session/page`，不调用 `acquire_request`、不 close/retire/re-lane。

`src/aistudio_api/api/state.py`：

- `RuntimeState.discovery_sessions()`：`104-120`，只从 `instance_manager.discovery_sessions()` 取，**不回退控制面**。
- `discovery_sessions_for(state)`：`27-45`，优先 pool sessions；为空时走 `discovery_session_for()`。
- `discovery_session_for(state)`：`48-66`，先 `manager.discovery_session()`，再回退 `client._session`（见 N3）。

`src/aistudio_api/api/routes_models.py`：

- `15` 导入 `discovery_sessions_for`；
- `/v1beta/models`：`81-84`，`get_models(sessions=sessions)`；
- `/v1beta/models/{model_id}`：`95-99`，`get_model_by_id(..., sessions=sessions)`。

`src/aistudio_api/api/routes_openai.py`：

- `21` 导入；
- `/v1/models`：`165-169`；
- `/v1/models/{model_id}`：`184-189`；
- 均改为 `sessions=sessions`。

结论：正常（有 READY pool slot）时，三个模型路由走 pool session；只有 pool 为空时才可能走控制面回退（N3，非启动路径）。

### 3.2 Page XHR 使用 pool session 自己的凭据

`src/aistudio_api/infrastructure/gateway/model_discovery.py`：

- `_session_context/_session_auth_file/_session_auth_user/_session_tier`：`94-165`，优先 `active_context.auth_file/auth_user/tier`，兼容 `_auth_file` 与 getter。
- `get_models()`：`335-474`
  - `sessions` → candidates（`366-375`）；
  - 每个 candidate：取 `page`、检查 closed、取该 session 的 `auth_file`（`380-390`）；
  - `calculate_sapisid_hash(candidate_cookies)` 生成 `Authorization`（`391-395`）；
  - `_fetch_via_page(page, authorization=..., auth_user=..., tier=...)`（`396-401`）。
- `_fetch_via_page()`：`602-669`
  - 设置 `X-Goog-AuthUser`、`Authorization`、`X-AIStudio-G1-Tier`（`620-628`）；
  - 仍保留 `X-Goog-Api-Key` / `X-Goog-Ext-519733851-bin` / `withCredentials`；
  - XHR 非 200/超时/解析空都抛错（`657-668`），不再静默返回 `[]`。
- HTTP fallback：`414-454`，使用 pool session 的 cookies/auth_user/tier；失败会记录 `last_error`，不再伪装 live。
- 快照：`_save_snapshot()` `318-333` 写完整 model dict；`_load_snapshot()` `295-316` 读回并 `set_paid_models`。

### 3.3 fallback 诚实标记与 16 条保底

- `last_source` 初始化 `fallback`（`282`）；快照加载置 `snapshot`（`312`）；只有 `_accept_live()` 成功才置 `live_page`/`live_http`（`476-486`）。
- 无 pool session/网络失败且无缓存时返回 `_FALLBACK_MODELS` 并置 `fallback`（`456-465`）。
- `_FALLBACK_MODELS` 实测 16 条（离线导入计数）：
  `gemini-3.8-flash, gemini-4-argon, gemini-3-flash-preview, gemini-3.7-flash, gemini-3.5-flash-lite, gemini-3.1-pro-preview, gemini-3.6-flash, gemini-3.5-flash, gemini-3.1-flash-image, gemini-3-pro-image, gemma-4-31b-it, gemma-4-26b-a4b-it, veo-3.1-generate-preview, gemini-pro-latest, gemini-flash-latest, gemini-flash-lite-latest`
- 测试 `tests/unit/test_model_discovery_live_wiring.py:215-225` 断言 16 条、`last_source == "fallback"`、不属于 `LIVE_SOURCES`。

### 3.4 31 条 live 证据核对（离线一致性）

`docs/dev/evidence/B6/model_discovery_fix.json`：

- `discovery.last_source = "live_page"`、`warmup_count = 31`、`model_count = 31`（`8-15`）；
- 实测 JSON `models` 数组 31 条；
- `data/models_cache.json` 的 `fetched_at = 1793394499.3724995` 与证据 `snapshot_fetched_at` 完全一致；快照中 31 条且含 `paid/access_tier/accessModes`；
- 关键模型：
  - `antigravity-preview-09-2026`：live-only，`paid=true`、`access_tier=2`、`accessModes=[1]`、`required_tiers=[Pro,Ultra,Ultra_20X]`
  - `gemini-3.6-flash`：`paid=true`、`access_tier=2`、`required_tiers=[Pro,Ultra,Ultra_20X]`
  - `gemini-3.1-pro-preview`：`paid=false`、`access_tier=null`、`required_tiers=[]`（Free）
  - `gemini-3.1-flash-lite-image`：动态 `paid=false`、`access_tier=1`，但静态 `image` 规则命中，`required_tiers=[Pro,Ultra,Ultra_20X]`（路由 paid）
- 离线重算：对 31 条中命中静态规则的记录逐条比对，`static_mismatch=[]`；对未命中静态规则的记录按 `accessModes/paid` 重算，`dynamic_mismatch=[]`。

限制：这是对历史证据与当前代码/快照的**一致性**核对，不是本轮 live 重放；见 N9。

---

## 4. Paid rules：默认值、历史、语义与冲突

### 4.1 默认值与 getter/validation

- `defaults/config.yaml:87-93`：
  - `ultra_required.contains = ["veo","ultra"]` → `["Ultra","Ultra_20X"]`
  - `pro_required.contains = ["argon","gemini-4","image","imagen"]` → `["Pro","Ultra","Ultra_20X"]`
- `src/aistudio_api/infrastructure/gateway/model_defaults.py:799-807`：代码内默认回退完全一致。
- `model_defaults.py:381-399`：`validate_config_mapping` 校验 `paid_model_rules` 为 dict、`contains/required_tiers` 为 list。
- `model_defaults.py:1289-1301`：`get_configured_paid_model_rules()` 优先 config，缺失回退代码默认。
- `model_defaults.py:1412-1418`：`get_configured_instance_specs()` 归一化 `instances`。

### 4.2 历史提交核对

- `99c38ba feat: account grouping, preset management, model quota weighting, and multi-tier preemption`
  - `git show 99c38ba -- defaults/config.yaml` 确认首次引入 `paid_model_rules` 默认块（同时引入 `tier_quotas`）。
- `a9e7dc3 feat(rebuild): 落地 P9-pre 配置深合并、API 鉴权与 lifespan 钩子`
  - `git show a9e7dc3 -- src/aistudio_api/infrastructure/gateway/model_defaults.py` 确认新增 `paid_model_rules` 类型校验（`contains/required_tiers` 必须为 list），并保留代码内默认块。
- `paid_rules_audit.md` 的提交/行号引用与实际代码一致。

### 4.3 `contains` 匹配到底做什么

`src/aistudio_api/application/routing_eligibility.py:121-131`：

- 模型 ID 先 `normalize_model_key()`（去 `models/`、去 chat/build 等 channel 前缀、转小写）；
- `contains`：小写子串匹配（`any(token in clean_model)`）；
- `prefixes`：前缀匹配；
- `exact`：全等匹配。

因此：

- `ultra_required` 命中 `veo` 或 `ultra` 子串 → Ultra/Ultra_20X；
- `pro_required` 命中 `argon`、`gemini-4`、`image`、`imagen` 子串 → Pro/Ultra/Ultra_20X；
- `image` 是**子串**，所以 `gemini-3.1-flash-lite-image` 即使 live `paid=false` 也会被静态规则送入 paid lane（Q1）。

### 4.4 与 live `paid/access_tier/accessModes` 的组合顺序

`routing_eligibility.resolve_required_tiers()` `134-182`：

1. 先遍历 `paid_model_rules`；命中即返回其 `required_tiers`，不再看动态发现（静态优先）。
2. 未命中时看 `model_discovery.get_cached_model_info()`：
   - `accessModes` 含 4 且不含 3 → Ultra/Ultra_20X；
   - 含 3 → Pro/Ultra/Ultra_20X；
   - `paid=True` → Pro/Ultra/Ultra_20X。
3. 仍未定 → `is_paid_model()` 全局 Paid 集合 → Pro/Ultra/Ultra_20X；否则无限制。

`resolve_model_route()` `212-225`：

- `paid = is_paid_model(clean) or any(tier_rank(tier) > 0 for tier in required)`；
- 所以静态规则即使没有动态 `paid` 标记，也会把模型送入 paid lane；
- 未命中静态规则的动态 Paid 模型（如 `antigravity-preview-09-2026`）会因 `is_paid_model()` 为真而走 paid lane。

`model_access.set_paid_models()` `55-73`：对快照内模型，`paid is True` 加入、否则移除；快照外模型保持既有状态（保守，不让已知 Paid 因缺席变 Free）。

解析侧 `model_discovery._parse_raw_models()` `786-846`：

- `paid = access_tier == 2 or 3 in accessModes or 4 in accessModes`（`846`）；
- 快照写入完整 `paid/access_tier/accessModes`（`822-852`）。

### 4.5 冲突结论

| 模型 | 动态发现 | 静态规则 | 实际路由 | 判定 |
|---|---|---|---|---|
| `gemini-3.1-flash-lite-image` | `paid=false`、`access_tier=1`、`accessModes=[]` | `pro_required.contains` 含 `image` | `required_tiers=Pro/Ultra/Ultra_20X`、`paid=True` | 静态能力限制优先；**Q1**，不按 bug 处理 |
| `gemini-3.1-pro-preview` | `paid=false`、`access_tier=null` | 无规则命中（`pro_required` 不含裸 `pro`） | `required_tiers=()`、`paid=False`、Free | **Q2**，确认预期 |
| `gemini-3.6-flash` | `paid=true`、`access_tier=2` | 无规则命中 | 动态 Paid → Pro | 符合“动态 Paid 即使不在静态规则也 Paid” |
| `antigravity-preview-09-2026` | `paid=true`、`access_tier=2` | 无规则命中 | 动态 Paid → Pro | 符合；冷启动种子问题见 Q3/Q4 |

### 4.6 Free 请求对 live Paid 模型的本地拒绝

离线用 `RoutingPipeline` + Free-only `FakeManager` 验证：

- 先 `set_paid_models([{id:"antigravity-preview-09-2026", paid:True}])`；
- `pipeline.begin(model_path="antigravity-preview-09-2026")`；
- 结果：`LOCAL_TIER_REJECTED Pro Free`（`TierRequiredError`，`required_tier=Pro`、`current_tier=Free`），未进入 `acquire`，未发上游。

代码路径：

- `routing_pipeline.py:319-346`：无 paid lane 候选或候选 tier 不足时，`tier_satisfies_required()` 失败 → `_tier_error()`（`413-420`）→ `TierRequiredError`；
- `app.py:322-328` + `error_mapping.py:60-81`：`TierRequiredError` 统一映射 HTTP 403（OpenAI/Gemini），不会先用 503/429。
- 现有测试 `tests/unit/test_account_routing.py:65-82` 也覆盖“paid lane 只有 Plus/Free 时 `begin()` 抛 `TierRequiredError`”。

---

## 5. B6 证据更正审计

### 5.1 原始 B6：明确 fallback，不再声称 live

`docs/dev/evidence/B6/results.json`：

- `evidence_scope = "original B6 run only; the 14-model result below is fallback, not live"`（`5`）；
- `models_count = 14`、`models_source = "fallback / discovery not wired"`、`models_are_live = false`（`16-19`）；
- `models_source_root_cause` 明确写出：无 `_model_warmup`、路由用 `client._session`、page XHR 失败被 debug 吞掉、静态 fallback（`19`）；
- `later_live_fix` 指向 `model_discovery_fix.json`，明确“不回溯原始 B6、不构成 PASS”（`6-10`）。

`docs/dev/evidence/B6/diagnostics.json`：

- `evidence_scope` 明确原始 B6 为 fallback（`4`）；
- `model_discovery.models_are_live = false`（`14`）；
- `root_cause` 与 results 一致（`15`）。

`docs/dev/rebuild/domains/P5-generation/reviews/REV-B6-live-verification.md`：

- `8`、`11-19` 更正原“Page XHR 实拉 14 个模型”为错误；14 个 ID 是 `_FALLBACK_MODELS`；
- `9` 结论 **FAIL / PARTIAL**；
- `19` 补充 later live-fix 是独立运行，不回溯、不构成 PASS；
- `58` 再次隔离 later live-fix。

### 5.2 later live-fix：单独证据，内部一致

`docs/dev/evidence/B6/model_discovery_fix.json`：

- `evidence_scope` 明确“later live fix run (separate from the original B6 run and not a B6 acceptance PASS)”（`3`）；
- `last_source=live_page`、31 条、`forced_refresh=true`、`refresh_trigger=_model_warmup startup task`（`8-15`）；
- 关键模型 paid/tier 字段与当前代码重算一致（见 3.4）；
- `data/models_cache.json` 的 `fetched_at` 与证据 `snapshot_fetched_at` 一致（`1793394499.3724995`），快照 31 条且含 `paid/access_tier/accessModes`；
- 未发现“原始 B6 是 live”的残留声明。

### 5.3 证据内部不一致（非 live 声明问题）

`results.json:11` 写 `"generation_requests": 1`，但 `diagnostics.json:88` 写 `"generation_budget_used": 3`，且 `REV-B6-live-verification.md:42` 写“已使用 3 次生成预算”；`diagnostics.json:24-46` 列出 3 次 generation attempts。该差异不影响“B6 = FAIL/PARTIAL”和“非 live”结论，但应统一口径（N8/Q6）。

---

## 6. AGENT.md 审计

`AGENT.md`：

- §4.6 实机防风控铁律：`234-238`
  - 严禁 Worker/子 Agent 私自启动 `main.py server` 或真实浏览器；
  - 冷启动触发 BotGuard，短时间重复启停会被风控；
  - 获批实机测试也只能由主控/用户在单次常驻进程完成，严禁反复重启；
  - 多实例池启用时，启动预热只允许 `InstanceManager.startup()` 在 `PooledSession` 上完成；控制面 `client.session` 不得在启动时自动开页/跑 BotGuard；控制面仅在按需场景（无活跃实例、手动导入 Cookie、保活）打开且保持单页；
  - 离线开发/回归默认 mock/fixture。
- §10.4 硬禁令：`417-427`，其中 `423` 再次写明实机防风控并引用 §4.6。
- 编号：§4.1 `197`、§4.2 `205`、§4.3 `213`、§4.4 `220`、§4.5 `228`、§4.6 `234`、§4.7 `240`、§4.8 `248`，连续无重复/缺失。
- 与 §4.4 不矛盾：§4.4 `222-226` 说控制面只负责登录/保活、保持单页、有活动实例时 Cookie 回写走实例页、禁止蓝绿；§4.6 说启动期自动预热只允许 pool，控制面按需单页。两者一致。

---

## 7. Q 项（需用户裁决，全部列出）

**Q1（静态 image → Pro vs live free）**
`gemini-3.1-flash-lite-image` 的 live `paid=false/access_tier=1`，但 `defaults/config.yaml:91-93` 的 `pro_required.contains` 含 `image`，`routing_eligibility.py:151-163` 静态优先，最终 `paid=True`、最低 Pro。证据与 `paid_rules_audit.md` 将其解释为“静态能力限制/规则优先契约”，不是接线 bug。请确认这是预期的产品口径；若希望动态发现覆盖静态规则，需单独裁决并补测试。

**Q2（`gemini-3.1-pro-preview` 为 Free）**
该模型名称含 `pro`，但 `pro_required` 不含裸 `pro`；`tier_quotas.pro_models.contains=["pro"]` 只影响配额分组，不参与付费判定（`routing_eligibility.py:180-182`）。live 与 fallback 均为 Free。请确认“名称含 pro ≠ Pro tier”是预期。

**Q3（live-only Paid 未进入默认种子）**
`antigravity-preview-09-2026`、`deep-research-*`、`lyria-*`、`gemini-nano-banana-2.1`、`gemini-omni-1.1-flash` 等 live Paid 模型不在 `_DEFAULT_PAID_MODELS`（`model_access.py:16-27`）或 `_FALLBACK_MODELS`（`model_discovery.py:171-267`）。当前依赖 `data/models_cache.json` 快照或首次 live discovery 才标记 Paid；全新 data 目录在首次 discovery 前会把未知 live-only Paid 模型当 Free（见 Q4/N10）。是否要把关键 live-only Paid ID 加入种子，或在 discovery 完成前对未知模型做保守 gating？

**Q4（启动窗口的 Free 路由）**
`_model_warmup` 是后台任务（`app.py:262-290`），API 可同时接受请求。首次 discovery 完成前，未知 live-only Paid 模型会按 Free 路由。快照存在时可避免；全新安装无快照时有窗口。是否需要“模型发现就绪”门闩/未知模型保守拒绝？

**Q5（手动激活的同账号双页）**
`POST /accounts/{id}/activate`（`routes_accounts.py:363-382` → `account_service.py:51-71` → `session.py:228-308`）会在控制面打开/切换到该账号 Page，且不检查该账号是否已有 pool slot。若同一账号已有 pool 活动页，则是同账号双页。它属于既有手动/按需路径，不是本 diff 的启动路径；是否要在该路由加“该账号有活动实例时禁止控制面切页/改走实例页”的硬门槛？

**Q6（证据生成计数口径）**
`results.json:11` `generation_requests=1` vs `diagnostics.json:88` `generation_budget_used=3` vs `REV...:42` “3 次生成预算”。请确认哪个是权威口径；不影响 FAIL/PARTIAL，但应统一。

**Q7（legacy pool-disabled 分支去留）**
`_start_control_warmup(pool_enabled=False)` / `_warmup_control_session()`（`app.py:72-95`）在生产配置下不可达：`_normalize_instance_specs` 拒绝空 `instances`，默认配置总有 2 条（`model_defaults.py:257-311,866-870`）。保留为防御代码、删除，还是新增显式“禁用池”配置开关？

**Q8（access_tier 其他取值）**
`_parse_raw_models` 只把 `access_tier == 2` 或 `accessModes` 含 3/4 判 Paid（`model_discovery.py:786-846`）；若上游未来用 `access_tier=3/4` 而不填 `accessModes`，当前会判 Free。请确认字段语义是否始终只有 2 表示 Paid。

---

## 8. N 项（非阻塞说明/覆盖缺口，全部列出）

**N1 legacy 分支不可达**
见 Q7。`app.py:216-226` 的 `pool_enabled` 在合法配置下恒为 True；`_warmup_control_session`/`_start_control_warmup(False)` 仅测试可达。不是 B6 blocker，但注释中的“pool-disabled legacy 模式”实际上没有配置入口。

**N2 无 READY pool 时的 HTTP fallback**
`get_models(sessions=())` 时，`model_discovery.py:411-451` 会从 `AccountStore` 取 active auth 文件并发 `_fetch_via_http`（`live_http`）。这不是控制面 Page/BotGuard，不会双预热，但严格说 warmup 在降级场景并非“只用 pool session”。是否接受该降级路径由 Q 决定。

**N3 路由控制面回退**
`discovery_sessions_for()`（`state.py:27-45`）在 pool 无 READY session 时回退 `discovery_session_for()` → `client._session`（`48-66`）。正常有 READY pool 时不会发生；回退时控制面 page 为 None，会继续走 HTTP fallback，不会开页/跑 BotGuard。若按“路由绝不使用控制面 session”严格解释，这是当前实现的边界；不影响启动双预热修复。

**N4 discovery 与在途请求共用 pool page**
`discovery_sessions()` 不检查 `active_requests`（`instance_manager.py:607-620`），`get_models` 直接在 READY session 的 page 上发 ListModels XHR（`model_discovery.py:378-403`）。它不增/减 `active_requests`、不 retire（符合要求），但可能与同页在途生成并发。是否需要跳过 busy slot 或使用专用发现页，见 Q4。

**N5 shutdown 测试覆盖不足**
`tests/unit/test_app_startup_warmup.py:111-123` 只断言 `state.client is None`，未真正创建并取消 live task，未验证 `_shutdown_runtime` 的 cancel+gather。实现本身在 `app.py:136-142` 是正确的；这是测试覆盖缺口，不是代码 blocker。

**N6 启动测试 mock 了 manager.startup**
`tests/unit/test_app_startup_warmup.py:18-36` 用 `SimpleNamespace(startup=AsyncMock())`，因此断言 `client.session.ensure_cdp_client.await_count == 0` 只证明 `_startup_instances` 没直接调用该函数；真实 `InstancePool` 仍会通过该 callable 获取 CDP（这是预期）。测试证明了 warmup_task=None + manager.startup 一次，但没有端到端跑真实 pool 启动。

**N7 fallback 条目字段不一致**
`_FALLBACK_MODELS` 的 `gemini-3.5-flash`（`model_discovery.py:214-218`）没有 `"paid"` 键，其余条目有。`set_paid_models` 会把缺失当 False（对该模型正确），但列表本身不一致，建议补齐。

**N8 证据生成计数不一致**
见 Q6。`results.json:11`、`diagnostics.json:88`、`REV-B6-live-verification.md:42` 三处口径不一致；不影响 live/fallback 结论。

**N9 live 证据无法离线重放**
本审计不启动服务/浏览器/网络，因此不能独立重放 `model_discovery_fix.json` 的 31 条 live 运行；只能核对 JSON 内部一致性、`data/models_cache.json` 时间戳/字段、以及用当前代码离线重算 `required_tiers`（全部匹配）。这是审计限制，不是证据错误。

**N10 快照文件被忽略**
`data/models_cache.json` 被 `.gitignore:12:/data/` 忽略；它存在于当前 worktree，但不会入库。全新 clone/全新 data 目录在首次 live discovery 前没有 31 条快照，Q3/Q4 的冷启动窗口真实存在。

**N11 access_tier 语义**
见 Q8。`_parse_raw_models` 仅认 `access_tier==2`；其他值需上游/协议确认。

**N12 手动激活同账号双页（既有，非本 diff）**
见 Q5。`routes_accounts.py:363-382` / `account_service.py:51-71` / `session.py:228-308` 是既有手动路径；不是启动路径，不由本 diff 引入，但确实是“同账号双页”可能路径。建议单独任务裁决/加固。

---

## 9. Blocker 声明

- **本审计范围内无 blocker。**
- 上述 Q/N 均为非阻塞裁决或说明项；其中 Q3/Q4（fresh install 冷启动窗口）、Q5/N12（手动激活同账号双页）、N3（路由控制面回退）若被用户按最严格契约解释，可能升级为独立任务，但不改变本轮“启动双预热已修复、pool-aware discovery 已接线、证据已更正、门禁通过”的结论。
- 按用户要求，未对任何 Q/N 做代码修改，未 commit。

---

## 10. 双写与哈希

- 本报告逐字节写入：
  - `.kilo/worktrees/rebuild-upstream/docs/dev/rebuild/domains/P5-generation/reviews/REV-B6-discovery-hardening.md`
  - `docs/dev/rebuild/domains/P5-generation/reviews/REV-B6-discovery-hardening.md`
- 两文件内容完全相同；SHA256 在写入后计算，见本次审计回复。
- 根 `config.yaml` 仍 untracked（`git ls-files config.yaml` 为空），未 stage、未 commit。

---

## 11. 2026-10-31 Q1-Q8/N1-N12 closeout 独立只读离线复审

- 复审对象：`.kilo/worktrees/rebuild-upstream` 当前未提交改动（16 个修改文件 + 4 个新增测试/报告/证据；`git diff --stat` 为 805 insertions / 150 deletions）
- 复审方式：只读源码/证据 + 离线门禁；未启动 `main.py server`，未启动 Chromium/CloakBrowser，未主动发起真实 Google/上游网络请求；未修改源码/测试/配置；未 stage/commit
- 复审时 HEAD：`9bffda1 feat(rebuild): 完成 OpenAI/ATG 最小闭环与生成主链`
- 结论：**PASS（审计范围内无 blocker）**
- 说明：第 0-10 节保留上一轮审计原文与全部 Q/N；若与本轮用户裁决冲突，以 11.3 为准

### 11.1 精确门禁

```text
$env:PYTHONUTF8='1'; $env:PYTHONIOENCODING='utf-8'; uv run pytest -q
=> 2 failed, 758 passed, 1 skipped in 10.17s

uv run ruff check src tests
=> All checks passed!  (exit 0)

uv run pyright src tests
=> 0 errors, 0 warnings, 0 informations  (exit 0)

git diff --check
=> exit 0（仅 LF→CRLF warning）
git diff --cached --check
=> exit 0；git diff --cached --stat 为空（staged=0）
```

- `758 passed` 与本轮期望完全一致（较上轮 `746 passed` +12）。
- 2 failed 仍是既有 Windows 平台差异，与上轮同名同因，未在本步改动：
  1. `tests/unit/test_browser_engine.py::test_find_chromium_executable_strictly_requires_cloakbrowser`
  2. `tests/unit/test_browser_scripts_syntax.py::test_evaluate_wrapper_expressions_parse_cleanly`（`bun.CMD -e` → `\n was unexpected at this time`）
- 1 skipped 仍是 POSIX-only watchdog 用例。

### 11.2 无 service / 无 browser / 无 network

```text
port 8080 closed
port 9222 closed
chrome=0  chromium=0  cloakbrowser=0
main_server_processes=0
staged_files=0
root config.yaml untracked
```

- 审计未启动 `main.py server`、未拉起真实 Chromium/CloakBrowser、未访问真实 Google/上游；`pytest` 唯一外部子进程是 `bun.CMD -e` 的本地 JS 语法检查（因 Windows shim 失败），不是网络/浏览器/服务。
- 机器上存在 21 个 `msedge.exe`（用户交互浏览器，创建时间早于本轮审计），不是本审计拉起；Chrome/Chromium/CloakBrowser 计数均为 0。
- `git ls-files config.yaml` 为空；worktree `git check-ignore -v config.yaml` → `.gitignore:15:/config.yaml`（主仓为 `.gitignore:62`）。未 stage/commit。

### 11.3 Q1-Q8/N1-N12 逐项核验

| 项 | 本轮状态 | 核验结果与证据 |
|---|---|---|
| Q1 | **resolved / corrected** | live/snapshot `paid=False` 且 `accessModes` 无 3/4 时，`_live_paid_truth` 判 False，`resolve_required_tiers` 先返回 `()`（`routing_eligibility.py:134-164`），`resolve_model_route` 强制 `paid=False` / `lane=free`（`:234-250`）。`gemini-3.1-flash-lite-image` 离线实测 `tiers=()`、`paid=False`、`lane=free`；`veo/ultra` 仍 Ultra，`imagen/argon/gemini-4` 仍 Pro+；fallback/unknown 仍走静态规则。裸 `image` 已从 tracked 默认删除（`defaults/config.yaml:89-93`、`model_defaults.py:802-807`）。测试：`test_model_discovery_live_wiring.py:268-300`、`:312-324` |
| Q2 | **resolved** | `gemini-3.1-pro-preview` 为 Free，名称含 `pro` 不参与 Paid 判定；离线矩阵与 `test_live_free_gemini_pro_preview_is_not_paid_from_name`（`:303-309`）一致 |
| Q3 | **resolved** | `_DEFAULT_PAID_MODELS` 已覆盖证据中全部 14 个 live Paid ID（差集为空，`model_access.py:14-37`）；16 条 fallback 全部显式 bool `paid`（`model_discovery.py:171-267`）；`gemini-3.5-flash paid=False`；无阻塞式 readiness gate（`app.py:206-290` 启动仅建 task，不 gate 请求）。测试：`test_model_access.py:117-128`、`test_model_discovery_live_wiring.py:244-248` |
| Q4 | **resolved by ruling** | 用户选择“种子已知 Paid + 无阻塞门闩”，不以 readiness gate 挡请求；未知且不在快照的未来 Paid 仍有冷启动窗口，见 N16 |
| Q5 | **resolved** | `/accounts/{id}/activate` 先查 READY pool slot（`routes_accounts.py:367-390`）；命中时只 `set_active_account` + rotator `add_account/clear_cooldown`，`ready_slot_for_account` 同步 `_current_id`（`instance_manager.py:632-662`），不切控制面/不跑 BotGuard；无 slot 才走原 `activate_account` 回退。测试：`test_routes_accounts.py:151-212`、`test_instance_manager.py:541-558` |
| Q6 | **resolved** | `results.json:11` `generation_requests=3`、`diagnostics.json` `generation_budget_used=3`、`REV-B6-live-verification.md` “3 次生成预算” 三处一致 |
| Q7 | **unchanged** | legacy `_warmup_control_session` / `_start_control_warmup(pool_enabled=False)` 仍保留（`app.py:72-95`）；合法配置下 `pool_specs` 非空，生产不可达（N1） |
| Q8 | **unchanged** | `_parse_raw_models` 仍以 `access_tier == 2` 或 `accessModes` 含 3/4 判 Paid（`model_discovery.py:906`）；仅 `access_tier=3/4` 且无 modes 的语义仍需上游确认（N11） |
| N1 | **unchanged** | legacy 分支仍无配置入口，仅测试可达 |
| N2 | **unchanged** | 无 READY pool 时 `get_models` 仍可走 HTTP fallback（`model_discovery.py:731-770`），非控制面 Page/BotGuard |
| N3 | **unchanged** | `discovery_sessions_for` 在 pool 无 READY 时仍回退 `client._session`（`state.py:27-66`）；启动预热本身只取 `RuntimeState.discovery_sessions()`，不回退控制面（`state.py:104-116`、`app.py:117-128`） |
| N4 | **resolved** | `discovery_sessions()` 按 paid→free、同 lane 内 `active_requests==0` 优先排序（`instance_manager.py:607-630`）；测试 `test_model_discovery_live_wiring.py:327-346`；不增/减 in-flight |
| N5 | **resolved** | 新测试真正创建 pending task 并断言 cancel+gather（`test_app_startup_warmup.py:128-155`），`_shutdown_runtime` 先 cancel 再 `gather(return_exceptions=True)`（`app.py:131-143`） |
| N6 | **unchanged** | 启动测试仍 mock `manager.startup`，未端到端跑真实 pool；非阻塞覆盖缺口 |
| N7 | **resolved** | 16 条 fallback 全部显式 bool `paid`；`gemini-3.5-flash` 为 False |
| N8 | **resolved** | 同 Q6，三处生成计数统一为 3 |
| N9 | **unchanged** | 本审计仍无法离线重放历史 live 运行，只能做证据/代码/快照一致性核对 |
| N10 | **resolved / mitigated** | `data/models_cache.json` 仍被 `.gitignore` 忽略，但已知 14 个 live Paid 已进入默认种子，fresh install 的已知 Paid 冷启动窗口已关闭；未知未来 Paid 见 N16 |
| N11 | **unchanged** | `access_tier` 非 2 的语义仍需协议确认 |
| N12 | **resolved** | 同 Q5，手动激活命中 READY slot 时不再制造同账号双页 |

### 11.4 关键实现证据（file:line）

- 启动分流：`app.py:72-128`（legacy warmup / startup / pool-only catalog refresh）、`:206-226`（`pool_enabled` 分流）、`:255-297`（startup/model warmup task 与 shutdown）。
- pool-only 发现：`api/state.py:27-66,100-116`（`discovery_sessions_for` / `RuntimeState.discovery_sessions`）、`model_discovery.py:410-476`（候选 session 循环，失败逐个回退）、`:533-543`（`_accept_live`）。
- 真值优先级：`model_discovery.py:270-320`（`LIVE_SOURCES` / `DISCOVERY_TRUTH_SOURCES` / source marker / `discovery_entry_paid_truth`）、`routing_eligibility.py:134-164,234-250`。
- 快照：`model_discovery.py:346-367`（load 时标记 `snapshot` 并 `set_paid_models`）、`:369-384`（save）。
- Paid 解析：`model_discovery.py:906`（`access_tier == 2 or 3/4 in accessModes`）。
- 默认 Paid 种子：`model_access.py:14-37`；set/clear：`:65-88`。
- 默认规则：`defaults/config.yaml:89-93`、`model_defaults.py:802-807`。
- Q5 复用：`routes_accounts.py:364-390`、`instance_manager.py:607-662`。
- 新增测试：`test_model_access.py:117-140`、`test_model_discovery_live_wiring.py:157-429`、`test_app_startup_warmup.py:19-155`、`test_routes_accounts.py:151-212`、`test_instance_manager.py:541-558`。

### 11.5 凭证来源核验

- `calculate_sapisid_hash()` 从 cookies 的 `SAPISID` / `__Secure-1PAPISID` / `__Secure-3PAPISID` 与当前 `time.time()` 计算，返回 `SAPISIDHASH <ts>_<sha1>`，不是硬编码（`cookie_parser.py:276-294`）。
- `X-Goog-AuthUser` 来自 pool session 的 `active_context.auth_user` / `get_current_auth_user()`（`model_discovery.py:112-127`），HTTP header 使用该 `auth_user`（`:747`），不是硬编码用户。
- `X-Goog-Api-Key` 与 `X-Goog-Ext-519733851-bin` 是既有公开 Web 常量（`model_discovery.py:678-679`），不是用户秘密；本轮 diff 未新增任何 Cookie/邮箱/API Key/真实 IP 字面量。
- 新增 `Authorization` / `X-Goog-AuthUser` / `X-AIStudio-G1-Tier` 均来自运行期 session/cookie（`model_discovery.py:680-686,709-715`）。

### 11.6 本轮新增 N 项（非阻塞）

**N13 测试依赖历史证据文件（新增）**
`test_model_access.py:117-140` 与 `test_model_discovery_live_wiring.py:35-42,251-257` 直接读取 `docs/dev/evidence/B6/model_discovery_fix.json` 的 31 条模型作为测试 oracle；该目录当前仍为 untracked（`?? docs/dev/evidence/B6/`）。本机全量测试通过，但若最终提交不包含该证据或证据被轮换/清理，干净 checkout 会 `FileNotFoundError`。建议把最小 31 条 fixture 落到 `tests/fixtures/`（或让测试从 parser 生成），不要依赖历史证据文件。

**N14 快照持久化内部来源标记（新增）**
`_save_snapshot` 现在会把内部 `_discovery_source` 键写入 `data/models_cache.json`（`model_discovery.py:369-384`）；旧快照无该键，`_load_snapshot` 会重新标记为 `snapshot`（`:346-367`），行为安全，但缓存 schema 多了一个实现细节键。非阻塞。

**N15 stale snapshot 的 Free 真值窗口（新增，裁决内接受）**
snapshot 记录按 Q1 被视为真值；在 startup 与 TTL 刷新前，若上游已把某模型从 Free 改为 Paid，旧快照仍会按 Free 路由。`_model_warmup` 会周期性 `force_refresh`（`app.py:262-290`），但首次刷新失败时保持旧快照。属 Q1 “live/snapshot 真值优先”的既定取舍，非阻塞。

**N16 旧种子/未来 Paid 的冷启动残留（新增）**
`_DEFAULT_PAID_MODELS` 除 14 个 live Paid 外仍保留 `argon`、`argon-2-preview`、`gemini-3.1-flash-image-preview`、`veo-2.0-generate-001`、`veo-3.1-generate-preview` 等保守种子；已知 14 个 Paid 已覆盖，但未来新出现的 live-only Paid 在首次 discovery/快照前仍可能按 Free 路由（Q4 裁决接受，不做 readiness gate）。

**N17 Q5 current pointer 覆盖缺口（新增）**
`ready_slot_for_account` 会同步 `_current_id`（`instance_manager.py:655-661`），但 `test_activate_reuses_ready_pool_slot_without_control_switch` 只断言无 page/BotGuard 副作用，未直接断言 `current_instance_id` 变化；实现正确，属测试覆盖缺口。

**N18 本地 untracked 根配置仍含 `image`（新增，非提交范围）**
worktree 根 `config.yaml`（untracked）的 `paid_model_rules.pro_required.contains` 仍含 `image`（`config.yaml:43-49`）；tracked 默认已删除（`defaults/config.yaml:89-93`）。live Free 真值优先，因此 Q1 目标模型仍为 Free；但本机非 live/未知 `*image*` 模型仍会走静态 Pro+。根配置按要求保持 untracked、不改动；最终部署若继续使用该本地配置，应另行同步。

### 11.7 剩余项与 blocker 声明

- **本审计范围内无 blocker；verdict=PASS。**
- 剩余非阻塞项：Q7、Q8、N1、N2、N3、N6、N9、N11，以及本轮新增 N13-N18。
- Q3/Q4/N10 的“无 readiness gate”是用户明确裁决；N16 的未知未来 Paid 窗口按该裁决接受。
- Q8/N11 的 `access_tier` 非 2 语义仍需上游协议确认，不影响本轮已知 31 条证据与 14 个 Paid 种子。
- 未修改任何源码/测试/配置；未 stage/commit。

### 11.8 双写与哈希

- 本报告逐字节写入：
  - `.kilo/worktrees/rebuild-upstream/docs/dev/rebuild/domains/P5-generation/reviews/REV-B6-discovery-hardening.md`
  - `docs/dev/rebuild/domains/P5-generation/reviews/REV-B6-discovery-hardening.md`
- 两文件写入后字节完全一致（由同一字节流复制）；本轮写入前基线 SHA256 为 `5AFC1BA44D45DB09B5AF184EAAF633191F31937BA3DEDC95DB1FB8A77D14EA66`。写入后 SHA256 见本次审计回复（自引用哈希无法内嵌）。
- 根 `config.yaml` 仍 untracked（`git ls-files config.yaml` 为空），未 stage、未 commit。
