# REV-2.1.5.10 — verify-account 扫码验证链路只读审查

- 审查日期：2026-11-22
- 审查角色：独立只读 Reviewer
- 审查对象：状态拆分、验证会话服务、前端扫码弹窗
- 结论：**FAIL**
- 依据：`AGENT.md`、`docs/dev/rebuild/06-decision-log.md`、`docs/dev/rebuild/08-09-decision-record.md`（D-01..D-14）、本次实际 diff 与实跑门禁

## 0. 审查边界与工作区说明

本轮只审 `verify-account` 扫码工程。当前工作树同时含上一轮未提交的 exit-pool 特殊模式改动（`exit_pool/`、`exit_arbitration.py`、`routing_pipeline.py` 的出口仲裁、`REV-2.1.5.8/2.1.5.9` 成果），不属于本审查对象。

核验结果：本轮 verify-account 改动没有修改 exit-pool / routing pipeline 出口逻辑；`git diff -U0 -- src/aistudio_api/application/routing_pipeline.py` 中不含 `verification_required|verify` 的 verify 新增行（verify 分支原已存在）。但工作树整体仍含上一轮出口改动，提交时必须由 integrator 按文件域拆分，不能把出口改动混入本步 commit。

唯一写操作：本文件。

## 1. 结论

**FAIL**

存在 1 个 P0 和 3 个 P1：

1. **P0**：QR 取码前“Verify device”按钮可能处于 `disabled`，代码只点击一次且不检查禁用态，点击落空后不会重试，核心取码链路会稳定/间歇失败。
2. **P1**：验证通过后只清内存与 `AccountMeta`，未立即持久化 rotator 状态；在 `persist_rotator=true`（默认）下重启，旧 `verify_required=true` 会从 rotator_state 回潮，账号继续被锁。
3. **P1**：`start()` 全程持有全局会话锁（最长约 70s），`cancel()`/`status()` 只能排队；关闭弹窗后的取消、立即换账号验证都会被阻塞，违反“可取消/关闭必 cancel”的即时性。
4. **P1**：默认验证浏览器路径 `/opt/cloakbrowser/chrome` 与本仓的安装布局不一致（`.cloakbrowser/chromium-*/chrome` + Termux `scripts/cloakbrowser_termux/run-chrome.sh`），未配置环境变量时在 Termux 必 503；其它宿主也需人工配置才能用。

A–G 必查项逐项结论见第 2 节；P0/P1/P2 细节见第 3 节；门禁见第 5 节。

## 2. A–G 必查项核查

### A. 状态语义

- **verify 与 session_expired 独立**：通过。`AuthError.verification_required` 在 `account_orchestrator.py:72-83` 只调用 `record_verify_required`，不写 `session_expired`；`record_verify_required` 只置 `verify_required/verify_required_count/last_verify_required_at`（`account_rotator.py:418-436`）；`record_session_expired` 仍保留 sibling 连坐（`account_orchestrator.py:84-97`）。
- **verify 账号不可调度**：通过。`is_available` 在 `account_rotator.py:240-252` 对 `verify_required` 返回 False；候选过滤 `account_rotator.py:908-936` 与 `select_rebind_account` 的 `stats.is_available()`（`:758-779`）都会排除；路由准入 `routing_pipeline.py:560-563,683-693` 走 `is_account_available`。
- **verify 账号不被保活探测**：通过。`AccountKeepaliveService._is_auth_locked` 优先调用 `rotator.is_auth_locked`（`account_keepalive.py:202-227`），后者覆盖 verify（`account_rotator.py:700-711`）；metadata refresh、cookie rotate、auth update 三处均已跳过（`account_keepalive.py:409-418,593-611,716`）。
- **`is_available=false`**：通过。API `_account_status` 在 `routes_accounts.py:160-171` 把 verify 纳入 false 判定；沙盒同样（`ui_sandbox.py:1868-1889`）。
- **status 优先级 disabled > expired > verify > cooldown**：通过。前端唯一分类器 `web/src/utils/accountStatus.ts:30-46` 顺序完全一致；`AccountRow.vue:160-167,320-349` 的琥珀/红色视觉也按此顺序。
- **旧 meta.json 兼容**：通过。`AccountMeta.from_dict` 用 `bool(data.get("verify_required", False))` 兜底（`account_store.py:387`），并有单测 `test_account_meta_verify_required_round_trip_and_legacy_default`（`tests/unit/test_runtime_persistence.py`）。
- **原子写**：通过。`update_account_refresh` 走 `_atomic_write_json` 写 meta.json 后再 `_save_registry`（`account_store.py:1062-1114`）。

### B. 验证会话服务

- **全局单会话 / 同账号幂等 / 异账号 409**：通过。`start` 复用同账号 STARTING/PENDING/PASSED（`verify_account_service.py:361-395`），否则 `VerifySessionConflict`；路由映射 409（`routes_accounts.py:951-962`），测试 `test_same_account_start_is_idempotent_and_other_account_conflicts`。
- **start 预算**：实现为 `_wait_for_qr` 20s（`verify_account_service.py:66-67,773-789`）+ 外层 `wait_for` 70s（`:408-410`）。20s/50s 的预算方向合理（导航 30s + 取码 20s + 启动余量），但不可中途取消（见 P1-2）。
- **cancel 幂等**：通过。`cancel` 对无会话/异账号返回 cancelled，对 active 会话置 CANCELLED 并关闭 driver（`:469-479`）；路由重复调用测试通过。
- **TTL 180s**：通过。`DEFAULT_TTL_SECONDS=180.0`（`:66`），定时器置 EXPIRED（`:583-605`）。
- **passed 才清锁且 enabled 不变**：通过。仅 `confirm_verified` 返回 2 时 `_clear_verify_required`（`:523-535`）；store 更新只传 `verify_required=False`（`:537-545`），不改 enabled；测试 `test_passed_clears_verify_lock_keeps_enabled_and_closes_driver` 断言 enabled=true。
- **临时目录/进程 finally 清理**：主进程路径基本通过。`start` 的 `BaseException` 分支 `close()`（`:699-710`），`_finish_locked`/`_dispose_session_locked` 关闭 driver（`:547-575,618-632`），`close()` 关 page/client/process 并 `rmtree`（`:743-765`）。Windows 子进程清理存在 P2-6。
- **应用退出清理**：通过。lifespan 启动前清旧服务（`api/app.py:285-291`），`_shutdown_runtime` 先 `shutdown_verify_account_service`（`api/app.py:233-250`），失败不掩盖后续关闭。
- **无自动重试/后台循环**：通过。服务内只有一次性过期/清理 timer（`:577-605`），状态只由 API 调用推进。

### C. 浏览器链路

- **独立无头 Chromium**：通过。`_build_verify_chrome_command` 独立进程、`--headless=new`、独立 user-data-dir（`:854-875`）。
- **默认直连**：通过。`AISTUDIO_VERIFY_PROXY` 为空时 `proxy=None`，不追加 `--proxy-server`（`:654-658,873-874`）。
- **Cookie 注入字段完整**：通过。`set_cookies` 保留 name/value/domain/path/secure/httpOnly/sameSite/expires，并特殊处理 `__Host-`（`cdp_client.py:649-712`）；`load_cookies_to_page` 先清 Cookie/Cache 再全量注入（`session_auth.py:117-145`）。
- **登录跳转→session_invalid**：通过。启动和轮询均检查 `is_login_page_url`（`verify_account_service.py:766-771,510-514`）。
- **available-regions→region_blocked**：通过。启动和轮询均检查 URL 子串（`:515-519,770-781`）。
- **bframe isolated world 取 canvas/href**：逻辑方向与实证一致（`Page.createIsolatedWorld` + `contextId` + href/title/fallback，`:791-851`），但存在两个问题：P0-1 的点击落空，以及 P2-1 的 context 缓存不失效。
- **二次确认用页面内 fetch 的风险**：
  - 风险等级：**中低，但必须实机确认**。
  - `_VERIFY_RPC_URL` 是 `https://alkalimakersuite-pa.clients6.google.com/...`（`public_web_constants.py:19-28`），页面 origin 是 `aistudio.google.com`，属于跨域；带 `authorization`/`x-goog-*` 非简单头会触发预检。Google 该 RPC 域对 AI Studio 前端开放 CORS，且本仓已有同域 in-page XHR 先例（`session_auth.py:161-211` 的 `extract_account_tier`），因此大概率可用。
  - 若 CORS/预检失败，`fetch` 会 reject 而不会永久 pending；`page.evaluate` 还有 15s 超时（`:725-734`）。`_refresh_locked` 捕获异常后保持 pending（`:523-532`），因此最坏是“验证页已成功但锁不清”，不会误清锁。
  - 但 `SAPISID` 依赖 `document.cookie` 可读（`:124-136`）。Google 自身页面 JS 也走同一 Cookie 读取链，理论可读；若上游改为 HttpOnly，二次确认会一直拿不到 status，需实机验证。
  - 建议：为 confirm 增加明确的 401/403/超时分类与重试上限；必要时改为由 Python 侧 `calculate_sapisid_hash` 生成 Authorization 后传入页面，降低对 `document.cookie` 的依赖。

### D. API

