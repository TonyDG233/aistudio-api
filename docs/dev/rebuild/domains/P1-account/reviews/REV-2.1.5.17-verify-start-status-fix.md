# REV-2.1.5.17 — 扫码验证「首次打开不出码 / 扫码后检查无反应」修复只读审查

- 审查日期：2026-11-23
- 审查角色：独立只读 Reviewer
- 审查对象（本地未提交工作区改动；代码/测试/构建范围 11 文件 / **+788 −235**，来自 `git diff --numstat` 扣除下述文档同步）：
  - 后端：`src/aistudio_api/application/verify_account_service.py`（+306/−155）、`src/aistudio_api/api/routes_accounts.py`（+7/−2）、`src/aistudio_api/api/ui_sandbox.py`（+42/−29）
  - 前端：`web/src/components/modals/VerifyAccountModal.vue`（**+140/−34**；含本轮 P2 修复增量）、`web/src/api/accounts.ts`（+2/−0）
  - 构建产物：`src/aistudio_api/static/assets/{AccountsView.js,index.css,index.js}`（line diff +8/−8、+1/−1、+1/−1，内容随源码更新）
  - 测试：`tests/unit/test_verify_account_service.py`（+193/−0）、`test_verify_account_routes.py`（+66/−2）、`test_ui_debug_sandbox.py`（+22/−3）
- 非本轮代码范围：`docs/dev/rebuild/domains/P1-account/research/exit-pool/{CONTRACT,HANDOVER,PLAN}.md`（+31/−9）由另派文档同步任务修改，不计入本次代码审查结论。
- 依据：`AGENT.md`、`docs/dev/rebuild/06-decision-log.md`、`docs/dev/rebuild/08-09-decision-record.md`（D-01..D-14）、本轮任务口径（上游 `3` 视为通过并清锁、TTL 300s、出码后自动每 10s 查询、手动检查 force、单次启动、starting 轮询、状态行展示「上次检查」）。
- **结论：PASS（0 P0 / 0 P1 / 0 P2 / 5 Q / 4 N）**。两个实机问题的根因（前端双 watcher 双 start、后端仅页面 success 才查上游且 3 不判通过、手动检查被 in-flight 吞掉且无反馈）均已在代码与实跑中确认修复；上轮唯一 P2「关闭弹窗发送两次 cancel」已由 Worker 修复并经本轮 UI 实测与代码复核关闭（见 P2-1）；全量门禁沿用上轮结果（后端/测试文件未变）并通过，本轮定向测试与前端 lint/type-check/build 实跑通过，UI Debug 沙盒复测通过。Q 为口径/边界提示，N 为基线备注。

## 0. 审查边界与工作区说明

- 唯一写操作：更新本文件（`REV-2.1.5.17`）；`reviews/assets/` 下 2 张截图沿用上轮、本轮未重写。未改任何代码/配置/测试/契约/`static` 源码，未 commit/push。
- 未启动真实服务/真实浏览器/真实账号，未访问 VPS/外网/真实 Google；唯一运行时实测为 `--ui-debug` 纯内存沙盒，结束后已停止进程、确认端口释放、清理本次临时目录。
- 只读命令：`git status/diff/log`、`read/grep`、`uv sync --extra dev`、`uv run pytest`、`uv run ruff check .`、`uv run ruff format --check`、`uv run pyright src tests`、`bun run lint/type-check/build`、Playwright MCP（自带 Chromium 无头内核）。

## 1. 必查项逐条结论

> 记号：PASS=符合；P2=非阻塞观察；Q=提示；N=备注。

### A. 单次启动（PASS）

- 双 watcher → 单 watcher：`watch([() => props.modelValue, () => props.account?.id], ...)`（`VerifyAccountModal.vue:519-546`）。`AccountsView.openVerifyModal` 在同一同步函数内先写 `verifyingAccount` 再写 `verifyModalOpen`（`AccountsView.vue:130-133`），Vue 同 tick 批量合并，回调只跑一次。
- `openedFor` 二次护栏：同一打开周期内相同 `accountId` 直接 return（`:536`），避免其它触发源重复 start。
- 关闭重置 guard：`open=false` 时 `openedFor = null`、`closeHandled` 时只清理不再 cancel，随后复位 `closeHandled`（`:522-531`）。
- 账号切换先取消旧会话：`openedFor !== null` 时先 `flowToken += 1` 并 `cancelSession(openedFor)` 再 `openFlow()`（`:537-542`）；取消请求失败不阻塞 UI（`:407-427`）。
- 不存在“第二次 start 的 abort 丢弃第一次启动”：`resetSession()` 的 `abortStart()` 只 abort 前端自己持有的 `startAbort`（`:271-276`），打开流程中不会误 abort 本次请求。
- 实跑：Network 仅 1 条 `POST /api/accounts/acc_free_verify/verify/start`；关闭→重开各 +1 次，无重复（见 §4.1）。

