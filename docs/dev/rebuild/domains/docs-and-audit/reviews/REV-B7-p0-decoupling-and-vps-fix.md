# REV-B7 P0 解耦与 VPS 修复独立复核

- 复核日期：2026-11-01（UTC+8）
- 目标工作树：`D:\LLM\self_programming\aistudio-api\.kilo\worktrees\rebuild-upstream`
- 目标分支 / HEAD：`rebuild-upstream` / `d1f48b5dce9a724e454d473e58f0f1196783290f`
- 主工作树：`D:\LLM\self_programming\aistudio-api`（`rebuild` / `f353f7a5f85548223f6d0fa38f1b0d66322b00e8`）
- 审计范围：VPS #1（429 雪球 / Free header）、VPS #2（D-14 只读模型端点）、VPS #3（`/health` 200 日志）、P0 B7-01/B7-02/B7-03 解耦及 B7-05/B7-07 清理
- 审计模式：严格离线、只读；未启动 `main.py server`、浏览器/Chromium、服务端，未发起任何网络请求
- 代码改动：无；未修改 `src/`、`tests/`、既有文档或配置；未 stage/commit/push
- 唯一写入：本报告的目标树副本与主仓副本

## 0. 结论

**Verdict: PASS。**

- 三个 VPS 修复均已在源码与测试中落地，关键行为与任务口径一致；测试没有真实等待退避。
- P0 分层边界已由 AST 测试与全仓 grep 双重确认：`application` 不导入 `aistudio_api.api`/FastAPI/`runtime_state`；`infrastructure` 不导入 `application`；`domain` 不导入外层；四个旧模块已删除。
- DTO、Gemini 渲染器、错误分类器、finish reason 映射均已完成归位；统计与 rotator 事件改为显式注入，无全局 `runtime_state` 回退。
- 独立门禁结果与预期一致：`2 failed, 864 passed, 1 skipped`；两个失败均为既有 Windows 环境问题，相关测试文件未被本 diff 触碰；Ruff、Pyright、diff 检查全部通过。
- 无 P0/P1 阻断项。存在若干 P2 文档/清理/残余风险项，均不影响本次 PASS 判定。

## 1. 基线、范围与工作树状态

| 项 | 结果 | 证据 |
|---|---|---|
| 目标 HEAD | `d1f48b5dce9a724e454d473e58f0f1196783290f` | `git rev-parse HEAD` |
| 目标分支 | `rebuild-upstream` | `git rev-parse --abbrev-ref HEAD` |
| `git diff HEAD --shortstat` | `71 files changed, 1222 insertions(+), 445 deletions(-)` | 本机实跑 |
| 状态条目 | 76 = 67 个未暂存修改 + 4 个纯重命名 + 5 个未跟踪 | `git status --porcelain` 分类计数 |
| 暂存区 | 仅 4 个 `R100` 纯重命名，0 行内容变更 | `git diff --cached --stat` |
| 未跟踪新文件 | `application/contracts/__init__.py`、`application/finish_reasons.py`、`infrastructure/gateway/public_web_constants.py`、`tests/unit/test_layer_boundaries.py`、`tests/unit/test_public_web_constants.py` | `git status --short` |
| 复核开始时根 `config.yaml` | 未跟踪（`git ls-files config.yaml` 为空） | 实跑 |
| 复核新增 | 本报告 1 个未跟踪文件（目标树；主仓同路径副本） | 本文件 |

> 说明：4 个重命名（`api_service_gemini.py -> api/gemini_service.py`、`api/schemas.py -> application/contracts/gemini.py`、`api/schemas_openai.py -> application/contracts/openai.py`、`application/model_access.py -> domain/model_access.py`）在 index 中已是 `R100`，但所有内容修改仍在工作区、未暂存。**未提交 / 未 push。**

## 2. VPS 修复证据

### 2.1 429 雪球与 Free header

