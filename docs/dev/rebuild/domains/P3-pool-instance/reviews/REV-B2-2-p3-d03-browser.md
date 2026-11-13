# REV-B2-2 — P3-D03 浏览器 / CDP / BrowserContext / PooledSession / 单页生命周期 只读审查报告

- 步骤：B2-2（P3 / D03：Browser / CDP / BrowserContext / PooledSession / 单页生命周期）
- 工作树：`.kilo/worktrees/rebuild-upstream`，HEAD `8b80971`（B2-1 提交之后），B2-2 改动**尚未提交**
- 权威依据：`AGENT.md`；`06-decision-log.md` P3 章节（177-204）与 99c38ba 裁决（420-433）、08-09 闭合（438/463/475-480）；`08-09-decision-record.md` D-02 / D-03 / D-09 / D-10 / D-11；`06-feature-index.csv` D03-001..036；`06-test-disposition.csv` 六个相关测试行
- 审查方式：只读；对照实际未提交 diff、生产代码与测试断言，实跑 pytest / ruff / git 检查
- 审查日期：2026-10-29（局部返工后复审）

## 1. 结论

**PASS**

- P-01 / P-02 / P-03 三个阻塞项已按上轮意见修复，逐条复核成立；P-04 的 watchdog 测试已补强（POSIX 存活进程真实回收，Windows 显式 skip），不再零断言。
- 实跑：**351 passed / 2 failed / 1 skipped**（2 failed 仍为 `AGENT.md` §2.4 既有 Windows 平台差异；1 skipped 为 POSIX-only watchdog 用例）；`uv run ruff check src tests` 全绿；`git diff --check` 退出码 0。
- 未发现新阻塞。保留 P-02b / P-03b 两项非阻塞建议与 P-05..P-10 原有的非阻塞问题/裁决项（见第 4、5 节）。
- 仍无 B2-3 多实例池落地，无 `05/06/08-09/archive` 改动，无真实 Google/账号/IP 访问，未 commit。

### 1.1 局部返工复核记录（2026-10-29）

| 项 | 复核结果 | 证据 |
|---|---|---|
| P-01 watchdog PDEATHSIG | **已修复**：watchdog 的 `Popen` 不再传 `preexec_fn=_setup_child_pdeathsig`，注释明确说明原因；保留 `start_new_session=True` 与 pipe EOF 语义 | `browser_engine.py:814-825`（`:817-819` 注释）；`ChromiumProcess` 持有 `watcher`/`pipe_w` 并在 `terminate()` 关闭写端（`:913-965`）；`launch_chromium_process` 统一接线（`:1067`） |
| P-01/P-04 watchdog 测试 | **已补强**：POSIX 用例起一个真实存活的 `sleep(60)` 子进程，关闭 pipe 写端后断言其被回收；Windows 用例显式断言返回 `(None, None)`，POSIX 测试在 Windows 显式 `skip`，不再零断言通过 | `tests/unit/test_browser_engine.py:169-173`（Windows）、`:176-213`（POSIX live reap）；实跑 skipped 行即 `test_spawn_process_watchdog_reaps_live_target_on_pipe_eof` |
| P-02 进程死亡分支第二页 | **已修复**：进程死亡分支改为 `_ensure_browser_cdp(connect_page=False)`，只恢复进程/CDP，不提前接页；随后统一一次 `create_new_page` | `session.py:284-295`；`_ensure_browser_cdp(connect_page=...)` 早退与返回语义 `:575-598`；`create_new_page` `cdp_client.py:893-911` |
| P-02 新增用例 | **已补**：模拟死进程 rebind，断言 `ensure_calls == [False]`、`connect_page` 零调用、`create_new_page` 恰一次、旧页关闭 | `tests/unit/test_session_blue_green_readiness.py:169-225` |
| P-03 BotGuard 登录页恢复 | **已修复**：reload 成功后 `goto` AI Studio，最多 10×500ms（5s）有界等待回到非登录的 AI Studio 页，超时抛 `SessionExpiredError`；随后重装 Hook、重填 prompt、重新 Run，再 `continue` 外层有界轮询 | `botguard_bootstrap.py:253-292`；`AISTUDIO_HOME_URL` `:23` |
| P-03 新增用例 | **已补**：login → reload → 回 aistudio → 重触发 → 捕获成功全链路 | `tests/unit/test_botguard_bootstrap.py:75-92`（断言 reload 1 次、goto 目标、snap_key、捕获模板） |
| 返工范围 | 未夹带无关改动：本轮 mtime 仅 6 个文件变化（`browser_engine.py`、`botguard_bootstrap.py`、`session.py`、`test_browser_engine.py`、`test_session_blue_green_readiness.py`、新增 `test_botguard_bootstrap.py`）；`cdp_client.py`、`config.py`、`session_state.py`、`pooled_session.py`、`test_cdp_browser_context.py` 等仍为此前 B2-2 版本 | 文件 mtime 21:41-21:42（返工时刻）；其余 21:26-21:33 |