- **404/409/503/401**：通过。start 404/409/503、status 404、cancel 404、web auth 401 均有路由测试（`tests/unit/test_verify_account_routes.py`）。
- **disabled 账号可验证**：通过。路由只查存在性（`routes_accounts.py:949-950,988-989,1004-1005`），不查 enabled；测试 `test_verify_start_allows_disabled_account`。
- **响应不含 Cookie/邮箱/短码日志泄漏**：通过。`to_public` 只含 session_id/state/qr_url/short_code/expires_at/message（`verify_account_service.py:271-279`）；`routes_accounts.py:975,1008` 只记录 id/state；服务日志不打印 short_code。响应本身含 short_code 属产品设计（供用户复制）。
- **status 只由调用推进**：可接受，但需明确 UX 后果。没有后台轮询符合 §4.6 的保守方向；若用户在扫码成功与下一次 2.5s 轮询之间关闭弹窗，关闭会 cancel 会话，账号会保持 verify 锁，直到再次打开验证弹窗。当前弹窗标题“关闭并取消验证”已提示，但未提供“关闭前先检查一次”的兜底（见 Q-3）。

### E. 前端

- **琥珀 vs 红色**：通过。verify 用 amber 边框/徽章/按钮（`AccountRow.vue:166-167,335-342,394-404`），expired 仍为 rose（`:164-165,327-334,382-392`）；截图为琥珀。
- **“验证”仅 verify 出现且替代“恢复”**：通过。`isVerifyRequired` 排除 expired（`AccountRow.vue:93-99`），恢复按钮仅 `isExpired`（`:382-392`），验证按钮仅 `isVerifyRequired`（`:395-404`）。
- **筛选与统计**：通过。`AccountTable.vue:47-61` 有 verify 过滤，`StatsStrip.vue:42-43,112-114` 有 verify 计数。
- **弹窗轮询/自动关闭/关闭取消/卸载兜底**：基本通过。`POLL_INTERVAL_MS=2500`、`AUTO_CLOSE_MS=2000`（`VerifyAccountModal.vue:35-37`）；仅 pending 轮询（`:171-178`）；passed 自动关闭并刷新（`:195-205,207-219`）；关闭与卸载均 cancel（`:305-323,369-404`）。存在 P2-3 轮询无在途保护、P1-2 关闭时 start 无法即时取消。
- **错误文案直白中文**：通过（`:263-275`）。
- **二维码来自 qr_url 且用 qrcode 库**：通过。`QRCode.toDataURL(url, ...)`（`:180-192`）；`web/package.json` 仅新增 `qrcode` 与 `@types/qrcode`。
- **不显示敏感信息**：通过。邮箱/ID 掩码（`:149-167` 附近），不显示 Cookie/Authorization。
- **截图**：存在 1440/1024/768 的列表与 pending、1440 的 passed；但截图时间早于最终源码与 static build（见 P2-12）。

### F. 测试质量

抽查的真实断言（均非形式化断言）：

1. `test_same_account_start_is_idempotent_and_other_account_conflicts`：session_id 相等、state=pending、QR/短码、异账号 conflict。
2. `test_cancel_is_idempotent_and_closes_driver`：两次 cancel 均返回 cancelled，driver.closed=true。
3. `test_expiry_marks_expired_and_closes_driver`：过期后 state=expired 且 driver 关闭。
4. `test_login_redirect_sets_session_invalid` / `test_region_redirect_sets_region_blocked`：错误映射与 driver 关闭。
5. `test_passed_clears_verify_lock_keeps_enabled_and_closes_driver`：store=false、enabled=true、rotator=false、confirm 参数 "0"、driver 关闭、10s 后 status 404。
6. `test_page_success_without_status_confirmation_stays_pending`：page success 但 status=1 时不清锁、不关 driver。
7. `test_verify_start_status_cancel_contract` / `test_verify_start_conflicts_for_other_account` / `test_verify_start_allows_disabled_account` / `test_verify_start_returns_503_when_browser_unavailable` / `test_verify_routes_require_web_auth`：HTTP 契约全覆盖。
8. `test_verify_account_sandbox_flow`：sandbox start/status/cancel/simulate=passed 后 store 与 is_available 同步。
9. `web/tests/accountStatus.test.ts` 的 verify 优先级/与 expired 区分断言。
10. `test_account_meta_verify_required_round_trip_and_legacy_default` / `test_account_stats_verify_required_is_separate_from_session_expired`：持久化与状态隔离。

fake 与生产类型一致：`FakeDriver`/`RouteFakeDriver` 结构满足 `VerifyBrowserSession`，`FakeRotator` 只暴露服务实际使用的 `get_account_stats` 字段；pyright 通过。

未覆盖的真实边界（须在部署待办中明确）：真实 CloakBrowser 下 reCAPTCHA Enterprise 是否给 headless 下发 QR、`document.cookie` 的 SAPISID 是否可读、clients6 CORS/预检、`Verify device` 按钮真实 DOM/状态、二维码 canvas/fallback 是否与 bundle 证据一致、Windows/Termux 进程树清理。当前证据只到静态 bundle 与 fake driver，**不能宣称实机通过**。

### G. 回归与产物

- **未改 exit_pool/routing_pipeline 出口逻辑**：verify-account 变更本身未改；但工作树含上一轮 exit-pool 未提交改动（见第 0 节）。
- **门禁**：ruff/pyright/pytest/web lint/type-check/bun test 全绿（见第 5 节）。
- **依赖**：仅新增 `qrcode@^1.5.4`、`@types/qrcode@^1.5.6`；`bun.lock` 新增 yargs/pngjs/dijkstrajs 等 qrcode 传递依赖，属正常解析，未见无关版本漂移。
- **static 与源码一致（只读核对）**：static 产物中已检出 `verify_required`、`扫码验证`、`qrcode`、`verify/start|status|cancel`；`index.html` 资源哈希已更新；static mtime 03:21:42 晚于 `VerifyAccountModal.vue` 03:21:19，产物包含最终源码。未执行 build。
- **截图文件存在**：yes；但截图早于最终源码/构建（P2-12）。

## 3. P0/P1/P2

### P0-1 “Verify device”按钮可能 disabled，点击一次后永不重试

- 位置：`src/aistudio_api/application/verify_account_service.py:80-93,773-789`
- 关联实证：`docs/dev/rebuild/domains/P1-account/research/verify-account/bundle/mod-VI9Wbd.js:3381,3387,3391`
- 现象：
  - `_CLICK_VERIFY_DEVICE_JS` 选取 `button, a, [role=button]` 中第一个 textContent 含 `verify device` 的元素，**不检查 `disabled`/`aria-disabled`**，找到后无条件 `match.click()` 并返回 `true`。
  - 页面的按钮在组件 state 不为 `ready` 时带 `disabled`（bundle `:3381`：`_.F("disabled",a.state()!=="ready")`）；组件初始 state 为 `loading`（`:3387`），`load()` 先查状态再 `render` reCAPTCHA（`:3386`）。
  - `_wait_for_qr` 第一次循环在 `domcontentloaded` 后立即执行；此时按钮通常已渲染但 disabled。对 disabled `<button>` 调 `.click()` 不会派发 click 事件，但 JS 仍返回 true，`clicked` 被置 true（`:775-784`），后续 20s 内不再点击。
  - 结果：不触发 `grecaptcha.enterprise.execute`，bframe 不下发挑战，永远读不到 QR，最终 `qr_timeout`。该失败与网络/账号无关，属核心链路缺陷。
- 复现（静态推断）：打开任一需验证账号 → start → 页面在 reCAPTCHA 脚本加载完成前（state=loading）执行第一次点击 → 返回二维码获取失败/超时。慢网络、冷启动、首屏慢时概率更高。
- 建议：
  - 点击前过滤 `!el.disabled && el.getAttribute('aria-disabled') !== 'true'`，只对 enabled 元素 click；
  - 若没有 enabled 匹配，返回 false 并保持 `clicked=false`，循环继续重试；
  - 可优先选 `[data-test-verify-button]:not([disabled])`，并在点击后确认 state 离开 `ready`/出现 bframe 再置 `clicked=true`；
  - 新增一个 fake DOM/JS 单测覆盖“disabled 命中后仍需继续重试”。

### P1-1 验证通过后未持久化 rotator 状态，重启会回潮旧 verify 锁

- 位置：`src/aistudio_api/application/verify_account_service.py:537-545`；`src/aistudio_api/application/account_rotator.py:600-617,622-629,670-684`
- 现象：
  - `record_verify_required` 立即 `save_state()`，rotator_state 中 `verify_required=true`（`account_rotator.py:1108-1138`）。
  - 验证通过时 `_clear_verify_required` 只 `update_account_refresh(False)` 并改内存 `stats.verify_required=False`，**没有 `save_state()`**。
  - 重启后 `_load_state` 把旧 state 原样载入（`:600-617`）；`get_account_stats` 只在 store 为 true 时置 true，**store 为 false 时不会清掉 state 里的 true**（`:670-684`）；`get_all_stats` 同样只单向上写（`:813-817`）。
  - 因此“通过验证 + 立即重启（或在下一次 state save 之前重启）”会把账号重新显示为“需扫码验证”，调度/保活继续排除，用户必须再走一次验证。
- 复现：默认 `persist_rotator=true`；账号触发 verify → 通过（store=false）→ 不产生任何后续 `record_success`/`save_state` 就重启 → `/api/accounts` 仍返回 `verify_required=true`。
- 建议：
  - `_clear_verify_required` 中在 store 更新成功后调用 `rotator.save_state()`（或统一的 `record_verify_cleared`）；或
  - `get_account_stats`/`_load_state` 以 store 为权威双向对齐：`stats.verify_required = bool(account.verify_required)`，并在启动时清理 stale true；
  - 增加 `test_verify_clear_persists_across_rotator_reload` 单测。

### P1-2 start 持有全局锁且不可取消，关闭/换号验证被阻塞