| 检查项 | 结论 | 证据 |
|---|---|---|
| 显式 `Free` 不再回退全局 active Pro | **PASS** | `src/aistudio_api/infrastructure/gateway/transport.py:183-187`：`supplied_tier = (tier or "").strip()`，非空即直接使用；`Free` 在 `BENEFIT_TIER_TO_HEADER` 中无映射，因此不再写 Pro 的 `TIER1` |
| 模板中的陈旧 tier header 被移除 | **PASS** | `transport.py:170-181` 将 `x-aistudio-g1-tier` 从 incoming headers 中剔除后按实际 tier 重建 |
| 测试真实 seed 了 SAPISID 并 monkeypatch active tier | **PASS** | `tests/unit/test_stream_transport.py:35-58`：`page._cached_cookies = {"SAPISID": ...}`，`_get_active_account_tier -> "Pro"`，断言结果不是 Pro 且 stale header 被移除 |
| 其它显式 tier / 空 tier 行为 | **PASS** | `tests/unit/test_stream_transport.py:61-90`：`Ultra` 保留；空白 tier 才回退 active tier |
| `features.routing.retry_same_delay_sec` 默认 2.0 | **PASS** | `defaults/config.yaml:49`；`model_defaults.py:44-46, 815-819`；`get_configured_routing_settings` 在缺失时返回 2.0（`model_defaults.py:1480-1513`） |
| 0..30 clamp | **PASS** | `model_defaults.py:802-812`；`routing_pipeline.py:414-417` 再次 `min(30, max(0, ...))` |
| 环境变量覆盖 | **PASS** | `model_defaults.py:839-849`；`AISTUDIO_RETRY_SAME_DELAY_SEC`；非法值忽略并回退配置值 |
| `RouteContext` 传播 | **PASS** | `routing_pipeline.py:198`、`:414-423` |
| 仅 429 `retry_same` 睡眠 | **PASS** | `routing_pipeline.py:726-732`；403 分支 `:682-704` 与 204/404 分支 `:744-767` 均无 sleep |
| 测试不真实等待 | **PASS** | `tests/conftest.py:182` 全局 `AISTUDIO_RETRY_SAME_DELAY_SEC=0`；`tests/unit/test_routing_pipeline.py:506-609` 用 `AsyncMock` 替换 `asyncio.sleep` |
| 429/403 warning 含 model/surface/reason | **PASS** | `routing_pipeline.py:305-321`（`_summarize_error`）、`:695-703`（403）、`:717-725`（429）、`:916-928`（rebind）、`:980-989`（switch） |
| 不拼接 Cookie/API key | **PASS（有残余风险，见 §5）** | `_summarize_error` 只取异常类型、`status` 与折叠后的消息前 160 字符；测试 `tests/unit/test_routing_pipeline.py:612-666` 断言不含 `SAPISID` |
| rotator reason 可选、向后兼容 | **PASS** | `account_rotator.py:920-940, 956-976`；`account_orchestrator.py:17-57` 仅在 reason 非空时转发；旧调用无需改动 |
| Free 模板处理确实被检查 | **PASS（仅单元级）** | `tests/unit/test_stream_transport.py:35-58` 覆盖 stale tier header；`tests/unit/test_session_lifecycle.py:136-163` 覆盖 `capture_template` 不改写 base template。未做真实 Free/Pro 会话模板抓取 |

### 2.2 D-14 只读模型端点

| 路由 | 鉴权 | 证据 |
|---|---|---|
| `GET /v1beta/models` | API Key 或合法 Web 管理台 Bearer 会话 | `src/aistudio_api/api/routes_models.py:79-83` |
| `GET /v1beta/models/{id}` | 同上 | `routes_models.py:97-101` |
| `GET /v1/models` | `require_openai` + API Key 或 Web 会话 | `src/aistudio_api/api/routes_openai.py:169-172` |
| `GET /v1/models/{id}` | 同上 | `routes_openai.py:190-193` |
| `POST /v1/chat/completions` | 仅 API Key（Web 密码不作为 Key） | `routes_openai.py:84-87`；`app.py:467` 的 Gemini 生成路由仅挂 `require_api_key` |

