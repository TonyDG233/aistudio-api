# REV-MERGE-852de53 独立只读离线审计

- 审计对象：目标工作树 `D:\LLM\self_programming\aistudio-api\.kilo\worktrees\rebuild-upstream` 中**未提交**的 `git merge --no-commit upstream/master`
- 被合并提交：`852de532efa6b538425443e7abc2212a7487a588`（含 `ccd15e3`，上游区间 `99600c7..852de53`，53 个文件）
- 目标 HEAD：`b1a749e95b42670d8a156c7b11185faa03dfeca0`
- 审计日期：2026-10-31
- 审计方式：只读检查 index/staged diff、HEAD diff、上游 diff、工作区文件与产物，并独立离线跑门禁
- 审计边界：**未启动 `main.py server`，未拉起 Chromium/CloakBrowser，未发起真实网络请求，未修改源码/测试/配置，未 stage/commit/abort merge**

## 1. 结论

**PASS。无阻断项（blocker = 0）。**

- 24 个冲突文件、29 个自动合并文件逐一核对：B1–B6、B4 日志三 sink、B5a 工具流、B6 模型发现、D-05/D-10/D-11 单页有界 drain 均未被上游覆写；无目标侧独有测试文件被删除。
- 上游 `index.js` 曾移除 `/logs` 与 `LogsView`，本次按冲突“保留目标实现”解析，B4 日志页仍完整；`LogsView.vue`、`routes_logs.py`、`log_broadcast.py` 均无 diff。
- 门禁独立实跑：`758 passed, 2 failed, 1 skipped`，2 个失败均为既有 Windows 平台用例；`ruff`、`pyright`、`git diff --check`、`git diff --cached --check` 全部通过。
- 前端门禁**未独立复跑**（`web/node_modules` 不存在，按指令未安装/未联网/未建 junction）；仅静态核对源码与已生成 bundle，与 Worker 报告一致。
- 存在若干**非阻断**残余风险，最值得后续处理的是 `app.py:508` 新增的参数校验 WARNING 会把 Pydantic `str(exc)`（含 `input_value` 片段）写入持久日志；以及 `logger.py:157-163` 丢失 `global _dump_to_file_override`。二者均未破坏本次审计要求的能力边界，详见 §12/§13。

## 2. 合并状态与文件分类

### 2.1 合并状态证据

| 项目 | 实测 |
| --- | --- |
| 分支 | `rebuild-upstream` |
| HEAD | `b1a749e95b42670d8a156c7b11185faa03dfeca0`（未变，未产生合并提交） |
| `MERGE_HEAD` | `852de532efa6b538425443e7abc2212a7487a588`（`.git/worktrees/rebuild-upstream/MERGE_HEAD` 存在） |
| 未解决冲突 | `git diff --name-only --diff-filter=U` 为空（0） |
| staged | 48 个文件，`+977 / -376` |
| 工作区 | `git status --porcelain` 仅 48 行 staged；无 unstaged、无额外 untracked |
| 根 `config.yaml` | 未跟踪且被忽略：`git ls-files config.yaml` 为空、`git status --ignored` 显示 `!! config.yaml`、`.gitignore:15` 为 `/config.yaml` |
| `defaults/config.yaml` | tracked（`git ls-files` 命中），与 HEAD 无 diff |

### 2.2 53 个上游文件 → 48 staged 的对账

- 上游区间 `99600c7..852de53`：`53 files changed, +1000/-502`。
- 冲突：`MERGE_MSG` 的 `# Conflicts:` 共 **24** 个文件（25 行减去 `Conflicts:` 标题）。
- 自动合并：53 − 24 = **29** 个文件。
- 冲突文件中 17 个产生净差异并进入 staged；7 个按“保留目标实现”解析后与 HEAD 相同，故不出现在 `git diff --cached`：
  - `config.yaml`、`src/aistudio_api/application/account_orchestrator.py`、`src/aistudio_api/application/api_service_gemini.py`、`src/aistudio_api/static/assets/index.js`、`tests/unit/test_account_orchestrator.py`、`tests/unit/test_account_rotation_concurrency.py`、`tests/unit/test_api_responses.py`
- 自动合并 29 个中 27 个进入 staged；2 个被回退为目标 HEAD 版本（非冲突路径）：
  - `src/aistudio_api/infrastructure/gateway/session_auth.py`
  - `web/src/stores/accounts.ts`
- 另有 4 个 staged 文件不属于上游区间，是本次合并/目标侧必要改动：
  - `docs/dev/rebuild/domains/upstream-merges/index.md`（新增台账）
  - `src/aistudio_api/application/routing_pipeline.py`（403 首次原位重试）
  - `src/aistudio_api/infrastructure/gateway/pooled_session.py`（快照新契约 `{prompt,hash}` / `auth_user`）
  - `tests/unit/test_routing_pipeline.py`（403 两阶段断言）
- 对账：17（冲突净改） + 27（自动净改） + 4（目标侧） = **48 staged**。

### 2.3 24 个冲突文件处置

| 文件 | 处置/证据 | 结论 |
| --- | --- | --- |
| `AGENT.md` | 仅新增 403 两阶段规则（`:260`） | 边界保留 |
| `config.yaml` | 保留本地未跟踪版本（N18 `pro_required` 无裸 `image`） | 符合 Q-04 |
| `api/app.py` | 吸收启动 tier 自检/日志；保留 B3 keepalive、B4 logs、B6 pool-only warmup | 通过 |
| `api/dependencies.py` | 仅日志文案 | 通过 |
| `api/routes_accounts.py` | 仅日志文案 | 通过 |
| `api/routes_gemini.py` | 仅日志文案 | 通过 |
| `api/routes_system.py` | 仅日志文案 | 通过 |
| `application/account_orchestrator.py` | 保留 HEAD（未 staged） | 通过 |
| `application/account_rotator.py` | 403 去硬冷却：`auth_cooldown = 0.0`（`:378`） | 通过 |
| `application/api_service_gemini.py` | 保留 HEAD（未 staged） | 通过 |
| `infrastructure/account/account_store.py` | `update_account_tier(..., checked_at)`（`:647-671`） | 通过 |
| `infrastructure/gateway/botguard_bootstrap.py` | temp chat、`auth_user`、真实模板、finally 清空 | 通过 |
| `infrastructure/gateway/client.py` | 仅日志；`_dump_raw_exchange` 仍为 `safe_*`（`:141-149`） | 通过 |
| `infrastructure/gateway/session.py` | 单页 drain 未动；新增 `auth_user`/快照参数/gc | 通过 |
| `infrastructure/gateway/streaming.py` | 截断修复、finish fallback；2s idle、CONTINUING=22 保留 | 通过 |
| `infrastructure/utils/logger.py` | httpx/httpcore 噪音抑制 + safe dump 字段 | 通过（见 N-28） |
| `static/assets/AccountsView.js` | 真实重建，含新文案 | 通过 |
| `static/assets/DashboardView.js` | 真实重建，含新 API 示例 | 通过 |
| `static/assets/SettingsView.js` | 真实重建，含“日志输出模式” | 通过 |
| `static/assets/index.css` | 真实重建（`border-l` 等） | 通过 |
| `static/assets/index.js` | 保留 HEAD（含 `/logs` 与 `LogsView`） | B4 保留 |
| `tests/unit/test_account_orchestrator.py` | 保留 HEAD（未 staged） | 测试保留 |
| `tests/unit/test_account_rotation_concurrency.py` | 保留 HEAD（未 staged） | 测试保留 |
| `tests/unit/test_api_responses.py` | 保留 HEAD（未 staged） | 测试保留 |

