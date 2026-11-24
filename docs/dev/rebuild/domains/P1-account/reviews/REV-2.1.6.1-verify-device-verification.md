# REV-2.1.6.1 — verify-account 设备验证 / Cookie 注入 / 出口探针归因 收口审查

- 审查对象：`2.1.6` 发布的全部代码改动（基线 `2.1.5` → 当前工作树，含未提交改动）
- 基线提交：`c5903efe3234c0bd807d768b2bad28b4c70ede6a`（`git rev-list -n1 2.1.5`）
- 被审 HEAD：`3a72af6c3312d37c57d484c756183f685bca187c`（+ 未提交工作树）
- 审查性质：**只读** Reviewer；除本文件外未修改任何源码/测试/配置/文档
- 结论：**CONDITIONAL**（代码可入库，但有一个提交完整性问题必须先处理，见 P1-1）
- **更新**：第 2 轮复核见文末「复核轮次（Round 2）」；新结论仍为 **CONDITIONAL**（P2-2/Q-1/Q-2/Q-3 已关闭，P1-1、P2-1、P2-3 保留，新增 P2-4）。

---

## 一、结论（首轮 Round 1）

**CONDITIONAL** — 代码逻辑、门禁、测试与安全口径整体达标，未发现阻断级（P0）缺陷；但 `2.1.6` changelog 与 `AGENT.md` 明确引用的两份新文档目前仍是 **untracked**，若不随本次提交入库会造成悬空引用与交付缺件（P1-1）。其余为加固/可观测性建议（P2）与待确认项（Q）。

问题数量（首轮）：P0 = 0，P1 = 1，P2 = 3，Q = 3，N = 5。

---

## 二、范围内改动 file:line 摘要

### 指定必需覆盖文件

| 文件 | 关键改动 | 证据 |
|---|---|---|
| `src/aistudio_api/application/verify_account_service.py` | 取码预算 45s；诊断日志收敛；全量取证（默认关）；网络记录/RPC 指纹/VerifyUser 响应体；扫码页面引导；`confirm_verified` 直连 RPC 分类；独立 `check()`；有头模式与参数剔除；Cookie 名称检查 | `:100`、`:250-267`、`:189-193`、`:1984-1988`、`:2160-2184`、`:2386-2413`、`:2519`、`:2698-2768`、`:1199-1369`、`:3130-3235`、`:2066-2088` |
| `src/aistudio_api/infrastructure/account/cookie_parser.py` | 探针出口 fail-closed：external 下 `direct_fallback` 降级为 `skip` | `:48-70`（`_resolve_probe_proxy`） |
| `src/aistudio_api/infrastructure/browser/cdp_client.py` | `__Secure-`/`__Host-`/`SameSite=None` Cookie 合法化；注入后缺失名单（仅名称）审计 | `:649-727`、`:694-706`、`:721`、`:729-755` |
| `src/aistudio_api/api/routes_accounts.py` | 导出 `probe_egress_allowed/probe_skip_reason`；新增 `POST /{id}/verify/check`；status 支持 `force` | `:585-590`、`:1298`、`:1339-1371`、`:216-227` |
| `src/aistudio_api/api/app.py` | `AISTUDIO_SKIP_CONTROL_WARMUP`（默认关）跳过控制面预热 | `:108-122` |
| `src/aistudio_api/infrastructure/gateway/model_defaults.py` | `AISTUDIO_ALLOW_EMPTY_INSTANCES`（默认关）允许空实例，默认仍抛同一错误文本 | `:411-420`、`:553-560` |
| `tests/unit/test_{verify_account_service,cookie_import,cdp_client,features_config,credential_exit_routing,exit_arbitration}.py` | 新增/加强断言，覆盖下列全部断言点 | 见各节 |
| `pyproject.toml` / `waa-signer/cmd/waa-signer/main.go` / `waa-signer/README.md` / `web/package.json` | 应用版本统一 `2.1.6` | `pyproject.toml:7`、`main.go:42`、`README.md:106`、`package.json:4` |

### 范围 diff 内、超出上表但已一并查看的文件（较轻深度）

`application/exit_arbitration.py`、`application/account_keepalive.py`、`infrastructure/account/account_info_refresher.py`、`infrastructure/exit_pool/{resolver,__init__}.py`、`infrastructure/account/account_store.py`（`accounts_dir` 属性）、`api/ui_sandbox.py`（沙盒 starting→pending）、`scripts/clear_verify_required.{py,sh}`（新工具，dry-run 默认 + 备份 + `--apply --yes`）、`Dockerfile`/`.dockerignore`、`web/src/**`（VerifyAccountModal、类型/API）、`docs/changelog.md`、`AGENT.md`。未发现越界或破坏性默认值。

