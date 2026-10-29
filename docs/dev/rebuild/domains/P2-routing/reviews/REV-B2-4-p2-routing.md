# REV-B2-4 — P2/D05 多实例池路由、Tier 403、429 重试切换只读审查报告

- 步骤：B2-4（P2 / D05：Free/Pro 多实例池路由、Tier 403、429 重试切换）
- 目标工作树：`D:\LLM\self_programming\aistudio-api\.kilo\worktrees\rebuild-upstream`，HEAD `ca8496c`；本步改动**尚未提交**
- 主仓留档：`D:\LLM\self_programming\aistudio-api`，HEAD `31a824c`
- 权威依据：目标树 `AGENT.md`；`06-decision-log.md` P2 章节（约 102-140）及 P2-A..P2-G 裁决；`08-09-decision-record.md` D-01/D-04/D-06；`06-feature-index.csv` D05-001..D05-047；`06-test-disposition.csv` 相关行
- 审查方式：只读；对照未提交 diff、生产代码、测试断言与实跑结果；**未修改任何代码/配置/测试，未 commit**
- 审查日期：2026-10-29（收尾复审；初审 2026-10-29）

## 1. 结论

**PASS**

初审提出的三项口径/范围问题已由用户裁决并落地：Q-01 D05-032 关闭 session 原地重试 **dropped**；Q-02 非 429 耗尽保留 401/403/502 原错误；Q-03 paid 强制 chat-only（`[ROUTING-RULE: PAID_CHAT_ONLY]` + `surface_capability` 拦截 + build 503）。本轮 6 处修复已复核成立，未发现新的阻塞性回归。保留 N-01、N-03、N-05、N-06、N-07、N-09、N-10、N-12、N-13、N-15、N-18、N-19 及少量残余 N-04R/N-08R/N-20，均为非阻塞项或后续阶段事项。

本轮实跑：**442 passed / 2 failed / 1 skipped**（2 failed 仍为既有 Windows 平台差异：CloakBrowser 路径与 `bun.CMD` 语法检查；1 skipped 为 POSIX-only watchdog）；`ruff` 0；`pyright src tests` **0 errors, 0 warnings**；`git diff --check` 退出码 0。

### 1.1 局部返工复核记录（2026-10-29）

| 项 | 复核结果 | 证据 |
|---|---|---|
| **Q-01** D05-032 hang 回退 | **用户裁决 dropped**：不再要求“预算耗尽后关 session 原地重试”；保持“预算内切号，耗尽后抛原错误”。代码与裁决一致，不再作为阻塞项 | `routing_pipeline.py:677-680,697-718`；本轮未新增 session close/rebootstrap 路径 |
| **Q-02** 非 429 耗尽 | **用户裁决保留 401/403/502**：SessionExpired/AuthError/hang 耗尽继续抛原错误；仅 429 耗尽映射 `surface_switch_exhausted` 503 | `routing_pipeline.py:603-623,677-680,715-718`；`test_routing_pipeline.py:359-486` |
| **Q-03** paid→build | **已修复**：`resolve_model_route` 用 `surface_capability` 拒绝 paid 模型进入 build，抛 `SurfaceCapacityError`（503 `surface_instance_busy`）；`model_access.py:89-95` 与 `routing_eligibility.py:199-211` 均带可检索的 `# [ROUTING-RULE: PAID_CHAT_ONLY]` 中文注释 | `routing_eligibility.py:199-211`；`model_access.py:89-95,162-172`；`test_routing_pipeline.py:488-503` |
| **N-02** 429 切号后 acquire None / last_error 429 | **已修复**：非流式 `:295-301` 与流式 `:507-513` 在 `last_error` 为 `UsageLimitExceeded` 时统一抛 `SurfaceSwitchExhaustedError`；`acquire` 返回 None 同样走 503 | `api_service_gemini.py:207-210,295-301,428-431,507-513` |
| **N-04** 手动切号 surface | **已修复**：`_get_eligible_candidates` 与 `get_next_account` 新增 `surface` 并透传给 `stats.is_available` | `account_rotator.py:648,751-777,795-814` |
| **N-11** 204 耗尽 | **已修复**：204 同号重试后仍为空改抛 `BrowserTransportError`，API 映射 502 | `routing_pipeline.py:667-675`；`api_service_gemini.py:128-135`（`BrowserTransportError → 502`）；`test_routing_pipeline.py:292-310` |
| **N-14** 共享 capture | **已修复**：`PooledSession` 持有 `RequestCaptureService` 并通过 `capture_service` 暴露；`InstanceRequestClient` 复用 session 的 service；`PooledSession.close()` 清空模板 | `pooled_session.py:51-61,261-264`；`instance_client.py:48-53` |
| **N-16** 移除 `pg` | **已修复**：`_normalize_surface` 只接受 `None/chat/build`；`pg` 作为控制面输入会抛 `InstanceNotReadyError` | `instance_manager.py:891-896` |
| **N-17** cooldowns 启动校验 | **已修复**：`validate_config_mapping` 校验 `rotator.cooldowns` 为字典且四个键为数值；新增非法值用例 | `model_defaults.py:250-292,362-368`；`test_features_config.py:77-86` |
| **N-08** 核心测试补齐 | **已补齐核心分支**：SessionExpired/AuthError 切号成功与耗尽、hang 切号与耗尽、paid→build 拒绝、204 耗尽转 502；残余缺口见 N-08R | `test_routing_pipeline.py:359-486,488-503`；`test_routing_pipeline.py:292-310` |
| 终态实跑 | **442 passed / 2 failed / 1 skipped**；ruff 全绿；pyright 0 errors；`git diff --check` 通过 | 本轮实跑 |