### B. STARTING（PASS）

- 前端 starting 轮询：`syncPolling()` 条件扩展为 `starting || pending`（`:216-223`），间隔 2.5s（`:35`）。
- starting 骨架与已等待秒数：`phase === 'starting' || busy` 展示骨架，`startingElapsed` 基于 `flowStartedAt` + 1s ticker（`:77-80`、`:288`、`:638-645`）。
- status 返回 starting 也会刷新「上次检查」：`pollStatus` 在任何成功响应后执行 `lastCheckedAt.value = Date.now()`（`:383`），不区分 state。
- 约 2s 后自动切二维码：沙盒 `starting_seconds = 2.0`（`ui_sandbox.py:1185`），到点后 `status` 置 `PENDING` 并写入固定假码（`ui_sandbox.py:1260-1266`）；实测 2.6s 出现二维码与 `TESTSHORT`。
- start 75s 超时可见错误并可刷新：`START_TIMEOUT_MS = 75_000`（`:39`），超时 abort 后区分 `timedOut` 写错误文案（`:318-322`、`:332-348`），`busy` 在 finally 复位（`:349-355`），刷新按钮恢复可点（`:732-743`）。
- 主动关闭不误报超时：`if (controller.signal.aborted && !timedOut) return`（`:334`），用户关闭/切换账号的 abort 静默返回。
- 状态行文案：`phaseLabel` starting 显示「浏览器启动中…」（`:118-122`），按钮在 starting 期间显示「启动中…」且禁用（`:745-761`）。

### C. 手动检查（PASS）

- pending 下可点：`:disabled="!isPending || checking || busy"`（`:749`），仅 pending 且空闲可点。
- 点击立即「检查中…」：manual 分支在 in-flight 判断之前先 `checking.value = true`（`:360-366`），实测点击后 34ms 起按钮即为「检查中…」且禁用至响应返回（见 §4.2）。
- in-flight 排队补发一次 force：manual 且 `pollInFlight` 时置 `manualQueued = true` 并返回（`:363-366`）；finally 中在仍处于 pending/starting 时补发一次 `pollStatus(true)`（`:393-404`）。
- URL 必含 `force=true`：`if (force) query.set('force', 'true')`（`accounts.ts:168`）；manual 透传（`:375-380`）。实测唯一手动请求为 `...&force=true`。
- 返回 pending 的可读提示：后端 1/None 置 `message="still_pending"`（`verify_account_service.py:964-965`），前端 `VERIFY_MESSAGE_LABELS` 映射为「仍在等待扫码」（`:41-46`、`:93-103`），状态行渲染「上次检查 HH:MM:SS · 仍在等待扫码」（`:626-632`）；未知/空值回退「仍在等待扫码」（`:99-101`），不暴露内部变量名。
- 自动轮询不会叠加：非 manual 且 `pollInFlight` 直接 return（`:367-371`）；实测 force 被延迟 1.5s 期间仅 1 条 force 请求，下一跳自动轮询被丢弃（Network 78→79 无并发）。

### D. 后端判定（PASS）

- force 绕过页面 success：`should_confirm = probe.success or force or (now - last_confirm_at >= 10)`（`verify_account_service.py:895-901`）；手动 `force=True` 时即使页面仍停在 verify-account 也立即查询上游。
- 自动 10s 节流：`CONFIRM_RPC_INTERVAL_SECONDS = 10.0`（`:96`），`last_confirm_at` 在 RPC 发出前更新（`:903`）；单测 `test_non_force_status_throttles_upstream_rpc` 断言 10s 内仅 1 次、手动回拨 11s 后第 2 次（`test_verify_account_service.py:1294-1311`）。
- 2→passed/verified：`:956-959`，先 `_clear_verify_required` 再 `_finish`。
- 3→passed/not_required 并清 registry/rotator：`:960-963`；清锁走 `account_store.update_account_refresh(verify_required=False)`（`:967-968`，落 registry，`account_store.py:1077-1119`）+ `rotator.record_verify_cleared`/统计回退（`:969-984`）。
- 1/None→pending/still_pending：`:964-965`。
- 异常/超时不终态误判：confirm 的传输异常被捕获后仅置 `rpc_pending` 并返回，不进入终态（`:919-935`）；`_await_or_cancel` 返回 `done=False`（取消）直接返回（`:936-937`）；401/403 仅命中显式失效/地区标记才终态（`:909-918`、`:1247-1256`）。
- login/region/failed 优先终态：login、available-regions、failed 在三处提前 `_finish` 并 return（`:878-890`）。
- force 日志可观测：手动 INFO 含 `probe_success/status`，不含 Cookie/邮箱/短码（`:929-934`、`:941-947`）。