---

## 三、逐项核实（断言 → 证据 → 结论）

### 断言 1：默认关闭不变量 — **部分满足（P2 偏差）**

- `AISTUDIO_VERIFY_DIAGNOSTICS` 未设置：`_verify_diagnostics_enabled()` 返回 False，`_log_diagnostic()` 落 DEBUG（`verify_account_service.py:250-267`）。无 INFO 级诊断刷屏。✔
- `AISTUDIO_VERIFY_HEADED` 未设置：`_verify_headed_enabled()` 读环境变量默认 False，`_build_verify_chrome_command` 传 `headless=True`（`:3118-3127`、`:3149-3157`）。✔
- `AISTUDIO_VERIFY_FULL_CAPTURE` 未设置：`_start_full_capture` 首行即返回，不注册任何全量取证监听（`:2167-2168`）。✔
- `AISTUDIO_ALLOW_EMPTY_INSTANCES` 未设置：`_allow_empty_instances()` 返回 False，仍抛 `配置校验失败：instances 未包含任何有效实例配置`，错误文本与 2.1.5 完全一致（`model_defaults.py:411-420`、`:553-560`；`test_features_config.py:561-575` 逐字断言）。✔
- `AISTUDIO_SKIP_CONTROL_WARMUP` 未设置：走原 `asyncio.create_task(_warmup_control_session(client))`（`app.py:108-122`）。✔
- **偏差**：网络记录/帧导航监听 **无条件注册**（`verify_account_service.py:1984-1988` 在 `start()` 中直接调用 `_register_net_trace` / `_register_frame_nav_trace`，未受 `AISTUDIO_VERIFY_DIAGNOSTICS` 或全量取证开关门控）。该监听为只读（仅 `Network.requestWillBeSent/responseReceived/loadingFailed`、`Page.frameNavigated`，`Network.enable` 本就在 `connect_page` 开启，见 `cdp_client.py:334-336`），不改变请求、不新增 INFO 刷屏，但严格意义上违反“未设置时不注册额外 CDP 监听”。→ **P2-1**。
- 另：`set_cookies` 每次注入后都会调用 `_log_missing_cookie_names`（额外 `Network.getCookies` 往返，`cdp_client.py:721`），并在检测到缺失时输出 WARNING（`:755`）；页面未导航返回空时不误报（`:741-745`）。这是 2.1.6 有意的 Cookie 修复，默认启用，属“行为变化但符合 changelog”。→ **N-1**。

### 断言 2：敏感信息 — **满足（附 P2 加固建议）**

- 逐条扫描新增日志：Cookie 仅输出 **名称/计数**（`verify_account_service.py:2066-2088` `_log_cookie_names`；`cdp_client.py:729-755` 只列缺失名称）；API key 仅前 10 位 + `…`（`:544-549`）；Authorization 只做桶分类（`:532-541`）；post 预览先截断再折叠 ≥32 字符令牌与 `eyJ` JWT（`:557-568`）；URL 中 `qr/<code>` 与长令牌段被替换为 `…`（`:398-448`）。未发现 Cookie 值 / Authorization 值 / 完整 API key / 短码明文进入常规 INFO/WARNING。
- 测试坐实：`test_start_logs_cookie_names_without_values`（`test_verify_account_service.py`）、`test_set_cookies_warns_missing_names_without_values`（`test_cdp_client.py`）、`test_post_preview_masks_long_token_and_jwt` 均断言密文不出现。
- 全量取证为唯一例外：默认关、仅 `AISTUDIO_VERIFY_FULL_CAPTURE=1` 开启，开启时显式 WARNING 标注“含 Cookie/Authorization/Set-Cookie 明文”并打印落盘路径（`verify_account_service.py:2180-2184`），写入 `<data_dir>/verify-capture`（gitignored）；changelog 与 docstring 均写明“仅限本机隔离目录使用”（`docs/changelog.md`、`:1809-1816`）。✔
- 残留建议：`_sanitize_body_preview` 只按固定 key 名（`sapisidhash/cookie/authorization/token/...`）折叠 ≥24 字符值（`:461-466`、`:501-509`），若上游 body 出现未登记 key 的长令牌，INFO 的 VerifyUser 失败体会原样显示。→ **P2-2**（低风险）。

