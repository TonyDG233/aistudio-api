# REV-merge-99600c7 独立只读复核（upstream 合并）

- 复核对象：`rebuild-upstream` 工作树中未提交的 `git merge --no-commit upstream/master`
- 目标提交：`99600c795e9e0563f9ad4caae5cc58c1e72e7bbd`（含 `16450cc061212b36b5f58ae886c5419e11c51eb3`）
- 本地 HEAD：`424f896cd8c55eb41c952d4d962f3e2f6ae8b2b1`
- 复核日期：2026-10-30（初检）；2026-10-30（B-01 修复复审）
- 复核方式：只读检查实际 staged diff、HEAD diff、上游提交 diff、行为代码与产物，并独立实跑门禁

## 1. 结论

**PASS**（B-01 修复复审后）。

- **B-01（已解决）**：快照生成重试已按上游 `99600c7` 吸收为 5 次、间隔 250ms，并同步到控制面 `BrowserSession.generate_snapshot` 与请求面 `PooledSession.generate_snapshot`；空快照（返回空字符串）也走同一重试/等待路径；新增 3 个定向测试真实断言 5 次 `evaluate` 与 4 次 250ms 等待。详见 §5.3 与 §11。
- 其余强制检查（D-02、架构红线、签名/无请求超时、无限流式、gzip 移除、BotGuard 就绪、App 启动同步、UI/config、N-14、合并卫生）在初检已通过，本轮复核确认未因 B-01 修复引入回归，详见下文。
- 剩余问题均为非阻塞 N 项与用户已裁决 Q 项；无新的阻塞缺陷。
- 未修复其他缺陷、未提交、未中止合并、未开始 B5a。仅更新本报告两份副本；源码/测试/配置均未改动。

## 2. 合并状态与范围

### 2.1 合并状态证据

| 项目 | 实测结果 |
| --- | --- |
| 分支 | `rebuild-upstream` |
| HEAD | `424f896cd8c55eb41c952d4d962f3e2f6ae8b2b1`（未变，未产生合并提交） |
| `git rev-parse MERGE_HEAD` | `99600c795e9e0563f9ad4caae5cc58c1e72e7bbd` |
| `.git/worktrees/rebuild-upstream/MERGE_HEAD` | 存在（`Test-Path` = True） |
| 冲突标记 | `git status` 无 `UU`/`AA`/`DD`/`AU`/`UA`；`git diff --name-only --diff-filter=U` 为空 |
| 工作区 | `git status --porcelain` 共 29 行：27 个 staged 文件（其中 `session.py`、`pooled_session.py`、`test_browser_session.py` 另有 unstaged 修改）、1 个仅 unstaged（`test_cdp_browser_context.py`）、1 个 untracked（本报告）；无未解决冲突 |
| staged diff 规模 | 27 files changed, +199/−193（初检后未变） |
| 未暂存 diff（B-01 修复） | 4 files changed, +95/−6：`session.py`、`pooled_session.py`、`test_browser_session.py`、`test_cdp_browser_context.py` |
| 根 `config.yaml` | 未跟踪；`.gitignore:15` 命中 `/config.yaml`；`git ls-files --error-unmatch config.yaml` 报 `did not match`；`git status --ignored` 显示 `!! config.yaml`；`defaults/config.yaml` 为 tracked 模板 |

### 2.2 上游提交与范围

- `16450cc`：移除所有请求超时看门狗（无限流式时长）。
- `99600c7`：BotGuard Wasm 快照就绪等待、每请求 pin `SessionContext`、移除上游 gzip、启动账号同步。
- staged 改动集中在 gateway/browser/api/config/web 静态产物与对应测试，未出现 P5/B5a 工具调用、OpenAI seam 或其他无关域的新增文件。
- 27 个 staged 文件中，`api/dependencies.py`、`api/response_models.py`、`api/routes_accounts.py` 的改动仅为上游空行/格式化；无行为变化。
- 本轮 B-01 修复只改动 4 个文件（2 个快照实现 + 3 个测试文件中的 2 个），未触碰其他行为。

### 2.3 冲突解决与取舍