| 检查项 | 结论 | 证据 |
|---|---|---|
| Web 会话识别 | **PASS** | `dependencies.py:18-28`（仅 Bearer）、`:51-68`（`auth_enabled` + `compare_digest`） |
| API Key 校验保持严格 | **PASS** | `dependencies.py:87-139`：空 Key 且匿名关闭 -> 401；缺 token -> 401；无效 -> 401 |
| 两个 router 的 router-level 依赖已移除 | **PASS** | `app.py:467-469`：`gemini_router` 保留 `require_api_key`；`models_router`/`openai_router` 不再挂全局依赖 |
| 无隐藏双重鉴权 | **PASS** | `routes_openai.py` 三个路由逐个挂载；`routes_models.py` 两个路由逐个挂载 |
| 空 Key / 匿名关闭仍拒绝 | **PASS** | `tests/unit/test_api_auth.py:46-68`；`dependencies.py:101-114` |
| Web 密码单独不能调用生成 | **PASS** | `tests/unit/test_routes_models.py:69-87` |
| 四个矩阵端点均接受 Web 会话 | **PASS** | `tests/unit/test_routes_models.py:18-33` |

### 2.3 `/health` 200 日志

| 检查项 | 结论 | 证据 |
|---|---|---|
| 200 成功不再写 INFO | **PASS** | `src/aistudio_api/api/app.py:693-697`：`path == "/health" and response.status_code == 200` -> `logger.debug` |
| 非 200 仍写 INFO | **PASS** | `app.py:698-709` 的 else 分支；`tests/unit/test_logging.py:425-463` |
| 其它路径仍写 INFO | **PASS** | 仅 `/health` + 200 进入 DEBUG 分支；其余路径走原 `logger.info` |
| 测试证明 | **PASS** | `tests/unit/test_logging.py:385-423` 断言无 INFO、存在 DEBUG |

## 3. P0 解耦证据

### 3.1 分层边界

| 边界 | 结论 | 证据 |
|---|---|---|
| `application/**` 不导入 `aistudio_api.api` / FastAPI / `runtime_state` | **PASS** | 全仓 grep 0 命中；`tests/unit/test_layer_boundaries.py:81-84` AST 校验 |
| `infrastructure/**` 不导入 `aistudio_api.application` | **PASS** | 全仓 grep 0 命中；`test_layer_boundaries.py:86-90` |
| `domain/**` 纯 | **PASS** | 全仓 grep 无 `application`/`api`/`infrastructure`/`fastapi`；`test_layer_boundaries.py:76-79` |
| 旧模块已删除 | **PASS** | `api/schemas.py`、`api/schemas_openai.py`、`application/api_service_gemini.py`、`application/model_access.py` 均不存在；`test_layer_boundaries.py:93-95` 固化 |
| 代码内旧 import 清零 | **PASS** | `src/` 与 `tests/` 对 `application.model_access`、`api_service_gemini`、`api.schemas*` 的引用为 0（仅边界测试断言其不存在） |

### 3.2 归位与一致性

| 检查项 | 结论 | 证据 |
|---|---|---|
| 请求 DTO 在 `application/contracts` | **PASS** | `application/contracts/gemini.py`、`openai.py`；无 FastAPI 依赖 |
| Gemini 渲染/SSE 在 API 层 | **PASS** | `api/gemini_service.py:1-8, 50, 218` |
| 错误分类/清理在 API 层 | **PASS** | `api/error_mapping.py:30`（clean）、`:48`（classify）；`api/format_errors.py:12-15` 已改为从 API 层导入 |
| finish reason 映射在 application | **PASS** | `application/finish_reasons.py:5-31`；`generation_service.py:135-137, 290-292` |
| 无循环导入 | **PASS** | 依赖方向 `api -> application/domain/infrastructure`、`application -> domain(+infrastructure 实现)`、`infrastructure -> domain`；AST 测试与全量 pytest 均通过 |
| 错误状态/消息/SSE parity | **PASS** | `tests/unit/test_api_responses.py:8, 157-190`；`tests/unit/test_format_error_routing.py:20-130`；`tests/unit/test_api_service_gemini.py:12-20` |