有界性核对：

- 外层 `for i in range(max_wait_seconds)`（`botguard_bootstrap.py:247`）本身有界；恢复分支内层 `for _ in range(10)`（`:265-278`）有界，失败走 `else` 抛错后退出；`continue` 只回到外层循环，不存在自循环或 `while True`。
- 进程死亡分支的 `connect_page=False` 已在三条调用语义上核对：进程已死 + 有旧 context → `create_new_page` 一次；进程已死 + 无旧 context → 走 `connect_page` 一次；进程存活 → 不新增页。

## 2. 实际执行命令与结果

| 命令 | 结果 |
|---|---|
| `$env:PYTHONUTF8='1'; $env:PYTHONIOENCODING='utf-8'; uv run pytest -q` | **2 failed, 351 passed, 1 skipped in 4.96s**。失败仍为既有 Windows 差异：`test_find_chromium_executable_strictly_requires_cloakbrowser`、`test_evaluate_wrapper_expressions_parse_cleanly`；skip 为 `tests/unit/test_browser_engine.py:179`（`watchdog POSIX only`） |
| `uv run pytest -q tests/unit/test_browser_engine.py -rs` | `1 failed, 8 passed, 1 skipped`，skip 原因确认是 POSIX-only watchdog；失败为上述既有 CloakBrowser 查找用例 |
| `uv run pytest -q tests/unit/test_session_blue_green_readiness.py tests/unit/test_botguard_bootstrap.py tests/unit/test_cdp_client.py tests/unit/test_browser_session.py tests/unit/test_cdp_browser_context.py` | **41 passed in 0.58s**（覆盖 P-01/P-02/P-03/D-03/D-09/D-11/Context/PooledSession 相关断言） |
| `uv run ruff check src tests` | `All checks passed!`（0 error） |
| `git diff --check` | 退出码 0；仅 4 个文件的 LF→CRLF 提示 |
| `git status --short` | 14 个已跟踪文件修改 + 4 个未跟踪文件（`pooled_session.py`、`test_cdp_browser_context.py`、新增 `test_botguard_bootstrap.py`、本报告）；**未 commit** |
| `git diff --stat` | `14 files changed, 933 insertions(+), 316 deletions(-)`（未跟踪的 3 个源/测试文件另计） |
| `uv run ruff format --check src tests`（额外） | 8 个文件未格式化，均不在本步 `git status` 变更内，属既有基线问题，不计入 B2-2 |

> 说明：`uv run pyright src tests` 与前端命令不在本步审查指令内，未执行。

## 3. 逐项核对表

### 3.1 D-02 进程 watchdog（D03-004）