## 2. 实际执行命令与结果

| 命令 | 结果 |
|---|---|
| `$env:PYTHONUTF8='1'; $env:PYTHONIOENCODING='utf-8'; uv run pytest -q` | **2 failed, 442 passed, 1 skipped in 6.39s**。2 failed 均为既有 Windows 平台差异：`test_browser_engine.py::test_find_chromium_executable_strictly_requires_cloakbrowser`、`test_browser_scripts_syntax.py::test_evaluate_wrapper_expressions_parse_cleanly`（`bun.CMD` 报 `\n was unexpected at this time`）；1 skipped 为 POSIX-only watchdog |
| `uv run ruff check src tests` | `All checks passed!`（0 error） |
| `uv run pyright src tests` | **`0 errors, 0 warnings, 0 informations`** |
| `git diff --check` | 退出码 0；无空白错误，仅有 LF→CRLF 提示 |
| `git status --short` | 27 个已跟踪修改 + 20 个未跟踪条目（19 个新代码/测试文件 + 本报告）；**未 commit** |
| `git diff --stat` | `27 files changed, 1230 insertions(+), 1876 deletions(-)`（未跟踪新文件另计） |
| 目标树/主仓 HEAD | 目标树 `ca8496c`；主仓 `31a824c` |

## 3. 逐项核对表

### 3.1 Drop 红线与旧机制

| 检查点 | 结论 | 证据 |
|---|---|---|
| 不恢复 `channel_registry`/groups/tag/quota_pools/account_routing/affinity | 通过 | 全 `src` grep 仅命中 `browser_engine.py:709` 的 CPU `Affinity`（与本主题无关）；`test_account_routing.py:85-91` 断言 `AccountRotator` 无 `active_groups`；`routing_eligibility.py:30,80-84` 拒绝 `pg/playground/official` 请求前缀 |
| 不恢复 `circuit_breaker`/`record_switch_failure`/`defer`/InstanceSwitcher/后台重排 | 通过 | `routing_safety.py` 仅 `RoutingBudget`；`routing_safety.py:1-6,36-41`；`instance_manager.py:6-11` 声明无后台 rebind/rebalance；grep 无命中 |
| `account_orchestrator.py` 只做显式事件记录 | 通过 | `account_orchestrator.py:1-50` 仅 `record_rotator_event`；`test_account_orchestrator.py:18-21` 断言 `ensure_active_account`/`try_switch_account`/`MAX_RETRIES` 不存在 |
| 不恢复旧全局切号 | 通过 | 全 `src` 仅 `account_orchestrator.py:3` 文档字符串提及；请求路径只通过 pipeline |
| control-plane `switch_auth` 仅管理面使用 | 通过 | `routes_accounts.py`/`account_service.py` 调用属账号管理控制面；请求路由未调用 |
| 旧 `pg` 请求前缀不进入路由 | 通过 | `routing_eligibility.py:80-84` 拒绝；`instance_manager.py:891-896` 已移除 `pg` 输入；`model_access.py:12`/`model_catalog.py:16` 的 `pg` 仅保留为模型名归一化/目录显示别名，不参与请求 surface 解析（N-16 已复核） |