### 3.3 统计注入与 rotator 事件

| 检查项 | 结论 | 证据 |
|---|---|---|
| `app.py` 显式注入 `runtime_state.record` | **PASS** | `app.py:377` |
| 无全局 `runtime_state` 回退 | **PASS** | `routing_pipeline.py:647-658, 1009-1017` 仅使用 `self._stats_recorder` |
| `record_rotator_event` 无 `runtime_state` 回退 | **PASS** | `account_orchestrator.py:17-57`；`rotator is None` 时直接返回 |
| 生产路径不丢统计 | **PASS** | 生产只由 `app.py:373-378` 构造 pipeline，`stats_recorder` 与 `rotator` 均已传入；`RuntimeState.record` 签名兼容（`api/state.py:195-201`） |
| 旧调用无需改动 | **PASS** | `record_rotator_event` 与 rotator 方法均使用可选关键字参数；`tests/unit/test_account_orchestrator.py` 全绿 |

### 3.4 公共 Web 常量

| 检查项 | 结论 | 证据 |
|---|---|---|
| `AIzaSy` 字面量单源 | **PASS** | 全仓 grep 仅 `infrastructure/gateway/public_web_constants.py:13,15` |
| `MakerSuiteService` / `alkalimakersuite` 单源 | **PASS** | 全仓 grep 仅 `public_web_constants.py:19,21` |
| 调用方改用常量 | **PASS** | `cookie_parser.py`、`account_info_refresher.py`、`model_discovery.py`、`session.py`、`session_auth.py`、`instance_client.py` 均引用常量模块 |
| 测试固化 | **PASS** | `tests/unit/test_public_web_constants.py:27-36` |

### 3.5 `pg/` 移除与 surface 行为

| 检查项 | 结论 | 证据 |
|---|---|---|
| catalog 不再输出 `pg/` | **PASS** | `application/model_catalog.py:16-17, 25-62`；`tests/unit/test_model_catalog.py:24-88` |
| generation 拒绝 `pg/playground/official` | **PASS** | `application/routing_eligibility.py:30, 60-84`；`tests/unit/test_routing_eligibility.py:38-64` |
| bare / `build/` 行为保留 | **PASS** | `project_model_ids` 仍返回 bare IDs，并在 build surface 启用时追加 `build/<id>` |
| build chat-only（Paid）限制 | **PASS** | `model_catalog.py:57-58`；`domain/model_access.py:13, 255-261`；`tests/unit/test_model_catalog.py:37-61` |
| OpenAI metadata 对 `pg/` 的拒绝 | **PASS** | `tests/unit/test_format_openai.py:906-933`：`/v1/models/pg/...` -> 404 |

### 3.6 文档

| 检查项 | 结论 | 证据 |
|---|---|---|
| `ARCHITECTURE.md` 依赖方向与 P0 债务表述 | **PASS** | `docs/ARCHITECTURE.md:89-92`：Application 不导入 API/FastAPI；Infrastructure 不反向依赖 Application；历史 P0 已消除 |
| 新增模块职责表 | **PASS** | `ARCHITECTURE.md:108-120`：`contracts/`、`finish_reasons.py`、`domain/model_access.py`、`gemini_service.py`、`error_mapping.py` |
| changelog 结构与事实 | **PASS** | `docs/changelog.md:34-35, 46-47, 65`：契约新增、分层收紧、旧模块移除；未混入内部施工编号 |
| 无 stale P0 债务 | **PASS** | `ARCHITECTURE.md:92` 与 `test_layer_boundaries.py` 一致 |

## 4. 独立门禁结果

执行环境：`$env:PYTHONUTF8='1'; $env:PYTHONIOENCODING='utf-8'`；工作目录为目标工作树。