- 位置：`src/aistudio_api/application/verify_account_service.py:361-410,469-479,481-484`
- 现象：
  - `start()` 在 `async with self._lock` 内执行 `driver.start()`，最长 `wait_for(..., timeout=code_timeout + 50.0)`（`:408-410`），即约 70s。
  - `cancel()`/`status()`/`close()` 都需同一把锁（`:449-467,469-479,481-484`）。
  - 前端在 `starting` 阶段 `sessionId` 为空，`closeModal()` 的 `cancelSession()` 直接返回（`VerifyAccountModal.vue:305-323`），依赖 `start()` 返回后的 post-check 取消（`:254-260`）；因此关闭后取消最多延迟 70s，关闭后立即打开另一个账号验证会排队到 start 结束，随后得到 409。
  - 应用退出时 `_shutdown_runtime` 也会等这把锁（`api/app.py:233-250`），start 未结束时可能拖慢关闭。
- 复现：将 `driver.start()` 人为 delay 30s；点 start 后立刻关闭并打开另一账号 → 第二请求长时间 pending；原浏览器实例在关闭后仍存活。
- 建议：
  - 把“建会话”与“启动浏览器”拆开：锁内只做单会话占用/状态置 STARTING，释放锁后再 `driver.start()`；cancel 设置取消事件或直接 `driver.close()`，start 在关键 await 后检查取消标志；
  - 或 start 使用短预算（例如只锁创建，取码在独立 task），cancel 不再等 start；
  - 前端 close 不必 await 取消，改为 fire-and-forget + 后端可取消；或提供 `AbortController`。

### P1-3 默认验证浏览器路径与仓库安装/Termux 布局不一致

- 位置：`src/aistudio_api/application/verify_account_service.py:58,650-673`
- 关联：`src/aistudio_api/infrastructure/browser/browser_engine.py:42-62,65-122`；`scripts/cloakbrowser_termux/run-chrome.sh:1-18`；`scripts/setup-browser.sh:126`
- 现象：
  - 验证服务默认 `DEFAULT_CHROME_PATH="/opt/cloakbrowser/chrome"`，并直接 `Path(...).is_file()` 后 `create_subprocess_exec`。
  - 仓库安装脚本把 CloakBrowser 建在项目 `.cloakbrowser/chromium-*/chrome`；Termux 必须通过 `scripts/cloakbrowser_termux/run-chrome.sh` 进 proot 运行（glibc/bionic ABI 原因）。`/opt/cloakbrowser/chrome` 是 proot 容器内路径，Termux 宿主不可见。
  - 现有浏览器解析器 `find_chromium_executable()` 会在 Termux 自动替换成 wrapper（`browser_engine.py:57-62,118-122`），但验证服务没有复用它。
  - 结果：默认部署在 Termux 下 start 必返回 503（“未找到验证浏览器”）；其它宿主如果把浏览器装在 `.cloakbrowser` 也会 503，必须人工设置 `AISTUDIO_VERIFY_CHROME_PATH`，且 Termux 下应指向 wrapper 而非 `/opt/...`，报错提示没有说明这一点。
- 建议：
  - 优先复用 `find_chromium_executable()`/`build_chromium_args`，或至少复用 `_resolve_local_chrome()` 的 Termux wrapper 逻辑；
  - 默认值、错误提示与 docs 明确三种部署布局；为 wrapper 增加 smoke test；
  - 若坚持独立路径，增加启动自检：可执行 + 可运行（wrapper）两项都检查。

### P2-1 bframe isolated-world context 缓存不失效

- 位置：`verify_account_service.py:791-832`
- 现象：`self._worlds[frame_id]` 一旦创建就永久复用；若 bframe 在挑战过程中导航/重建，旧 `executionContextId` 失效，`Runtime.evaluate` 持续异常并被 `continue` 吞掉，永不重建，QR 读取永久失败。
- 建议：evaluate 失败时删除该 frame 的缓存并在下一轮重建；监听 `Page.frameNavigated` 清空；对 `createIsolatedWorld` 失败也避免永久缓存。

### P2-2 页面失败状态未映射到 `failed`

- 位置：`verify_account_service.py:73-78,173,320-326`
- 现象：`FAILED` 枚举存在但代码从不赋值；页面出现 “We couldn't verify your account… / Try a different mobile device” 时，`_is_success_page` 为 false，服务保持 PENDING 到 180s TTL，前端也继续轮询/等待，用户得不到“本次失败”的明确反馈。
- 建议：增加 `_FAILURE_MARKERS`（来自 research 的 failedMessage / try-different-device 文案），探测到后 `_finish_locked(..., FAILED, "page_failed")`；或把失败文案作为 status 的一个 message，但不自动清锁。

### P2-3 前端轮询无在途保护，confirm 15s 时会堆积请求

- 位置：`web/src/components/modals/VerifyAccountModal.vue:171-178,283-303`
- 现象：`setInterval(2500)` 无条件发起 `pollStatus`，没有 `if (polling) return`；若 `driver.confirm_verified` 每次耗时 15s（CORS 慢/超时），会同时排队 6 个 status 请求，后端同一把锁串行处理，cancel/关闭也要排在后面。
- 建议：用 in-flight flag 或递归 `setTimeout` 保证一次只有一个 poll；confirm 失败时指数退避；manual 检查与自动轮询互斥。

### P2-4 页面内 confirm 的跨域/凭据风险（中低）

- 位置：`verify_account_service.py:124-165,721-741`
- 判断：
  - `clients6` 跨域 + Authorization/自定义头会预检；Google 该 RPC 对 AI Studio 前端开放 CORS，本仓同域 XHR 有先例，**大概率可用**。
  - `fetch` reject/15s 超时不会永久 pending；`_refresh_locked` 捕获后保持 pending，不会误清锁。
  - 依赖 `document.cookie` 读 SAPISID；若上游改为 HttpOnly，确认永远返回 null，功能卡 pending。
- 风险等级：**中低**；必须在真实浏览器/真实账号环境验证一次，并增加 401/403 分类与超时上限；必要时改由 Python 侧生成 SAPISIDHASH。

### P2-5 验证浏览器未复用低内存/指纹参数

- 位置：`verify_account_service.py:854-875`
- 现象：独立 Chromium 只带 `--headless=new/--disable-gpu/...`，缺少 `browser_engine.build_chromium_args` 的 `--renderer-process-limit=1`、`--in-process-gpu`、`--js-flags=--max-old-space-size=128`、`--fingerprint-platform=windows`、`--fingerprint-timezone` 等 §4.7 低内存/伪装参数。实例池同时运行时，额外一套完整 Chromium 会放大 VPS 内存压力，也更易被识别。
- 建议：复用 `build_chromium_args`（传独立 user-data-dir/port/proxy）或至少补齐低内存与指纹参数；在 VPS 上采样单实例 RSS 后再定行。

### P2-6 Windows 子进程清理不彻底

- 位置：`verify_account_service.py:900-945`
- 现象：Windows 下 `process.terminate()` 是立即终止主进程；`_terminate_process` 成功后直接 return，`taskkill /F /T` 只在 `wait()` 超时后执行。若 Chromium 子进程未被 Job Object 连带回收，可能留下 renderer/GPU 进程；`shutil.rmtree(..., ignore_errors=True)` 会静默留下临时目录。
- 建议：Windows 下无论主进程 wait 是否成功，都执行一次 `taskkill /F /T /PID`；清理后检查 `tasklist`/目录（诊断日志）；在 Linux/Termux 保持 killpg。

### P2-7 删除账号不取消活动验证会话

- 位置：`api/routes_accounts.py:1012-1030`
- 现象：删除路由只清 registry/rotator/sandbox，不调用 `controller.cancel`；若账号正在验证，独立 Chromium 会继续存活到 180s TTL，且 session 指向已删除账号。
- 建议：删除前若 `controller` 的当前 session.account_id 匹配，先 cancel；或由 service 提供 `cancel_for_account(account_id)` 并在删除路径调用；补一条路由测试。

### P2-8 verify 账户仍显示“清除锁定”图标，点击不会清 verify

- 位置：`AccountRow.vue:442-450`；`AccountTable.vue:584`；`account_rotator.py:438-459`
- 现象：`rate_limited>0 && !isExpired` 时显示清除锁定；verify 账户通常 `rate_limited=0`，但历史 429 后可能 >0。点击走 `clear_cooldown`，它只清 429/403/失效，不清 verify；用户会误以为能解除扫码锁。
- 建议：`v-if` 增加 `!isVerifyRequired`；或在 clear 路径对 verify 账户明确提示“需扫码验证，不能通过清除冷却解锁”。

### P2-9 `to_account_response` 无 status 时 is_available 忽略 verify

- 位置：`routes_accounts.py:274-285`
- 现象：`status=None` 分支 `is_available = enabled and not expired`，但 `verify_required` 仍可能 true；在启动早期/rotator 缺失时 API 会给出 `verify_required=true, is_available=true` 的矛盾组合。前端状态优先级会显示 verify，但 API 语义不一致。
- 建议：`status=None` 时也把 `not verify_required` 纳入 `is_available`；补一条无 status 的单测。

### P2-10 重新导入同一账号会静默清掉 verify_required

- 位置：`account_store.py:936-971`
- 现象：`save_account` 重建 `AccountMeta` 时只继承 tier/group/cookie_id 等，不继承 `verify_required`（也不继承 expired）；重新导入同一账号 Cookie 会把本地 verify 锁清掉，随后请求会再次撞 403，形成一次无效重试。
- 建议：明确产品语义。若 verify 是账号级上游锁，重新导入应保留；若导入即代表用户已重新验证，则应在 UI 明确提示“重新导入会清除扫码锁”。补单测固化选择。