### 3.2 Free/Pro 分流、唤醒、排序、容量与 `active_requests`

| 检查点 | 结论 | 证据 |
|---|---|---|
| Free 模型只 Free lane；paid 模型只 Pro+ | 通过 | `routing_eligibility.py:212-225` 冻结 `paid/lane/required_tiers`；`routing_pipeline.py:317-344` 先按 lane 取 `eligible_instances`，再 `tier_allows` 过滤；`test_account_routing.py:38-82`、`test_model_tier_admission.py:83-88` |
| 请求经 `eligible_instances → wake_eligible → begin_request` | 通过（顺序为 wake→eligible，语义等价） | `routing_pipeline.py:316-319` 先 `_wake`，再取候选；`:508-517` 对 SLEEPING/UNSTARTED 再唤醒；`begin_request` 在 `:519-539`；`test_routing_pipeline.py:50-76,98-113` |
| SLEEPING/UNSTARTED 消息到达唤醒所有合格实例 | 通过 | `instance_manager.py:443-484`；`routing_pipeline.py:486-495`；`test_routing_pipeline.py:50-76` |
| sticky + 简单 LRU/FIFO + warm 仅同分 tie-break | 通过（额外排序键见 N-01） | `routing_pipeline.py:382-409`：sticky → total_requests → model_requests → in_flight → last_used → warm → number；`test_routing_pipeline.py:83-94` |
| 容量满 chat 503 `surface_quota_exceeded`、build 503 `surface_instance_busy` | 通过 | `domain/errors.py:94-116`；`test_routing_pipeline.py:115-124`；`test_routes_gemini.py:75-89` |
| 不跨账号抢占、不自动新开实例 | 通过 | `routing_pipeline.py:317-360` 只从现有记录取候选；`instance_manager.py:237-249`；请求路径无新建 slot |
| `active_requests` 成功/失败/取消/流关闭 finally 释放 | 通过 | 非流式 `api_service_gemini.py:203-301`（finally 291-293）；流式 `:418-524`（finally 522-524）；切号时 `_consume_action` 先释放（`:156-170`）；`test_api_service_gemini.py:73-138`、`test_routes_gemini.py:92-141`、`test_routing_pipeline.py:98-113` |
| 不新增第二套并发账本 | 通过 | `routing_pipeline` 只用 `RequestHandle`；`instance_pool.py:76-96,151-160`；`PooledSession._in_flight` 仅会话诊断 |

### 3.3 Tier 准入与 403

| 检查点 | 结论 | 证据 |
|---|---|---|
| `paid_model_rules → accessModes → paid/capability` 顺序 | 通过 | `routing_eligibility.py:134-182`；`model_discovery.py:406-460`；`test_routing_eligibility.py:70-131`、`test_model_tier_admission.py:28-88` |
| 空规则不得误判 Ultra-only | 通过 | `routing_eligibility.py:144-182`；`test_routing_eligibility.py:95-103`、`test_model_tier_admission.py:69-71` |
| `TierRequiredError` 领域错误；application 不依赖 FastAPI `HTTPException` | 通过 | `domain/errors.py:24-62`；`application/**` 无 `HTTPException`；`routing_pipeline.py:598-623` 只抛领域错误；残留 `StreamingResponse` 导入见 N-13 |
| API 映射 403，错误体含 `tier/model/required_tier/current_tier` | 通过 | `api/error_mapping.py:56-81`；`api/app.py:256-268`；`test_routes_gemini.py:50-73`、`test_format_error_routing.py:26-39` |
| 请求期零切号/重绑；不得用 503 表达 tier 不足 | 通过 | `routing_pipeline.py:297-377` 在 `acquire` 前抛 `TierRequiredError`；`handle_error` 对错误 tier 的 AuthError 直接抛 403（`:612-616`）；`test_tier_preemption.py:24-114` |
| paid 模型只能走 chat | 通过（当前 build 不可用；残余 rule-paid 边界见 N-20） | `routing_eligibility.py:199-211` 用 `surface_capability` 拦截；`model_access.py:162-172`；`test_routing_pipeline.py:488-503` |