| 主题 | 处理 | 结论 |
| --- | --- | --- |
| `session.py` 单页有界 drain / 120s readiness | 保留本地实现（`session.py:206`、`:56`、`:337-353`），拒绝上游“无界 `wait_ready` / 600s drain”版本 | 符合架构红线 |
| 每请求 `SessionContext` pin（`CapturedRequest.ctx`、`capture.py`、`replay.py`、`streaming.py`、`send_* ctx=`） | 全部拒绝；`CapturedRequest` 保持原字段，`capture.py` 无 `ctx`，调用点无 `ctx=` | 符合架构红线 |
| `timeout_ms` / 请求 deadline 签名 | 吸收上游；从 `session_protocol.py`、`pooled_session.py`、`session.py`、`transport.py`、`streaming.py`、`replay.py` 全部移除 | 通过（用户已授权，Q-02） |
| gzip 自动压缩 / `Content-Encoding: gzip` | 吸收上游并删除 `scripts.py`、`hooked_request.js`、`streaming_init.js` 与对应测试 | 通过 |
| BotGuard Wasm 快照就绪轮询 | 吸收 `botguard_bootstrap.py:333-357` | 通过 |
| 启动账号同步 | 吸收 `app.py:124-130` | 通过（见 N-15） |
| 快照重试 5×250ms | **已吸收（B-01 修复复审确认）**：`session.py:415-425` 与 `pooled_session.py:166-178` 均为 `range(5)` + `wait_for_timeout(250)`；空快照同样等待并重试 | 通过 |
| 99600c7 的 `set_ready_context` auth 同步、新页 cookie 回写、`_cached_cookies=None` | 未吸收（本地设计不同/仅测试调用） | 非阻塞，见 N-15/N-16 |
| `static/index.html` 空白回退 | 本合并未产生 `index.html` diff，无需处理 | 可接受 |

## 3. 门禁（独立实跑）

环境：`$env:PYTHONUTF8='1'; $env:PYTHONIOENCODING='utf-8'`，目标工作树根目录；B-01 修复后的复审实跑。

| 命令 | 结果 |
| --- | --- |
| `uv run pytest -q` | **2 failed, 535 passed, 1 skipped in 8.49s** |
| `uv run ruff check src tests` | **All checks passed!**（退出码 0） |
| `uv run pyright src tests` | **0 errors, 0 warnings, 0 informations**（退出码 0） |
| `git diff --check` | 退出码 0，无输出 |
| `git diff --cached --check` | 退出码 0，无输出 |
| `uv run ruff check .`（附加，全仓） | 16 errors，全部位于既有 `docs/dev/evidence/*/harness/*.py`（W292×14、PIE810×1、RUF005×1），不在 `src`/`tests`，亦非本合并引入（N-25） |

2 个失败均为既有 Windows 平台差异，非本合并回归：

- `tests/unit/test_browser_engine.py::test_find_chromium_executable_strictly_requires_cloakbrowser`（CloakBrowser 路径）
- `tests/unit/test_browser_scripts_syntax.py::test_evaluate_wrapper_expressions_parse_cleanly`（`bun.CMD -e` 报 `\n was unexpected at this time`）

1 skipped 为既有 POSIX-only watchdog 用例。535 passed 相对初检的 532 净增 3，均为 B-01 定向测试（BrowserSession 异常路径 1、BrowserSession 空快照路径 1、PooledSession 异常路径 1）；相对 B4 的 531 净增 4。初检的净增 1（N-14 定向测试 +1、`test_stream_transport.py` 超时用例拆分净 +1、删除 gzip 用例 −1）不变。

**前端门禁限制**：`web/node_modules` 不存在（`Test-Path` = False）；按指令未安装依赖、未联网、未建 junction，因此**未重跑** `vue-tsc`/lint/`bun run build`，仅检查 `web/src` 源码与 `src/aistudio_api/static` 产物，并依赖 Worker 报告的前端门禁。本轮 B-01 修复不涉及前端文件，前端结论与初检一致。

## 4. D-02 与架构红线

### 4.1 D-02 进程级看门狗完整保留

`browser_engine.py` **不在 staged diff 中**（`git diff --cached --name-only -- browser_engine.py` 为空）。上游 `16450cc` 对该文件仅删除一个空行（`_assign_process_to_job` 之后），`git show 16450cc -- browser_engine.py` 已核实无看门狗删除。当前实现：

- `_spawn_process_watchdog`：`browser_engine.py:768`
- pipe EOF 回收 Chromium 进程组：`:784-825`（`os.read(r, 1)` 等待 EOF，`killpg` SIGTERM→SIGKILL，再 `kill` 目标 PID）
- P-01 禁止给 watchdog 设 `PR_SET_PDEATHSIG` 的显式保护与注释：`:817-819`
- Windows Job Object：`:683-747`（`JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`）、分配到 Job：`:752-765`
- 启动路径：`:1058`（创建 Job）、`:1067`（spawn watchdog）

结论：D-02 未被削弱或移除；本次合并与 B-01 修复均未触碰该文件。

### 4.2 架构红线

- `session_state.py` 全文 42 行，仅 `SessionContext` 与单页 drain 原语（`:15-42`）；无 `SessionReadinessGate` 实现。
- 全仓 `src`/`tests` 检索无 `SessionReadinessGate` 的新增实现（仅 `tests/unit/test_browser_session.py:365`、`tests/unit/test_session_blue_green_readiness.py:38` 的“不存在”负向断言）；无 `_retiring_contexts`、blue-green 双页、lease/reservation/pinned/defer/自动 rebind 的新增实现。
- `session.py` 保持单页有界 drain：readiness 默认 120s（`:206`），`DRAIN_TIMEOUT_S = 12.0`（`:56`），drain 使用 `deadline` 与 `min(remaining, 2.0)` 轮询（`:337-353`），超时强制关闭旧页。
- 无请求级 timeout：`session.py`/`pooled_session.py` 的 `send_hooked_request`、`send_streaming_request` 均无 `timeout_ms`（全仓 `src`/`tests` 检索 0 命中）。
- 无 `ctx=` 传参：`CapturedRequest`（`capture.py:18-23`）无 `ctx` 字段；`capture.py`/`replay.py`/`streaming.py`/`session.py`/`pooled_session.py` 调用点均不传 `ctx=`（`src` 检索 0 命中）。