### P2-11 `simulate` 参数暴露在生产 status 路由

- 位置：`routes_accounts.py:979-994`；`verify_account_service.py:449-456`
- 现象：生产服务 `del simulate` 忽略，沙盒才消费；但生产 API 契约仍暴露测试参数，易被误用/误解。
- 建议：仅 ui_debug 路由暴露 `simulate`，或在路由层用 `runtime_state.ui_debug` 拒绝非沙盒的 simulate。

### P2-12 截图早于最终源码与 static build

- 位置：`docs/dev/rebuild/domains/P1-account/reviews/assets/verify-*.png`
- 现象（只读时间戳）：
  - `verify-list-1440x900.png` / pending 截图 03:15–03:17；
  - `VerifyAccountModal.vue` 03:21:19；
  - `static/assets/AccountsView.js` 03:21:42。
  截图不是最终源码/构建产物生成的，不能作为最终 UI 验收证据（虽然可见文案与当前源码一致）。
- 建议：在最终 build 后按 1440/1024/768 重新跑 UI debug 沙盒并覆盖截图；Reviewer 本轮按只读纪律未启动服务/浏览器。

### P2-13 关闭弹窗时若扫码已完成但尚未轮询，锁会保留到下次验证（设计取舍）

- 位置：`VerifyAccountModal.vue:305-323`；`verify_account_service.py:469-479`
- 现象：status 只由调用推进；关闭必 cancel。若用户扫码成功后在 2.5s 轮询间隙关闭，会话被 cancel，`verify_required` 保持 true，账号不会自动恢复。
- 评价：与“无后台轮询/关闭必取消”的约束一致，可接受，但建议在关闭前做一次可选的 final status 检查（不改变“无后台循环”），或在弹窗文案中明确“关闭将取消本次验证，若已扫码请等待状态刷新”。

## 4. Q/N

- **Q-1（部署前置）**：真实 CloakBrowser/Termux 下 reCAPTCHA Enterprise 是否给 headless 下发 QR、`document.cookie` 的 SAPISID 是否可读、clients6 CORS 是否放行、`Verify device` 真实 DOM 是否与 bundle 证据一致，均未实测；按当前规则不能访问真实账号/VPS。这些是上线前必须由用户授权的一次性实机验证项，不能由 fake driver 测试替代。
- **Q-2（出口）**：`AISTUDIO_VERIFY_PROXY` 默认空即直连；若 VPS 直连 Google 触发风控，需要用户决定是否显式配置 10809/专用出口。当前实现符合“默认直连”的审查口径，但与仓库外部网络代理约束存在张力。
- **Q-3（关闭语义）**：是否接受“扫码成功后关闭弹窗会保留 verify 锁，直到再次打开验证”？若不可接受，应在关闭前增加一次 final status 检查，或延迟 cancel 数秒。
- **Q-4（研究结论）**：research 建议后台自动取码默认不做；本实现是用户显式点击的单会话，仍会每次刷新冷启动 Chromium。是否进一步限制“刷新二维码复用同一 page/上下文”以降低 §4.6 与内存风险，需要产品决策。
- **Q-5（产物）**：static 已由 worker 构建，本轮只读核对未 rebuild；截图需重拍后才能作为最终 UI 证据。
- **Q-6（提交边界）**：工作树含上一轮 exit-pool 未提交改动；本步提交必须只暂存 verify-account 相关文件，不能把 exit-pool/REV-2.1.5.8/2.1.5.9 混入。

## 5. 实跑门禁输出

在仓库根目录执行：

```text
$ uv run ruff check .
All checks passed!

$ uv run pyright src tests
0 errors, 0 warnings, 0 informations

$ $env:PYTHONUTF8='1'; $env:PYTHONIOENCODING='utf-8'; uv run pytest -q
1847 passed in 54.23s

$ cd web && bun run lint
$ bun --bun x eslint src --max-warnings 0
(exit 0；无 error/warning 输出)

$ cd web && bun run type-check
$ bun --bun x vue-tsc -b
(exit 0；无 error 输出)

$ cd web && bun test
bun test v1.3.14 (0d9b296a)
102 pass
0 fail
843 expect() calls
Ran 102 tests across 11 files. [142.00ms]
```

- **未执行**：`bun run build`（只读约束，禁止写 `src/aistudio_api/static/`）。
- **只读核对**：static 产物含 verify 文案、qrcode、verify API；`index.html` 资源哈希已更新；未 rebuild。
- **截图**：文件存在，但时间戳早于最终源码/build（P2-12）。

## 6. 只读声明

本轮审查为 100% 只读：

- 未修改任何源码、测试、配置、迁移、静态产物；
- 未执行 `git add` / `git commit` / `git checkout` / `git merge` / `git push`；
- 未启动真实服务、真实浏览器或 UI debug 服务；
- 未访问 VPS、外网、真实 Google 账号；
- 未执行 `bun run build`（会写 static）；
- 唯一写入：本文件 `docs/dev/rebuild/domains/P1-account/reviews/REV-2.1.5.10-verify-account-flow.md`。

审查完成时间：2026-11-22。

---

# 二轮复审（返工复核，2026-11-22 20:14 UTC）

- 复审对象：本轮对 P0-1、P1-1/2/3 与 P2 清单的返工
- 复审方式：100% 只读；逐文件核对 diff、测试断言、static 只读内容与截图 mtime
- 二轮结论：**CONDITIONAL**
- 说明：P0-1、P1-1、P1-3 已修复；P1-2 仍残留取消路径的锁等待/清理竞态，P2 仍有若干项未闭环，因此不能写“可进入部署联调阶段”。

## 7. 二轮逐项复核

### 7.1 P0-1 点击 disabled / 后续重试

- **代码通过**：`_CLICK_VERIFY_DEVICE_JS` 已加入 `usable(el) = !el.disabled && aria-disabled !== 'true'`，只对 enabled 元素点击，没找到可用元素返回 false（`verify_account_service.py:107-126`）。
- **行为通过**：`_wait_for_qr` 在点击失败时保持 `last_click_at=0`，下一轮 0.5s 继续尝试；只有 `_read_bframe_qr` 真正解析出 QR 才返回成功（`:952-975`）。
- **测试部分通过**：`test_wait_for_qr_retries_disabled_click_until_enabled` 验证“首次 false → 后 true → 取到 QR”（`tests/unit/test_verify_account_service.py:612-621`），但 fake 是“按调用次数返回 false/true”，**没有执行真实 JS，也没有断言 `disabled`/`aria-disabled` 过滤**；把 JS 的 usable 过滤删掉该测试仍会通过。残留 P2-R4。

### 7.2 P1-1 清锁持久化 / store 权威

- **通过**：`_clear_verify_required` 调用 `record_verify_cleared`（`verify_account_service.py:652-669`），后者立即 `save_state()`（`account_rotator.py:1148-1166`）。
- **通过**：`AccountRotator.__init__` 在 load_state 后用 store 覆盖 `verify_required`，旧 `true` 不再回潮（`account_rotator.py:590-604`）。
- **测试真实**：`test_verify_clear_persists_across_rotator_reload` 先断言落盘 false，再人为写回 stale true 并重建 rotator，断言 `get_all_stats`/`is_auth_locked` 均为 false（`tests/unit/test_verify_account_service.py:475-514`）。
- **残留**：重新导入的运行时路径仍未清 rotator 内存锁，见 P2-R3。

### 7.3 P1-2 start/cancel 锁与竞态

- **start 主体通过**：锁内只建 session/占位与 driver，`driver.start()` 在锁外执行（`verify_account_service.py:360-433`）；取消事件可中断启动（`:542-572`）；异账号并发 409 有真实测试（`tests/unit/test_verify_account_service.py:453-471`）。
- **cancel 基础通过**：cancel 在锁内只改状态/取 driver，teardown 在锁外（`:542-572`）；`test_cancel_during_start_aborts_launch_and_cleans_up` 断言 <2s（`:424-450`）。
- **残留 P1-R1**：`status()` 在持有全局锁期间执行 `driver.poll()` 与 `confirm_verified()`（`:530-539,626-647`，RPC 超时 8s）；`cancel()` 需要同一把锁（`:545`）；`driver.close()` 的 `Page.close` 默认 30s 超时（`cdp_client.py:212-217,792-801`），`_terminate_process` 还有 3s+2s（`verify_account_service.py:1153-1210`）。前端 `closeModal()` 还会无超时地 `await finalStatusCheck()`（`VerifyAccountModal.vue:343-359`）。因此在 confirm 正在执行或页面无响应时，关闭/cancel 不可能稳定 ~2s 返回。详见 P1-R1。
- **残留 P2-R6**：`asyncio.CancelledError` 分支（`:445-449`）直接 re-raise，未清 `_session`/`_driver`，无过期 timer；若 start 协程被客户端断开取消，session 会永久停在 STARTING，后续同账号 start 直接复用该僵死 session。需补 CancelledError 收尾测试。

### 7.4 P1-3 Chrome 路径

- **通过**：`_resolve_binary` 优先 env，否则 `find_chromium_executable()`，因此复用 `.cloakbrowser/**/chrome` 与 Termux `run-chrome.sh` wrapper（`verify_account_service.py:791-807`）；503 文案列出三种布局（`:1068-1078`）；测试 `test_verify_chrome_env_override_wins` / `test_verify_chrome_missing_hint_lists_all_three_layouts`（`tests/unit/test_verify_account_service.py:380-399`）。
- **默认直连通过**：`_proxy` 为空时不追加 `--proxy-server`（`:1091-1112`）。