### 断言 3：彻底移除 — **满足**

- `git grep -n "IMPORT_KEEP_OSID\|MINIMAL_FLAGS\|keep_osid\|_minimal_flags_enabled"` 仅命中两处 **测试断言**：`tests/unit/test_cookie_import.py:741-757`、`tests/unit/test_verify_account_service.py:3120-3121`（均断言符号已不存在）。
- `AISTUDIO_VERIFY_MINIMAL_FLAGS` 全库无命中。
- `cookie_parser.py` 相对基线仅 15 行出口相关改动，无 `keep_osid`/`MINIMAL_FLAGS` 死分支；`test_import_keep_osid_switch_symbols_removed` 显式断言 4 个私有符号与两处函数签名参数均已消失。✔

### 断言 4：Cookie 合法化副作用 — **满足，风险 LOW**

- 规则：`__Secure-` → `secure=True`；`__Host-` → `secure=True` 且 `path="/"` 且走 `url`（不写 `domain`）；`SameSite=None` 且 `secure is False` → `secure=True`（`cdp_client.py:694-706`）。`secure` 缺省为 `True`（`:663`），因此不存在“默认把 sameSite=None 降成不安全”的路径。
- 与规范一致：`__Host-` 要求 secure + path=/ + 无 domain；`__Secure-` 要求 secure；`SameSite=None` 必须 secure。不会把本应保留的 Cookie 改坏。
- 同一注入路径被池化会话复用：`infrastructure/gateway/session.py:636`（临时导入页）、`infrastructure/gateway/session_auth.py:136`（`load_cookies_to_page`）。合法化对池化会话同样是“让 Chromium 不再拒收”，属正向修复；唯一理论副作用是对显式 `secure=False` 且走 http 的 Cookie 强制 secure，但本服务 AI Studio 走 https，实际无影响。
- 测试：`test_set_cookies_forces_secure_for_samesite_none`、`test_set_cookies_enforces_prefix_rules`、`test_set_cookies_never_sends_insecure_samesite_none`、`test_set_cookies_empty_readback_is_not_reported_as_missing`（`test_cdp_client.py:310-498`）。→ **风险 LOW**。

### 断言 5：裸探针归因修复 — **满足**

- external 模式下裸 httpx 探针 `raw_transport=True` → `_probe_exit_id` 返回 `""`（`account_info_refresher.py:752-761`）；`ExitArbitration.adjudicate` 对 `probe_exit_id == ""` **在 assignment 查询 / canary / report 之前**早退 `inconclusive`（`exit_arbitration.py:291-300`）。另对“探针出口与当前绑定不一致”也返回 `inconclusive`（`:313-322`）。
- 池化 signer 路径未破坏既有口径：`_post_tier_request` 对 signer 传输返回 `_TierProbeOutcome(..., signer_transport=True)`（`account_info_refresher.py`），`account_bad` 仍 `update_account_refresh(..., verify_required=True)`（`account_info_refresher.py:862-867`）。
- 对应测试名：
  - 裸探针早退：`test_probe_exit_empty_string_stays_inconclusive`、`test_probe_exit_empty_string_short_circuits_before_canary`（`test_exit_arbitration.py:346-397`）
  - 出口不一致不写 verify：`test_probe_exit_mismatch_stays_inconclusive`（`:327-343`）、`test_region_403_probe_exit_mismatch_never_writes_verify`、`test_pooled_session_probe_exit_mismatch_never_writes_verify`（`test_credential_exit_routing.py`）
  - signer/绑定一致仍写 verify：`test_probe_exit_match_allows_account_bad`（`:400-421`）、`test_pooled_session_probe_exit_match_writes_verify`
  - 空绑定出口：`test_keepalive_probe_exit_id_empty_bound_socks_is_inconclusive`（`test_credential_exit_routing.py:620-644`）
  - 裸 tier 探针不烧出口/不写 verify：`test_bare_tier_probe_account_bad_stays_inconclusive`、`test_bare_tier_probe_exit_bad_does_not_burn_exit`

### 断言 6：取码预算 45s — **满足**