### 3.4 quota_scope（D-01）

| 检查点 | 结论 | 证据 |
|---|---|---|
| 默认 `account`；非法值回退 `account` | 通过 | `model_defaults.py:110-117,1212-1224`；`defaults/config.yaml:36`；`test_account_rotation_concurrency.py:80-97` |
| `account`/`account_surface` 记账键 | 通过 | `account_rotator.py:63-93,199-217,277-406`；`test_account_rotation_concurrency.py:113-136` |
| `/stats` 与配额展示共用口径 | 通过 | `routes_system.py:47-146`（`split_quota_key`、`quota_scope` 字段）；`response_models.py` 新增 `surface`/`quota_scope` |
| 429 冷却共用口径 | 通过 | `account_rotator.py:230-275`（`quota_keys` + surface）；`test_account_rotation_concurrency.py:121-135` |
| 只影响记账键，不新增实例/页面 | 通过 | 未改实例/Context/Page 数量路径；B2-3 池结构不变 |
| 旧键兼容与手动切号 surface | 通过（残余旧键迁移见 N-04R） | `account_rotator.py:247-252` 在 `account_surface` 下回看 legacy plain key；`:751-777` `_get_eligible_candidates` 透传 surface；`:795-814` `get_next_account` 接受 surface；`test_account_rotation_concurrency.py:138-145` |
| 请求期不改口径 | 通过 | `resolve_quota_scope` 只在 `AccountRotator.__init__` 调用；无请求期重建 rotator |
| UI 开关 | 未实现（用户已列为非阻塞） | 无 `web/` 改动；见 4.3 |

### 3.5 retry_same / 429 / 204 / 404 / 流式

| 检查点 | 结论 | 证据 |
|---|---|---|
| 204 空响应未出正文 → 同账号原地重试一次 | 通过 | `routing_pipeline.py:647-671`；`test_routing_pipeline.py:292-310`；`test_api_service_gemini.py:45-72` |
| 瞬态 404（Ambiguous RPC）未出正文 → 同账号原地重试 | 通过 | `domain/errors.py:249-254`；`routing_pipeline.py:648-671`；`test_routing_pipeline.py:306-327` |
| 非瞬态 404 / 其他 4xx 不重试、保留状态码 | 通过 | `routing_pipeline.py:647-653,682-685`；`test_api_responses.py` 分类用例 |
| 429 未出正文 → 同账号重试 → 唤醒/切池 → 保存消息切号 → max_switches/耗尽 503 | 通过 | `routing_pipeline.py:625-645,697-722`；`instance_client.py:148-191` 重新 capture；`test_routing_pipeline.py:228-270`；`test_api_service_gemini.py:142-162` |
| 429 切号后 `acquire` 返回 None 或 `last_error=UsageLimitExceeded` 统一 503 | 通过 | `api_service_gemini.py:207-210,295-301,428-431,507-513`；残余 acquire 抛 `SurfaceCapacityError` 的分支见 N-02R |
| `has_yielded_data=True` 后禁止切号 | 通过 | `routing_pipeline.py:606-610,619-623,627-628,655-657,676-677`；`test_routing_pipeline.py:272-290`、`test_api_service_gemini.py:90-116` |
| bot/BotGuard hang：预算内切号；耗尽抛原错误 | 通过（Q-01 已裁决 dropped） | `routing_pipeline.py:677-680,697-718`；`test_routing_pipeline.py:444-486`；无 session close/rebootstrap 分支，符合裁决 |
| 204 耗尽转 502 | 通过 | `routing_pipeline.py:667-675`；`api_service_gemini.py:128-135`；`test_routing_pipeline.py:292-310` |
| finally 释放与流关闭 | 通过 | `api_service_gemini.py:291-293,522-524`；`test_api_service_gemini.py:119-138` |

### 3.6 testing_hooks

| 检查点 | 结论 | 证据 |
|---|---|---|
| 仅 `AISTUDIO_TEST_HOOKS=1` 生效 | 通过 | `testing_hooks.py:23-24,43-45`；`test_testing_hooks.py:25-27` |
| account/model/surface/count 过滤与 reset | 通过 | `testing_hooks.py:35-62`；`test_testing_hooks.py:29-46` |
| 生产默认关闭 | 通过 | 默认空环境返回 False；`defaults/config.yaml` 无开启项 |