### 7.5 P2 清单

| 项 | 结果 | 证据 |
|---|---|---|
| bframe isolated world 重建 | 通过 | `:1014-1065`；`test_bframe_world_is_rebuilt_after_context_invalidation`（`tests/unit/test_verify_account_service.py:624-633`） |
| 页面失败 → failed | 通过 | `_FAILURE_MARKERS` 在 `:96-102`，`_is_failure_page`，status 映射 `:620-622`；测试 `:517-540` |
| 轮询在途保护 | 通过 | `VerifyAccountModal.vue:58,295-319`；`pollInFlight` |
| 状态确认改后端直连 RPC | 通过（有残留风险） | `verify_account_service.py:862-920`；SAPISIDHASH、Firefox 152 UA、8s 超时、200[2]/[1]/401/403 分类已实现；残留 P2-R1/R2 |
| `--mute-audio` | 通过 | `:1098-1110` |
| Windows taskkill 树 | 通过（单测级） | `_taskkill_tree_windows`（`:1138-1170`）；`test_windows_terminate_kills_tree_before_root_exit`（`:636-650`） |
| 删除账号取消会话 | 通过 | 单删/批删/组删均调用 `_cancel_verify_for_deleted_account`（`routes_accounts.py:1047-1056,1087-1097,1124-1142`）；路由测试 `:242-254` |
| AccountRow 隐藏清除锁定 | 通过 | `AccountRow.vue:443` 增加 `!isVerifyRequired`，title 明确“不解除扫码验证” |
| status=None 尊重 verify | 通过 | `routes_accounts.py:296-305`；测试 `test_account_response_without_status_respects_verify_lock` |
| 重导入清锁语义与测试 | 部分通过 | store 层测试 `test_reimport_same_account_clears_verify_lock_explicitly`（`tests/unit/test_runtime_persistence.py:170-201`）通过；运行时 rotator 未同步，见 P2-R3 |
| 生产忽略 simulate | 通过 | `routes_accounts.py:1006-1024` 仅在 `get_ui_sandbox` 时传入；测试 `test_production_status_ignores_simulate` |
| 关闭前 final status | 通过（有锁等待风险） | `VerifyAccountModal.vue:343-364`；无超时/abort，放大 P1-R1 |

### 7.6 新风险复核（状态确认后端直连 RPC）

- **同出口一致性**：显式 `AISTUDIO_VERIFY_PROXY` 时浏览器与 httpx 都走该 proxy；但 `httpx.AsyncClient(..., proxy=self._proxy)` 默认 `trust_env=True`（`verify_account_service.py:897-899`）。当 `AISTUDIO_VERIFY_PROXY` 为空而宿主设置了 `HTTPS_PROXY`/`ALL_PROXY` 时，浏览器直连、RPC 却走环境代理，**不同出口**。残留 P2-R1。
- **401 分类**：401 统一判 `session_invalid`（`:904-905`）；若因时间偏移导致 SAPISIDHASH 失效或瞬时鉴权失败，会误判为登录失效并关闭验证页。风险中低。
- **403 分类**：403 统一判 `region_blocked`（`:906-907`）；真实环境中的 403 也可能是权限、配额、账号未验证或 API key 限制。会给出“地区不支持”的误导结论并提前结束会话（不清锁）。残留 P2-R2，建议只对 `available-regions`/`not available in your country` 类 body 判 region，其余保持 pending/error。
- **API 契约**：start/status/cancel 的 200/404/409/503/401 均有路由测试且通过；start 在 STARTING 期间对异账号仍 409（`test_concurrent_start_for_other_account_conflicts_while_starting`）。未发现契约破坏。

### 7.7 截图 mtime 对照

| 文件/源 | mtime |
|---|---|
| `AccountRow.vue` | 2026-11-23 03:47:15 |
| `VerifyAccountModal.vue` | 2026-11-23 03:47:46 |
| verify-list/pending/passed（9 张） | 2026-11-23 04:00:30 – 04:00:43 |
| `web/src/api/system.ts`（exit-pool） | 2026-11-23 04:06:04 |
| `web/src/views/SettingsView.vue`（exit-pool） | 2026-11-23 04:06:23 |
| `static/assets/AccountsView.js` / `index.html` | 2026-11-23 04:08:31 |

- 文件齐全：3 分辨率 × list/pending + passed = 9 张。
- verify 专属源码（03:47）早于截图（04:00），因此截图覆盖了最终 verify UI 源码；但截图早于最后 static build（04:08）。04:06 的源码变更属于 exit-pool UI，未改 verify 组件。若按“截图必须晚于最终 static”的字面门禁，仍不满足，需在最终 build 后重拍或由 integrator 单击说明（残留 P2-R7，证据 hygiene，不影响 verify 视觉）。

### 7.8 实跑门禁（二轮）

```text
$ uv run ruff check .
All checks passed!

$ uv run pyright src tests
0 errors, 0 warnings, 0 informations

$ $env:PYTHONUTF8='1'; $env:PYTHONIOENCODING='utf-8'; uv run pytest -q
1868 passed in 50.91s

$ cd web && bun run lint
$ bun --bun x eslint src --max-warnings 0
(exit 0；无 error/warning 输出)

$ cd web && bun run type-check
$ bun --bun x vue-tsc -b
(exit 0；无 error 输出)

$ cd web && bun test
bun test v1.3.14 (0d9b296a)
103 pass
0 fail
844 expect() calls
Ran 103 tests across 11 files. [126.00ms]
```

- **未执行**：`bun run build`（只读约束，禁止写 `src/aistudio_api/static/`）。
- **static 只读核对**：static 产物含 `verify/start|status|cancel`、`扫码验证`、`需扫码验证`、`qrcode`；`index.html` 资源哈希已更新；未 rebuild。
- `git diff --check` 仅报告 `static/index.html` 的 CRLF/trailing-whitespace 与 `web/tests/systemApi.test.ts` CRLF 警告，属产物换行差异，不影响验证。

## 8. 二轮残留 P0/P1/P2

### P1-R1（残留）cancel/关闭仍可能被 status/confirm 与 close 阻塞，非稳定 ~2s

- 位置：`verify_account_service.py:530-539,626-647,542-572,671-699`；`cdp_client.py:212-217,792-801`；`VerifyAccountModal.vue:343-359`。
- 现象：
  - `status()` 持全局锁执行 `driver.poll()`（页面 snapshot，最长约 5s）与 `confirm_verified()`（httpx 8s）。
  - `cancel()` 必须等同一把锁；`_finish_locked()`/`close()` 还在锁内 `await driver.close()`。
  - `driver.close()` 的 `Page.close` 默认 30s 超时；进程清理另有 3s+2s；前端 `finalStatusCheck()` 无超时。
  - 因此“扫码成功瞬间点关闭/取消”可能先等 8s final status，再等 8s confirm 释放锁，再等 close；真实 Chrome 页面卡死时可达数十秒。cancel 的 HTTP 响应不满足 ~2s，关闭弹窗也会被 finalStatusCheck 拖住。
- 复现：让 `FakeDriver.confirm_verified` sleep 8s，进入 pending 且页面 success；点击关闭并同时调用 cancel；观察 finalStatusCheck/cancel 延迟。或使用不回 `Page.close` 的 CDP 页面。
- 建议：
  - 状态确认不要持全局锁，改为每 session 状态锁 + 取消事件；cancel 置事件后立即返回 `{"state":"cancelled"}`，driver 关闭放到后台 task（有界超时、日志）；
  - `finalStatusCheck` 加 1~2s AbortController 超时；
  - `_finish_locked`/`close` 的 driver.close 移出全局锁；
  - 增加“慢 confirm + cancel <2s”和“慢 Page.close + cancel 返回 cancelled”测试。

### P2-R1（残留）后端 RPC 默认可能走环境代理，与浏览器直连不一致

- 位置：`verify_account_service.py:897-899`。
- 现象：`httpx.AsyncClient(..., proxy=self._proxy)` 默认 `trust_env=True`；`AISTUDIO_VERIFY_PROXY` 为空时，若宿主有 `HTTPS_PROXY`/`ALL_PROXY`，RPC 会走环境代理，而 Chrome 仍直连。用户要求的是“同出口”。
- 复现：设置 `$env:HTTPS_PROXY='http://127.0.0.1:10809'`，不设 `AISTUDIO_VERIFY_PROXY`，对 RPC 侧抓包/打日志；浏览器仍直连。
- 建议：`httpx.AsyncClient(..., proxy=self._proxy, trust_env=False)`；增加断言测试（检查构造参数或 mock httpx client）。

### P2-R2（残留）401/403 分类过粗，可能误判

- 位置：`verify_account_service.py:904-907`。
- 现象：所有 401→session_invalid、所有 403→region_blocked；403 也可能是权限/配额/API key/账号状态，导致错误文案并提前结束会话（不会误清锁）。
- 复现：mock RPC 返回 403 + `{"error":"permission"}`，观察会话进入 region_blocked。
- 建议：仅当 body 命中 `available-regions`/`region not supported`/`not available in your country` 才判 region；401 仅在明确 `unauthenticated`/`invalid credentials` 时判 session_invalid；其余 4xx 保持 pending/error 并记录 status code。补 200[2]/[1]/401/403 的 RPC 单元测试。

### P2-R3（残留）重新导入清 store 未清 rotator 内存锁