- 常量 `DEFAULT_CODE_TIMEOUT_SECONDS = 45.0`，注释与值一致，且注明“无 env 覆盖”（`verify_account_service.py:96-100`）。
- `_wait_for_qr` 用 `deadline = monotonic() + self._code_timeout`（`:2828`），循环 `while monotonic() < deadline`，超时后 `log.warning` + `_dump_net_trace("qr_timeout")` + `raise VerifyChallengeTimeout("qr_timeout")`（`:2858-2860`）——必定触发，不会永不触发。
- 外层启动守卫为 `self._code_timeout + 50.0 = 95s`（`:970`、`:1098`），给浏览器冷启动预留约 50s 后再跑满 45s，未被 TTL（300s）或 10s/2s 轮询间隔挤掉。
- 备注：若浏览器冷启动本身 >50s，外层 95s 守卫会先撤销 `start_task` 而表现为 `qr_timeout`；属极端慢机，记录为 **N-2**。

### 断言 7：诊断开关语义 — **满足（Q 备注）**

- 关时仍为 INFO 的关键节点：会话就绪 `验证会话就绪`（`:1027-1032`）、二维码就绪 `扫码二维码已就绪`（`:2846`）、会话结束/最终结果 `验证会话结束 ... state/message/upstream_status`（`:1730-1739`）、清锁 `验证锁已清除`（`:1681`）、独立检查开始/通过/待验证（`:1292`、`:1357`、`:1364`）。
- `VerifyUser` 非 2xx：响应到达即 INFO 打印 status/len/body 预览（`:2518-2524`）；权威路径 `GetUserVerificationStatus` 只有 2/3 清锁（`:1627-1634`、`:1354-1362`）。
- `verify_user=yes(...)` 只统计 **POST**（排除 OPTIONS 预检）并取“最后一个非 2xx”作为失败码（`:2614-2634`），逻辑正确。该汇总行本身是 `_log_diagnostic`（关时 DEBUG，`:2635`），但失败原因仍由 `:2519` 的 INFO body 行兜底。
- 盲区评估：关闭后若 VerifyUser 失败体为空/非文本，则 INFO 只剩 `body=-`，具体状态码需靠 dump（`session_close` 必跑 dump，`:2775-2776`）才能看到，且该 dump 汇总行是 DEBUG。→ **Q-1**（低）。

### 断言 8：版本一致性 — **满足**

- `pyproject.toml:7 = "2.1.6"`、`waa-signer/cmd/waa-signer/main.go:42 = "2.1.6"`、`waa-signer/README.md:106 = "2.1.6"`、`web/package.json:4 = "2.1.6"`。
- 全库（除 docs/AGENT 说明）无其它“应用版本”声明被误改；`2.1.5` 未落到文件是 AGENT.md 已记载的历史欠账（tag 2.1.5 发布时 pyproject 仍为 2.1.4），本次直接补齐到 2.1.6，符合既定口径。→ **N-3**。

### 断言 9：测试诚实性 — **满足**

- 未新增任何 `pytest.skip` / `mark.skip` / `xfail` / `skipif`（`git grep` 于 6 个指定测试文件 + 新增 `test_clear_verify_required.py` 均无命中）。
- 被删除/改写的既有断言均有正当理由且不弱化：
  - `test_cdp_page_set_cookies_normalization`：`conn.send.assert_called()` 改为显式挑选 `Network.setCookies` 调用（因新增 `Network.getCookies` 审计），更精确（`test_cdp_client.py:204-217`）。
  - 验证浏览器参数测试：删除 `--disable-features` 内含 `Translate/SitePerProcess` 断言，替换为 **更强的** `assert not any(arg.startswith("--disable-features="))` + `--disable-site-isolation-trials`，并注释说明原因（`test_verify_account_service.py:871-882`）。
  - 沙盒流程测试：`pending` 断言改为 `starting→pending` 两段并新增 QR 精确断言（`test_ui_debug_sandbox.py:815-865`），覆盖更细。
  - `test_emergency_direct_fallback_sends_without_proxy` → `test_emergency_direct_fallback_never_raw_probes_in_external_mode`（`test_credential_exit_routing.py`）：断言由“发送”反转为“skip 且 0 请求”，与本次修复意图一致且 docstring 说明；off 模式（external=False）仍走旧行为（`probe_egress_allowed` 非 external 时返回 `decision.allowed`，`resolver.py:146-161`）。
- `RouteFakeDriver.confirm_verified` 由 `return 2` 改 `return 1` 保证非强制轮询稳定 pending（`test_verify_account_routes.py:43-47`），同时存在 2/3 → passed/not_required 的参数化用例（`:367-394`），未丢失通过路径覆盖。

---

## 四、问题清单

### P0（阻断发布）
- 无。