### E. STARTING 复用等待（PASS）

- 第二次 start 等待而非返回空 starting：STARTING 分支取 `self._start_future` 并在锁外 await（`:451-459`、`:519-520`、`:650-670`）；成对 start 的 `finally` 恰好一次解析 future，覆盖返回/异常/取消所有路径（`:630-648`）。
- 单测：`test_duplicate_start_waits_for_in_flight_launch` 断言第二调用得到 `pending + qr_url`、同 session（`test_verify_account_service.py:1185-1207`）。
- 启动失败不悬挂：`test_duplicate_start_sees_launch_failure_without_hanging` 断言第二调用得到 `error/qr_timeout` 且 driver 已关闭（`:1209-1231`）。
- 取消不悬挂：`test_duplicate_start_sees_cancel_without_hanging` 断言 owner 与 duplicate 均 `cancelled`（`:1233-1264`）。
- 第二个客户端 abort 不取消第一个启动：`_await_start_future` 使用 `asyncio.shield(future)`（`:654-657`）。补充探针实跑：`duplicate_cancelled=yes`、`owner_state=pending qr=...`、`follow_state=pending same_session=True`（上轮探针实跑，本轮仅前端改动，不影响该后端路径）。
- 驱动创建失败时先解析错误快照再抛出，等待者不悬挂（`:497-504`）。

### F. 回归（PASS）

- TTL 300：`DEFAULT_TTL_SECONDS = 300.0`（`:88`）；单测 `test_default_ttl_is_300_seconds` / `test_new_session_expires_after_default_ttl`（`test_verify_account_service.py:1165-1183`）；沙盒同样用 `DEFAULT_TTL_SECONDS`（`ui_sandbox.py:1234`）。
- 原有 cancel/expired/passed/404/409/503：全量 2010 passed，既有终态/路由用例未回归。
- `simulate` 仍仅沙盒消费：`sandbox_simulate = simulate if get_ui_sandbox(...) is not None else None`（`routes_accounts.py:1300`），生产忽略；`force` 为正式手动检查语义，生产路由透传（`:1287`、`:1302-1307`），非测试旁路。
- `message` 契约一致：后端 `VerifySessionResponse.message: str | None`（`routes_accounts.py:568-574`）↔ 前端 `VerifySessionResponse.message?: string | null`（`web/src/types/accounts.ts:108-115`）。
- 沙盒起始态改为 `starting` 并固定假码 `TESTSHORT`（`ui_sandbox.py:1170-1171`、`:1228-1239`），`force` 在沙盒显式忽略（`:1249-1252`），不加速确定性时间线。

### G. 测试与门禁（PASS）

见 §2 实跑记录：全部门禁 0 error，前端 build 后 `git status` 仍为原代码/测试/static 文件集合（仅另加 3 个文档同步文件，非本轮代码），static 仅 3 个预期产物变化，无源码回退。

### H. UI Debug 沙盒实测（PASS）

见 §4.1：单次 start、starting 骨架+已等待、2.6s 出码 `TESTSHORT`、手动 force、检查中反馈、上次检查提示、关闭/重开不重复启动、三分辨率无横向溢出/遮挡；本轮 P2 复测见 §4.5。

## 2. 实跑门禁与输出摘要（本机 Windows，真实执行）