| 检查点 | 结论 | 证据 |
|---|---|---|
| `_spawn_process_watchdog` 存在且只在 `launch_chromium_process` 调用 | 通过 | `browser_engine.py:768-830`；调用点 `:1067` |
| watchdog 不参与账号 stale 复活 / 自动 rebind / 自动重启会话 | 通过 | `browser_engine.py:772-777` 明确边界；会话层无 `create_task`/`ensure_future`；无 watchdog→账号回调 |
| atexit / POSIX signal / Windows Job Object 保留 | 通过 | `_cleanup_all_chromium` + `install_process_cleanup_handlers`；`_create_windows_job_object`/`_assign_process_to_job` |
| 父进程死亡后 watchdog 能存活并回收进程组 | **通过（P-01 已修复）** | `browser_engine.py:814-825` 不再设置 PDEATHSIG；watcher 阻塞在 `os.read(r,1)`，EOF 后 `killpg(SIGTERM→SIGKILL)`；`:817-819` 注释说明原因 |
| `terminate()` 正常释放 pipe/watchdog | 通过 | `browser_engine.py:936-965`（先关 `pipe_w`，再终止 watcher，随后原有 process/job 清理） |
| watchdog 测试真实覆盖 pipe/reap | **通过（P-04 已补强）** | `tests/unit/test_browser_engine.py:176-213`：真实 `sleep(60)` 子进程 + 关闭写端 + `wait(5s)` 断言死亡；Windows `:169-173` 显式断言 `(None, None)`，POSIX 测试显式 skip |

### 3.2 D-03 CDP 传输（D03-005、D03-008、D03-009、D03-036）

| 检查点 | 结论 | 证据 |
|---|---|---|
| 只保留 WebSocket ping（5s/5s） | 通过 | `cdp_client.py:114-125`（`:120-122`） |
| 无 `set_liveness_checker` / 周期 `Runtime.evaluate` 探活 | 通过 | `cdp_client.py` 全文无该符号；`test_cdp_client.py:41-64`、`test_session_lifecycle.py:142-154` |
| `is_alive` 仅一次性探针，不是轮询 | 通过 | `cdp_client.py:311-323`；调用方仅 `session.py:232/245`（一次性）；该 API 无生产调用方（见 P-06） |
| `Target.targetCrashed` / `Inspector.detached` 立即失败 pending futures | 通过 | `cdp_client.py:160-182`、`:344-365` |
| 命令超时与断线兜底 | 通过 | `cdp_client.py:241-245`（`wait_for`）、`:198-210`（断线清 futures）、`:219-220`（关闭后拒发） |
| `$rpc/` `Network.loadingFailed` 证据链保留 | 通过 | `cdp_client.py:290-292,755-756`；`transport.py:37-107,299,383-398` 未改动；相关测试全绿 |

### 3.3 D03-010..015 BrowserContext / Page 原语

| 检查点 | 结论 | 证据 |
|---|---|---|
| D03-010 chat/build 资源拦截拆分 | 通过（声明层）；生产消费者待 P4 | `cdp_client.py:24-81`；`BLOCKED_URL_PATTERNS` 内容/顺序与 HEAD 一致；当前无生产调用方（见 P-08） |
| D03-011 `Target.createBrowserContext {disposeOnDetach:True}` + 进程内串行锁 | 通过 | `cdp_client.py:928-940`；`test_cdp_browser_context.py:54-69` |
| D03-012 `create_page_in_context` 轮询 `/json/list`，失败关 target | 通过 | `cdp_client.py:956-1012`；`test_cdp_browser_context.py:86-161` |
| D03-013 `dispose_browser_context` 忽略不存在/已释放 | 通过 | `cdp_client.py:943-954`；`test_cdp_browser_context.py:72-83` |
| D03-014 browser 级连接随 `CDPClient.close` 关闭 | 通过 | `cdp_client.py:1021-1028` |
| D03-015 `PooledSession` 在指定 context 内建页/注 Cookie/导航/BotGuard/快照/传输 | 通过 | `pooled_session.py:52-100,130-230`；`test_cdp_browser_context.py:164-201` |
| `PooledSession` 无 `set_liveness_checker` 调用 | 通过 | 全仓仅测试出现该符号；`pooled_session.py` 无调用 |
| 未实现 InstancePool / PoolManager（B2-3） | 通过 | 无 `instance_pool.py`；`pooled_session.py:1-6` 明确未来由 InstancePool 创建 |

### 3.4 D-10 / D-11 单页生命周期与 rebind