## 5. 签名对齐、超时与无限流式

### 5.1 `timeout_ms` 删除与 TypeError 风险

staged diff 已从以下签名与调用链删除 `timeout_ms`：

- `session_protocol.py:24-38`（`RequestSession` 协议）
- `pooled_session.py:183-235`（`PooledSession` 两个发送方法）
- `session.py:431-474`（`BrowserSession` 两个发送方法）
- `transport.py:270-311`（`XHRStreamTransport` 两个方法）
- `streaming.py:100`（`StreamingGateway` 调用）
- `replay.py:16-31`（`RequestReplayService.replay`，同时删除 `settings.timeout_replay`）

核查：

- 全仓 `src`/`tests` 已无 `timeout_ms`（`grep` 0 命中）。
- replay 调用点 `client.py:283`、`client.py:363`、`instance_client.py:156` 均只传 `captured`/`body`，无位置参数超时。
- 所有 `send_hooked_request`/`send_streaming_request` 调用点（`pooled_session.py:198,224`、`session.py:446,472`、`replay.py:28`、`streaming.py:100`、`transport.py`）与协议签名一致，无 TypeError 路径。
- `XHRStreamTransport.send_hooked_request` 使用 `page.evaluate(HOOKED_REQUEST_JS, args, timeout_s=None)`（`transport.py:291`）；`CDPConnection.send` 新增 `timeout_s: float | None`，`None`/`<=0` 直接 `await fut`（`cdp_client.py:216,242-244`），`CDPPage.evaluate` 同步支持 `None`（`:407`）。

### 5.2 无限流式

- `transport.py:350` 仅以 `asyncio.wait_for(queue.get(), timeout=1.0)` 作为“页关闭检测切片”；`TimeoutError` 分支只检查 `page.is_closed()` 后继续（`:351-357`）。
- 已删除整体 `deadline`、每状态/每 chunk 的 deadline 重置、以及 `if not is_terminal: raise TimeoutError("streaming response timed out before completion")`。
- `hooked_request.js` 删除 AbortController/`timeoutMs`；`streaming_init.js` 删除 idle timer；`scripts.py` 不再注入 `timeout` 参数。
- 新测试 `test_transport_stream_tolerates_idle_gap_without_deadline` 构造 1.2s 空闲后仍收到 `chunk`；`test_transport_streaming_page_closed_raises` 断言页关闭立即抛 `BrowserTransportError`。

### 5.3 快照重试（B-01，已解决）

**实现证据（实际代码，非声明）：**

- `src/aistudio_api/infrastructure/gateway/session.py:408` `BrowserSession.generate_snapshot`；`:415` `for attempt in range(5)`；`:416-421` 在 `try` 内 `evaluate`，仅当 `snapshot` 非空字符串才 `return`；`:423-424` 捕获异常后记日志；`:424-425` `if attempt < 4: await ctx.page.wait_for_timeout(250)`。
- `src/aistudio_api/infrastructure/gateway/pooled_session.py:161` `PooledSession.generate_snapshot`；`:166` `for attempt in range(5)`；`:167-172` 在 `try` 内 `evaluate`，仅当 `isinstance(snapshot, str) and snapshot` 才 `return`；`:174-175` 捕获异常后记日志；`:177-178` `if attempt < 4: await self._context.page.wait_for_timeout(250)`。
- 两处等待均位于 `try/except` **之外**的循环体层级，因此“异常失败”与“返回空快照”共用同一等待/重试路径；最后一次（第 5 次）失败后不再等待，总等待 4×250ms = 1000ms。
- 与上游 `99600c7` 的 `session.py` 目标实现逐行对齐：`git show 99600c7:.../session.py` 为 `:385 for attempt in range(5)`、`:395 wait_for_timeout(250)`，且 `if snapshot ... return` 同样位于 `try` 内；本地仅将 `active_ctx` 命名为 `ctx`，语义一致。
- 全仓 `timeout_ms`/`gzip` 检索仍为 0；B-01 修复未改动其他行为。

**测试证据（真实断言，非空测试）：**