### P1（必须修）
- **P1-1 两份新文档未入库（提交完整性问题）**：`docs/DEBUG_GUIDE.md`、`docs/VERIFY_ACCOUNT_DEVICE_VERIFICATION.md` 目前为 `git status` 中的 `??`（untracked），但 `docs/changelog.md:14-15`、`AGENT.md:259-262` 已将其作为 2.1.6 交付物引用。若本次提交未 `git add` 这两份文件，发布后 changelog/规范文档将悬挂到不存在的路径，且 2.1.6 “Added”交付缺件。
  - 证据：`git status --porcelain` → `?? docs/DEBUG_GUIDE.md`、`?? docs/VERIFY_ACCOUNT_DEVICE_VERIFICATION.md`。
  - 建议：提交前显式 `git add docs/DEBUG_GUIDE.md docs/VERIFY_ACCOUNT_DEVICE_VERIFICATION.md`，并确认正文中不含真实账号/Cookie 明文样本。

### P2（建议修）
- **P2-1 网络/帧导航 CDP 监听未受开关门控**：`verify_account_service.py:1984-1988` 在 `start()` 无条件注册 `_register_net_trace` 与 `_register_frame_nav_trace`，即使 `AISTUDIO_VERIFY_DIAGNOSTICS`/全量取证均关闭。虽为只读、无 INFO 刷屏、`Network.enable` 本已开启（`cdp_client.py:334-336`），但与断言 1“未设置时不注册额外 CDP 监听”字面不符，且为每个请求增加 Python dict 构建开销。建议：把两个 `_register_*_trace` 收敛到 `AISTUDIO_VERIFY_DIAGNOSTICS`/专用开关下；或若坚持常开，在 changelog/注释中明确“网络记录常驻监听、仅日志级别受开关影响”。
- **P2-2 VerifyUser 失败体预览仅按固定 key 名脱敏**：`_sanitize_body_preview`（`:501-509`）依赖 `_BODY_SECRET_ASSIGN_RE`（`:461-466`）的 key 白名单折叠 ≥24 字符值；未登记的 key（如未来新增签名字段）长令牌会以 INFO 原样输出（`:2519`）。建议对 body 预览同样追加“折叠任意 ≥32 字符连续令牌”的兜底（复用 `_RPC_FINGERPRINT_LONG_RUN_RE`）。
- **P2-3 诊断关闭时仍执行诊断期 CDP 采样**：`_wait_for_qr` 每 2s 无条件调用 `_log_page_state(..., diagnostic=True)`（`:2838-2843`），其内部 `_describe_page_snapshot` 会执行页面 evaluate，即便最终只是 DEBUG 输出；`_log_cookie_names` 同样在 DEBUG 下仍调用 `get_cookies()`（`:2066-2088`）。建议在 `diagnostic=True` 且 `_verify_diagnostics_enabled()` 为 False 时直接返回，避免无谓的页面读写与往返。

### Q（待确认）
- **Q-1 关诊断时 VerifyUser 状态码可观测性**：失败状态码数字仅出现在 `_dump_net_trace` 汇总行（DEBUG）与 INFO 的 `body=` 行中；若失败体为空/二进制，关闭诊断后 INFO 侧无法直接看到 403/401 数字（仅靠 `session_close` dump 的 body）。请确认是否接受“失败码数字仅在开启诊断时可见”。
- **Q-2 account_keepalive 的 `_probe_exit_id` 缺 `raw_transport` 判定**：`application/account_keepalive.py` 新增的 `_probe_exit_id(account_id, arbiter, kind)` 仅依据“槽位 session 的 `_bound_socks` 是否为空”判 `""`，没有 `raw_transport` 入参；若某条保活维护调用实际走裸 httpx（非 signer），仍可能以绑定 exit_id 归因。现有测试覆盖“空绑定→inconclusive”，请确认保活链路是否始终为 signer/池会话（若是则无需改）。
- **Q-3 全量取证根目录假设**：`_start_full_capture` 用 `self._auth_file.parents[2]` 推 data 根（`verify_account_service.py:2170`），依赖 `accounts/<id>/auth.json` 的目录层级。请确认在自定义 `AISTUDIO_ACCOUNTS_DIR` 布局下仍落在 data 隔离目录而非意外越界。