| 检查点 | 结论 | 证据 |
|---|---|---|
| `connect_page` prune 关闭所有非当前 page target | 通过 | `cdp_client.py:860-868`；`test_cdp_client.py:253-300` 断言 `["t2","t3"]` 均被关 |
| 无蓝绿双页 / `_retiring_contexts` / `SessionReadinessGate` | 通过 | `session.py:1-7,251-343`；`session_state.py:1-42`；`test_session_blue_green_readiness.py:36-44`、`test_browser_session.py:333-342` |
| 旧 Page/Context 一次有界 drain，超时强制关闭 | 通过 | `session.py:56,345-367`；`test_session_blue_green_readiness.py:117-166` |
| 先 drain/关旧页，再建新页；期间不建新页 | 通过 | `session.py:279-295`；`test_session_blue_green_readiness.py:48-113`（`order == ["close_old","create_new"]`） |
| 进程死亡分支不提前 `connect_page`、rebind 后恰好 1 页 | **通过（P-02 已修复）** | `session.py:284-295`；`_ensure_browser_cdp(connect_page=False)` `:575-598`；`test_session_blue_green_readiness.py:169-225` |
| 无后台常驻 drain task | 通过 | 三个会话模块无 `create_task`/`ensure_future`/`sleep`；测试 `:113` 断言无 `drain_*` task |
| 同一账号最多 1 active Page | 通过（rebind 路径） | 同上；`import_cookies` 死进程分支仍有同源残余，见 P-02b（非阻塞） |
| 失败显式返回、无自动 stale 复活 | 通过 | `session.py:337-343` 设 `_last_error`；`ensure_ready` 在重试前抛错（`:206-226`） |

### 3.5 D-09 snapshot hash

| 检查点 | 结论 | 证据 |
|---|---|---|
| `executable_code` / `code_execution_result` 纳入 hash | 通过 | `session.py:59-85`；`test_browser_session.py:270-283` |
| `hash_include_code=false` 回退上游行为 | 通过 | `session.py:68-69,79-82`；`test_browser_session.py:287-330` |
| 代码旁有“严重路由异常时优先怀疑/回退”注释 | 通过 | `session.py:62-67`；`defaults/config.yaml:17-21` |
| 默认 true，env > YAML > 默认 | 通过 | `config.py:250-274,280`；`defaults/config.yaml:20-21`；`test_features_config.py:58-67` |
| 不进普通用户 UI | 通过 | `web/` 无改动 |
| PooledSession 复用同一 hash 口径 | 通过 | `pooled_session.py:21,149-152` |

### 3.6 BotGuard / Cookie（D03-020..026）

| 检查点 | 结论 | 证据 |
|---|---|---|
| D03-021 捕获期跳登录页重注入后继续 | **通过（P-03 已修复）** | `botguard_bootstrap.py:253-292`：reload → goto AI Studio → 5s 有界等待 → 重装 Hook/重填/重 Run → `continue`；失败显式 `SessionExpiredError`；`test_botguard_bootstrap.py:75-92` |
| 恢复过程有界、不无限循环 | 通过 | 外层 `botguard_bootstrap.py:247` + 内层 `:265-278`（10×500ms，`else` 抛错）；有界性见 1.1 |
| D03-025 控制面 Cookie 导入使用临时页并关闭 | 通过 | `session.py:524-541` |
| D03-026 关闭前回写当前 Cookie | 通过 | `session.py:556-561`（`close()` 前）、`:359-361`（drain 关闭旧页前） |
| 无真实 Google/账号/IP | 通过 | 全量 pytest 4.96s；相关用例均 mock/本地 fake page |

### 3.7 范围与洁净度