### 2.4 29 个自动合并文件要点

- 实际进入 staged 的 27 个：`docs/ARCHITECTURE.md`、`docs/BOTGUARD_VERIFICATION_CHAIN.md`、`api/routes_models.py`、`config.py`、`account/cookie_parser.py`、`account/cookie_refresher.py`、`browser/browser_engine.py`、`browser/cdp_client.py`、`browser/js/dialog_cleanup.js`、`browser/js/install_hooks.js`、`browser/js/snapshot_generate.js`、`browser/js/snapshot_probe.js`、`browser/scripts.py`、`gateway/capture.py`、`gateway/model_defaults.py`、`gateway/stream_parser.py`、`gateway/transport.py`、`tests/unit/test_browser_scripts_syntax.py`、`tests/unit/test_browser_session.py`、`tests/unit/test_runtime_persistence.py`、`web/src/components/{accounts/AccountRow,dashboard/QuickApiExamples,modals/CookieImportModal,settings/LogConfigCard}.vue`、`web/src/views/{AccountsView,DashboardView,SettingsView}.vue`
- 被回退为 HEAD 的 2 个：`session_auth.py`（保留完整 Cookie 注入与合并回写，拒绝上游 OSID/SIDCC 过滤与替换式回写）、`web/src/stores/accounts.ts`（保留 active 失败时回退首账号）。
- 其余 25 个均为日志文案/诊断、JSON 截断修复、快照参数、前端文案/布局；未发现 B1–B6 能力被自动合并语义覆盖。

## 3. 受保护能力逐项

### 3.1 B1–B6 测试保留

- HEAD `tests/` 共 **88** 个受跟踪文件；工作区非 `__pycache__` 文件同为 **88**，`HEAD tests` 缺失项为空。
- 4 个被修改测试文件的测试函数数量与 HEAD 完全一致：`test_browser_scripts_syntax.py` 3、`test_browser_session.py` 18、`test_routing_pipeline.py` 19、`test_runtime_persistence.py` 4。
- 无目标侧独有测试文件删除；`test_pg_toolcall_harness.py`、`test_signature_cache.py`、`test_model_discovery_paid.py`、`test_model_discovery_live_wiring.py`、`test_routes_models.py` 等 B5/B6 测试均未在 staged diff 中。

### 3.2 B4：日志三 sink / safe_* / D09

| 检查 | 证据 | 结论 |
| --- | --- | --- |
| 5000 ring | `infrastructure/utils/log_broadcast.py:23` `DEFAULT_CAPACITY = 5000`；`:25` replay 5000 | 保留 |
| 7 天归档 | `log_broadcast.py:28` `LOG_RETENTION_DAYS = 7`；`:321-344` archive/retention | 保留 |
| SSE | `api/routes_logs.py:117-150` StreamingResponse；`:119-124` Last-Event-ID | 保留 |
| 路由/侧边栏 | `api/app.py:407-410` logs protected_router；`:668,672` `/logs` SPA；`static/assets/index.js` 含 `LogsView.js`、`/logs` | 保留 |
| LogsView | `web/src/views/LogsView.vue`、`src/aistudio_api/static/assets/LogsView.js` 均无 diff | 保留 |
| safe_* dump | `infrastructure/utils/logger.py:390-419` sanitize/safe_json；`:494-504` safe_body/safe_response；`client.py:141-149` 仅 `safe_prompt/safe_request_body/safe_response` | 保留 |
| 无 raw 恢复 | staged 新增行无 `raw_response`/`headers`/`body` 恢复；仅 `streaming.py:99` 打印请求体长度 | 通过 |

D09 映射相关文件（`logger.py`、`log_broadcast.py`、`routes_logs.py`、`LogsView.vue`）除 `logger.py` 噪音/字段增强外无行为改动。

### 3.3 B5a：PG-01..04 / 首事件真实 HTTP / finish-only / CONTINUING=22 / 2s idle / 签名与 Struct

| 检查 | 证据 | 结论 |
| --- | --- | --- |
| PG-01 签名优先级 | `application/chat_service.py` 未在 staged diff；`wire_types.py:338-372` 签名/Struct 逻辑未改 | 保留 |
| PG-02 首事件真实 HTTP | `application/api_service_gemini.py:348-366` 预读 `first_chunk` 后才返回 `StreamingResponse`；`api/routes_openai.py:111-133` 同样预读 | 保留 |
| PG-03 finish-only / idle | `infrastructure/gateway/streaming.py:37` `TOOL_STREAM_IDLE_FINISH_S = 2.0`；`:118-136` 仅工具流且未 finish 时有界等待；`:242-247` 结束后统一发一次 finish | 保留 + fallback |
| PG-04 CONTINUING=22 | `wire_spec.py:127-128`；`streaming.py:163-168`、`:197-202` 均排除 `0` 与 `22` | 保留 |
| 截断不丢 finish/usage | `stream_parser.py:230-283` `finish()` 修复并 yield；`streaming.py:190-214` flush | 新增通过 |
| 无重复 finish | `streaming.py:244-247` 仅在末尾 yield 一次 `finish_reason` | 通过 |
| 测试 | `tests/unit/test_pg_toolcall_harness.py`、`test_toolshape_and_signature.py`、`test_gemini_atg_native.py` 未改；`uv run pytest` 全绿项含这些 | 通过 |