### 3.7 测试质量

| 目标 | 结论 | 证据/缺口 |
|---|---|---|
| Free/Pro lane 与 tier 准入 | 通过 | `test_account_routing.py:22-91`、`test_model_tier_admission.py:28-88`、`test_tier_preemption.py:24-114` |
| surface/alias/空规则/accessModes | 通过 | `test_routing_eligibility.py:34-143` |
| 候选/唤醒/容量/排序 | 通过 | `test_routing_pipeline.py:50-225` |
| SessionExpired/AuthError/hang 切号与耗尽 | 通过 | `test_routing_pipeline.py:359-486` |
| paid→build 拒绝 | 通过 | `test_routing_pipeline.py:488-503` |
| 429 同号→切换→503 | 通过 | `test_routing_pipeline.py:228-270` |
| 204/瞬态 404；204 耗尽 502 | 通过 | `test_routing_pipeline.py:292-327`；`test_api_service_gemini.py:45-72` |
| 已出正文不切号 | 通过 | `test_routing_pipeline.py:272-290`、`test_api_service_gemini.py:90-116` |
| 403 四协议错误体 | 通过 | `test_format_error_routing.py:26-70`、`test_routes_gemini.py:50-73` |
| active_requests 释放/流关闭 | 通过 | `test_api_service_gemini.py:73-138` |
| testing_hooks | 通过 | `test_testing_hooks.py:25-46` |
| `account_orchestrator` 只记录事件 | 通过 | `test_account_orchestrator.py:18-77` |
| quota_scope 双口径 | 通过（核心）；残余缺口见 N-08R | `test_account_rotation_concurrency.py:80-154` |
| cooldowns 启动校验 | 通过 | `test_features_config.py:77-86` |
| 为过测试削弱断言 | 未发现 | 旧 `handle_attempt_exception` 用例随函数删除而移除，其 204/404 行为由新 pipeline 用例覆盖；403 四字段断言语义更强；204 断言已更新为 502 领域错误 |
| 覆盖率残余缺口 | 存在（N-08R） | 无 `/stats` 端点 quota_scope 断言、无 `features.routing.max_switches` 配置解析测试、无流式 403/204/404 用例、无 N-02 wrapper 的 acquire None 直接测试、无 N-14 共享 capture 的直接单测 |
| `tests/__init__.py` / `tests/unit/__init__.py` | 合理（N-09） | 为空文件，用于 `from tests.unit.routing_fakes import ...` 的包导入；全量 442 passed，无副作用 |

### 3.8 范围与洁净

| 检查点 | 结论 | 证据 |
|---|---|---|
| 无 B3/P6 | 通过 | 无 keepalive/logs 新文件或路由；`app.py` 仅接线 `routing_pipeline` |
| 无 05/06/08-09/archive 改动 | 通过 | `git status --short` 无 docs 变更；仅代码/配置/测试 |
| 无真实 Google/账号/IP | 通过 | 新测试全部 `FakeManager`/ScriptedPipeline/MagicMock/ASGI transport；无真实网络 |
| 未 commit | 通过 | HEAD 仍为 `ca8496c`；27 个已跟踪修改 + 20 个未跟踪条目 |
| 改动范围符合本步 | 通过 | 修改集中在 routing/eligibility/safety/error_mapping/error/api_service/app/config/rotator/instance_manager/pooled_session/model_* 及对应测试；未见无关功能 |
| `git diff --check` | 通过 | 退出码 0，仅 LF→CRLF 提示 |
| 未跟踪文件全为本步产物 | 通过 | 7 个 `src` 新模块 + 12 个 tests 新文件 + `routing_fakes.py`，均在 B2-4 范围 |

## 4. 问题清单

### 4.1 已修复 / 已裁决项