| 检查点 | 结论 | 证据 |
|---|---|---|
| 无 B2-3 多实例池 / Manager | 通过 | 无 `instance_pool.py`；`PooledSession` 为单 context 原语 |
| 无 `05/06/08-09/archive` 改动 | 通过 | `git status --short` |
| 无真实凭据/密钥/个人数据 | 通过 | diff 仅既有 `$rpc/` 公共 API Key 模板与诊断开关 |
| 未 commit | 通过 | HEAD 仍为 `8b80971` |
| `docs/ARCHITECTURE.md` 与实现一致 | 基本一致 | `4.4`（单页 + 有界 drain + 先关后建）、`4.5`（hash 诊断优先级）与代码一致；worker 改架构文档的流程问题见 P-05 |

### 3.8 测试质量

| 检查点 | 结论 | 证据 |
|---|---|---|
| 无 readiness gate、无双页、一次有界 drain/超时强关、无后台 task | 通过 | `test_session_blue_green_readiness.py:36-44,48-113,117-166` |
| 进程死亡 rebind 恰好 1 页 | 通过 | `test_session_blue_green_readiness.py:169-225` |
| 无 liveness 轮询但保留 ping | 通过 | `test_cdp_client.py:41-64` |
| hash 代码 part 与 `hash_include_code` 开关 | 通过 | `test_browser_session.py:270-283,287-330` |
| D03-021 恢复链路 | 通过 | `test_botguard_bootstrap.py:75-92` |
| 是否为过测试删有效断言 | 未发现 | `test_cdp_client.py` prune 断言强化；`test_browser_session.py` 按 D-09 更新期望；`test_session_lifecycle.py` 无删除；watchdog 旧用例被替换为更强的两个用例 |

## 4. 问题清单

### P-01（已修复）watchdog 不再被 PDEATHSIG 杀死

- 位置：`src/aistudio_api/infrastructure/browser/browser_engine.py:814-825`。
- 复核：watchdog 的 `Popen` 已移除 `preexec_fn`；注释 `:817-819` 明确“绝不能给 watchdog 设置 PR_SET_PDEATHSIG”；保留 `start_new_session=True`、`pass_fds=[pipe_r]`，父进程死亡后 watcher 存活并读 EOF，再 `killpg`。
- 影响：D-02 的孤儿 Chromium 回收职责恢复成立。
- 是否阻塞：否（已修复）。

### P-02（已修复）进程死亡 rebind 不再产生第二页

- 位置：`src/aistudio_api/infrastructure/gateway/session.py:284-295`。
- 复核：进程死亡分支使用 `_ensure_browser_cdp(connect_page=False)`；`_ensure_browser_cdp` 在 `:596-598` 仅返回进程/CDP，不接页；随后仅一次 `create_new_page`。新用例断言 `ensure_calls == [False]`、`connect_page` 未调用、`create_new_page` 恰一次、旧页关闭。
- 影响：D-11“同一账号最多 1 active Page、不允许 Page 泄露”在 rebind 路径成立。
- 是否阻塞：否（已修复）。

### P-03（已修复）BotGuard 登录页重注入后可继续捕获

- 位置：`src/aistudio_api/infrastructure/gateway/botguard_bootstrap.py:253-292`。
- 复核：reload 成功后 `goto(AISTUDIO_HOME_URL)`，内层 10×500ms 等待回到非登录 AI Studio，超时抛 `SessionExpiredError`；随后 `install_page_hooks`、`wait_for_selector`、重填 prompt、`click_run_button`，再 `continue` 外层有界循环。新增用例覆盖 login→reload→回 aistudio→捕获成功。
- 影响：D03-021 的“重新注入后继续”成立，且不再把登录跳转伪装成 capture timeout。
- 是否阻塞：否（已修复）。

### P-04（已补强）watchdog 测试真实覆盖 pipe/reap

- 位置：`tests/unit/test_browser_engine.py:169-213`。
- 复核：POSIX 用例起真实 `sleep(60)` 子进程（独立 session），关闭 pipe 写端后 `wait(5s)` 断言死亡；Windows 用例显式断言 `(None, None)`；POSIX-only 用例在 Windows `pytest.skip`。
- 影响：D-02 的测试证据成立（本机 Windows 上该条为 skip，属平台差异；行为路径由 Linux/Termux CI 侧执行）。
- 是否阻塞：否（已补强）。