### 3.4 B6：模型发现 / 动态 Paid / tier_checked_at / 无双页预热

| 检查 | 证据 | 结论 |
| --- | --- | --- |
| `routes_models` 使用 `discovery_sessions_for` | `api/routes_models.py:15,81,95` | 通过 |
| `routes_openai` 使用 `discovery_sessions_for` | `api/routes_openai.py:21,165,184` | 通过 |
| 启动发现仅池会话 | `api/state.py:100-115` `RuntimeState.discovery_sessions()` 明确“never the control session”；`api/app.py:361-380` `_refresh_model_catalog_once` 使用它 | 通过 |
| 路由冷启动仅回退控制面 | `api/state.py:27-60`（READY 池优先，控制面仅冷启动回退） | 既有设计，保留 |
| 控制面不重复开页 | `api/app.py:92-98` `_start_control_warmup` 在 `pool_enabled` 时返回 `None`；`:227-236` 使用 | 通过 |
| 动态 Paid 真值优先 | `infrastructure/gateway/model_discovery.py:300-318` `discovery_entry_paid_truth`；`:612-614` 真值 `False` 直接返回 Free，不被静态规则覆写 | 通过 |
| `tier_checked_at` 3 天跳过 | `api/app.py:270-353`（`:294` 解析 ISO，`:353` 启动任务）；`account_keepalive.py:298-304` 同用 ISO | 通过（见 N-35/N-36） |

### 3.5 D-05 / D-10 / D-11：单页有界 drain，无蓝绿/lease

- `infrastructure/gateway/session.py:57` `DRAIN_TIMEOUT_S = 12.0`；`:336-355` `_drain_and_close_context` 使用 `deadline` 与 `min(remaining, 2.0)`，超时强制关闭旧页。
- `infrastructure/gateway/session_state.py:15-42` 仅 `SessionContext` 与 in-flight/drain 原语；无 `SessionReadinessGate`。
- 全仓 `src`/`tests` 检索：无 `_retiring_contexts` 实现、无 blue-green 实现、无 `lease/reservation/pinned/defer` 调度机制；仅存在“不存在”负向断言：
  - `tests/unit/test_browser_session.py:366,368`
  - `tests/unit/test_session_blue_green_readiness.py:36-43`
  - `tests/unit/test_instance_pool.py:294-311`
- `application/generation_service.py:9-10`、`routing_pipeline.py:5-6`、`instance_pool.py:7` 仍明确声明无 lease/reservation/pinned/defer。

### 3.6 AGENT.md §4.6 / §10.4

- `AGENT.md:234` `### 4.6 实机防风控铁律` 完整保留。
- `AGENT.md:418` `### 10.4 硬禁令` 完整保留；`:424` 明确禁止 Worker/子 Agent 私自启动真实服务/浏览器。
- 本合并只新增 `:260` 的 403 两阶段规则，未改动 §4.6/§10.4。

## 4. PooledSession / 快照 / 临时聊天

| 检查 | 证据 | 结论 |
| --- | --- | --- |
| `auth_user` 贯穿 | `pooled_session.py:81,92,103`；`session.py:284,297,305`；`botguard_bootstrap.py:204` | 通过 |
| Temporary Chat | `botguard_bootstrap.py:246-270` 调用 `enable_temporary_chat`；`ENABLE_TEMPORARY_CHAT_JS` 在 `:44-84` | 通过 |
| 真实模板、无假 fallback | `botguard_bootstrap.py:365-369` 显式抛错拒绝假模板 | 通过 |
| finally 清空输入框 | `browser/js/snapshot_generate.js:45-51`；bootstrap 清理 `botguard_bootstrap.py:357-370` | 通过 |
| `BrowserSession` 快照重试 | `session.py:417` `range(5)`；`:432` `wait_for_timeout(250)` | 5×250ms |
| `PooledSession` 快照重试 | `pooled_session.py:168` `range(5)`；`:185` `wait_for_timeout(250)` | 5×250ms |
| 新参数契约 | `browser/scripts.py:26-31` `build_snapshot_args`；`snapshot_generate.js:1` `{prompt, hash}`；两处均使用 | 通过 |

说明：上游 `ccd15e3` 移除了 99600c7 引入的“BotGuard 快照就绪 20×400ms 轮询”；当前依赖请求期 `generate_snapshot` 的 5×250ms 重试与 `aistudio.snapshot` 写回（`snapshot_generate.js:36`）。见 N-36。

## 5. 403 / 429 / Tier 映射

| 检查 | 证据 | 结论 |
| --- | --- | --- |
| 403 不加硬冷却 | `application/account_rotator.py:364-378` `record_auth_error` 强制 `auth_cooldown = 0.0`；`:230-240` `is_available` 不再因 403 拒绝 | 通过 |
| 首次 403 原位重签重试 | `application/routing_pipeline.py:614-635`（`:623` `same_retries == 0` → `"retry_same"`） | 通过 |
| 二次 403 切换 | 同上，`same_retries` 已为 1 → `_switch_or_raise`（`:633-635`） | 通过 |
| Paid 本地 TierRequiredError 403 不切号 | `routing_pipeline.py:600-601` 直接 raise；`api/error_mapping.py:60-81` → 403；`api_service_gemini.py:89-90` → 403；`error_mapping.py:5-6` 明确不与 429/503 混用 | 通过 |
| `quota_scope` D-01 | `account_rotator.py:66-78,151,214-268` 未改；`quota_scope` 记账口径保留 | 通过 |
| 429→503 | `routing_pipeline.py:637-657` 先同账号重试再切换；`:709-731` 耗尽时 `SurfaceSwitchExhaustedError`（`domain/errors.py` `status_code = 503`） | 通过 |
| 测试 | `tests/unit/test_routing_pipeline.py:413-456` 首次 retry_same/二次 switch/耗尽；`test_runtime_persistence.py:73-90` 403 后仍可用 | 通过 |

残余：Paid `TierRequiredError` 不切号目前以代码 + 错误映射为主，缺少单独“路由层不 switch”的直接断言（N-39）；429 与 403 交替场景无专项测试（N-39）。