- `tests/unit/test_browser_session.py:83` `test_browser_session_generate_snapshot_retries_five_times_with_250ms`：`evaluate` 恒抛错；断言 `evaluate.await_count == 5`（`:104`）、`wait_for_timeout.await_count == 4`（`:105`）、调用参数列表精确等于 `[(250,)] * 4`（`:106-108`），并断言最终抛 `RuntimeError("Snapshot generation failed")`。
- `tests/unit/test_browser_session.py:112` `test_browser_session_generate_snapshot_empty_result_also_retries`：`evaluate` 恒返回 `""`；断言 `evaluate.await_count == 5`（`:133`）且等待参数为 `[(250,)] * 4`（`:134-136`），证明空快照也重试。
- `tests/unit/test_cdp_browser_context.py:207` `test_pooled_session_generate_snapshot_retries_five_times_with_250ms`：`PooledSession` 的 `page.evaluate` 恒抛错；断言 `evaluate.await_count == 5`（`:229`）、`wait_for_timeout.await_count == 4`（`:230`）、参数 `[(250,)] * 4`（`:231-233`），并断言抛 `RuntimeError("PooledSession snapshot generation failed")`。
- 上述 3 例在本轮 `uv run pytest -q` 中通过（含在 535 passed 内），排除“仅改代码未验证”的可能。

结论：B-01 已按要求修复，控制面与请求面一致，异常与空快照两条路径均覆盖。

## 6. 上游吸收

| 项 | 证据 | 结论 |
| --- | --- | --- |
| BotGuard Wasm 快照就绪轮询 | `botguard_bootstrap.py:333-357`：最多 20 次、每次 `wait_for_timeout(400)`；优先读 `window.__AISTUDIO__.snapshot`，否则调用 `default_MakerSuite[a.snapKey]` 生成并写回；`snap_ready is not False` 才 break | 吸收 |
| 快照重试 5×250ms | `session.py:415-425`、`pooled_session.py:166-178`：`range(5)` + `attempt < 4` 时 `wait_for_timeout(250)`；空快照同样重试 | 吸收（B-01 已关闭） |
| `dom_gc_cleanup.js` 不再删除 `ms-chat-turn` | 删除“保留最后一个 turn、删除其余”的整段逻辑；仍清理 options/loading/backdrop/canvas/video/audio/blob | 吸收 |
| App 启动账号同步 | `app.py:124-130`：非 `session_expired` 且 `auth_cooldown > 0` 清零；读取 `get_active_auth_path()` 并写入 `client.session._auth_file` | 吸收（N-15） |
| gzip 自动压缩/`Content-Encoding: gzip` | `scripts.py` 删除 `base64`/`gzip`/`_GZIP_MIN_BYTES`/64KB+ 压缩分支；`hooked_request.js`/`streaming_init.js` 直接发送原始 body；`test_build_request_args_gzip_above_threshold` 删除；全仓 `src` 无 gzip 命中 | 吸收，无回退 |
| B3 保活 / B4 日志 / `/logs` SPA | `app.py:181-188` 保活构造与 `start()`；`routes_keepalive`/`routes_logs` 路由注册 `:226-232`；`/logs` SPA 白名单与 `is_logs_spa` `:456-464` | 保留 |
| 上游每请求 `SessionContext` pin | 拒绝（架构红线）；`capture.py` 未改，`replay.py`/`streaming.py` 无 `ctx=` | 按红线拒绝 |
| 上游 `set_ready_context` auth 同步 / 新页 cookie 回写 | 未吸收；`set_ready_context` 仅测试调用，`transition_to_account` 自行设置 `_auth_file`（`session.py:255`）并在关闭旧页前回写 cookie（`:355-357`） | 非阻塞（N-15/N-16） |

## 7. UI 与配置

| 项 | 证据 | 结论 |
| --- | --- | --- |
| `web/src/views/SettingsView.vue` 不再展示 `timeout_stream` | `:67` “推理流式传输”、`:70` “无时长限制”、`:73` “支持超长思考与百万 Token 输出”；无 `timeout_stream` | 通过（Q-01 用户已批准） |
| `web/src/types/system.ts` | 删除 `timeout_stream: number` | 通过 |
| `routes_system.py` | 删除响应字段 `"timeout_stream": settings.timeout_stream` | 通过 |
| `config.py` | 删除 `timeout_replay`/`timeout_stream` 设置项 | 通过 |
| `.env.example` | 删除 `AISTUDIO_TIMEOUT_REPLAY`/`AISTUDIO_TIMEOUT_STREAM` | 通过 |
| tracked `defaults/config.yaml` | 无任何 `timeout` 键 | 通过 |
| 根 `config.yaml` | `git ls-files --error-unmatch` 报 `did not match`；`git status --ignored` 显示 `!! config.yaml`；`.gitignore:15` 为 `/config.yaml` | 未跟踪（Q-04 已确认） |
| 静态 `SettingsView.js` 重建 | 71,214 bytes（HEAD 64,795 字符 / 本次 64,751 字符）；UTF-8 读取包含“无时长限制”“推理流式传输”“百万 Token”；不含 `timeout_stream`/“流式响应超时” | 已重建 |
| 生成产物是否手工合并 | 单行 bundle 自 offset 1846 起即出现压缩器标识符轮换（`y,m,f` 分配顺序变化），整行内容随之变化，符合真实重建特征；非仅替换 UI 文案的定点手改 | 无手工合并迹象 |
| 前端门禁复跑 | `web/node_modules` 不存在；未安装/未联网/未建 junction | **未复跑**（限制，见 N-21） |