| 检查 | 命令 | 真实结果 |
|---|---|---|
| 依赖同步 | `uv sync --extra dev` | 成功（Resolved/Audited 47 packages；上轮） |
| 全量测试 | `uv run pytest -q` | **2010 passed in 72.68s**（上轮；本轮后端/测试文件与上轮完全一致，未重跑） |
| 定向测试（本轮） | `uv run pytest tests/unit/test_verify_account_service.py tests/unit/test_verify_account_routes.py tests/unit/test_ui_debug_sandbox.py -q` | **80 passed in 13.64s** |
| Ruff（本轮） | `uv run ruff check .` | All checks passed!（exit 0） |
| Pyright（本轮） | `uv run pyright src tests` | 0 errors, 0 warnings, 0 informations |
| 空白检查（本轮） | `git diff --check` | exit 0（仅 CRLF 将被转 LF 的 warning，非错误） |
| 格式化（全库，上轮） | `uv run ruff format --check .` | **57 files would be reformatted, 370 files already formatted**（基线未格式化，详见 N-1；本次 6 个 Python 文件均不在其中） |
| 格式化（本次文件，上轮） | `uv run ruff format --check <6 files>` | **6 files already formatted** |
| 前端 lint（本轮） | `cd web && bun run lint` | exit 0（eslint 0 warning） |
| 前端类型（本轮） | `cd web && bun run type-check` | exit 0（vue-tsc） |
| 前端构建（本轮） | `cd web && bun run build` | built in 3.11s；`AccountsView.js 128.94 kB`；build 后 static 仍仅 3 个预期产物变化，无源码回退 |

- 全库 57 个未格式化文件中不含本次改动的任何 Python 文件（已逐一比对列表），属于既有基线；本次未引入新的格式化回归。
- `git diff` 全量逐行审阅：无调试残留（无新增 `console.*` / `print(` / `pdb` / `TODO` / `FIXME`），`ui_sandbox.py` 中部分纯格式化差异不影响语义。

## 3. 缺陷与观察

### P2-1（已修复，上轮非阻塞）：关闭弹窗曾发送两次 `POST /verify/cancel`

- **历史现象（上轮实测，保留作回归基线）**：一次关闭产生两条 cancel。UI 请求序列：`dt=27 POST cancel`、`dt=28 POST cancel`；第二次关闭同样 `dt=1180/1181` 两条，服务端日志连续两条「验证会话取消」。
- **历史根因**：`closeModal()` 自身发送 cancel 后，`emit('update:modelValue', false)` 又触发 modelValue watcher 的 `cancelSession()`，造成第二条 cancel。
- **影响**：仅冗余请求与日志噪声，后端 `cancel` 幂等，无状态/资源影响；上轮未出现错误或重复 start。
- **本轮修复（代码位置）**：
  - 新增关闭/取消结算标记 `closeHandled`、`cancelSettled`（`VerifyAccountModal.vue:74-75`）。
  - `cancelSession()` 增加 `if (!id || cancelSettled) return` 幂等护栏，并支持显式传入旧 `accountId`（`:407-427`，护栏在 `:412-413`）。
  - `closeModal()` 独占取消职责：先 `cancelSettled=true` 再 fire-and-forget 一次 cancel（`:449-473`）；`passed` 分支只置 `cancelSettled=true`、不清锁、不发 cancel（`:457-462`）。
  - modelValue watcher 在 `closeHandled` 时只做清理、不再 cancel，并复位 `closeHandled`（`:522-531`）；外部 `modelValue=false`（`closeHandled=false`）仍走 `cancelSession()`。
  - `onBeforeUnmount` 仅在 `cancelSettled=false` 兜底取消（`:556-560`）；`resetSession()` 复位 `cancelSettled=false` 以允许新会话正常取消（`:296`）。
- **本轮复测证据（UI Debug 沙盒 + 请求计数）**：
  - `pending` X 关闭：**1** 条 cancel / 1 条 start，弹窗关闭。
  - `starting` X 关闭：**1** 条 cancel / 1 条 start，弹窗关闭。
  - `pending` 遮罩关闭：**1** 条 cancel / 1 条 start，弹窗关闭。
  - `passed` 自动关闭（沙盒 `simulate=passed`）：**0** 条 cancel / 1 条 start，弹窗关闭。
  - 「刷新二维码」：**1** 条 cancel + 1 条 start，二维码重新出现。
  - 路由切换卸载（弹窗开启）：**1** 条 cancel；正常关闭后再卸载：额外 **0** 条 cancel。
  - 服务端日志（另一次同样四阶段实测）亦为每个关闭动作仅一条 `POST /verify/cancel`，`passed` 后无 cancel。
- **外部 `modelValue=false` 路径**：当前 `AccountsView` 仅由弹窗自身 `update:modelValue` 关闭，未暴露外部关闭入口；生产构建不暴露 `__vueParentComponent`/`setupState`，无法在不改代码的前提下注入父级状态，故该分支为代码复核（watcher `:522-531` 中 `closeHandled=false` 会调用 `cancelSession()`），未做 UI 注入实测。其余全部关闭/卸载路径均已实测。