## 6. 流式解析与 generation_service

- `stream_parser.py:145-152` `_repair_truncated_json` 最多 8 次补全；`:230-283` `finish()` 仅在能解析出 list 且含 usage/responseId/可用候选时 yield，避免空 chunk 污染。
- `streaming.py:190-214` 在 `aclose` 后 flush `parser.finish()`，补发 text/thinking/tool_calls/images/signature，不丢截断文本。
- `streaming.py:242-247` 工具流无 finish 时合成 STOP；否则保证一个 `finish_reason`（`1`），只发一次。
- `streaming.py:118-136` 2s 工具流 idle 仅在 `has_tool_calls and latest_finish_reason is None` 时启用；非工具流仍无限等待。
- `generation_service.py` 未在 staged diff（目标独有文件，上游无此文件）；`stream_generation` 的 `retry_same/switch/release` 逻辑未变。
- 结论：无重复 finish、无工具 idle 回归；新增的是截断兜底与统一 finish fallback。

## 7. logger / transport / capture 安全

| 检查 | 证据 | 结论 |
| --- | --- | --- |
| 仅吸收 httpx/httpcore 噪音 | `logger.py:305-307` 固定 WARNING；`:340-341` 接入统一格式 | 通过 |
| 未恢复 raw 字段 | `logger.py:444-541` 仍用 `safe_url/safe_body/safe_response_body/safe_*`；新增仅 `model/account_id/auth_user/token_usage/finish_reason` | 通过 |
| transport 仅日志/计数 | `gateway/transport.py` diff 仅 debug/耗时/分块计数；无 timeout 恢复 | 通过 |
| capture 无 ctx | `gateway/capture.py:18-23` `CapturedRequest` 仍无 `ctx`；全仓 `ctx=` 0 命中 | 通过 |
| B4 日志 toggle | `logger.py:157-163` 丢失 `global`（见 N-28） | 非阻断 |

## 8. 配置

| 检查 | 实测 | 结论 |
| --- | --- | --- |
| 根 `config.yaml` 未跟踪 | `git ls-files config.yaml` 0 行；`git status --ignored` = `!! config.yaml`；`.gitignore:15` `/config.yaml` | 通过 |
| 本地 N18 内容 | `pro_required.contains = [argon, gemini-4, imagen]`，无裸 `image` | 通过 |
| 根配置可解析 | `yaml.safe_load(config.yaml)` 成功，10 个顶层键 | 通过 |
| `defaults/config.yaml` | tracked，未修改；`yaml.safe_load` 成功；`pro_required` 同步无裸 `image` | 通过 |
| `auth_error` 死字段 | 根/默认配置仍保留 `rotator.cooldowns.auth_error: 300`；`model_defaults.py:369-379` 仍校验该键，但 403 已不使用（`account_rotator.py:378`） | 非阻断（N-31/Q-08） |

## 9. 前端与静态产物

| 检查 | 证据 | 结论 |
| --- | --- | --- |
| `web/node_modules` | `Test-Path` = False | 无法独立复跑前端门禁 |
| 构建产物含新 UI | `AccountsView.js` 含“当前方案将包含/切换下一账号/border-l”；`DashboardView.js` 含“cURL (标准 HTTP)/Python (Google GenAI SDK)/window.location.origin”；`SettingsView.js` 含“日志输出模式/报文持久化” | 通过 |
| LogsView 产物 | `static/assets/LogsView.js` 存在（21,574 B）且无 diff；`index.js` 的 `__vite__mapDeps` 与路由含 `LogsView.js`、`/logs` | B4 保留 |
| 非手工合并 | 4 个改动 bundle 均为单行压缩产物、无 `sourceMappingURL`、无 CRLF；大小随源码变化（如 `AccountsView.js` HEAD 41,226 → 41,611） | 通过 |
| `web/tsconfig.tsbuildinfo` | 存在但 `git status` 无输出；未进入 staged | 无噪音 |

## 10. 台账索引

- `docs/dev/rebuild/domains/upstream-merges/index.md` 两份副本（目标树/主仓）内容一致。
- 内容包含 `f2b1e28`、`16450cc & 99600c7`、`ccd15e3 & 852de53` 三段，并记录本合并边界。
- 两树文件 SHA256：`572EDDC274BA5FE2B0B7E2B69259F9C9BF1472ECE26AC9A05008C2AA5820A9CB`（独立复算，字节一致）。

## 11. 门禁（独立实跑，离线）

环境：`$env:PYTHONUTF8='1'; $env:PYTHONIOENCODING='utf-8'`；目标工作树根目录。

| 命令 | 结果 |
| --- | --- |
| `uv run pytest -q` | **2 failed, 758 passed, 1 skipped in 9.15s** |
| `uv run ruff check src tests` | **All checks passed!**（exit 0） |
| `uv run pyright src tests` | **0 errors, 0 warnings, 0 informations**（exit 0） |
| `git diff --check` | exit 0，无输出 |
| `git diff --cached --check` | exit 0，无输出 |
| `git diff --name-only --diff-filter=U` | 空（0） |
| `git rev-parse HEAD` | `b1a749e95b42670d8a156c7b11185faa03dfeca0` |
| `git rev-parse MERGE_HEAD` | `852de532efa6b538425443e7abc2212a7487a588` |

2 个失败均为既有 Windows 平台用例，与本次合并无关：

- `tests/unit/test_browser_engine.py::test_find_chromium_executable_strictly_requires_cloakbrowser`：本机未安装 CloakBrowser，`browser_engine.py:121` 抛 `FileNotFoundError`。
- `tests/unit/test_browser_scripts_syntax.py::test_evaluate_wrapper_expressions_parse_cleanly`：`bun.CMD -e` 在 Windows 报 `) was unexpected at this time.`。

1 skip 为既有 POSIX-only watchdog 用例。758 passed 与 Worker 声明一致。

**前端门禁限制**：`web/node_modules` 不存在；按指令未安装依赖、未联网、未建 junction，故 `vue-tsc`/lint/`bun run build` **未独立复跑**；仅核对 `web/src` 源码、`src/aistudio_api/static` 产物与 Worker 报告，结论一致。

## 12. Q 项（全部）

编号沿用 `REV-merge-99600c7` 的 Q-01..Q-04，并为本合并新增 Q-05..Q-13。