### N（备注）
- **N-1**：`set_cookies` 现在每次注入后多一次 `Network.getCookies`（`cdp_client.py:721`），并在缺失时 WARNING。池化路径（`session.py:636`、`session_auth.py:136`）一并生效；页面未导航时空结果不误报（`:741-745`）。属 changelog 已声明的有意改动。
- **N-2**：外层启动守卫 95s（`:970`）在浏览器冷启动 >50s 的极端慢机上会提前产生 `qr_timeout`；正常机器不受影响。
- **N-3**：`2.1.5` tag 存在但 `pyproject.toml` 等版本文件从 `2.1.4` 直接跳到 `2.1.6`，符合 AGENT.md 已记载的历史欠账补齐口径。
- **N-4**：`scripts/clear_verify_required.py` ship 进镜像（`Dockerfile:204-211`、`.dockerignore` 保留），默认 dry-run，写操作需 `--apply --yes`、先备份并在失败时回滚，且对“无 `no_exit` 指纹”的目标需 `--force`；设计保守，可用。
- **N-5**：新增 `POST /api/accounts/{id}/verify/check` 与既有账号路由一致地受 `require_web_auth` 保护（`app.py:612-615`），`test_verify_check_route_requires_web_auth` 断言未登录 401。

---

## 五、独立实跑门禁（真实输出摘要）

| 门禁 | 命令 | 结果 |
|---|---|---|
| Ruff Lint | `uv run ruff check .` | `All checks passed!` ✅ |
| Ruff Format | `uv run ruff format --check <24 个改动 .py>` | `24 files already formatted` ✅ |
| Pyright | `uv run pyright src tests` | `0 errors, 0 warnings, 0 informations` ✅ |
| Pytest | `uv run pytest -q` | `2140 passed in 74.18s (0:01:14)` — 0 failed / 0 skipped ✅ |
| 空白检查 | `git diff --check c5903efe...` | 无输出（无空白错误）✅ |
| 已移除符号 | `git grep -n "IMPORT_KEEP_OSID\|MINIMAL_FLAGS\|keep_osid\|_minimal_flags_enabled"` | 仅命中 `test_cookie_import.py:741-757`、`test_verify_account_service.py:3120-3121` 的“已移除”断言 ✅ |

备注：以上均在本机（Windows/PowerShell 7）离线执行，未启动服务/浏览器/外网/ssh/扫码。

---

## 六、只读声明

本次审查为**只读**：未修改任何源码、测试、配置或既有文档，未执行 `git add`/`commit`/`push`，未启动服务或浏览器。

唯一写入的文件：`docs/dev/rebuild/domains/P1-account/reviews/REV-2.1.6.1-verify-device-verification.md`（本报告）。

---

## 七、复核轮次（Round 2，2026-11-24T08:2x）

### 7.1 新结论

**CONDITIONAL** — 两批新改动的实现与测试均成立，未引入 P0/P1 级回归；**P2-2、Q-1、Q-2、Q-3 已关闭**。仍保留 P1-1（两份新文档未入库）、P2-1（网络监听无条件注册）、P2-3（诊断关闭仍做页面采样），并新增 1 条 P2-4（keepalive signer 判定的 TOCTOU 窗口）。

问题数量（Round 2）：P0 = 0，P1 = 1，P2 = 4，Q = 2，N = 6。

### 7.2 首轮问题关闭状态表