| 门禁 | 精确结果 |
|---|---|
| `uv run pytest -q` | **2 failed, 864 passed, 1 skipped in 11.44s** |
| `uv run ruff check .` | `All checks passed!`，exit 0 |
| `uv run pyright src tests` | `0 errors, 0 warnings, 0 informations`，exit 0 |
| `git diff --check` | exit 0 |
| `git diff --cached --check` | exit 0 |
| `git ls-files config.yaml` | 空 |

### 4.1 两个失败项的归因

| 失败测试 | 现象 | 是否被本 diff 触碰 | 归因 |
|---|---|---|---|
| `tests/unit/test_browser_engine.py::test_find_chromium_executable_strictly_requires_cloakbrowser` | `FileNotFoundError`（`browser_engine.py:121`） | **否**（不在 `git diff HEAD --name-only`） | Windows 本机无 CloakBrowser 运行时；属既有平台差异 |
| `tests/unit/test_browser_scripts_syntax.py::test_evaluate_wrapper_expressions_parse_cleanly` | `bun.CMD` 返回 1，stderr `) was unexpected at this time.` | **否** | Windows `bun.CMD -e` 参数/转义问题；与 AGENT §2.4 记载一致 |

两个失败的测试文件均未出现在本次 `git diff HEAD --name-only` 中；失败原因与 P0/VPS 改动无关。

## 5. Findings（P0/P1/P2）

### P0

无。

### P1

无。

### P2（均为非阻断）

1. **`_routing_settings` 的异常回退缺少退避默认值**
   - `routing_pipeline.py:523-529` 在注入 provider 抛异常时只返回 `{"max_switches": 2}`，于是 `retry_same_delay_sec` 退化为 0.0。
   - 生产路径未注入 provider，实际走 `get_configured_routing_settings()`，默认 2.0；因此当前不影响生产。仍建议回退值包含 `retry_same_delay_sec=2.0`，避免未来注入 provider 时静默关闭 429 退避。

2. **reason 摘要未做模式脱敏，stderr sink 未套脱敏器**
   - `routing_pipeline.py:305-321` 仅折叠空白并截断 160 字符；ring/file sink 会经 `sanitize_log_message`（`log_broadcast.py:61-78`），但 stderr 的 `UnifiedFormatter` 不调用脱敏（`logger.py:77-97`）。
   - 当前 429/403 消息来自上游响应体（`streaming.py:223-234`），不拼接请求 Cookie/API Key，未发现实际泄露路径；仍建议在 `_summarize_error` 内显式调用 `sanitize_log_message`，或让 stderr formatter 统一脱敏。

3. **`ARCHITECTURE.md` 迁移后部分 `file:line` 漂移**
   - `ARCHITECTURE.md:236` 仍写 `api/gemini_service.py:147-151`、`315-319`，而新文件仅 273 行，实际调用点在 `api/gemini_service.py:50`、`:218`。
   - `ARCHITECTURE.md:237-238, 259, 280` 的 `routing_pipeline.py` 行号也因新增代码整体下移而未同步。
   - 依赖方向、路径与 P0 结论本身准确；仅行号锚点陈旧。

4. **其它规格文档仍引用已删除模块**
   - `docs/PROTOCOL_AND_TOOLCALL_SPEC.md:24,345,382,394,607,883`、`docs/INSTANCE_POOL_AND_ROUTING_SPEC.md:17,368,688,690,864,873` 仍写 `application/api_service_gemini.py`、`application/model_access.py`。
   - 本次 B.8 只要求 `ARCHITECTURE.md`/`changelog.md`，故不阻断；但仓库级文档已出现旧路径。

5. **`api/gemini_service.py` 保留了未使用的 `to_gemini_finish_reason` 再导出**
   - 导入在 `api/gemini_service.py:26`，仅出现在 `__all__`（`:272`），模块体内未使用；测试均直接引用 `application/finish_reasons.py`。
   - 属 clean-cutover 的小清理项，不影响行为。