| 编号 | 事项 | 本次结论 |
| --- | --- | --- |
| Q-01 | 上游 UI 文案变更（原“推理流式传输/无时长限制”）是否获批 | **既往 USER-APPROVED**（移除 `timeout_stream`）；本次 `SettingsView.vue` 将该卡片改为“日志输出模式/日志级别/报文持久化”，见 Q-05 |
| Q-02 | 无限流式移除所有请求 deadline，非流式由上层兜底 | **维持 USER-AUTHORIZED**；`transport.py` diff 未恢复任何 timeout |
| Q-03 | 快照重试统一 5×250ms（含空快照） | **PASS**：`session.py:417/432`、`pooled_session.py:168/185` |
| Q-04 | 根 `config.yaml` untracked、`defaults/config.yaml` 唯一 tracked 模板 | **PASS**：`git ls-files config.yaml` 空、`.gitignore:15` 命中 |
| Q-05（新） | `SettingsView.vue:64-77` 将“推理流式传输/无时长限制”卡片替换为“日志输出模式/日志级别/报文持久化” | **OPEN（非阻断）**：属上游 UI 文案变更；建议用户按 AGENT §10.2 确认后再提交 |
| Q-06（新） | 403 两阶段口径：首次原位重签、二次切号、Paid 不切号 | **确认通过**：代码/测试/`AGENT.md:260`/`docs/ARCHITECTURE.md` 一致 |
| Q-07（新） | `_auto_refresh_tiers`（3 天）与 keepalive metadata refresh（24h，启动即跑）是否会重复探测 | **OPEN（非阻断）**：两者可同时启动，见 N-29；建议二选一或加互斥 |
| Q-08（新） | `rotator.cooldowns.auth_error` 已成为死字段（上游 config 已删，本仓仍保留并被校验） | **OPEN（非阻断）**：建议删除根/默认配置键及 `model_defaults.py:369-379` 校验，或明确保留兼容 |
| Q-09（新） | 文档称 `auth_errors * 200.0` 动态惩罚，但代码只累加 `auth_errors`，调度未使用该分值 | **OPEN（非阻断）**：文档与实现不一致，见 N-30 |
| Q-10（新） | `app.py:508` 参数校验 WARNING 记录 `str(exc)`（Pydantic 含 `input_value` 片段） | **OPEN（非阻断，安全优先）**：建议改用 `exc.errors(include_input=False)`，见 N-27 |
| Q-11（新） | `logger.py:157-163` 丢失 `global _dump_to_file_override` | **OPEN（非阻断）**：当前由 `settings.dump_to_file` 掩盖，见 N-28 |
| Q-12（新） | 实时 B6 生成/发现仍未实机验证 | **维持 PENDING**：本次按只读离线审计，不启动服务/浏览器，见 N-40 |
| Q-13（新） | `snapshot_probe.js`/`build_snapshot_probe_args` 已加入但生产未使用 | **OPEN（非阻断）**：接入 bootstrap 或删除，见 N-32 |

## 13. N 项（全部）

### 13.1 继承自 `REV-merge-99600c7` 的 N-01..N-26

| 编号 | 状态（本次核对） |
| --- | --- |
| N-01 | 已解决（控制台 dump key-based 脱敏，B4 已关闭） |
| N-02 | 已解决（aware/naive ISO 范围 400，B4 已关闭） |
| N-03 | 非阻断（多日导出静默跳过缺失归档；本次无 diff） |
| N-04 | 非阻断（uvicorn 日志只进 stderr；本次无 diff） |
| N-05 | 已解决（噪音测试断言真实命名空间；本次新增 httpx/httpcore 固定 WARNING） |
| N-06 | 非阻断（启动/跨天清理“调用”未测试） |
| N-07 | 非阻断（SSE 默认 15s/5000 与非法 Last-Event-ID 未断言） |
| N-08 | 非阻断（`test_stream_route_headers_and_spa_bypass` 名不副实） |
| N-09 | 已解决（SSE 重连清除陈旧 droppedNotice；`LogsView.vue` 无 diff） |
| N-10 | 已解决（tracked 静态前端已重建；本次 4 个 bundle 已更新） |
| N-11 | 已解决（instance raw dump 模型分量白名单） |
| N-12 | 非阻断（aware 输入按当前本地固定偏移归一化） |
| N-13 | 非阻断（非 JSON 表单/纯文本 body 仅正则脱敏） |
| N-14 | 已解决（`AIStudioClient._dump_raw_exchange` 已 safe 字段） |
| N-15 | 非阻断（`app.py:200-201` 仍直接写 `client.session._auth_file`；`set_ready_context` 未同步） |
| N-16 | 非阻断（上游新页 Cookie 回写/`_cached_cookies` 清理未吸收；本地保留旧页回写） |
| N-17 | 非阻断（safe dump 仍保留完整 prompt/body/response，仅按 key 脱敏；`kind` 未清洗） |
| N-18 | **状态变化**：上游 99600c7 的 BotGuard 快照就绪轮询已在 ccd15e3 中移除；当前仅请求期 5×250ms 兜底（见 N-36） |
| N-19 | 非阻断（启动账号同步无定向测试；现由 `_auto_refresh_tiers` 承担，见 N-33） |
| N-20 | 非阻断（历史文档仍含旧 timeout 键） |
| N-21 | 非阻断（前端门禁未独立复跑；本次同样受限，见 §11） |
| N-22 | 非阻断（非流式 hooked replay 亦无请求 deadline） |
| N-23 | 非阻断（`PooledSession.ensure_ready` 仍 `del timeout_s`，`pooled_session.py:138-140`） |
| N-24 | 非阻断（`PooledSession` 空快照重试缺直接测试；异常路径有定向测试） |
| N-25 | 非阻断（`ruff check .` 的历史 docs harness 告警；本次只跑 `src tests`） |
| N-26 | 已失效（B-01 修复已在 HEAD 并随本合并处于 staged；不再存在“未 stage”风险） |

### 13.2 本次新增 N-27..N-41