- 位置：`routes_accounts.py:1501-1512`；`account_rotator.py:590-604,668-684,1165-1179,1188-1193`；`account_store.py:912-980`。
- 现象：`save_account` 重建 AccountMeta（`verify_required` 默认 false）并有 store 层测试；但导入路由只 `rotator.add_account` + `clear_cooldown`，后者只清 429/session，不清 `verify_required`；`get_account_stats` 也不会用 store=false 清内存 true。结果是“重新导入成功但 UI/调度仍显示需扫码验证，直到重启或再次扫码”。
- 复现：账号 verify 锁 → 重新导入同账号 Cookie → `/api/accounts` 仍 verify_required=true；重启后因 `__init__` reconcile 才变 false。
- 建议：导入路径检测 store 的 verify 从 true→false 时调用 `record_verify_cleared`（或在 `add_account`/`clear_cooldown` 中 reconcile store false）；补“reimport 后同进程 stats=false”的测试。

### P2-R4（残留）P0-1 测试未真正执行 disabled 过滤

- 位置：`tests/unit/test_verify_account_service.py:587-621`。
- 现象：`_FakeQrPage.evaluate` 仅按 `click_attempts` 返回 false/true，不解析 JS；删除 `_CLICK_VERIFY_DEVICE_JS` 的 `usable` 过滤后测试仍绿。它验证了 driver 的重试状态机，但没有验证真实 DOM/JS 的禁用判断。
- 建议：用 JSDOM/浏览器沙盒执行该 JS 的两个按钮（disabled + enabled），断言只点击 enabled；或至少断言 JS 源包含 `disabled`/`aria-disabled` 且 `match` 分支只在 usable 时返回 true。补一个“disabled 按钮点击不计数”的 fake 更贴近真实。

### P2-R5（残留）低内存/指纹参数仍未复用

- 位置：`verify_account_service.py:1091-1112`；对比 `browser_engine.py:148-227`。
- 现象：本轮加了 `--mute-audio`，但仍缺 `--renderer-process-limit=1`、`--in-process-gpu`、`--js-flags=--max-old-space-size=128`、`--fingerprint-platform=windows`、`--fingerprint-timezone`。实例池同时运行时额外一套完整 Chromium 会放大 RSS，且指纹差异更易被识别。
- 建议：复用 `build_chromium_args`（独立 user-data-dir/port/proxy）或至少补齐 §4.7 低内存与指纹参数；在 VPS 采样 RSS 后定值。

### P2-R6（残留）start 协程被取消时留下 STARTING 会话/无 timer

- 位置：`verify_account_service.py:445-449,472-499`。
- 现象：`except asyncio.CancelledError` 取消 start_task 后直接 re-raise，不清 `_session`/`_driver`，也不置 CANCELLED/调度清理；若客户端断开导致 ASGI 取消 handler，session 永久 STARTING，同账号 start 会复用该僵死 session；driver 引用可能保留已关闭对象。
- 复现：`_SlowDriver` 启动后取消 `service.start` task；再调用 `service.start("acc_1")`，返回 STARTING；`status` 永远 STARTING。
- 建议：`except CancelledError` 中加锁把 session 置 CANCELLED、清 driver、调度 cleanup（或用 `finally` 统一收尾）；补取消协程测试。

### P2-R7（残留，证据）截图早于最终 static

- 位置：`docs/dev/rebuild/domains/P1-account/reviews/assets/verify-*.png`。
- 现象：verify 源码 03:47 < 截图 04:00 < static 04:08；04:06 的变更属 exit-pool UI（SettingsView/system.ts），未改 verify 组件。按“截图必须晚于最终 static”的字面要求不满足。
- 建议：最终 build 后重拍 9 张（3 分辨率 × list/pending + passed）；若 integrator 接受“verify 源码未变”的说明，需在提交说明中写明。

### P2-R8（残留）后端 RPC 无直接测试

- 位置：`verify_account_service.py:862-920`；现有 service 测试只用 `FakeDriver.confirm_verified`。
- 现象：SAPISIDHASH、200[2]/[1]、401/403、JSON 形状、超时/代理参数均无单元断言；真实环境风险只能靠部署联调发现。
- 建议：用 `httpx.MockTransport` 或 monkeypatch 覆盖 200 标量/嵌套、401、403、超时、非 JSON；同时断言 `trust_env=False` 与 proxy 传递（配合 P2-R1）。

## 9. 二轮 Q/N

- **N-1**：真实 CloakBrowser/Termux 的 reCAPTCHA headless 行为、QR 实际下发、SAPISID 可读性、clients6 的 401/403 语义仍未实测；本轮只读规则下无法访问真实账号/VPS。即使代码修复完成，部署联调仍需用户授权的一次性实机验证。
- **N-2**：`AISTUDIO_VERIFY_PROXY` 默认空（直连）符合本轮口径；但默认直连会暴露 VPS IP，是否上线需结合 Google 风控策略决策。
- **N-3**：截图字面门禁未满足（P2-R7）；verify 源码本身未在 03:47 后变更，视觉风险低。
- **N-4**：工作树仍含上一轮 exit-pool 未提交改动；本步提交需按文件域拆分，不能混入 `REV-2.1.5.8/2.1.5.9` 与 `exit_pool/`。
- **Q-1**：是否接受“关闭弹窗最多等待一次 final status（8s）”？若不接受，必须给 finalStatusCheck 加超时并让 cancel 立即返回。
- **Q-2**：403 的 region 判定是否要保守到只认 body marker？建议是，否则误报“地区不支持”会误导排障。
- **Q-3**：验证浏览器是否复用主引擎的完整 args？若担心内存，可仅复用低内存/指纹子集。

## 10. 二轮只读声明

本轮仍为 100% 只读：

- 未修改任何源码、测试、配置、迁移、静态产物；
- 未执行 `git add` / `git commit` / `git checkout` / `git merge` / `git push`；
- 未启动真实服务、真实浏览器或 UI debug 服务；
- 未访问 VPS/外网/真实 Google 账号；
- 未执行 `bun run build`；
- 唯一写入：本文件末尾追加的「二轮复审」章节。

二轮复审完成时间：2026-11-22 20:14 UTC。

---

# 三轮复审（P1-R1 + P2-R1..R8 返工复核，2026-11-22 20:53 UTC）

- 复审角色：独立只读 Reviewer
- 复审方式：逐文件核对本次返工 diff、测试断言、static 只读产物、截图 mtime/尺寸；实跑门禁
- 三轮结论：**CONDITIONAL**
- 说明：P1-R1 与 P2-R1/R2/R3/R4/R6/R7/R8 已闭环；无 P0、无 P1。仅剩 **1 个 P2（P2-R5 验证浏览器参数未完全对齐 `browser_engine`）** 与若干非阻塞 N 项，因此不写“可进入部署联调阶段”。

## 11. P1-R1 复核：cancel 响应与资源清理

- **cancel 响应预算：通过**
  - `status()` 在全局锁内只做查找，释放锁后才拿 per-session lock 执行 poll/confirm（`verify_account_service.py:579-614`）。
  - `cancel()` 只持有全局锁做状态/取 driver，随后把 teardown 交给后台 task，立即返回 `cancelled`（`:616-646`）。
  - `_finish()` 走 session lock → global lock（`:806-816`）；`cancel`/`start`/timer 只取 global，不存在 global→session 的反向加锁，未发现死锁路径。
  - 测试：`test_cancel_returns_within_budget_while_confirm_is_slow`（8s confirm，cancel <2s，status 返回 cancelled，driver 最终关闭，`:717-741`）；`test_slow_driver_close_does_not_block_cancel`（`:765-784`）；`test_cancel_during_start_aborts_launch_and_cleans_up`（`:435-462`）。
- **driver 关闭顺序：通过**
  - 先 `_terminate_process`（Windows taskkill /T 先于根进程退出；Linux SIGTERM→SIGKILL），再 `Page.close`（`wait_for 2s`），再 `client.close`（2s），最后 `rmtree`（`:1084-1110,1328-1354`）。
- **finalStatusCheck：通过**
  - `FINAL_STATUS_TIMEOUT_MS=1500`，AbortController 1.5s 中止；API `getVerifyStatus` 的 signal 已透传到 fetch（`VerifyAccountModal.vue:38,344-362`；`web/src/api/accounts.ts:161-171`；`web/src/api/client.ts:54-57`）。
- **后台清理有界：通过（有 N-3 记录）**
  - `_close_driver_bounded` 用 `wait_for(driver.close(), 5s)` 兜底（`:675-681`）；失败只记录 warning，不会阻塞 cancel。
  - 备注：`driver.close` 内部理论上限约 2.5s（进程）+2s（Page）+2s（client）=6.5s > 5s；由于进程先被杀，Page/CDP 通常立即失败，实际风险低。若 5s 兜底触发，临时目录可能残留；见 N-3。

## 12. P2-R1..R8 复核

### P2-R1 httpx 出口

- **通过**：`httpx.AsyncClient(..., proxy=self._proxy, trust_env=False)`（`:1047-1053`），环境 `HTTPS_PROXY/ALL_PROXY` 不再影响 RPC；显式 `AISTUDIO_VERIFY_PROXY` 仍透传。
- **测试**：`test_confirm_rpc_client_disables_env_proxy_and_forwards_explicit_proxy` 断言 `trust_env is False`、`proxy` 透传、`transport is None`（`tests/unit/test_verify_account_service.py:932-962`）。

### P2-R2 401/403 分类