| 编号 | 首轮问题 | 状态 | 依据 |
|---|---|---|---|
| P1-1 | `docs/DEBUG_GUIDE.md`、`docs/VERIFY_ACCOUNT_DEVICE_VERIFICATION.md` 未入库 | **保留** | `git status --porcelain` 仍为 `?? docs/DEBUG_GUIDE.md`、`?? docs/VERIFY_ACCOUNT_DEVICE_VERIFICATION.md`；提交前需 `git add` |
| P2-1 | 网络/帧导航监听无条件注册 | **保留** | `verify_account_service.py:2022-2023` 仍在 `start()` 无条件 `_register_net_trace` / `_register_frame_nav_trace` |
| P2-2 | body 预览仅按固定 key 脱敏 | **已关闭** | 新增 `_BODY_LONG_RUN_RE`（`:471`）与 `_sanitize_body_preview` 通用兜底（`:506-522`）；`test_body_preview_masks_unregistered_long_token`（80 字符无 key 令牌）、`test_body_preview_masks_jwt_fragment`、`test_body_preview_keeps_short_text`（无空格短文本不被误伤）、`test_body_preview_keeps_known_key_redaction` |
| P2-3 | 诊断关闭时仍执行页面采样 | **部分关闭（保留）** | 日志级别已收敛为 DEBUG，但 `_log_page_state(diagnostic=True)` 仍先执行 `_describe_page_snapshot()` 再落 DEBUG（调用点 `verify_account_service.py:2878`，函数 `:2074-2098`），关闭诊断时仍有页面 evaluate 开销 |
| Q-1 | 关诊断时 VerifyUser 状态码不可见 | **已关闭** | 非 2xx VerifyUser 响应体现在固定 INFO（`:2544-2560`），dump 中失败 VerifyUser 的 `body=` 子行也固定 INFO（`:2709-2716`）；`test_verify_user_non_2xx_stays_info_with_diagnostics_off` 断言 403=INFO、200=DEBUG |
| Q-2 | keepalive `_probe_exit_id` 缺 raw_transport | **已关闭（并修复真实漏洞）** | `_probe_exit_id(..., raw_transport=)`（`account_keepalive.py:319-350`）；`_arbitrate_region_once(..., raw_transport=)`（`:384-407`）；`auth_update` 在 `:1242-1249` 以 `AccountService._session_can_rotate` 判定并 `raw_transport=not rotation_via_signer`（`:1260`）；`account_service.py:173-174` 确认裸路径回落与判定同源 |
| Q-3 | 全量取证根目录假设 `parents[2]` | **已关闭** | `_resolve_capture_data_root` 优先 `resolve_data_dir()`，异常回退旧启发式（`:1821-1838`）；`AISTUDIO_VERIFY_CAPTURE_DIR` 优先级不变（先查 override，`_FullCaptureSink.__init__`）；相对 override 固定按 cwd 转绝对（`:1849-1860`）；`test_capture_data_root_prefers_project_data_dir_and_falls_back`、`test_start_full_capture_lands_in_project_data_root_not_auth_ancestor`、`test_full_capture_relative_override_resolves_against_cwd` |

### 7.3 新发现问题清单

#### P0（阻断发布）
- 无。

#### P1（必须修）
- **P1-1（沿用首轮）** 两份新文档仍未 `git add`，见 7.2。

#### P2（建议修）
- **P2-1（沿用首轮）** 网络/帧导航监听无条件注册：`verify_account_service.py:2022-2023`。只读、无 INFO 刷屏，但与断言 1 字面不符，建议门控或文档化。
- **P2-3（沿用首轮）** 诊断关闭仍执行 `_describe_page_snapshot` 页面 speak：`verify_account_service.py:2074-2098` + 调用点 `:2878`。建议 `diagnostic and not _verify_diagnostics_enabled()` 时直接返回，跳过快照。
- **P2-4（新增，低）keepalive signer 判定存在 TOCTOU 窗口**：`account_keepalive.py:1242` 先算 `rotation_via_signer = _session_can_rotate(acc_sess, id)`，`:1249` 再调 `rotate_account_cookies`；后者在 `account_service.py:174` **重新**判定同一 gate。若会话恰在两次判定之间死亡（`is_alive()` 由 True→False 或 `transport_state` 变化），keepalive 会以 `raw_transport=False`（视为 signer）上报，但实际走裸 httpx；此时命中 region 403 仍可归因写 `verify_required`。窗口为微秒级、概率极低，但正是“判定 signer 实际走裸”的字面窗口。
  - 建议：让 `rotate_account_cookies` 返回实际使用的传输（仿 `_TierProbeOutcome.signer_transport`），keepalive 用返回值而非预先判定。

#### Q（待确认）
- **Q-4** `_BODY_LONG_RUN_RE = [A-Za-z0-9_\-\.=/+]{32,}`（`:471`）不含 `%`、`:`、空格、换行：URL 编码（`%3D`）、`Bearer<空格>` 分隔或折行的长令牌会被切成多段。若每段仍 ≥32 字符仍会被折叠；若被切到 <32 则不会整段遮蔽，存在部分残留。因已有 `sanitize_log_message` 的 cookie/auth 兜底且只影响诊断预览，风险低，请确认可接受。
- **Q-5** `resolve_data_dir()` 会把 `<AISTUDIO_DATA_DIR 或 项目 data>/` 作为根（`config.py:145-153`），当运维只设 `AISTUDIO_ACCOUNTS_DIR` 而未设 `AISTUDIO_DATA_DIR` 时，取证目录会落在项目 `data/verify-capture` 而非账号目录旁。属有意的“不越界”选择，请确认与运维预期一致。