### Q-1（提示）：开启状态下切换账号的取消/启动未串行

- `openedFor` 变化时 `void cancelSession(openedFor)` 后立即 `openFlow()`（`VerifyAccountModal.vue:537-542`），cancel 未 await。若新 start 先到后端，全局单会话会看到旧账号的 ACTIVE 会话并抛 `VerifySessionConflict`（`verify_account_service.py:460-461`）→ 409。
- 当前 UI 下弹窗为 `fixed inset-0 z-50` 全屏遮罩，账号卡片按钮不可点，正常用户无法在弹窗开启时切换账号，因此不可达；若未来开放“弹窗内切换账号”，建议 `await cancelSession(...)` 后再 start，或后端提供原子换绑。

### Q-2（提示）：STARTING 期间 `status(force=True)` 不查询上游

- `status()` 在 `state is STARTING` 时直接返回快照（`verify_account_service.py:706-709`），force 被忽略；前端同时禁用检查按钮并显示「启动中…」（`VerifyAccountModal.vue:745-761`）。当前口径为 pending 才手动检查，符合预期；如后续要求 starting 也能强制查询，需要调整后端分支。

### Q-3（提示）：`_start_future` 仅在 dispose 时清空

- 正常结束后 future 已解析但 `self._start_future` 仍持有引用（`verify_account_service.py:630-648` 解析，`:1082` 才清空）。仅保留一个已完成 future 的小对象，无泄漏风险；可在 `_finish_locked` 中顺带清空以更严谨。

### Q-4（提示）：沙盒 2s 起始窗口短于首个 2.5s 轮询

- 浏览器实测无法观察到「status 返回 starting」这一步（首次轮询在 2.5s，沙盒已翻 pending）。该路径由单测 `test_verify_account_sandbox_flow` 的强制 status（`test_ui_debug_sandbox.py:824-830`）与前端 `lastCheckedAt` 无条件刷新（`VerifyAccountModal.vue:381-383`）共同覆盖，不构成缺口。

### Q-5（提示）：手动 force 与自动轮询的提示文案来自同一 `message`

- 自动 10s 确认把 `still_pending` 写入 `session.message`（`verify_account_service.py:964-965`），因此自动轮询后「上次检查」行也会显示「仍在等待扫码」，与手动检查观感一致，符合口径。

### N-1（备注）：全库 ruff format 基线未收敛

- `uv run ruff format --check .` 报 57 个文件待格式化（含 docs 内嵌代码块、若干既有 src/tests 文件），均为本轮之前既有状态；本轮 6 个 Python 文件已格式化。未在本轮顺手格式化以避免无关 diff。

### N-2（备注）：static 产物与源码一致且构建幂等

- P2 修复后重新执行 `bun run build`（built in 3.11s），`git diff --stat` 仍仅 3 个 static 产物（`AccountsView.js +8/−8`、`index.css +1/−1`、`index.js +1/−1`），无新增/删除产物；bundle 内含「启动中…」「检查中…」「上次检查」「验证浏览器启动超时」「still_pending」等文案，且实测关闭行为已体现新的 cancel 去重逻辑。

### N-3（备注）：Windows CRLF 警告

- `git diff --check` / `git diff` 仅提示 `verify_account_service.py`、`test_verify_account_routes.py` 的 CRLF 将被转 LF，属仓库既有换行策略，不是本次引入的错误。

### N-4（备注）：无真实环境副作用

- 未触发真实 Google/BotGuard/账号；唯一运行的是 `--ui-debug` 纯内存沙盒（本轮端口 `11428`）。三次沙盒进程均已停止，`PORT_11428_FREE`；本轮 `aistudio-ui-debug-{6runmys4,d93sfcxp,pykjftil}` 临时目录均已清理。上轮探针脚本已清理，本轮无需重复。

## 4. UI Debug 沙盒实测明细

- 首轮 UI 功能实测（§4.1–§4.4）：`uv run python main.py server --port 2586 --ui-debug`。
- 本轮 P2 复测（§4.5）：`uv run python main.py server --port 11428 --ui-debug`。

Playwright MCP（自带 Chromium 无头内核）；分辨率为 1440x900 / 1024x900 / 768x900。首轮仅对 `/verify/status?...&force=true` 在 Playwright 侧注入 1.5s 延迟以观察「检查中…」，不修改任何应用代码。

### 4.1 单次 start + STARTING 骨架 + 出码