6. **OpenAI metadata 对显式 `chat/` 前缀的行为变化**
   - `model_catalog.py:16-32` 只解析 `build/`；`resolve_model_request("chat/<id>")` 返回原串，`/v1/models/chat/<id>` 变 404（`tests/unit/test_model_catalog.py:78-88`）。
   - catalog 已不再输出 `chat/`，且 Gemini generation 仍接受 `chat/`；对“实际列出的 ID”一致，但显式传 `chat/` 的 OpenAI metadata 客户端会回归。建议在文档/发布说明中明确该兼容窗口。

7. **`/health` 200 的 DEBUG 日志仍会进入 DEBUG ring**
   - `BroadcastHandler` 级别为 DEBUG（`log_broadcast.py:276`），root 为 DEBUG（`logger.py:335`），因此 200 的 DEBUG 记录仍可能出现在日志 SSE 的 DEBUG 视图；代码注释“and its broadcast sink”（`app.py:693-694`）表述不精确。
   - 任务要求是“no INFO UI stream”，该要求已满足（测试 `test_logging.py:385-423`）。

## 6. 残余风险

1. **无实机 VPS 验证**：本次全部为离线单元测试；未对真实上游 429 退避、Free/Pro 账号 header、`/health` 轮询做 live 复核。
2. **Cookie vs Bearer 会话**：`require_api_key_or_web_session` 只接受 `Authorization: Bearer <web password>`（`dependencies.py:18-28, 66-68`）；Web 控制台当前把 token 存在 `localStorage` 并发 Bearer（`web/src/api/client.ts:12-24,42-44`）。端点与现有控制台兼容，但 AGENT §6.3“服务端会话、不用 localStorage token”的表述仍与实际前端不一致（既有问题，非本 diff 引入）。
3. **Free 模板未做实机抓取**：仅验证了 transport 对显式 `Free` 的 header 处理与模板复制不改写 base；未在真实 Free/Pro 池会话中抓取并对比完整模板。
4. **reason 摘要的凭据依赖上游响应体**：ring/file 已脱敏，stderr 未脱敏；若未来上游 429/403 响应体回显凭据，stderr 仍可能泄露。建议按 §5.P2-2 加固。
5. **两个 Windows 环境失败**：与本 diff 无关，但会持续影响本机全绿判定；Linux/Termux 仍以平台结果为准。

## 7. 只读与提交状态声明

- 本次复核未修改 `src/`、`tests/`、配置、前端或既有文档；仅新增本报告。
- 未启动 `main.py server`、浏览器/Chromium、服务端；未发起任何本地或外部网络请求。
- 未 stage、未 commit、未 amend、未 push。
- **未提交 / 未 push。**
- 本报告在目标树与主仓逐字节一致；两份文件的 SHA256 由交付说明给出。

## 8. Post-review P2 closure（2026-11-01 追加独立复核）

> 本节由第二次独立只读复核追加，复核对象仍为同一未提交工作树（HEAD `d1f48b5dce9a724e454d473e58f0f1196783290f`）。§0–§7 保留为历史审计记录，不重写、不删改；本节结论为最终结论。复核期间未启动服务器、浏览器或任何网络请求，未修改 `src/`、`tests/`、配置与既有文档。

### 8.1 P2 逐项核验（以源码为准，不采信 Worker 声明）