## 8. N-14：`AIStudioClient._dump_raw_exchange` 安全落盘

证据（`src/aistudio_api/infrastructure/gateway/client.py`）：

- `_sanitize_model_component`：`:54-65`，白名单 `A-Za-z0-9._-`，消除连续 `..`、去首尾 `.`，空值回退字面量 `model`。
- `_dump_raw_exchange`：`:121-159`；文件名 `aistudio_{kind}_{safe_model}_{timestamp}.json`（`:142`），仅写安全字段：
  - `safe_prompt = sanitize_log_message(capture_prompt)`（`:146`）
  - `safe_request_body = safe_json_payload(modified_body)`（`:147`）
  - `safe_response = sanitize_log_message(raw_response)`（`:148`）
- 不再写原始 `capture_prompt`/`modified_body`/`raw_response`，无 `headers`/`body`/凭据字段；异常转为 `logger.debug`，不会因落盘失败中断请求（`:155-156`）。
- `kind` 仍直接进入文件名，但当前仅由内部固定常量 `generate_content`/`generate_image` 传入（`:287-288`、`:367-368`），属既有残余（N-17）。
- 与 `InstanceRequestClient._dump_raw_exchange`（`instance_client.py:80-114`）对比：同用 `safe_*` 字段、同用 `_sanitize_model_component`、同用 `resolve_data_dir()` 解析相对路径；差异仅为控制面 payload 无 `instance`/`surface` 元数据（控制面本就无实例身份），**不构成泄漏**，记为 N-17 元数据差异。

定向测试：