| 编号 | 说明（含 file:line） | 阻断性 |
| --- | --- | --- |
| N-27 | `api/app.py:508` 新增 `logger.warning(... error=%s, exc)`；Pydantic `str(RequestValidationError)` 含 `input_value`（实测截断前缀+后缀），畸形请求可能把 prompt 片段写入 ring/7d archive/stderr。建议 `exc.errors(include_input=False)` 或只记录 loc/type。 | 非阻断（安全优先跟进） |
| N-28 | `infrastructure/utils/logger.py:157-163` `set_dump_to_file` 丢失 `global _dump_to_file_override`，赋值成为局部变量；当前因同时写 `settings.dump_to_file` 而功能可用，但运行时 override 语义失效，未来若配置/设置分叉会回归。 | 非阻断 |
| N-29 | 启动 metadata 探测重复：`api/app.py:353` 立即启动 `_auto_refresh_tiers`（3 天阈值，`probe_account_email/probe_account_benefit_tier` HTTP），同时 `account_keepalive.start()`（`account_keepalive.py:160-174`）在 `metadata_refresh_on_startup=True` 时立即刷新（24h 阈值，`AccountInfoRefresher` HTTP）。>3 天旧账号会双份探测。 | 非阻断 |
| N-30 | `docs/ARCHITECTURE.md` 与 `docs/BOTGUARD_VERIFICATION_CHAIN.md` 声称 `auth_errors * 200.0` 动态避让；代码仅累加 `auth_errors`（`account_rotator.py:373`），`is_available`/调度未使用该分值。 | 非阻断（文档/实现不一致） |
| N-31 | `rotator.cooldowns.auth_error: 300` 仍存在于根/`defaults/config.yaml`，`model_defaults.py:369-379` 仍强制校验，但 `account_rotator.py:378` 已不再使用。上游 `config.yaml` 已删该键；若直接采用上游配置会校验失败。 | 非阻断 |
| N-32 | `browser/js/snapshot_probe.js`、`scripts.py:35-40 build_snapshot_probe_args` 仅被语法测试引用，生产路径未调用；`SNAPSHOT_PROBE_JS` 在 `scripts.py:22` 加载但无使用点。 | 非阻断 |
| N-33 | `_auto_refresh_tiers`（`app.py:281-353`）无开关/无环境门控，启动即对旧账号发真实 HTTP 探测；与离线/测试环境隔离需依赖无账号或 3 天内已校验。 | 非阻断 |
| N-34 | 手动 `detect-email`/`detect-tier`（`routes_accounts.py:479-483,534`）调用 `update_account_tier` 未传 `checked_at`，不会刷新 `tier_checked_at`，可能触发后续启动重复探测。 | 非阻断 |
| N-35 | `tier_checked_at` 统一按 ISO 字符串存取（`account_store.py:663,692-695`），`app.py:273-279` 与 `account_keepalive.py:_parse_ts` 均按 ISO 解析；历史 float 字符串会解析失败并按“陈旧”重探（安全但可能多一次探测）。 | 非阻断 |
| N-36 | BotGuard 快照就绪轮询被移除：`botguard_bootstrap.py` 不再等待 `snapshot` 就绪，仅在 `capture`/`generate_snapshot` 时以 5×250ms 重试；首请求若快照未就绪，依赖重试兜底。 | 非阻断（行为变化） |
| N-37 | 上游 `session_auth.py` 改动被回退：未吸收 OSID/__Secure-OSID/SIDCC 过滤与“替换式”`save_browser_cookies`；目标保留完整 Cookie 注入与合并回写（`session_auth.py` 当前实现）。 | 非阻断（目标设计优先） |
| N-38 | 上游 `web/src/stores/accounts.ts` 改动被回退：保留 active 失败时回退首账号的旧行为；`AccountsView.vue` 新文案不依赖该改动。 | 非阻断 |
| N-39 | 429+403 交互无专项测试：首次 403 `retry_same` 不记失败账号，若随后 429 再走同账号重试/切换；逻辑上受 `same_retries` 与 `max_retries` 约束，但缺直接断言。Paid `TierRequiredError` 不切号亦缺路由层直接测试。 | 非阻断 |
| N-40 | 实时 B6（真实 ListModels/生成/Paid 判定）与真实账号 403 仍未实机验证；本次严格离线，不启动服务/浏览器。 | 非阻断（待实机） |
| N-41 | `dump_request_exchange` 新增 `error_detail` 参数（`logger.py:463`）但无调用者；`account_id/auth_user/token_usage/finish_reason` 已写入 dump（非密钥，但扩大元数据面）。 | 非阻断 |

## 14. 残余风险汇总

1. **日志参数校验片段**（N-27，`app.py:508`）：最需要跟进的安全项；建议在提交前改为不含 `input_value` 的错误摘要。
2. **`auth_error` 死字段**（N-31/Q-08）：配置仍要求该键，但运行时不再使用；上游配置已删除该键，存在“用上游 config 启动即校验失败”的运维陷阱。
3. **`auth_errors * 200.0` 文档/实现不一致**（N-30/Q-09）。
4. **启动 metadata 双探测**（N-29）：HTTP 层重复，不是浏览器双页；不违反 B6 单页红线。
5. **`tier_checked_at` ISO vs float**（N-35）：ISO 一致，旧 float 只会导致一次安全重探。
6. **429+403 交互**（N-39）：缺专项测试。
7. **`set_dump_to_file` global 丢失**（N-28）：当前被 settings 掩盖。
8. **CRLF**：4 个重建 bundle 均为 LF（无 CRLF），`git diff --check` 干净。
9. **实时 B6 仍 pending**（N-40）。
10. **前端门禁未独立复跑**（§11）：`web/node_modules` 缺失，仅静态核对。

## 15. 只读声明

- 未启动 `main.py server`，未拉起 Chromium/CloakBrowser，未发起真实 Google/外网请求。
- 未修改任何源码、测试、配置、前端或生成产物；未执行 `git add`/`git commit`/`git merge --abort`/`git reset`/`git stash`。
- 仅创建/更新本报告两份副本；为写入报告创建了其父目录 `docs/dev/rebuild/domains/upstream-merges/reviews/`（两份均属允许输出路径）。
- `uv run pytest/ruff/pyright` 使用现有环境离线执行，未安装依赖；`web/node_modules` 不存在，未联网、未建 junction。

## 16. 报告路径与双写