| # | 原 P2 发现 | 状态 | 独立证据 |
|---|---|---|---|
| 1 | `_routing_settings` 异常回退缺少退避默认值 | **RESOLVED** | `src/aistudio_api/application/routing_pipeline.py:59` 新增 `DEFAULT_RETRY_SAME_DELAY_SEC = 2.0`；`:527-541` 的异常回退返回 `{"max_switches": 2, "retry_same_delay_sec": DEFAULT_RETRY_SAME_DELAY_SEC}`。新增测试 `tests/unit/test_routing_pipeline.py:539-554` 用抛异常的 provider 断言 `context.retry_same_delay_sec == 2.0`，实跑通过。 |
| 2 | `_summarize_error` 未做模式脱敏 | **RESOLVED** | `routing_pipeline.py:56` 引入 `sanitize_log_message`；`:308-324` 在截断前调用 `sanitize_log_message(collapsed)`。新增测试 `tests/unit/test_routing_pipeline.py:657-683` 注入 `Cookie: SAPISID=<secret>` 与 `Authorization: Bearer <secret>`，断言 warning 中不含 secret，实跑通过。 |
| 3 | `ARCHITECTURE.md` 迁移后 `file:line` 漂移 | **RESOLVED** | `docs/ARCHITECTURE.md:236-238`、`:253`、`:259`、`:280-287`、`:291` 已改为 `path::symbol` 形式；被引用符号均存在：`api/gemini_service.py:36`（`handle_gemini_generate_content`）、`:211`（`_build_gemini_streaming_response`）、`:174`（`format_sse_error`），`routing_pipeline.py:352/439/484/580/638/670/807/954`。`:416` 仅保留模块头 `api/gemini_service.py:1-7`，与实际 docstring 范围一致。 |
| 4 | 其它规格文档仍引用已删除模块 | **RESOLVED** | `docs/INSTANCE_POOL_AND_ROUTING_SPEC.md:17,26,368,688,690,864,873` 与 `docs/PROTOCOL_AND_TOOLCALL_SPEC.md:345,382,394,607,611,883` 均已改用 `domain/model_access.py::symbol`、`api/gemini_service.py::symbol`、`api/error_mapping.py::symbol`。全仓检索 `application/model_access`、`api_service_gemini`、`api/schemas*` 在 `src/` 与根 `docs/` 下 0 命中；仅剩 `docs/changelog.md:66` 的历史移除说明与 `tests/unit/test_layer_boundaries.py:94` 的“文件不存在”断言，均属预期。 |
| 5 | `api/gemini_service.py` 保留未使用的 `to_gemini_finish_reason` 再导出 | **RESOLVED** | `src/aistudio_api/api/gemini_service.py:265-271` 的 `__all__` 仅含 5 个实际导出，模块内已无 `to_gemini_finish_reason` 导入或引用；`src/` 全仓 0 命中。测试仍从 `application.finish_reasons` 直接导入（`tests/unit/test_api_responses.py:8`、`tests/unit/test_pg_toolcall_harness.py:25`），未受影响。 |
| 6 | `/health` 200 的 DEBUG 日志与注释表述 | **RESOLVED** | `src/aistudio_api/api/app.py:693-702` 注释已改为“keep success out of the INFO UI stream, but retain diagnostics”，不再声称 broadcast sink 被排除；行为仍为 200 -> `logger.debug`、其余 -> `logger.info`。DEBUG ring 行为按原设计保留，见 §8.5。 |
| 7 | OpenAI metadata 明示 `chat/` 的兼容窗口未文档化 | **RESOLVED** | `docs/changelog.md:48` 新增兼容性说明：“旧 `pg/` 前缀已移除；生成接口接受显式 `chat/` 前缀，但模型元数据目录不输出也不接受该前缀。” `application/model_catalog.py:3-7` 的模块说明同步声明不输出/不解析 `pg`/`playground`/`chat` 目录别名。行为本身未变，仅补齐发布说明。 |

### 8.2 回归检查

| 检查项 | 结果 | 证据 |
|---|---|---|
| 分层方向未因脱敏导入被破坏 | **PASS** | 新导入为 `application -> infrastructure.utils.log_broadcast`，属允许方向；`tests/unit/test_layer_boundaries.py:81-90` 仍全绿 |
| 摘要脱敏顺序 | **PASS** | 先 `sanitize_log_message` 后 `[:160]`，掩码不会被截断绕过 |
| 文档符号可解析 | **PASS** | 逐项 grep 确认全部 `::symbol` 存在于对应源码 |
| 旧模块引用清理 | **PASS** | 仅历史 changelog 与边界断言保留文本，无代码导入 |
| finish reason 可导入性 | **PASS** | `application.finish_reasons.to_gemini_finish_reason` 仍存在且被测试覆盖 |
| 差异规模 | 无异常 | `git diff HEAD --shortstat`：`73 files changed, 1324 insertions(+), 488 deletions(-)`；较 §1 的 71 文件增加的两项为本次补丁修改的两份规格文档；index 仍仅 4 个 `R100` 重命名、0 行内容变更 |