| 项 | 状态 | 复核证据 |
|---|---|---|
| **Q-01** D05-032 hang 回退 | **用户裁决 dropped**；不再阻塞 | `routing_pipeline.py:677-680,697-718`；本轮无 close/rebootstrap 请求期路径；`test_routing_pipeline.py:444-486` |
| **Q-02** 非 429 耗尽 | **用户裁决保留 401/403/502**；不再阻塞 | `routing_pipeline.py:603-623,677-680,715-718`；`test_routing_pipeline.py:359-486` |
| **Q-03** paid→build | **已修复**（当前 paid 集合路径）；残余 rule-paid 边界见 N-20 | `routing_eligibility.py:199-211`；`model_access.py:89-95,162-172`；`test_routing_pipeline.py:488-503` |
| **N-02** 429 切号后 acquire None / last_error 429 | **已修复**（主口径）；残余 acquire 抛 `SurfaceCapacityError` 的 code 差异见 N-02R | `api_service_gemini.py:295-301,507-513` |
| **N-04** 手动切号 surface | **已修复**（surface 透传）；旧键迁移残余见 N-04R | `account_rotator.py:751-777,795-814` |
| **N-11** 204 耗尽 | **已修复**（502） | `routing_pipeline.py:667-675`；`test_routing_pipeline.py:292-310` |
| **N-14** 共享 capture | **已修复**（共享 service + close 清缓存）；无直接单测（N-08R） | `pooled_session.py:51-61,261-264`；`instance_client.py:48-53` |
| **N-16** 移除 `pg` | **已修复**（控制面 `_normalize_surface`） | `instance_manager.py:891-896` |
| **N-17** cooldowns 启动校验 | **已修复** | `model_defaults.py:250-292,362-368`；`test_features_config.py:77-86` |
| **N-08** 核心测试 | **已补齐核心分支**；残余见 N-08R | `test_routing_pipeline.py:359-503` |

### 4.2 保留的非阻塞项

#### N-01（非阻塞）候选排序含额外维度

- **位置**：`routing_pipeline.py:398-406`：`(sticky, total_requests, model_requests, in_flight, last_used, warm, number)`。
- **证据**：D05-016..018 删除 affinity/组权重/账号权重/公平分，仅保留 sticky + 简单 LRU/FIFO + warm 同分 tie-break；当前额外按 `model_requests`、`in_flight` 排序。
- **影响**：不含被禁的 affinity/weight/公平分，属简单均衡负载延伸；与 `06` 字面排序口径不完全一致。
- **是否阻塞**：非阻塞；用户已接受。

#### N-02R（非阻塞）429 切号后 acquire 抛 `SurfaceCapacityError` 时 code 为 `surface_quota_exceeded`

- **位置**：`routing_pipeline.py:499-554`（acquire 末尾抛 `SurfaceCapacityError`）；`api_service_gemini.py:295-301,507-513`（只把 `last_error=UsageLimitExceeded` / None 转 `SurfaceSwitchExhaustedError`）。
- **证据**：切号后若所有候选 `begin_request` 都失败，`acquire` 抛 `SurfaceCapacityError`，最终 503 的 code 是 `surface_quota_exceeded`，不是 `surface_switch_exhausted`。
- **影响**：仍是 503，仅错误码/文案与“切号耗尽”略有差异；不影响状态码契约。
- **是否阻塞**：非阻塞。

#### N-03（非阻塞）流式在首块前失败仍返回 HTTP 200 SSE 错误

- **位置**：`api_service_gemini.py:410-530`；`:520-521` 的 `format_sse_error`。
- **证据**：`handle_gemini_generate_content` 先返回 `StreamingResponse`，`acquire` 后首块前错误以 SSE error 写出；`test_routes_gemini.py:50-89` 只覆盖 `begin` 阶段 403/503。
- **影响**：与 AGENT B5a“流式首事件统一预读，尽量保留真实 HTTP 状态”相关，属后续 B5a 范围。
- **是否阻塞**：非阻塞（后续 B5a）。

#### N-04R（非阻塞）quota_scope 旧键迁移

- **位置**：`account_rotator.py:515-520`（`from_dict` 用当前 scope 覆盖持久化 scope）；`:247-252`（`account_surface` 回看 legacy plain key）。
- **证据**：从 `account_surface` 切回 `account` 时旧 `chat::`/`build::` 键不会被 `is_available` 读取，也不会迁移；请求路径（`_account_available` 传 surface）不受影响。
- **影响**：极端配置切换下旧 surface 冷却被忽略，最多到 PST 午夜重置。
- **是否阻塞**：非阻塞；建议后续显式迁移/清理。