### P-02b（非阻塞，残余同源问题）`import_cookies` 仍使用默认 `connect_page=True`

- 位置：`src/aistudio_api/infrastructure/gateway/session.py:524-529`（`await self._ensure_browser_cdp()`）、`:575-598`。
- 证据：`import_cookies` 在进程已死时会先经 `_ensure_browser_cdp()` 建/接一页（A），随后再 `create_new_page(block_assets=False)` 建临时页（B）；B 在 `finally` 关闭，A 留存。若此时存在 active context，后续 rebind 走 `create_new_page` 而不再调用 `connect_page`，A 不会被 prune。
- 影响：D-11 的 1 页预算/无泄露在“进程死亡 + 账号 Cookie 导入”的罕见组合下仍不成立；属与 P-02 同源但不同调用点的残余，不是本轮返工新引入。
- 是否阻塞：**否**（建议一行改为 `_ensure_browser_cdp(connect_page=False)` 并补一条断言；见 U-07）。

### P-03b（非阻塞）登录页恢复的重复次数没有独立上限

- 位置：`src/aistudio_api/infrastructure/gateway/botguard_bootstrap.py:247,265-289`。
- 证据：外层最多 `max_wait_seconds`（默认 45）次，每次恢复最多 5s 等待 + 15s selector + 600ms；若每次都被重定向，总墙钟时间可达数分钟级，虽非无限循环但偏长。
- 影响：极端 Cookie 失效场景下会反复 reload/goto，建议限制恢复尝试次数（如 1-2 次）或改为绝对 deadline；不影响当前功能正确性。
- 是否阻塞：否。

### P-05（非阻塞，需用户裁决）`docs/ARCHITECTURE.md` 由 worker 修改，超出 worker 文件域

- 位置：`docs/ARCHITECTURE.md:125-129,224-236`。
- 证据：`AGENT.md` §10.1 规定 worker“不得改 `05/06/08-09`/架构文档”；本步 diff 包含该文档的 4.4/4.5 重写。
- 影响：内容与实现一致（单页 rebind、有界 drain、D-09 诊断优先级），流程上越界。
- 是否阻塞：否（内容正确，是否保留见 U-01）。

### P-06（非阻塞，需用户裁决）`ensure_context` 自动重连死 API

- 位置：`src/aistudio_api/infrastructure/gateway/session.py:228-245`；测试 `tests/unit/test_browser_session.py:175-202`。
- 证据：`ensure_context` 在页面无响应时 `_close_internal()` + `_ensure_browser_cdp()`；全仓 `src/` 无调用方（仅测试调用）。
- 影响：当前无运行时行为，但与“失败/stale 手动重启”口径存在语义张力，测试名会误导后续步骤。
- 是否阻塞：否（见 U-02）。

### P-07（非阻塞）`capture_template` 删除默认模板回退

- 位置：`src/aistudio_api/infrastructure/gateway/session.py:386-392`。
- 证据：HEAD 为 `base = dict(ctx.base_template if ctx else DEFAULT_BOOTSTRAP_TEMPLATE)`；现在 `ctx is None` 时 `ensure_ready()`，空 body 直接抛错。
- 影响：正常请求路径先 `ensure_ready()`，行为不变；属本步附带收紧，建议 B5 前复核降级路径。
- 是否阻塞：否（见 U-03）。

### P-08（非阻塞）`BUILD_SURFACE_BLOCKED_URL_PATTERNS` 尚无生产消费者

- 位置：`src/aistudio_api/infrastructure/browser/cdp_client.py:24-81`。
- 证据：全仓仅测试引用该常量；`init_domains(block_assets: bool)` 仍为布尔开关，build 页创建属 P4。
- 影响：D03-010 目前为声明层拆分，chat 行为不变；build 放行需 P4 接入。
- 是否阻塞：否（见 U-04）。

### P-09（非阻塞）rebind 失败路径未关闭已创建的新页