#### N（备注）
- **N-6** `_BODY_LONG_RUN_RE` 对正常文案误伤风险低：≥32 连续 `[A-Za-z0-9._=/+-]` 在中文/英文散文中极罕见；即便误伤也只影响诊断预览。`test_body_preview_keeps_short_text` 已锁定短文本不被改。
- **N-7（关键）全量取证**未被本次脱敏影响：`_capture_body`（`verify_account_service.py:2335-2370`）仍原样写 `text[:FULL_CAPTURE_BODY_LIMIT]` 到 JSONL，未调用 `_sanitize_body_preview`；脱敏只作用于 net-trace 预览与 INFO body 行。符合“全量取证不应脱敏”的要求。
- **N-8** `resolve_data_dir` 导入无循环依赖：`config.py` 仅依赖 stdlib + `dotenv`；`pyright`/`pytest`/导入均通过。
- **N-9** 新增 keepalive 对照用例均为真实行为锁定，非空跑：
  - `test_auth_update_bare_rotate_region_403_never_writes_verify`：无槽位会话 → `_session_can_rotate=False` → `probe_exit_id=""` → 真实 `ExitArbitration` 早退；断言 `canary.calls == 0`、`resolver.events == []`、`verify_required is False`、defer 300s。修复前该用例会失败（旧逻辑返回绑定 exit_id → canary account_bad → 写 verify）。
  - `test_auth_update_signer_rotate_region_403_account_bad_writes_verify`：提供 `SignerRotateSession` 槽位且 `_bound_socks` 与绑定一致 → 断言 `canary.calls == 1`、事件 `("acc_signer","exit-1","verify_account","account_bad")`、`verify_required is True`、defer。
- **N-10** 新增 sanitize/capture 测试同样非空跑：`test_start_full_capture_lands_in_project_data_root_not_auth_ancestor` 显式断言 `capture.path.parent != auth_file.parents[2]/verify-capture`，修复前会失败。

### 7.4 过度拦截（over-block）风险评估 — **LOW**

新 keepalive 修复只把“裸传输 region 403”从“可 canary 归因”降级为 `inconclusive` + defer（300s），不会永久压制真实标记：

- **signer 路径仍会写 verify**：`account_keepalive.py:1242-1260` 在 `_session_can_rotate=True` 时 `raw_transport=False`，`_probe_exit_id` 用槽位 `_bound_socks` 与绑定比对（`account_keepalive.py:350-381`）；命中 `account_bad` 后仍走既有写入口。证据测试：`test_auth_update_signer_rotate_region_403_account_bad_writes_verify`（断言 `verify_required is True`），以及 `application/account_info_refresher.py:862-867` 的 `update_account_refresh(..., verify_required=True)` 未被改动，配对测试 `test_pooled_session_probe_exit_match_writes_verify`（`tests/unit/test_credential_exit_routing.py`）。
- **裸路径仅延后不判定**：`inconclusive` → `deferred_accounts`（300s 重试），真实坏账号会在下一轮 signer/池会话或账号 `verify/check`/扫码流程中被判定；不会因为“没有裸归因”而永久漏标。
- 唯一会“少标”的场景：某账号**只可能**走裸 httpx 且从不扫码/不走 check —— 这种账号此前也没有任何可信证据证明其坏，跳过归因是正确的 fail-closed 选择。

### 7.5 Round 2 门禁实跑（真实输出）

| 门禁 | 命令 | 结果 |
|---|---|---|
| Ruff Lint | `uv run ruff check .` | `All checks passed!` ✅ |
| Ruff Format | `uv run ruff format --check <24 个改动 .py>` | `24 files already formatted` ✅ |
| Pyright | `uv run pyright src tests` | `0 errors, 0 warnings, 0 informations` ✅ |
| Pytest | `uv run pytest -q` | **`2150 passed in 74.46s (0:01:14)`** — 0 failed / 0 skipped ✅（首轮 2140 → +10 新用例） |
| 空白检查 | `git diff --check c5903efe...` | 无输出 ✅ |
| 已移除符号 | `git grep -n "IMPORT_KEEP_OSID\|MINIMAL_FLAGS\|keep_osid\|_minimal_flags_enabled" -- '*.py'` | 仅命中 `test_cookie_import.py:741-757`、`test_verify_account_service.py:3213-3214` 的“已移除”断言，源码零残留 ✅ |

### 7.6 Round 2 只读声明

本轮复核仍为**只读**：除在本文追加本小节外，未修改任何源码/测试/配置/既有文档，未 `git add`/`commit`/`push`，未启动服务/浏览器/外网/ssh/扫码。唯一写入文件：本报告。