- 目标树：`D:\LLM\self_programming\aistudio-api\.kilo\worktrees\rebuild-upstream\docs\dev\rebuild\domains\upstream-merges\reviews\REV-MERGE-852de53-oct31.md`
- 主仓：`D:\LLM\self_programming\aistudio-api\docs\dev\rebuild\domains\upstream-merges\reviews\REV-MERGE-852de53-oct31.md`
- 两份内容逐字节一致。

## 17. 最终 closeout 复审（2026-10-31）

本节为最终状态，覆盖 §12/§13 中同名条目的旧状态；原 Q/N 条目全部保留、不删除。

### 17.1 范围与状态

- closeout 代码/测试/文档改动位于工作区 **unstaged**：15 个文件（门禁时 `git diff --name-only`；本审计另更新 `index.md` 与报告，故最终 unstaged 计数为 16，其中 1 为台账），`+616/-99`；merge 本身仍为 48 staged。
- HEAD 仍为 `b1a749e95b42670d8a156c7b11185faa03dfeca0`；`MERGE_HEAD` 仍为 `852de532efa6b538425443e7abc2212a7487a588`；0 unmerged。
- 根 `config.yaml` 仍未跟踪且被忽略（`git ls-files config.yaml` 为空，`.gitignore:15` `/config.yaml`）。
- 未启动服务/浏览器/真实网络，未 stage/commit/abort。

### 17.2 门禁（独立离线复跑）

| 命令 | 结果 |
| --- | --- |
| `uv run pytest -q` | **2 failed, 767 passed, 1 skipped in 9.62s** |
| `uv run ruff check src tests` | **All checks passed!**（exit 0） |
| `uv run pyright src tests` | **0 errors, 0 warnings, 0 informations**（exit 0） |
| `git diff --check` | exit 0，无输出 |
| `git diff --cached --check` | exit 0，无输出 |
| 未解决冲突 | 0 |
| staged / unstaged（门禁时） | 48 / 15（本审计更新 `index.md` 后为 48 / 16） |

2 个失败仍为既有 Windows 平台用例：CloakBrowser 未安装；`bun.CMD -e` 的 Windows 换行问题。1 skip 为既有 POSIX-only 用例。767 与 closeout 声明一致。

### 17.3 五项 closeout 修复逐项证据

#### F1. Q-10 / N-27：参数校验日志仅 loc/type/msg

- `api/app.py:572-584` 从 `exc.errors()` 仅投影 `loc/type/msg`，不再记录 `str(exc)`；注释明确“keep prompts/body fragments out of logs”。
- 回归测试 `tests/unit/test_logging.py:654` `test_request_validation_log_omits_user_input` 注入 `input={"text": "SECRET_INPUT_FRAGMENT_9f3a"}`，断言日志不含 secret、含 `string_type`，并断言响应仍为 400。
- 结论：**RESOLVED**。剩余的 `loc` 在极端情况下可含攻击者自选的 JSON key，但不含请求值/`input_value`，属可接受残余（见 N-41 既有条目）。

#### F2. Q-11 / N-28：恢复 `global _dump_to_file_override`

- `infrastructure/utils/logger.py:157-160` 已恢复 `global _dump_to_file_override`。
- 回归测试 `tests/unit/test_logging.py:627` `test_set_dump_to_file_runtime_override_does_not_mutate_unrelated_settings` 先 `set_dump_to_file(True)`，再把 `settings.dump_to_file` 改为 False，断言 `is_dump_to_file_enabled()` 仍为 True；若缺少 `global` 该断言会失败。
- 结论：**RESOLVED**。

#### F3. Q-07 / N-29 / N-33 / N-34：启动探测协调、ISO checked_at、Free 新鲜跳过

- 手动 detect：
  - `api/routes_accounts.py:489` 与 `:544` 均传 `checked_at=datetime.now(UTC).isoformat()`；
  - `application/account_service.py:85-89` 透传到 `AccountStore.update_account_tier`；
  - 因此手动 `detect-email` / `detect-tier` 会刷新 `tier_checked_at`（N-34 代码面闭合）。
- 启动协调：
  - `api/app.py:397-413` 先执行 keepalive 启动 metadata refresh，`covered_ids` 来自 `result["accounts"]`，再对未覆盖账号调用 `_refresh_startup_account_metadata`；
  - `application/account_keepalive.py:290-292` `run_startup_metadata_refresh` 在 busy 时等待 `_idle_event`，`_metadata_refresh` 在 `:345-351` 返回 `accounts` 列表；
  - `application/account_info_refresher.py:144-152` `is_verified_free_tier_fresh`（3 天），`:247-271` 对 3 天内已验证 Free 跳过 `_post_tier_request`，仅按需补邮箱。
- 测试：
  - `tests/unit/test_account_keepalive.py:634`：2 天内 Free → `transport.posts == 0`，`tier_checked_at` 不变；
  - `tests/unit/test_account_keepalive.py:657`：4 天前 Free → `transport.posts == 1`，`tier_checked_at` 更新；
  - `tests/unit/test_app_startup_warmup.py:159`：covered 批次 app 侧不再探测（`calls == []`），未覆盖批次只补邮箱、不重复 tier POST。
- 结论：**Q-07 / N-29 / N-34 RESOLVED**。**N-33 按“避免重复探测 + 3 天新鲜度”口径 RESOLVED**；但原条目中的“配置门控”子项仍未闭合：
  - `api/app.py:400-413` 只用 `settings.metadata_refresh and settings.metadata_refresh_on_startup` 门控 **keepalive**；`_refresh_startup_account_metadata` 在条件外**无条件执行**。
  - 当用户显式关闭 `features.keepalive.metadata_refresh` 或 `metadata_refresh_on_startup` 时，旧账号仍会被应用侧 HTTP 探测；无对应测试。
  - 该子项为非阻断残余，单独保留为 **N-43**（由 N-33 拆分，不推翻 N-33 的重复探测口径闭合）。

#### F4. Q-08 / N-31：`auth_error` 可选、存在时必须数值、缺失默认 0

- `infrastructure/gateway/model_defaults.py:380-390`：`auth_error` 从必填列表移出；存在时拒绝 bool/非数值；缺失时写入 `0`。
- `_default_config()` 中默认值改为 `0`（`model_defaults.py:772`）。
- 测试：
  - `tests/unit/test_features_config.py:331` 缺失时得到 `0`；
  - `tests/unit/test_features_config.py:351` 存在但为字符串时抛 `ConfigValidationError`。