#### N-05（非阻塞 / 裁决项）`TierRequiredError.tier` 与 `required_tier` 恒同值

- **位置**：`routing_eligibility.py:216-224`（`ModelRoute.tier = minimum or "Free"`）；`routing_pipeline.py:411-418`。
- **证据**：`test_tier_preemption.py:32-42` 断言 `tier == "Pro"`、`required_tier == "Pro"`。
- **影响**：`tier` 没有独立语义（等于最低允许 tier）。
- **是否阻塞**：非阻塞；用户已接受。

#### N-06（非阻塞 / 裁决项）`features.routing.max_switches` 挂在 `features` 下

- **位置**：`defaults/config.yaml:44-46`；`model_defaults.py:1182-1209`；`routing_pipeline.py:361-367`。
- **证据**：与 D05-024 行一致；与旧 `rotator` 块分离。
- **影响**：合理；需与未来 `features` 其他开关统一 schema。
- **是否阻塞**：非阻塞；用户已接受。

#### N-07（非阻塞，用户已列出）quota_scope UI 未做

- **位置**：无 `web/` 改动；配置键仅 `defaults/config.yaml:36`（本地 `config.yaml` 被 ignore）。
- **影响**：D-01 要求 UI 开关；当前只能改 YAML 并重启。
- **是否阻塞**：非阻塞；用户已接受。

#### N-08R（非阻塞）测试覆盖残余缺口

- **位置/证据**：无 `/stats` 端点 quota_scope 断言（`test_routes_system.py:168` 只覆盖 paid marker）；无 `get_configured_routing_settings` 默认/非法值测试；无流式 403/204/404 分支测试；无 N-02 wrapper 的 `acquire` 返回 None 直接测试；无 N-14 共享 capture 的直接单测。
- **影响**：上述分支回归不会被当前 442 passed 捕获。
- **是否阻塞**：非阻塞；建议后续补齐。

#### N-09（非阻塞）新增空 `tests/__init__.py` / `tests/unit/__init__.py`

- **位置**：`tests/__init__.py`、`tests/unit/__init__.py`（均 0 字节）。
- **证据**：新增测试使用 `from tests.unit.routing_fakes import ...`；全量 442 passed。
- **影响**：路径解析更明确；无现有测试依赖被破坏。
- **是否阻塞**：非阻塞（合理）。

#### N-10（非阻塞）`_current_tier` 把账号库所有 enabled 账号计入 current_tier

- **位置**：`routing_pipeline.py:420-438`。
- **影响**：只影响 `TierRequiredError.current_tier` 诊断值；通常会走容量错误而非 tier 错误。
- **是否阻塞**：非阻塞。

#### N-12（非阻塞）build surface 当前一律拒绝（P4 项）

- **位置**：`routing_pipeline.py:310-314`；`parse_model_surface` 对未配置 build 抛 `SurfaceCapacityError`（`routing_eligibility.py:102-107`）。
- **影响**：符合“build 实际页面归 P4”；P4 接线前 build 请求为 503。
- **是否阻塞**：非阻塞（P4）。

#### N-13（非阻塞）application 层仍导入 FastAPI `StreamingResponse`

- **位置**：`api_service_gemini.py:15,415,526`。
- **证据**：B2-4 已删除 application 层所有 `HTTPException` 抛出/导入，但 `StreamingResponse` 仍是既有依赖。
- **影响**：D-06 严格表述“application 层不依赖 FastAPI”仍有残留；不影响 403 领域错误边界。
- **是否阻塞**：非阻塞；建议 B5b 协议层重构时移出。

#### N-14R（非阻塞）共享 capture 的并发清理副作用

- **位置**：`instance_client.py:48-53`、`pooled_session.py:51-61`、`capture.py:35-38`。
- **证据**：同一实例的多个 `InstanceRequestClient` 现在共享 `RequestCaptureService`；某个请求的 `clear_templates()` 会清空该实例所有请求的模板缓存，下一次 capture 重新读取 base template。
- **影响**：功能正确，可能带来少量重复 `capture_template` 开销；无跨账号污染（session 隔离）。
- **是否阻塞**：非阻塞。