### 8.3 最终独立门禁（目标工作树实跑）

执行环境：`$env:PYTHONUTF8='1'; $env:PYTHONIOENCODING='utf-8'`。

| 门禁 | 精确结果 |
|---|---|
| `uv run pytest -q` | **2 failed, 866 passed, 1 skipped in 10.59s** |
| `uv run ruff check .` | `All checks passed!`，exit 0 |
| `uv run pyright src tests` | `0 errors, 0 warnings, 0 informations`，exit 0 |
| `git diff --check` | exit 0 |
| `git diff --cached --check` | exit 0 |
| `git ls-files config.yaml` | 空 |

**计数偏差已核查**：任务预期为 `2 failed, 864 passed, 1 skipped`，实际为 `866 passed`。差值为 **+2 passed**，来源恰为本次 P2 收口新增的两个测试：`tests/unit/test_routing_pipeline.py:539-554`（回退退避）与 `:657-683`（secret 脱敏）；测试文件修改时间（1:43:42）晚于首轮报告落盘时间（1:41:53）。失败项、跳过项数量与身份均未变化，无其它计数漂移。

### 8.4 两个仅环境相关的失败（未改变）

| 失败测试 | 现象 | 归因 |
|---|---|---|
| `tests/unit/test_browser_engine.py::test_find_chromium_executable_strictly_requires_cloakbrowser` | `FileNotFoundError`（`browser_engine.py:121`） | Windows 本机无 CloakBrowser 运行时；既有平台差异 |
| `tests/unit/test_browser_scripts_syntax.py::test_evaluate_wrapper_expressions_parse_cleanly` | `bun.CMD` 返回 1，stderr `) was unexpected at this time.` | Windows `bun.CMD -e` 参数/转义问题；与 AGENT §2.4 一致 |

两个测试文件均不在 `git diff HEAD --name-only` 中，与 P2 收口改动无关。

### 8.5 残余风险（未因 P2 收口消除）

1. **无实机 VPS 验证**：全部为离线单元测试；真实上游 429 退避、Free/Pro header、`/health` 轮询仍未 live 复核。
2. **Bearer/localStorage 与服务端 Cookie 会话口径不一致**：`require_api_key_or_web_session` 只接受 `Authorization: Bearer <web password>`（`api/dependencies.py:18-28,66-68`），Web 控制台仍把 token 存在 `localStorage` 并发 Bearer（`web/src/api/client.ts:12-24,42-44`）；属既有问题，非本次改动引入。
3. **脱敏为模式匹配而非语义隔离**：`sanitize_log_message` 依赖固定正则（`infrastructure/utils/log_broadcast.py:61-78`），无法保证覆盖所有未来上游回显格式；若响应体出现未匹配的凭据形态仍可能泄露。
4. **`/health` 200 的 DEBUG 记录仍进入 DEBUG ring**：`BroadcastHandler` 与 root 均为 DEBUG（`log_broadcast.py:276`、`logger.py:335`），“no INFO UI stream”要求已满足，但 DEBUG 视图仍可见；本次仅修正注释表述，未改变该行为。

### 8.6 最终结论

**Verdict: PASS。**

- 7 项 P2 全部 `RESOLVED`，均有 `file:line` 源码/测试证据，未发现收口改动引入回归。
- 独立门禁：Ruff 0、Pyright 0、diff 检查 0、根 `config.yaml` 未跟踪；pytest 仅两个既有 Windows 环境失败，预期内计数因新增 2 个测试由 864 变为 866。
- 残余风险集中在“无实机验证、会话承载口径、正则脱敏边界、DEBUG ring”，均不构成阻断。
- **未提交 / 未 push。** 本节所在报告在目标树与主仓逐字节一致；最终 SHA256 与路径由交付说明给出。