- **通过**：仅当 401 body 命中 `_SESSION_INVALID_401_MARKERS` 才 `session_invalid`，否则保持 pending；仅当 403 body 命中 `_REGION_403_MARKERS` 才 `region_blocked`，否则 pending（`verify_account_service.py:1060-1072`）。
- **测试覆盖**：200[2]/[1]/嵌套（`:864-874`）、401 有/无 marker（`:876-893`）、403 region/其它（`:896-915`）、超时/非 JSON（`:918-929`）。分类符合“无 marker 不提前结束”的要求。

### P2-R3 重导入清 rotator 内存锁

- **代码通过**：`reconcile_verify_cleared` 在 store 已 false 时清内存 flag（`account_rotator.py:1165-1181`）；三个导入入口都调用 `_reconcile_verify_after_import`（`routes_accounts.py:1517-1520,1598-1603,1800-1804`）。
- **测试通过（单入口）**：`test_reimport_same_process_clears_rotator_memory_verify_lock` 直接调用 `import_cookies`，断言同进程 `stats.verify_required=False`、`is_auth_locked=False`，不依赖重启（`tests/unit/test_verify_account_service.py:966-998`）。
- 备注：`probe_and_import` 与 `import_bundle` 两个入口目前只有代码覆盖，没有针对 verify 清锁的直接用例；见 N-2（建议补参数化测试，非功能性残留）。

### P2-R4 disabled 过滤测试

- **通过（按约定的 JS 源 + 纯函数 + fake 状态机三层）**：
  - 判定抽为 `_VERIFY_BUTTON_USABLE_PREDICATE` 与 `_verify_button_usable`（`:130-142`）；
  - JS 两个候选分支均 `usable(el)`，`if (!match) return false` 在 `match.click()` 之前（`:144-162`）；
  - `test_click_verify_device_js_filters_disabled_buttons` 断言 JS 源与顺序（`tests/...:632-641`），`test_verify_button_usable_rejects_disabled_nodes` 断言纯函数（`:644-649`），`test_wait_for_qr_retries_disabled_click_until_enabled` 用 fake 首次 disabled → 后 enabled → 取到 QR（`:652-661`）。
- **明确边界**：这不是 JSDOM/真实浏览器执行，JS 源断言本身不等于 DOM 行为；本轮结论不把它表述为“真实 DOM 已验证”。真实页面仍需部署联调（N-1）。

### P2-R5 低内存/指纹参数

- **部分通过（残留 P2）**：
  - 已加入 `--renderer-process-limit=1`、`--in-process-gpu`、`--js-flags=--max-old-space-size=128`、`--fingerprint-platform=windows`、`--fingerprint-timezone={settings.timezone}`、`--mute-audio`（`:1258-1288`）。
  - 仍未与 `browser_engine.build_chromium_args` 完全一致：缺 `--fingerprint-locale`（`browser_engine.py:224-227`）、`--use-gl=angle`/`--use-angle=swiftshader`、`--no-zygote`、`--disable-gpu-compositing`、`--disable-features=...` 等（`browser_engine.py:155-186`）；`--js-flags` 也少了 `--expose-gc --optimize-for-size`。
  - 风险：headless 无 GPU 宿主上缺少 SwiftShader 显式参数可能影响 canvas/二维码渲染；`fingerprint-locale` 与池内实例不一致。详见第 13 节 P2-R5。
- **测试**：`test_verify_chrome_command_has_low_memory_and_fingerprint_args` 已验证五个目标参数与 headless/mute-audio（`tests/...:817-833`），但未覆盖上述缺失项。

### P2-R6 start 协程取消

- **通过**：`except asyncio.CancelledError` 会取消 start_task，并调用 `_abort_start` 置 CANCELLED、清 driver、调度后台关闭（`:487-499,565-577`）；`_finish_locked` 的 terminal guard 防止重复收尾。
- **测试**：`test_cancelling_start_coroutine_marks_cancelled_and_frees_driver` 断言取消后 session=CANCELLED、driver 关闭、随后同账号能重新 PENDING（`:786-814`）。

### P2-R7 截图

- **通过**：9 张齐全（3 分辨率 × list/pending + passed），PNG 实际尺寸均正确。
- mtime 对照（均为 2026-11-23）：

| 对象 | mtime |
|---|---|
| `VerifyAccountModal.vue` | 04:29:27 |
| `static/index.html` / `AccountsView.js` | 04:45:54 |
| `verify-list-*.png` | 04:46:26 – 04:46:39 |
| `verify-modal-pending-*.png` | 04:46:52 – 04:47:05 |
| `verify-modal-passed-*.png` | 04:47:18 – 04:47:30 |

9 张截图均晚于最终 static。尺寸：
`verify-list-768/1024/1440x900`、`verify-modal-pending-768/1024/1440x900`、`verify-modal-passed-768/1024/1440x900`。

### P2-R8 RPC MockTransport

- **通过**：`test_confirm_rpc_parses_scalar_nested_and_pending` 覆盖 `[2]`、`[1]`、`[[2]]`（`:864-874`）；401/403 有/无 marker（`:876-915`）；超时/非 JSON（`:918-929`）；构造参数断言 `trust_env=False`、显式 proxy（`:932-962`）。
- 未覆盖 200[3]（notRequired），但按要求“说明即可”，见 Q-3。

## 13. 新风险复核

### 13.1 per-session lock 与 global lock 死锁

- **未发现死锁**。加锁顺序为：
  - `status`：global（取引用）→ 释放 → session lock（`verify_account_service.py:579-614`）；
  - `_refresh`：session lock → `_finish` → global（`:714-746,806-816`）；
  - `start`/`cancel`/timer/`_dispose`：只取 global，从不取 session lock（`:413-473,616-655,848-904`）。
- 不存在 global→session 的持有链；`_dispose_session_locked` 取消 timer 时只等待被取消的 timer，timer 若在等 global 会被取消唤醒。设计可接受。

### 13.2 cancel 后后台关闭失败的可观测性

- **可观测但不完全可定位**：`_close_driver_bounded` 捕获异常后记录 `验证浏览器后台清理受限结束: <异常类型>`（`:675-681`）。
- 未带 session_id/account_id/pid，超时或进程树残留时排障无法直接定位。见 N-3（建议补充上下文，不是功能阻塞）。

### 13.3 200[3]（notRequired）仍 pending

- **按“只有 2 才清锁”的既定口径是合理的保守行为**：`confirm_verified` 对 200 只解析数值，`_refresh` 只在 `status == 2` 时清锁（`:783-785,1077-1082`）；3 会保持 pending 到 180s TTL。
- 代价：如果 3 表示“本次不需要验证”，用户会看到等待到过期；但不会误清锁/误恢复调度。建议后续如确认 3 的语义，可映射为独立终态 `not_required`（仍不清锁），不改变当前安全边界。记为 Q-3，非 P2。

### 13.4 start 取消与 cancel 并发幂等性

- **通过（代码级）**：cancel 只对 `_ACTIVE_STATES` 生效（`:625-640`），`_abort_start` 对已非 STARTING 的 session 只清/调度 driver（`:565-577`）；`_finish_locked` 的 `_TERMINAL_STATES` guard 防止重复状态写入（`:818-826`）。两次 cancel 均返回 cancelled；driver 关闭通过 `_schedule_driver_close` 只对仍有引用的 driver 触发，重复 close 由 `CdpVerifyBrowserDriver._closed` 守卫（`:1084-1087`）。
- 未发现 driver 泄漏/重复 close/僵死 STARTING；`test_cancelling_start...` 与 `test_cancel_during_start...` 覆盖主要路径。并发“同时 cancel + 同时取消 start 协程”没有专门测试，见 N-4。

### 13.5 API 契约回归

- start/status/cancel 的 200/404/409/503/401 契约测试仍通过；start 在 STARTING 期间对异账号 409（`tests/unit/test_verify_account_service.py:465-483`）。P1-R1 重构未破坏契约。

## 14. 三轮残留 P0/P1/P2

- **P0：无。**
- **P1：无。**
- **P2-R5（残留，小）：验证浏览器参数未完全对齐 `browser_engine`**
  - 位置：`src/aistudio_api/application/verify_account_service.py:1258-1288`；对照 `src/aistudio_api/infrastructure/browser/browser_engine.py:148-227`。
  - 复现：检查 `_build_verify_chrome_command` 返回列表，缺少 `--fingerprint-locale`、`--use-gl=angle`、`--use-angle=swiftshader`、`--no-zygote`、`--disable-gpu-compositing`、`--disable-features=...`；`--js-flags` 也少于 canonical。
  - 影响：无 GPU 的 Linux/Termux 宿主上，QR canvas 渲染依赖默认 SwiftShader 回退，建议显式对齐；`fingerprint-locale` 与池内实例不一致会扩大指纹差异。
  - 建议：复用 `build_chromium_args(..., extra_args=[...])` 或把共享 subset 抽成常量；若坚持手工列表，补齐上述低内存/渲染/指纹参数并补测试断言。其余行为已验证的 headless/mute-audio/user-agent/lang 不应回退。

## 15. 三轮 Q/N