- `tests/unit/test_logging.py:580` `test_ai_studio_client_raw_dump_only_writes_safe_fields`。
- 断言内容：落盘目录即 `tmp_path/raw`；文件名无 `/`、`\`、`..`；`set(data)` 精确等于 `{kind, model, safe_prompt, safe_request_body, safe_response}`；逐项断言 `capture_prompt`/`modified_body`/`raw_response`/`headers`/`body` 不存在；断言 `super-secret-token`、`AIzaSyRawBodyLeak1234567890`、`raw-cookie-value` 均不出现在文件文本中；`safe_request_body["text"] == "hello"` 证明非敏感字段保留、凭据按 key 脱敏。
- 该测试真实覆盖 key 集合、泄漏与路径穿越，不是空断言；本机实跑通过（与 `test_instance_client_raw_dump_*` 同组 3 passed）。

## 9. 合并卫生

- 无 `UU`/`AA`/`DD` 等未解决路径；`git diff --name-only --diff-filter=U` 为空（0 项）。
- `MERGE_HEAD` 存在且等于 `99600c7`；HEAD 仍为 `424f896`；无合并提交、无 B5a 提交。
- `git status --porcelain` 共 29 行：27 个 staged 文件（`session.py`、`pooled_session.py`、`test_browser_session.py` 为 staged+unstaged）、1 个仅 unstaged（`test_cdp_browser_context.py`）、1 个 untracked（本报告）；无其他意外 untracked。
- B-01 修复的 unstaged diff 仅含 4 个文件、+95/−6：`session.py` 6 行、`pooled_session.py` 6 行、`test_browser_session.py` +57、`test_cdp_browser_context.py` +32；无越界文件。
- 根 `config.yaml` 未跟踪且被 `.gitignore` 忽略；`defaults/config.yaml` 为唯一 tracked 模板。
- 未发现 B5a/toolcall/OpenAI seam 等越界改动；`static/index.html` 无 diff（空白回退可接受）。
- `git diff --check` 与 `git diff --cached --check` 均为退出码 0。

## 10. 问题清单

### 10.1 阻塞项

**无。** B-01 已于 2026-10-30 修复复审关闭（证据见 §5.3、§11）。

#### B-01（已解决）快照重试未吸收 5×250ms

- **原位置**：`src/aistudio_api/infrastructure/gateway/session.py:415-425`；`src/aistudio_api/infrastructure/gateway/pooled_session.py:166-178`
- **原证据**：初检时两处均为 `for attempt in range(3)`，失败后 `wait_for_timeout(100)`；上游 `99600c7` 为 `range(5)` + `wait_for_timeout(250)`。
- **修复后状态**：两处均已改为 `range(5)`，`attempt < 4` 时等待 250ms；空快照同样重试；新增 3 个定向测试断言 5 次 `evaluate` 与 4 次 250ms；`uv run pytest -q` 通过（535 passed）。
- **结论**：关闭。

### 10.2 非阻塞项

#### N-15（非阻塞，新）启动同步直接写 `client.session._auth_file`

- **位置**：`src/aistudio_api/api/app.py:129-130`
- **说明**：`client.session._auth_file = str(active_auth_path)` 直接改私有属性；上游 `99600c7` 选择在 `set_ready_context` 内同步 `_auth_file`，本次未吸收，而本地 `set_ready_context`（`session.py:144-149`）不同步该字段。当前 `set_ready_context` 仅测试使用，生产路径由 `transition_to_account` 设置 `_auth_file`（`:255`），暂无实际错配；但后续若有生产代码调用 `set_ready_context`，会出现 `_auth_file` 与实际上下文不一致。
- **建议**：改为公开 setter 或在 `set_ready_context` 同步 `_auth_file`。

#### N-16（非阻塞，新）上游新页 cookie 回写/`_cached_cookies` 清理未吸收

- **位置**：上游 `99600c7` 的 `session.py` 在创建新页后执行 `new_page._cached_cookies = None` 与 `save_browser_cookies(new_page, auth_file)`；本地保留的是关闭旧页前回写旧页 cookie（`session.py:355-357`）。
- **说明**：本地实现已覆盖“旧页最后状态不丢失”，但新页 cookie 归一化/持久化时机与上游不同；非阻塞行为差异，未见泄漏或崩溃路径。

#### N-17（非阻塞，新）raw dump 仍保存完整 prompt/body/response（仅凭据脱敏）

- **位置**：`client.py:146-148`；`instance_client.py:106-108`
- **说明**：`safe_prompt`/`safe_request_body`/`safe_response` 只按凭据 key/正则脱敏，仍保留用户 prompt、完整请求体（含可能的 base64 图片）与响应文本；`kind` 未纳入文件名清洗（当前仅内部常量）；`_sanitize_model_component` 在 `client.py` 与 `instance_client.py` 重复实现。控制面 payload 缺少 `instance`/`surface` 元数据，与请求面不同但不泄漏凭据；`dump_raw_response` 默认关闭。

#### N-18（非阻塞，新）BotGuard 就绪轮询为 best-effort

- **位置**：`botguard_bootstrap.py:333-357`
- **说明**：整段包在 `with suppress(Exception)` 中；20×400ms 后若仍未就绪则静默继续后续 `STOP_GENERATION`/`fill` 流程，不显式报错，也无定向测试。属上游原样行为，非本合并引入。

#### N-19（非阻塞，新）启动账号同步/冷却清零无定向测试

- **位置**：`app.py:124-130`
- **说明**：非过期账号 `auth_cooldown` 清零与 active auth 路径同步未见对应单测；535 passed 中无该行为断言。

#### N-20（非阻塞，新）历史文档仍含旧超时键

- **位置**：`docs/dev/rebuild/08-08-upstream-99c38ba-full-diff-audit.md:135`、`docs/dev/rebuild/domains/P5-generation/09-pg-toolcall-analysis.md:66,139,146,320`、`docs/dev/rebuild/archive/08-07-upstream-99c38ba-reaudit.md:86`
- **说明**：`timeout_stream`/`timeout_replay` 仅存在于历史审计/归档文档的叙述中，运行时源码/测试/前端/默认配置均已清除；不影响运行，但后续检索易误判。

#### N-21（非阻塞，新）前端门禁未独立复跑

- **说明**：`web/node_modules` 不存在；按指令未安装依赖、未联网、未建 junction，故未重跑 `type-check`/lint/`bun run build`。仅检查 `web/src` 与静态产物；静态 bundle 含新文案且无 `timeout_stream`，与 Worker 报告一致。B-01 修复不涉及前端。

#### N-22（非阻塞，新）非流式 hooked replay 也无请求 deadline

- **位置**：`transport.py:291`（`timeout_s=None`）、`cdp_client.py:242-244`
- **说明**：为满足无限流式，`HOOKED_REQUEST_JS` 的 `evaluate` 统一不再设超时；非流式重放若页面/网络永久挂起，将由上层路由/HTTP 生命周期兜底而非本层超时。属设计取舍，用户已授权（Q-02）。

#### N-23（非阻塞，新）`pooled_session.ensure_ready` 忽略 `timeout_s`

- **位置**：`pooled_session.py:136-140`
- **说明**：保留协议签名 `ensure_ready(timeout_s=120.0)` 但 `del timeout_s`，不做等待；单页架构下仅检查页未关闭。与协议一致，不构成 TypeError/行为回归。

#### N-24（非阻塞，新）`PooledSession` 空快照重试路径缺少直接测试

- **位置**：`tests/unit/test_cdp_browser_context.py:207-233`
- **说明**：`PooledSession` 的定向测试只覆盖 `evaluate` 抛异常路径；代码中“返回空字符串也重试”由同一循环层级保证（`pooled_session.py:167-178`），但无对应断言。控制面 `BrowserSession` 已有空快照测试（`test_browser_session.py:112`）。非阻塞测试缺口。

#### N-25（非阻塞，新）全仓 `ruff check .` 仍命中既有 docs 证据脚本告警

- **位置**：`docs/dev/evidence/wp5-*/harness/*.py` 共 16 处：W292×14（文件末尾无换行）、PIE810×1、RUF005×1。
- **说明**：`uv run ruff check src tests` 全绿；上述告警位于历史证据脚本，未在本次合并或 B-01 修复中改动，非回归。若 CI 使用 `ruff check .` 会失败，建议将 `docs/dev/evidence` 排除或单独清理。

#### N-26（非阻塞，新）B-01 修复尚未 staged，合并提交前必须 `git add`

- **位置**：`session.py`、`pooled_session.py`、`test_browser_session.py`、`test_cdp_browser_context.py`
- **说明**：当前 B-01 修复位于 unstaged 工作区；直接执行 `git commit`（不加 `-a`）只会提交 staged 的 3×100ms 版本，修复会丢失。按只读要求本轮未 stage；建议合入前先确认这 4 个文件进入 index，并重新跑一次门禁。

### 10.3 既有 N 项状态（承接 B4 复核）

| 编号 | 状态 | 说明 |
| --- | --- | --- |
| N-01 | 已解决 | 控制台 dump key-based 凭据脱敏（B4 已关闭） |
| N-02 | 已解决 | aware/naive ISO 范围 400（B4 已关闭） |
| N-03 | 非阻塞 | 多日导出静默跳过缺失归档（P7，未受本合并影响） |
| N-04 | 非阻塞 | uvicorn 日志只进 stderr（P7，未受本合并影响） |
| N-05 | 已解决 | 噪音测试断言真实命名空间（B4 已关闭） |
| N-06 | 非阻塞 | 启动/跨天清理“调用”未测试（P7，未受本合并影响） |
| N-07 | 非阻塞 | SSE 默认 15s/5000 与非法 Last-Event-ID 未断言（P7，未受本合并影响） |
| N-08 | 非阻塞 | `test_stream_route_headers_and_spa_bypass` 名不副实（P7，未受本合并影响） |
| N-09 | 已解决 | SSE 重连清除陈旧 droppedNotice（B4 已关闭） |
| N-10 | 已解决 | tracked 静态前端已重建（B4 已关闭；本合并仅 `SettingsView.js` 再生成） |
| N-11 | 已解决 | instance raw dump 模型分量白名单清洗（B4 已关闭） |
| N-12 | 非阻塞 | aware 输入按当前本地固定偏移归一化（P7，未受本合并影响） |
| N-13 | 非阻塞 | 非 JSON 表单/纯文本 body 仅正则脱敏（P7，未受本合并影响） |
| N-14 | **已解决** | 本合并将 `AIStudioClient._dump_raw_exchange` 改为 safe 字段 + 文件名净化，并新增真实断言测试（见 §8） |

### 10.4 决策 / 条件项

| 编号 | 事项 | 复核结论 |
| --- | --- | --- |
| Q-01 | 上游 UI 文案变更（“推理流式传输/无时长限制”）是否需按 AGENT.md §10.2 先提交《UI 页面与交互设计清单》并获用户确认？ | **USER-APPROVED**：用户已明确批准移除 `timeout_stream` UI（含对应上游文案变更），本合并按上游一致性直接吸收；该流程例外已获授权，非阻塞。 |
| Q-02 | 为无限流式移除所有请求 deadline 后，非流式重放同样无本层超时（N-22），是否接受由上层路由/HTTP 生命周期兜底？ | **USER-AUTHORIZED**：用户已授权移除请求超时。N-22 为该授权的已知取舍，由上层路由/HTTP 生命周期兜底。 |
| Q-03 | 快照重试标准是否确认为 5×250ms（B-01），并同步到 `PooledSession` 与控制面？ | **已解决**：B-01 修复复审确认两处均实现 5×250ms（含空快照重试），并有 3 个定向测试断言；见 §5.3/§11。 |
| Q-04 | 根 `config.yaml` 保持 untracked、`defaults/config.yaml` 为唯一 tracked 模板 | **已确认**：`git ls-files --error-unmatch config.yaml` 失败，`.gitignore:15` 命中，`git status --ignored` 显示 `!! config.yaml`，无个人配置入库。 |

## 11. B-01 复审（2026-10-30）

### 11.1 复审结论

**PASS**。B-01 阻塞项已关闭；无新增阻塞缺陷。复审全程只读代码/测试/配置/仓库，仅更新本报告两份副本。

### 11.2 代码证据

| 项 | 实测 |
| --- | --- |
| `BrowserSession.generate_snapshot` | `session.py:408` 起始；`:415` `for attempt in range(5)`；`:424-425` `if attempt < 4: await ctx.page.wait_for_timeout(250)` |
| `PooledSession.generate_snapshot` | `pooled_session.py:161` 起始；`:166` `for attempt in range(5)`；`:177-178` `if attempt < 4: await self._context.page.wait_for_timeout(250)` |
| 空快照 | 两处均仅在 `snapshot` 为非空字符串时 `return`；等待语句位于 `try/except` 外的循环体，空值与异常同样进入下一次重试 |
| 等待次数 | 5 次尝试之间等待 4 次，每次 250ms，总计 1000ms；第 5 次失败后不再等待 |
| 与上游对齐 | `git show 99600c7:.../session.py`：`:385 range(5)`、`:395 wait_for_timeout(250)`；本地语义一致（仅 `active_ctx`→`ctx`） |

### 11.3 测试证据

| 测试 | 断言 |
| --- | --- |
| `test_browser_session.py:83` 异常路径 | `evaluate.await_count == 5`、`wait_for_timeout.await_count == 4`、参数 `[(250,)]*4`、抛 `RuntimeError` |
| `test_browser_session.py:112` 空快照路径 | `evaluate.await_count == 5`、参数 `[(250,)]*4`、抛 `RuntimeError` |
| `test_cdp_browser_context.py:207` Pooled 异常路径 | `evaluate.await_count == 5`、`wait_for_timeout.await_count == 4`、参数 `[(250,)]*4`、抛 `RuntimeError` |

3 例均在本轮 `uv run pytest -q` 中通过，属 535 passed 的一部分。

### 11.4 门禁实跑（精确计数）

| 命令 | 结果 |
| --- | --- |
| `$env:PYTHONUTF8='1'; $env:PYTHONIOENCODING='utf-8'; uv run pytest -q` | **2 failed, 535 passed, 1 skipped in 8.49s** |
| `uv run ruff check src tests` | **All checks passed!**（exit 0） |
| `uv run pyright src tests` | **0 errors, 0 warnings, 0 informations**（exit 0） |
| `git diff --check` | exit 0，无输出 |
| `git diff --cached --check` | exit 0，无输出 |
| `git rev-parse HEAD` | `424f896cd8c55eb41c952d4d962f3e2f6ae8b2b1` |
| `git rev-parse MERGE_HEAD` | `99600c795e9e0563f9ad4caae5cc58c1e72e7bbd` |
| 未解决冲突 | 0（`--diff-filter=U` 为空） |
| 根 `config.yaml` | 未跟踪且 ignored |

2 个失败与 1 个 skip 均为初检已记录的既有平台用例（Windows CloakBrowser 路径、`bun.CMD -e` 换行、POSIX-only watchdog），与 B-01 无关。

### 11.5 剩余问题与前端限制

- 剩余 Q：无阻塞。Q-01 USER-APPROVED、Q-02 USER-AUTHORIZED、Q-03 已解决、Q-04 已确认。
- 剩余 N：N-03/04/06/07/08/12/13（P7 既有）、N-15/N-16/N-17/N-18/N-19/N-20/N-21/N-22/N-23（初检新增）、N-24/N-25/N-26（本轮新增）。均非阻塞。
- **关键操作提醒（N-26）**：B-01 修复仍在 unstaged；提交合并前须 `git add` 这 4 个文件，否则 `git commit` 会只提交旧的 3×100ms 版本。
- **前端限制**：`web/node_modules` 不存在，未安装依赖/未联网，`vue-tsc`/lint/`bun run build` 未能在本轮独立复跑；仅静态检查 `web/src` 与 `src/aistudio_api/static`。B-01 不涉及前端，前端结论沿用初检（静态产物已含新文案、无 `timeout_stream`）。

### 11.6 本报告 SHA256

- `SHA256 = e973d1608e67580f9883ce9a73920ec4fe57a5f295b8d5b7957972ae5c8e5d8f`
- 校验口径：将本行 64 位十六进制替换为 64 个 `0` 后，对全文（UTF-8，含换行）计算 SHA256，结果应等于替换前的值。
- 两份副本（工作树/主仓）逐字节一致，哈希相同。

## 12. 只读声明

- 未修改任何源码、测试、配置、前端或生成产物。
- 未执行 `git add`/`git commit`/`git merge --abort`/`git reset`/`git stage`。
- 未开始 B5a，未触碰 P5 域。
- 未访问真实 Google 账号、真实 IP 或外网；前端依赖未安装。
- 仅更新本报告两份完全一致副本（B-01 修复复审）。

## 13. 报告双写与 SHA256

- 目标树：`D:\LLM\self_programming\aistudio-api\.kilo\worktrees\rebuild-upstream\docs\dev\rebuild\domains\P3-browser\reviews\REV-merge-99600c7.md`
- 主仓：`D:\LLM\self_programming\aistudio-api\docs\dev\rebuild\domains\P3-browser\reviews\REV-merge-99600c7.md`
- 两份内容逐字节一致；SHA256 与校验口径见 §11.6。