- 位置：`src/aistudio_api/infrastructure/gateway/session.py:292-350`。
- 证据：`create_new_page` 成功后若 `load_cookies_to_page`/`navigate_to_aistudio`/`bootstrap_botguard_and_template` 抛错，`except` 只清 `_active_context` 并记录 `_last_error`，未关闭 `new_page`。
- 影响：失败后到下一次手动切换前保留半初始化页；下次无上下文时 `connect_page` 的 prune 会清掉，属可恢复泄露，但与 D-11 口径不完全一致。
- 是否阻塞：否（建议 `except` 内关页）。

### P-10（非阻塞）`PooledSession` 的失败/关闭路径未清 target，context dispose 归 B2-3

- 位置：`src/aistudio_api/infrastructure/gateway/pooled_session.py:83-86,235-237`。
- 证据：bootstrap 失败仅 `page.close()`；`close()` 仅关 page，不 `dispose_browser_context`。
- 影响：当前未接入生产路径；B2-3 需明确“slot 关闭先关页再 dispose context”（D03-014）。
- 是否阻塞：否（见 U-06）。

## 5. 用户裁决项

1. **U-01**：`docs/ARCHITECTURE.md` 的同步改动是否接受（内容正确但属 worker 禁改范围，P-05）；接受则提交说明标注“架构文档同步”，不接受则回退并在 P10 统一处理。
2. **U-02**：保留的 `ensure_context`/`is_alive` 自动重连死 API 是删除还是保留（P-06）。
3. **U-03**：`capture_template` 移除 `DEFAULT_BOOTSTRAP_TEMPLATE` 回退是否接受（P-07）。
4. **U-04**：`BUILD_SURFACE_BLOCKED_URL_PATTERNS` 以“仅声明、P4 接入”方式通过是否可接受（P-08）。
5. **U-05**：`PooledSession` 的 context dispose / 失败 target 清理是否统一留给 B2-3（P-10）。
6. **U-06**：Windows 下 POSIX-only watchdog 用例 skip 是否按平台差异记录（P-04；行为路径需 Linux/Termux 侧执行）。
7. **U-07**：`import_cookies` 的同源残余（P-02b）是否在本步顺手改为 `connect_page=False` 并补测；若不改，是否接受作为 B2-3 前遗留。

## 6. 只读声明与状态

- 本审查**只读**：未修改任何代码、配置、测试、`05/06/08-09`、`archive`；仅更新本报告（目标树 + 主仓各一份，内容一致）。
- **B2-2 尚未 commit**：目标树 HEAD 仍为 `8b80971`，全部改动在工作区（14 个已跟踪文件 + 3 个新增源/测试文件 + 本报告）。
- 结论为 **PASS**：P-01 / P-02 / P-03 已修复，P-04 已补强，实跑 351 passed / 2 failed / 1 skipped，ruff 与 `git diff --check` 全绿；无新阻塞，仅保留 P-02b / P-03b 两个非阻塞建议与既有非阻塞裁决项。
- 等待用户审计确认后再由主 agent 决定提交。

## 7. U-02 / U-07 微调复核记录（2026-10-29）

- **U-02**：已删除 `BrowserSession.ensure_context` / `is_alive` 自动重连死 API，并删除对应旧单测 `test_browser_session_liveness_and_auto_reconnect`；严格对齐“失败/stale 仅手动重启、禁止自动复活”。
- **P-02b**：`BrowserSession.import_cookies` 改为 `_ensure_browser_cdp(connect_page=False)`，避免死进程恢复时多留一页。
- **P-09**：`transition_to_account` 的失败路径已关闭本次已创建的 `new_page` 并 `close_target`，防止半初始化页残留。
- **P-03b**：BotGuard 登录页重注入恢复增加独立上限 2 次，超出直接抛 `SessionExpiredError`，避免过期 Cookie 反复刷登录页。
- 实测：`350 passed / 2 failed / 1 skipped`（2 个既有 Windows 平台差异；1 个 POSIX-only watchdog skip）；`ruff check src tests` 全绿；`git diff --check` 干净。
- 复核结论：**PASS**；U-02 / U-07 已完成，无新增阻塞。B2-2 等待用户确认 commit。