#### N-15（非阻塞）`/stats` 遍历模型 × 账号 × quota 键

- **位置**：`routes_system.py:69-120`。
- **影响**：`account_surface` 下每个模型每个账号可能有两行；模型/账号量大时统计端点开销上升。属 P7 统计账本范围。
- **是否阻塞**：非阻塞。

#### N-18（非阻塞）`parse_model_surface` 的 `requested_surface` 冲突抛 `ValueError` 会成 500

- **位置**：`routing_eligibility.py:88-95`。
- **证据**：当前 `/v1beta/...` 路由不传 `requested_surface`（`routes_gemini.py:19-43`），不可达；未来 API 传入非法/冲突 surface 时无专属 400 handler。
- **是否阻塞**：非阻塞。

#### N-19（非阻塞）`InstanceRequestClient._dump_raw_exchange` 可落盘 prompt/raw response

- **位置**：`instance_client.py:60-90`。
- **影响**：`settings.dump_raw_response` 默认关闭；开启时会写入 capture_prompt、modified_body、raw_response（可能含用户内容）。与 P7 dump 只落 `safe_*` 的口径相关。
- **是否阻塞**：非阻塞（P7 范围）。

#### N-20（非阻塞，P4 前）paid chat-only 的 rule/accessModes 边界

- **位置**：`routing_eligibility.py:199-211`（capability 在 `resolve_required_tiers` 之前）；`model_access.py:89-95,162-172`（`is_chat_only_model` 只查 `_PAID_MODELS`）。
- **证据**：`surface_capability` 使用 `is_chat_only_model(clean)`；若模型仅由 `paid_model_rules` 或 `accessModes` 判定为 Pro/Ultra（未被 discovery/内置 paid 集合收录），此时 `paid=True` 在 `routing_eligibility.py:213` 才计算，capability 检查可能放行 build。当前 build 在 `routing_pipeline.py:310-314` 仍被硬拒，P4 前不可见。
- **影响**：P4 打开 build 后，规则型 paid 模型可能绕过 chat-only 限制。
- **是否阻塞**：非阻塞（P4 前）；建议 P4 将 capability 判断改到 `paid` 冻结之后，或复用 `resolve_required_tiers` 的结果。

### 4.3 用户裁决项汇总

| 项 | 现状 | 裁决/确认状态 |
|---|---|---|
| D05-032 hang 回退 | 仅切号，耗尽抛原错误 | **用户已裁决 dropped** |
| D05-038 非 429 耗尽 | 401/403/502 原错误 | **用户已裁决保留** |
| D05-009/D05-014 paid→chat | `surface_capability` + `[ROUTING-RULE: PAID_CHAT_ONLY]` | **用户已裁决 paid 强制 chat-only**；残余 N-20 待 P4 |
| build surface 实际页面 | `routing_pipeline.py:310-314` 一律 503；无 build 页面 | 按既定 P4 接线；B2-4 不实现 |
| quota_scope UI | 仅 YAML + `/stats` 返回 `quota_scope`，无设置卡片 | 按 D-12 在 SettingsView 增量补；非本步 |
| `tier` 与 `required_tier` 同值 | `routing_eligibility.py:216-224` | 用户已接受；后续若需模型分级另立字段 |
| `features.routing.max_switches` 位置 | `defaults/config.yaml:44-46` | 用户已接受；后续统一 features schema |

## 5. 只读声明与状态

- 本次审查**只读**：未修改任何源代码、配置、测试或 git 状态；仅更新本报告（目标树与主仓各一份，内容一致）。
- 实跑命令均为只读检查；未访问真实 Google/真实账号/真实 IP，未启动真实浏览器，未 commit。
- **B2-4 尚未 commit**（目标树 HEAD `ca8496c`，工作区 27 个已跟踪修改 + 20 个未跟踪条目），等待用户审计并确认后再由有权限方提交。
- 结论：**PASS**。初审 Q-01/Q-02 已按用户裁决关闭，Q-03 与 N-02/N-04/N-11/N-14/N-16/N-17 已修复，N-08 核心测试已补齐；无新阻塞，保留 N-01、N-02R、N-03、N-04R、N-05、N-06、N-07、N-08R、N-09、N-10、N-12、N-13、N-14R、N-15、N-18、N-19、N-20 非阻塞项。