- **N-1（实机）**：真实 CloakBrowser/Termux 下 reCAPTCHA headless 是否下发 QR、`document.cookie`/SAPISID 的实际可读性、clients6 RPC 的 200/401/403 真实语义仍未实测；本轮只读规则下无法访问真实账号/VPS。部署联调需用户授权的一次性实机验证。
- **N-2（测试覆盖）**：P2-R3 的三个导入入口代码均覆盖；直接 verify 清锁测试目前只覆盖 `import_cookies`，建议补 `probe_and_import` 与 `import_bundle` 的参数化同进程用例。
- **N-3（运维可观测性）**：后台 driver 关闭失败只记录异常类型，建议带 session_id/account_id/pid；`DRIVER_CLOSE_TIMEOUT_SECONDS=5.0` 小于 `driver.close` 理论 6.5s 上限，若 5s 兜底触发，进程已被杀但临时目录可能残留。可把 `rmtree` 放到 `finally` 或设为 7s。
- **N-4（并发测试）**：start 取消与 cancel 的“同时触发”没有专门用例；代码路径幂等，建议补一个双任务并发测试固化。
- **N-5（前端）**：finalStatusCheck 的 1.5s AbortController 只做了源码/static 核对，没有组件级测试；现有 web 测试是纯工具测试。建议加一个组件/请求层用例。
- **Q-1**：是否接受 200[3] 继续 pending 到 180s TTL？若 3 确认为“不需要验证”，可映射独立终态但不自动清锁。
- **Q-2**：`fingerprint-locale` 与渲染参数是否作为本步 P2-R5 的验收项补齐？按字面“与 browser_engine 一致”目前未完全满足。

## 16. 三轮实跑门禁

```text
$ uv run ruff check .
All checks passed!

$ uv run pyright src tests
0 errors, 0 warnings, 0 informations

$ $env:PYTHONUTF8='1'; $env:PYTHONIOENCODING='utf-8'; uv run pytest -q
1884 passed in 53.64s

$ cd web && bun run lint
$ bun --bun x eslint src --max-warnings 0
(exit 0；无 error/warning 输出)

$ cd web && bun run type-check
$ bun --bun x vue-tsc -b
(exit 0；无 error 输出)

$ cd web && bun test
bun test v1.3.14 (0d9b296a)
103 pass
0 fail
844 expect() calls
Ran 103 tests across 11 files. [129.00ms]
```

- **未执行**：`bun run build`（只读约束，禁止写 `src/aistudio_api/static/`）。
- **static 只读核对**：`static/assets/AccountsView.js` 已含 `finalStatusCheck`/`FINAL_STATUS_TIMEOUT_MS=1500`、`verify/start|status|cancel`、二维码/短码 UI；`index.html` 哈希已更新；未 rebuild。
- **截图**：9 张 mtime 全部晚于 static（见第 12 节 P2-R7），尺寸已用 System.Drawing 核对为 768/1024/1440 × 900。

## 17. 三轮只读声明

本轮仍为 100% 只读：

- 未修改任何源码、测试、配置、迁移、静态产物；
- 未执行 `git add` / `git commit` / `git checkout` / `git merge` / `git push`；
- 未启动真实服务、真实浏览器或 UI debug 服务；
- 未访问 VPS/外网/真实 Google 账号；
- 未执行 `bun run build`；
- 唯一写入：本文件末尾追加的「三轮复审」章节。

三轮复审完成时间：2026-11-22 20:53 UTC。

---

# 四轮确认（仅 P2-R5 + N-2/N-3 收尾，2026-11-22 21:04 UTC）

- 复审方式：100% 只读；只核对本轮 P2-R5、N-2、N-3 及回归
- 四轮结论：**PASS**
- **0 P0 / 0 P1 / 0 P2，可进入部署联调阶段。**

## 18. P2-R5 命令参数复核

- `_build_verify_chrome_command` 已改为复用 `build_chromium_args(port, user_data_dir, headless=True, proxy_url=proxy, extra_args=[user-agent, lang])`（`src/aistudio_api/application/verify_account_service.py:1300-1335`）。
- **与 canonical 一致**：`--renderer-process-limit=1`、`--in-process-gpu`、`--js-flags=--max-old-space-size=128 --expose-gc --optimize-for-size`、`--fingerprint-platform`、`--fingerprint-timezone`、`--fingerprint-locale`、`--use-gl=angle`、`--use-angle=swiftshader`、`--no-zygote`、`--disable-gpu-compositing`、`--disable-features=...` 均来自 `browser_engine.build_chromium_args`（`browser_engine.py:148-227`）。
- **remote-debugging-port**：保留 `--remote-debugging-port={port}`，这是读取 reCAPTCHA bframe isolated world 的必要条件，不是调试残留；canonical 本身也包含该参数。
- **窗口差异**：将 canonical 的 `--window-size=1280,800` 替换为已实证的 `1280,900`（`:1329-1332`），且测试断言只有 1 个 `--window-size=`；属于合理差异。
- **默认直连**：`AISTUDIO_VERIFY_PROXY` 为空时 `proxy=None`；`build_chromium_args` 的 `settings.proxy_url` 回退项被显式剔除（`:1333-1334`），测试 `test_verify_chrome_command_defaults_direct_despite_settings_proxy` 在 monkeypatch `settings.proxy_url` 后断言无 `--proxy-server`（`tests/unit/test_verify_account_service.py:876-886`）。
- **显式代理**：`proxy` 非空时由 builder 生成 `--proxy-server=...`，测试断言透传（`:889-896`）；RPC 侧仍使用同一 `self._proxy` 且 `trust_env=False`（`:1047-1053`）。
- 测试已断言 `--fingerprint-locale`、`--use-gl`、`--use-angle`、`--no-zygote`、`--disable-gpu-compositing`、`--disable-features` 及 headless/mute-audio（`:837-873`）。P2-R5 闭环。

## 19. N-3 收尾复核

- `DRIVER_CLOSE_TIMEOUT_SECONDS = 7.0`（`verify_account_service.py:90-93`），高于 `driver.close` 的约 6.5s 理论组合上限（进程 1.5+1.0、Page 2、client 2），健康 teardown 不会被外层提前打断。
- `CdpVerifyBrowserDriver.close` 在 `finally` 中执行 `shutil.rmtree(user_data_dir, ignore_errors=True)`（`:1148-1152`），并新增 `test_close_removes_temp_dir_even_when_teardown_times_out`，在 0.1s 外层取消下断言临时 profile 已删除（`tests/unit/test_verify_account_service.py:817-834`）。
- 后台失败日志：`_close_driver_bounded` 记录 `session/account/pid/error`（`:705-711`），pid 由 `_schedule_driver_close` 在 close 前捕获（`:671-689`；`pid` property `:989-993`）；仅 opaque id/异常类型，无 Cookie/邮箱/Authorization。N-3 闭环。

## 20. N-2 收尾复核

- `probe_and_import` 与 `import_bundle` 两个入口均调用 `_reconcile_verify_after_import`（`routes_accounts.py:1517-1520,1598-1603,1800-1804`）。
- 新增参数化测试 `test_reimport_entrypoints_clear_verify_lock_same_process`，分别覆盖 `probe_and_import` / `import_bundle`；`probe_and_import` 的 Google 探测被 mock，测试断言同进程内 store `verify_required=False`、rotator memory flag=False、`is_auth_locked=False`，不依赖重启（`tests/unit/test_verify_account_service.py:1064-1129`）。N-2 闭环。

## 21. 回归与门禁

```text
$ uv run ruff check .
All checks passed!

$ uv run pyright src tests
0 errors, 0 warnings, 0 informations

$ $env:PYTHONUTF8='1'; $env:PYTHONIOENCODING='utf-8'; uv run pytest -q
1889 passed in 52.35s

$ cd web && bun run lint
$ bun --bun x eslint src --max-warnings 0
(exit 0；无 error/warning 输出)

$ cd web && bun run type-check
$ bun --bun x vue-tsc -b
(exit 0；无 error 输出)

$ cd web && bun test
bun test v1.3.14 (0d9b296a)
103 pass
0 fail
844 expect() calls
Ran 103 tests across 11 files. [126.00ms]
```

- **未执行**：`bun run build`（只读约束，禁止写 `src/aistudio_api/static/`）。
- **static 未变**：`static/index.html` 与 `static/assets/AccountsView.js` mtime 仍为 2026-11-23 04:45:54，与三轮一致；本轮前端源码未改（`VerifyAccountModal.vue` 04:29:27），未 rebuild。
- **截图**：沿用三轮 9 张（768/1024/1440 × 900），mtime 04:46:26–04:47:30，均晚于 static；本轮未改前端故无需重拍。

## 22. 四轮 Q/N

- **N-1（部署联调）**：真实 CloakBrowser/Termux 下 reCAPTCHA headless 下发、二维码实际内容、SAPISID 可读性、clients6 的 401/403 真实语义仍需用户授权的实机验证；这是“部署联调阶段”的验证项，不是代码残留。
- **N-2（可选测试）**：`AISTUDIO_VERIFY_PROXY` 的 env→`CdpVerifyBrowserDriver._proxy` 路径没有单独用例（显式 `proxy` 参数与 builder 已测）；后台清理日志的字段没有 caplog 断言。均为可选加固，不影响本轮结论。
- **Q-1**：200[3]（notRequired）仍按“仅 2 清锁”保持 pending 到 180s TTL；这是保守口径，建议部署联调确认 3 的真实语义后再决定是否映射独立终态。
- **Q-2**：无。

## 23. 四轮只读声明

本轮仍为 100% 只读：

- 未修改任何源码、测试、配置、迁移、静态产物；
- 未执行 `git add` / `git commit` / `git checkout` / `git merge` / `git push`；
- 未启动真实服务、真实浏览器或 UI debug 服务；
- 未访问 VPS/外网/真实 Google 账号；
- 未执行 `bun run build`；
- 唯一写入：本文件末尾追加的「四轮确认」章节。

四轮确认完成时间：2026-11-22 21:04 UTC。