| 时刻(ms) | 骨架 | 已等待 | 二维码 | TESTSHORT | 检查按钮 |
|---|---|---|---|---|---|
| 300 | 是 | — | 否 | 否 | 启动中… |
| 900 | 是 | — | 否 | 否 | 启动中… |
| 1400 | 是 | 1 秒 | 否 | 否 | 启动中… |
| 1900 | 是 | 1 秒 | 否 | 否 | 启动中… |
| 2400 | 是 | 2 秒 | 否 | 否 | 启动中… |
| 3200 | 否 | — | 是 | 是 | 检查状态 |

- 本次打开仅 1 条 `POST /verify/start`；关闭后重开 `beforeReopen=1 → afterReopen=2`，每次打开恰好 +1，无重复启动。
- 关闭发生在 STARTING 期间后立即重开：未出现「已取消/超时」误报，最终正常进入 pending + QR。

### 4.2 手动检查

- 点击「检查状态」后 34ms 起按钮文本为「检查中…」、`disabled=true`，持续至响应返回（采样 1.47s 仍为检查中，1.57s 恢复「检查状态」）。
- 唯一手动请求 URL：`/api/accounts/acc_free_verify/verify/status?session_id=...&force=true` → 200；延迟窗口内自动轮询被 in-flight 保护丢弃，未并发。
- 返回后状态行显示：`上次检查 19:54:50 · 仍在等待扫码`（另一次为 `19:57:30` 等），无内部变量名。

### 4.3 三分辨率布局

| 分辨率 | `documentElement.scrollWidth` / `innerWidth` | 横向溢出 | 弹窗 rect | 页脚可见 |
|---|---|---|---|---|
| 1440x900 | 1440 / 1440 | 否 | left 464, right 976, top 141, bottom 759 | 是（703-758） |
| 1024x900 | 1024 / 1024 | 否 | left 256, right 768, top 141, bottom 759 | 是 |
| 768x900 | 768 / 768 | 否 | left 128, right 640, top 141, bottom 759 | 是 |

### 4.4 截图（`reviews/assets/`）

- `docs/dev/rebuild/domains/P1-account/reviews/assets/REV-2.1.5.17-verify-pending-1440x900.png`：pending 态完整弹窗（等待扫码、剩余时间、上次检查、QR、TESTSHORT、底部三按钮）。
- `docs/dev/rebuild/domains/P1-account/reviews/assets/REV-2.1.5.17-verify-pending-768x900.png`：768 窄屏无横向溢出/遮挡，页脚按钮完整可见。
- 本轮 P2 修复仅收敛 cancel 请求次数，弹窗视觉与布局未变化，故沿用上轮截图、不重拍。

### 4.5 P2 修复复测（关闭/取消路径，port 11428）

以 `page.on('request')` 统计 `POST .../verify/cancel` 与 `POST .../verify/start`，每个动作后等待请求落地：

| 场景 | cancel 次数 | start 次数 | 弹窗关闭 |
|---|---|---|---|
| pending，X 关闭 | **1** | 1 | 是 |
| starting，X 关闭 | **1** | 1 | 是 |
| pending，遮罩点击关闭 | **1** | 1 | 是 |
| passed（沙盒 `simulate=passed` 200），自动关闭 | **0** | 1 | 是 |
| 「刷新二维码」 | **1** | 1 | 弹窗保留、QR 重新出现 |
| 路由切换卸载（弹窗开启） | **1** | 0 | 是（组件卸载） |
| 正常关闭后再路由卸载 | 关闭 **1**；卸载额外 **0** | 0 | 是 |

- 另一次同样四阶段的服务端日志：每次关闭仅一条 `POST /api/accounts/acc_free_verify/verify/cancel`（20:18:58、20:19:01、20:19:05 各一条），`simulate=passed` 后（20:19:12）无 cancel，与浏览器计数一致。
- 外部 `modelValue=false` 分支：当前 UI 无外部关闭入口，生产构建不暴露组件实例，无法无损注入；按代码复核，watcher 在 `closeHandled=false` 时会调用 `cancelSession()` 发送且仅发送一条 cancel（`VerifyAccountModal.vue:522-531`、`:412-413`）。

## 5. 只读声明

本审查为**只读**：除更新本报告外，未修改任何代码、配置、文档、测试或 `static` 产物；未 `git add/commit/push`；未启动真实服务/真实浏览器（UI 实测仅 `--ui-debug` 纯内存沙盒），未访问 VPS/外网/真实账号；实测进程已停止、端口已释放、临时目录已清理。