- 结论：**RESOLVED**。根/`defaults/config.yaml` 仍保留 `auth_error: 300`（数值合法但已无调度作用），不再导致上游无该键配置校验失败；清理该键属可选的后续整理。

#### F5. Q-09 / N-30 / N-39：文档修正、Paid 403 不切号、403→429 策略

- 文档已修正：
  - `docs/ARCHITECTURE.md:214` 改为“不设硬冷却锁（`auth_cooldown=0.0`），仅累加 `auth_errors`”；
  - `docs/BOTGUARD_VERIFICATION_CHAIN.md:332` 明确“调度准入不读取 `auth_errors`，不存在 `auth_errors * 200.0` 动态避让”。
  - `src` 中未发现 `auth_errors * 200.0` 实现；旧表述仅存在于历史审计报告文本中（本次已更新）。
- 新增测试：
  - `tests/unit/test_routing_pipeline.py:461` `test_paid_tier_required_error_raises_without_switch`：`TierRequiredError` 直接抛出，`switches == 0`、`failed_accounts` 为空、无 wake；
  - `tests/unit/test_routing_pipeline.py:491` `test_auth_error_then_429_follows_same_instance_policy`：首次 403 `retry_same`；随后同实例 429 立即 `switch`；新实例 429 重新获得一次 `retry_same`。
- 结论：**Q-09 / N-30 / N-39 RESOLVED**。

### 17.4 Q-05 / Q-13 / N-37 / N-38 未触碰

- `git diff --name-only`（closeout）不含以下文件，状态与上轮一致：
  - Q-05：`web/src/views/SettingsView.vue` 仍为 merge staged 变更（“日志输出模式”），closeout 未改；
  - Q-13：`infrastructure/browser/js/snapshot_probe.js` 仍为 staged 新增且生产未使用；
  - N-37：`infrastructure/gateway/session_auth.py` 仍等于 HEAD，保留目标完整 Cookie 注入/合并回写；
  - N-38：`web/src/stores/accounts.ts` 仍等于 HEAD，保留首账号回退。
- 结论：**未触碰，状态不变**。

### 17.5 保护面复验（closeout 后）

- B4：`log_broadcast.py`、`routes_logs.py`、`LogsView.vue`、`static/assets/index.js`、`LogsView.js` 无 closeout 改动；`client.py:141-149` 仍为 `safe_*` dump；`logger.py` closeout 仅新增 `global` 一行。
- B5a：`streaming.py`、`stream_parser.py`、`routing_pipeline.py`、`generation_service.py` 无 closeout 改动；2s idle、CONTINUING=22、首事件预读保持。
- B6：`api/app.py:96-127` `_start_control_warmup`/`_startup_instances`/`_refresh_model_catalog_once` 保留；`api/app.py:348-357` pool-only warmup；`routes_models.py`/`routes_openai.py` 无 closeout 改动。
- 单页：`session.py`、`pooled_session.py` 无 closeout 改动；`_retiring_contexts`/`SessionReadinessGate`/blue-green/lease 仍仅为“无此机制”的文档/负向断言。
- 快照：`session.py:417/432` 与 `pooled_session.py:168/185` 仍为 5×250ms。
- AGENT：`AGENT.md` 无 closeout 改动；§4.6/§10.4 与 403 两阶段规则保留。

### 17.6 状态覆盖表

| 条目 | 最终状态 | 依据 |
| --- | --- | --- |
| Q-10 / N-27 | **RESOLVED** | `app.py:572-584` + `test_logging.py:654` |
| Q-11 / N-28 | **RESOLVED** | `logger.py:157-160` + `test_logging.py:627` |
| Q-07 | **RESOLVED** | `app.py:397-413` + `test_app_startup_warmup.py:159` |
| N-29 | **RESOLVED**（正常启用路径） | covered_ids + 3 天新鲜度；`test_app_startup_warmup.py:159`、`test_account_keepalive.py:634/657` |
| N-30 | **RESOLVED** | `docs/ARCHITECTURE.md:214`、`docs/BOTGUARD_VERIFICATION_CHAIN.md:332` |
| N-31 | **RESOLVED** | `model_defaults.py:380-390/772` + `test_features_config.py:331/351` |
| N-33 | **RESOLVED（重复探测/新鲜度口径）**；配置门控子项拆为 **N-43** | `app.py:400-413` 仍无条件执行 app 侧探测 |
| N-34 | **RESOLVED** | `routes_accounts.py:489/544` → `account_service.py:85-89` |
| N-39 | **RESOLVED** | `test_routing_pipeline.py:461/491` |
| Q-05 / Q-13 / N-37 / N-38 | **未触碰，保持原状态** | closeout diff 不含对应文件 |

### 17.7 closeout 新增残余

| 编号 | 说明 | 阻断性 |
| --- | --- | --- |
| N-42 | 手动 `detect-email`/`detect-tier` 的 ISO `checked_at` 已实现，但无直接路由单测；当前由 `account_service.py`/`account_store.py` 代码路径保证。 | 非阻断 |
| N-43 | `app.py:400-413` 仅门控 keepalive；`_refresh_startup_account_metadata` 在 `metadata_refresh=false` / `metadata_refresh_on_startup=false` 时仍无条件执行，旧账号仍会被应用侧 HTTP 探测；无禁用场景测试。 | 非阻断 |
| N-44 | 5 项 closeout 修复当前为 **unstaged**（15 文件）；merge 的 48 个文件已 staged。提交前需由用户显式 stage closeout，否则会遗漏。 | 非阻断（操作提示） |

### 17.8 closeout 结论

- **PASS。0 blocker。**
- 5 项 closeout 中，F1/F2/F4/F5 完全闭合；F3 的重复探测/ISO/Free 新鲜度口径闭合，唯一未闭合的是 N-33 的“配置门控”子项，已拆为 N-43 非阻断保留。
- 所有原 Q/N 条目仍保留；本节的“RESOLVED”仅覆盖对应条目的本次 closeout 语义，不删除历史记录。

## 18. 最终报告 SHA256

- 两份副本逐字节一致；校验口径：将本行 64 位十六进制替换为 64 个 `0` 后，对全文（UTF-8，含换行）计算 SHA256，结果应等于替换前的值。
- SHA256 = 8c6cb0032ffa581979eaeec4843af5446c1a9fb1e3a9b4f1e2c42950ff46f223
