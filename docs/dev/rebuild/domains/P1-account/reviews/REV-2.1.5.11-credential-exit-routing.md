# REV-2.1.5.11 — 元数据/Cookie 链路按账号出口（credential exit routing）只读审查

- 审查日期：2026-11-23
- 审查角色：独立只读 Reviewer
- 审查对象：未提交改动（`git status --short` 列出的 `exit_pool/`、`account/`、`application/account_*`、`api/routes_accounts.py`、`tests/conftest.py` 与新增 `tests/unit/test_credential_exit_routing.py`）
- 前置提交：`e8c88a5 feat(exit-pool,verify)`（上一轮只覆盖 browserless 生成会话的按账号出口）
- 结论：**CONDITIONAL**
- 依据：`AGENT.md`、`docs/dev/rebuild/06-decision-log.md`、`docs/dev/rebuild/08-09-decision-record.md`（D-01..D-14）、本次实际 diff 与实跑门禁

## 0. 审查边界与工作区说明

- 唯一写操作：本文件。其余全部只读。
- 本轮工作树只有上表所列未提交文件；`git status --short` 无其它残留（见第 5 节）。
- 本轮重点：external 模式下所有“带账号凭据”的上游请求是否走该账号出口、拿不到出口是否 fail-closed、是否绝不回退 `settings.proxy_url`、off 模式是否零行为变化。
- `info` 级实测边界：按只读红线，未启动服务/浏览器、未访问真实 Google/VPS；所有结论来自源码、注入式单测与实际门禁。

## 1. 结论

**CONDITIONAL（不可写“可提交”）**。

browserless（external 官方支持的引擎）下的元数据/Cookie 主链已经正确：off 模式行为保持不变，external 有出口用绑定出口，无出口/管理器不可达/空账号一律 skip 且零请求，RotateCookies 池会话路径无回归，应急阀默认关闭且仅直连。门禁全绿（1904 passed）。

但仍存在 **3 个 P1 + 4 个 P2**：

1. **P1**：`session_auth.verify_account_identity` 的裸 tier 探测**已经有现成 `account_id` 却未透传**，external 下必然 `no_account` skip（凭证探测缺口，一行可修）。
2. **P1**：`cookie_refresher` 裸刷新路径未透传账号出口；唯一生产调用方（`routes_accounts.py:1586`）手里有 `account.id`，但 `Session.import_cookies` 未接住 `proxy`，external 下凭证预刷新被整段跳过（凭证轮换缺口）。
3. **P1**：生成主链（`engine_runtime.create_session`）在 external 无出口/解析异常时**仍回退全局 `settings.proxy_url`**（`or proxy`），与本轮 fail-closed 目标矛盾；坏出口/WARP 仍可发出生成与会话内 tier 探测请求。
4. **P2**：F 项合成 `cookie_<hex>` 作为管理器账号键会留下孤儿绑定并污染容量。
5. **P2**：keepalive 的 Free-fresh 快路径把“邮箱跳过”记为 `ok=True`，下一轮元数据刷新被推到 24h，而不是 300s。
6. **P2**：无出口的降级导入在拿不到邮箱时无法按邮箱去重，可能新建重复账号；`probe_and_import` 的降级分支还漏了 `rotator.add_account/clear_cooldown/reconcile`。
7. **P2**：external + browser 引擎只 WARNING 不 fail-fast，浏览器引擎启动仍用全局代理（`launch_browser` 不传 proxy），是 P1-1/P1-2 可达的根因。

## 2. 必查项逐条结论（A–K）

> 记号：PASS=符合；P1/P2=缺陷；Q/N=观察/提示。

### A. 语义正确（off 一致 / external 绑出口 / 无出口 skip / 空账号 skip）

- **PASS**：`resolve_credential_exit` 在无 resolver（off）时返回 `ExitDecision("proxy", proxy=_global_proxy())`，与旧 `settings.proxy_url` 等价（`resolver.py:122-124`、`88-91`）。
- **PASS**：external 有出口返回 `action="proxy", proxy=<socks>`（`resolver.py:141-142`）。
- **PASS**：无出口默认 `skip`，不构造 transport、不发起请求（`resolver.py:148-153`；`test_external_no_exit_skips_with_zero_requests` 断言 `proxies == []`）。
- **PASS**：空账号先于 emergency 阀直接 `skip`（`resolver.py:125-130`）。
- **PASS**：caller 层 off 模式无行为漂移：`probe_account_emails_map`/`probe_account_benefit_tier`/`rotate_google_cookies` 在 off 下 `decision.proxy == settings.proxy_url`；`_refresh_session_cookies` 未传 proxy 时仍读 `settings.proxy_url`（`cookie_refresher.py:61-74`）。

### B. 覆盖完整性（逐一排查 `settings.proxy_url`）

本轮改动文件内已无遗漏的全局代理使用：

- tier/邮箱/accounts_infinite：`cookie_parser.py:644-649,740-742,894-903`；`account_info_refresher.py:660-674,1077-1099,1133-1171`。
- 手动刷新 / keepalive：统一走 `AccountInfoRefresher`（`routes_accounts.py:89-101`、`app.py:378` 无注入 transport，默认工厂按账号建 transport）。
- RotateCookies 裸路径：`rotate_google_cookies(..., account_id=...)`（`account_service.py:194-196`）。
- 导入探测：`routes_accounts.py:1531,1647,1863`。
- 残余 `settings.proxy_url` 仅剩：off 分支（`resolver.py:91`、`cookie_refresher.py:72`、`HttpxTransport` 默认 `use_global_proxy=True`）与生成主链（见 P1-3），以及 `media.py:72` 的图片下载（非账号凭据 RPC，Q-1）。

**sentinel 复核**：`test_external_never_uses_global_proxy_sentinel` 真实走 `HttpxTransport`（monkeypatch `httpx.AsyncClient`），断言 `captured == [EXIT_ONE]` 且哨兵不在；`test_cookie_parser_probe_uses_resolved_exit_not_global` / `test_rotate_cookies_uses_resolved_exit_not_global` 同样有效。未见 external 下把 `SENTINEL_GLOBAL` 发出去的路径（在 browserless 下）。

**覆盖缺口**（均为 browser 引擎可达、browserless 不可达）：

- **P1**：`session_auth.py:245,289-296`。
- **P1**：`routes_accounts.py:1584-1589 → session.py:603-613 → cookie_refresher.py:47-72`。

### C. keepalive（skip 不推进 due、下一轮重试、job 锁、统计口径）

- **PASS（主路径）**：`_skipped_result` 返回 `ok=False, skipped=True`（`account_info_refresher.py:510-533`）；keepalive `failed>0` → `_retry_delay` 返回 `FAILURE_RETRY_SECONDS=300`（`account_keepalive.py:455-483,295-305`）；`mark_refresh_attempt` 只写 `last_refresh_attempt_at/last_refresh_error`，不动 `tier_checked_at`（`account_store.py:1183-1190`），故 24h due 不推进。
- **PASS（锁）**：metadata_refresh 仍在单 `_job_lock` 内串行（`account_keepalive.py:360-372`），外部串行 pacing 不新增第二套账本。
- **PASS（口径）**：`failed` 与 `skipped` 分开计数，日志与返回值都带 `skipped`（`account_keepalive.py:455-483`）。
- **P2**：Free-fresh 快路径把邮箱 skip 记为 `ok=True`（`account_info_refresher.py:626-658`），导致 keepalive 把下一轮推到 24h（详见 P2-2）。

### D. RotateCookies（池会话不回归 / 裸路径走出口 / no_exit 不扇出）

- **PASS**：有池会话优先 `rotate_cookies_via_signer`（`account_service.py:172-192`）；browserless 签名路径按 `self._proxy` 逐请求下发（`browserless_session.py:704-724`、`signer_client.py:371-374`），Batch A 未回归。
- **PASS**：裸路径走账号出口（`cookie_parser.py:818-836`；`test_rotate_cookies_uses_resolved_exit_not_global`）。
- **PASS**：`skipped=="no_exit"` 提前返回，不扇出兄弟、不写 Cookie（`account_service.py:198-205`；`test_rotate_cookies_fails_closed_without_exit` 断言语义与 `__Secure-1PSIDTS` 未变）。
- **Q-2**：兄弟扇出传了 `account_id=sib.id`，但只要 `client is not None` 该参数就被忽略（`account_service.py:232-234`、`cookie_parser.py:818-819`），兄弟轮换实际用 `client` 绑定的目标会话出口。属既有“合并 jar”设计，可接受但需知悉。

### E. 两个缺口的可接受性（重点）

**E.1（P1）`cookie_refresher` 裸刷新无账号标识**

- 调用链：`routes_accounts.import_cookies`（`account` 已落库、`account.id` 可用）→ `Session.import_cookies(req.cookies, auth_file=...)`（`routes_accounts.py:1584-1589`）→ `load_cookies_from_string(cookie_string)`（`session.py:603-613`）→ `_refresh_session_cookies(parsed)`（`cookie_refresher.py:47-72`）。
- external 下 `_refresh_session_cookies` 直接 WARNING 并返回原 Cookie（`cookie_refresher.py:62-71`），即“导入时预刷新”整段跳过。
- **判定 P1**：账号上下文**可透传**——`load_cookies_from_string`/`_refresh_session_cookies` 都已接受 `proxy` 参数，路由层是 async，完全可以 `await resolve_credential_exit(account.id)` 后把 `proxy` 透传到 `Session.import_cookies`。当前选择跳过而非透传，符合用户给定的“可直接透传却选择跳过 → P1”。
- 影响范围：仅 external + browser（browserless 下该 `if ... not _browserless_engine` 分支根本不执行）；因此实际爆炸半径被 P2-4 的“external 应只跑 browserless”限制。若团队正式宣布 external+browser 不受支持，可降为 P2；否则按 P1。

**E.2（P1）`session_auth` 裸 tier 探测无账号标识**

- `verify_account_identity` 在 `session_auth.py:245` 已取到 `account_id = meta.get("id")`，但 `:294-296` 调 `probe_account_benefit_tier(cookie_dict, auth_user=auth_user)` 未传 `account_id`。
- external 下 `resolve_credential_exit("")` → `no_account` skip（`resolver.py:125-130`），`probe_account_benefit_tier` 返回 `None`（`cookie_parser.py:740-742`），随后被 `:299 except Exception: pass` 静默吞掉。
- **判定 P1**：`probe_account_benefit_tier` 已接受 `account_id`，这里是一行透传即可修；后果是 browser 路径的 tier HTTP 交叉校验在 external 下永远不执行，页面提取失败/只剩 Free 时账号可能停留 Free，影响付费模型准入判断。
- 同样是 external+browser 可达；与 E.1 同受 P2-4 约束。

### F. 合成 `cookie_<hex>` 作为管理器账号键（P2）

- `import_cookies`/`import_bundle`/`probe_and_import` 在 `save_account` **之前**生成 `cid = f"cookie_{secrets.token_hex(4)}"` 并用它解析出口（`routes_accounts.py:1526-1531,1646-1647,1836,1863`）。
- 但 `AccountStore.save_account` 的 `id` 由 `_generate_account_id()` 生成，`cid` 只落到 `cookie_id`（`account_store.py:957,979`）。因此管理器收到的是 `HMAC(cid)`，与之后生成/元数据链路使用的 `HMAC(account.id)` **不是同一个键**。
- **判定 P2**：每个被探测的导入会留下一个一次性孤儿绑定（管理器粘性表/容量污染），且账号真正用于生成时的出口要等下一次 `assign` 才绑定；导入探测实际跑的出口也不是该账号长期出口。建议：先 `save_account` 拿到 `account.id` 再探测（失败再回写 tier），或管理器提供按 `HMAC` 释放/改绑接口，或把“出口键”统一为 `cookie_id` 并在所有链路一致使用（改动更大）。

### G. 应急阀（默认关 / 显式 env / 只直连 / 限频 / 测试）

- **PASS**：`AISTUDIO_EXIT_DIRECT_FALLBACK` 仅 `1/true/yes/on` 开启（`config.py:62-65`），conftest 已纳入隔离（`conftest.py:16`）。
- **PASS**：返回 `proxy=None`，caller 以显式 `proxy=None` 构造 httpx（httpx 显式 `None` 不读 env 代理），不回退全局 socks。
- **PASS**：WARNING 按账号 60s 限频（`resolver.py:44-46,80-105`），`test_emergency_direct_fallback_is_opt_in_and_log_throttled` 断言只 1 条。
- **PASS**：`test_emergency_direct_fallback_sends_without_proxy` 断言 `proxies == [None]`。
- **Q-3**：fail-closed 的普通 skip WARNING 没有限频（`resolver.py:126-129,148-152` + `_skipped_result` 再一条），43 账号故障期每 300s 约 86 条；不阻塞，但可考虑合并/限频。

### H. 批量节奏（0.3s / 不压同秒 / 无全局硬限流）

- **PASS**：`refresh_accounts` external 分支串行 + `EXTERNAL_BATCH_PACING_SECONDS=0.3` 可注入 sleep（`account_info_refresher.py:70-75,808-833`；`test_external_mode_uses_per_account_exit_for_tier_and_email` 断言 `sleeps==[0.3]`）。
- **PASS**：`import_bundle` 在 `_external_exits_active()` 时对 `idx>0` 串行 sleep 0.3（`routes_accounts.py:1805-1810`）。
- **PASS**：无全局硬限流；每个账号各自解析出口。
- **N-1**：`probe_google_accounts_infinite` 内部 `u/0,u/1,...` 多子账号循环无间隔（`cookie_parser.py:907-985`）。这是单次导入动作、共享同一 cookie 的一个出口，风险低；如担心同出口短时多请求，可在子账号循环加小间隔。

### I. 生成主链 external 仍回退全局代理（P1）

- `engine_runtime.create_session`：`proxy = settings.proxy_url`，`if resolver: proxy = await _resolve_exit_proxy(...) or proxy`（`engine_runtime.py:273-275`）。`_resolve_exit_proxy` 在无分配/异常时返回 `None`（`engine_runtime.py:295-315`），于是**回退全局 WARP**。
- **判定 P1**：与用户“IP 风控不区分路径”的前提直接冲突。无出口账号的生成请求、以及经由该会话的 tier 探测（`_post_tier_request` 走 `probe_tier_over_session`，`account_info_refresher.py:1057-1065`）都会走 WARP；WARP 本身刷新凭证都 403，用它发凭据请求会放大 region/verify 风控。
- **canary/事件污染路径**：`ExitArbitration.adjudicate` 用 `assignment_for(include_expired=True)`（`exit_arbitration.py:273-285`）。若某账号曾有 `_last_known` 绑定、但这次重建时 `resolve` 因 TTL 过期+拉取失败返回 `None` 而回退全局，region 403 会被归因到那个**并未实际承载请求**的旧出口并进入 canary/事件账本（`_last_known` 不随失败清除，`resolver.py:269,319-321,441-449`）。若旧出口恰好也 region-blocked，会被错误隔离。
- **建议**：生成主链复用 `resolve_credential_exit` 语义——无出口默认 `skip`（对该账号本次不建会话/返回明确错误），或仅在 `AISTUDIO_EXIT_DIRECT_FALLBACK` 下直连；并同步处理 `browserless_session.py:887` 的 `self._proxy or settings.proxy_url` 与 signer daemon `-proxy settings.proxy_url`（`engine_runtime.py:210`），否则一旦改成 `None` 仍会从这两处悄悄回退全局。
- 说明：这是 `e8c88a5` 的既有行为，不在本轮 diff 文件内，但用户将 I 列为必查项，且它是本轮“fail-closed”目标生成侧的对称缺口，故按 P1 记录。

### J. 测试质量

- **PASS（关键用例真实断言）**：
  - `test_external_mode_uses_per_account_exit_for_tier_and_email`：断言 `resolver.calls` 精确 1 次/账号、transport 工厂收到的 proxy 序列、pacing sleep。
  - `test_external_no_exit_skips_with_zero_requests`：断言 `skipped=True`、`skip_reason=="no_exit"`、`proxies==[]`（transport 根本没建）。
  - `test_external_never_uses_global_proxy_sentinel`：真实 `HttpxTransport` + monkeypatch `httpx.AsyncClient`，断言实际构造参数。
  - `test_keepalive_skipped_probe_does_not_advance_due`：断言 `next_due==now+300` 且 `tier_checked_at` 未变（真实门禁）。
  - `test_import_probe_skip_still_imports` / `test_probe_and_import_skip_imports_single_account`：断言探测未被调用 + 账号仍落库。
  - `test_rotate_cookies_*`：真实断言 proxy 与 Cookie 未被改写。
- **PASS（off 回归真实）**：`test_off_mode_uses_global_proxy_unchanged` 断言工厂收到 `SENTINEL_GLOBAL`。
- **Q-4（未覆盖）**：`import_bundle` 的 0.3s pacing 无测试；`resolve_credential_exit` 的 `except Exception` 分支（管理器异常）无测试，仅有 `fail=True` 返回 None 的路径；`_recover_web_api_key` 的 `allow_bare=False` 跳过分支无测试。

### K. 门禁

见第 5 节；ruff / pyright / pytest 全绿。

## 3. P0/P1/P2 明细

### P1-1（E.2）`session_auth` 裸 tier 探测丢弃现成 `account_id`

- 位置：`src/aistudio_api/infrastructure/gateway/session_auth.py:245,289-296`
- 复现：`AISTUDIO_EXIT_MODE=external` + browser 引擎（或任何调用 `verify_account_identity` 的路径）→ 账号 tier 为空/Free → 触发 `probe_account_benefit_tier(cookie_dict, auth_user=...)` → 无 `account_id` → `resolve_credential_exit("")` → `skip(no_account)` → `None` 被 `except: pass` 吞掉，不会被识别为“无出口”，也不会走账号出口。
- 建议：`probe_account_benefit_tier(cookie_dict, auth_user=auth_user, account_id=account_id)`（`account_id` 为 `"unknown"` 时不传）；最好把 `except: pass` 改为记录 `skip` 原因，避免静默。
- 说明：若按 P2-4 在 external 下 fail-fast 禁用 browser 引擎，本项自然不可达；两者择一闭环。

### P1-2（E.1）`cookie_refresher` 裸刷新未透传账号出口

- 位置：`src/aistudio_api/infrastructure/account/cookie_refresher.py:47-72`；`src/aistudio_api/infrastructure/gateway/session.py:603-613`；`src/aistudio_api/api/routes_accounts.py:1584-1589`
- 复现：external + browser，导入纯 KV Cookie（非 storageState JSON）→ `Session.import_cookies` 未传 proxy → `_refresh_session_cookies` 检测到 external 直接返回原 Cookie；导入成功但未做凭证预刷新（随后只能靠 24h 签名轮换兜底）。
- 建议：路由层 `decision = await resolve_credential_exit(account.id, kind="chat")`，把 `decision.proxy` 透传进 `Session.import_cookies(..., proxy=...)` → `load_cookies_from_string(..., proxy=...)`（参数已存在）；`skip` 时明确日志。若按 P2-4 禁用 external+browser，则本项降级为文档说明。

### P1-3（I）生成主链无出口回退全局代理

- 位置：`src/aistudio_api/infrastructure/waa/engine_runtime.py:273-275,295-315`；关联 `:210`、`src/aistudio_api/infrastructure/waa/browserless_session.py:887`
- 复现：external 下账号无分配/管理器异常 → `_resolve_exit_proxy` 返回 `None` → `proxy = ... or settings.proxy_url` → 会话经 WARP 建连；生成请求与会话内 tier 探测都走 WARP。若该账号 `_last_known` 仍有旧绑定，region 403 会被 `adjudicate(include_expired=True)` 归因到旧出口。
- 建议：与 `resolve_credential_exit` 统一：无出口默认不建会话（返回明确 `no_exit` 错误/跳过该账号），仅显式 `AISTUDIO_EXIT_DIRECT_FALLBACK` 时直连；同时修 `browserless_session.py:887` 的 `or settings.proxy_url` 与 signer daemon 启动参数（external 下不应再给 daemon 全局代理，否则省略 proxy 的请求仍走全局）。

### P2-1（F）合成 `cookie_<hex>` 出口键产生孤儿绑定

- 位置：`src/aistudio_api/api/routes_accounts.py:1526-1531,1646-1647,1836,1863`；`src/aistudio_api/infrastructure/account/account_store.py:957,979`
- 复现：导入时管理器按 `HMAC(cid)` 分配出口；账号落库后 `id` 是另一随机串，`cid` 仅存 `cookie_id`。后续生成/元数据用 `HMAC(account.id)`，两条键互不相干。
- 后果：每次导入探测留一个永不使用的粘性绑定（容量/绑定表污染）；导入探测出口 ≠ 账号长期出口。
- 建议：先落库拿 `account.id` 再探测并回写 tier/email；或管理器提供按 HMAC 释放/改绑；或统一以 `cookie_id` 作为出口键（需全链一致）。

### P2-2（C）Free-fresh 快路径的邮箱 skip 记为 `ok=True`，下一轮被推到 24h

- 位置：`src/aistudio_api/infrastructure/account/account_info_refresher.py:626-658`；`src/aistudio_api/application/account_keepalive.py:455-483,295-305`
- 复现：Free 账号 `tier_checked_at` 在 3 天内且缺 email，external 无出口 → 该分支 `decision.allowed=False` 只置 `email_error`，仍返回 `ok=True, skipped=False`；keepalive `failed=0` → `_retry_delay` 返回 24h。其它路径的 skip 都会 300s 重试，此处不一致。
- 建议：邮箱探测被 skip 时置 `skipped=True`（并让 keepalive 计入 `skipped`/`ok=False`），或至少让 `_retry_delay` 对 `skipped>0` 走 `FAILURE_RETRY_SECONDS`。

### P2-3（F/导入）无出口降级导入可能重复建号并漏注册 rotator

- 位置：`src/aistudio_api/api/routes_accounts.py:1648-1674`（`probe_and_import` skip 分支）；对照正常路径 `:1688-1693`、`import_cookies` `:1567-1579`
- 复现：external 无出口 + 已存在同账号 → skip 分支 `save_account_from_cookies(email=None, account_id=None)`；`save_account` 的邮箱去重需要 `email`，无 `account_id` 时直接 `_generate_account_id()`（`account_store.py:928-957`）→ 新建重复账号。且该分支未 `rotator.add_account/clear_cooldown/_reconcile_verify_after_import`。
- 建议：优先使用请求里的 `account_id`/已解析身份做去重；或在无出口时返回 409/502 让操作者稍后重试而非盲目降级；降级分支补齐 rotator 注册，与正常路径一致。

### P2-4（B/根因）external + browser 仅 WARNING，不 fail-fast

- 位置：`src/aistudio_api/infrastructure/waa/engine_runtime.py:392-402`；`src/aistudio_api/infrastructure/browser/browser_engine.py:206,1040-1048`
- 现象：`build_engine_runtime` 在 mode=browser 且 external 时只 WARNING，仍返回 `BrowserEngineRuntime`；`launch_browser` 调 `build_chromium_args` 不传 `proxy_url`，`build_chromium_args` 回退 `settings.proxy_url` → external+browser 的所有浏览/凭据流量走全局 WARP，直接违反本轮不变量，也是 P1-1/P1-2 可达的前置条件。
- 建议：external 下 mode=browser 直接启动失败（fail-fast）或强制切 browserless；若确有需要，至少禁止用全局代理并显式要求每账号出口。

## 4. Q/N

- **Q-1**：`format_converters/media.py:72` 仍用 `settings.proxy_url` 下载图片。图片 URL 来自模型响应、非账号 RPC 凭据；不属于本轮“带账号凭据”范畴，但 external 下仍走 WARP，若担心同 IP 风控可后续统一。
- **Q-2**：RotateCookies 兄弟扇出传 `account_id=sib.id`，但 `client` 非空时被忽略，实际用目标会话出口；合并 jar 场景可接受，建议注释说明。
- **Q-3**：fail-closed 普通 skip 的 WARNING 未限频（见 G）。
- **Q-4**：`import_bundle` pacing、resolver 异常分支、`_recover_web_api_key` 的 skip 分支无测试。
- **Q-5**：`_resolve_probe_proxy` 对显式 `proxy=None` 一律视为“直连已决议”，绕过 fail-closed（`cookie_parser.py:50-53`）。当前 callers 都传已决议值，但这是未来误用的 footgun；建议命名/类型上区分“已决议 proxy”与“未决议”。
- **N-1**：`probe_google_accounts_infinite` 内部多子账号循环无 pacing（单次导入、同出口，风险低）。
- **N-2**：`resolver.py:11-13` 模块 docstring 与 `:500-511` 注释仍写“回退全局 settings.proxy_url”，与新的 fail-closed 决策语义不一致，易误导后续维护者。
- **N-3**：`_fetch_account_email` 的 `allow_bare` 形参收到 True 后又立即传 `allow_bare=True` 给 `_fetch_app_html`（`account_info_refresher.py:1182-1186`），守卫重复但无害。
- **N-4**：`load_cookies_from_string` 的 `proxy` 形参目前无生产 caller（与 P1-2 相关），属半成品接线。

## 5. 实跑门禁输出

在仓库根目录执行（只读）：

```text
$ git status --short
 M src/aistudio_api/api/routes_accounts.py
 M src/aistudio_api/application/account_keepalive.py
 M src/aistudio_api/application/account_service.py
 M src/aistudio_api/infrastructure/account/account_info_refresher.py
 M src/aistudio_api/infrastructure/account/cookie_parser.py
 M src/aistudio_api/infrastructure/account/cookie_refresher.py
 M src/aistudio_api/infrastructure/exit_pool/__init__.py
 M src/aistudio_api/infrastructure/exit_pool/config.py
 M src/aistudio_api/infrastructure/exit_pool/resolver.py
 M tests/conftest.py
?? tests/unit/test_credential_exit_routing.py

$ uv run ruff check .
All checks passed!

$ uv run pyright src tests
0 errors, 0 warnings, 0 informations

$ uv run pytest -q
1904 passed in 55.21s

$ uv run pytest tests/unit/test_credential_exit_routing.py -q
15 passed in 0.61s
```

- 未执行 `git add/commit/checkout/merge/push`；未启动服务/浏览器；未访问 VPS/外网/真实凭据。
- 未执行 `bun run build`（就绪产物不在本轮对象）。

## 6. 只读声明

本轮为 100% 只读独立审查：

- 未修改任何源码、测试、配置、迁移、静态产物；
- 未执行 `git add` / `git commit` / `git checkout` / `git merge` / `git push`；
- 未启动真实服务、真实浏览器或 UI debug 服务；
- 未访问 VPS / 外网 / 真实 Google 账号 / 真实凭据；
- 唯一写入：`docs/dev/rebuild/domains/P1-account/reviews/REV-2.1.5.11-credential-exit-routing.md`。

审查完成时间：2026-11-23。

---

# 二轮复审（REV-2.1.5.11 返工复核，2026-11-23 04:19 UTC）

- 复审角色：独立只读 Reviewer
- 复审方式：逐文件核对返工 diff、测试断言；实跑 ruff/pyright/pytest；未启动服务/浏览器、未跑 build
- 复审对象：本轮新增改动 `session_auth.py`、`session.py`、`cookie_refresher.py`、`engine_runtime.py`、`browserless_session.py`、`local_daemon.py`、`routes_accounts.py`、`account_keepalive.py`、`account_info_refresher.py`、`cookie_parser.py`、`resolver.py` 与相关测试
- 复审结论：**CONDITIONAL**
- 说明：P1-1、P1-2、P2-1、P2-2、P2-4、Q-5（显式 `None` 不绕过 fail-closed）已闭环；P1-3 在 `engine_runtime` 单元层闭环，但**端到端未闭环**（P1-R1）；signer daemon 的 `discoverProxy` 默认使“省略 `-proxy`”不等于直连（P2-R1）；probe-import 503 与日志/文档仍有残留（P2-R2/R3/R4）。因此不能写“可提交”。

## 7. 逐条复核（1–10）

### 7.1 P1-1（session_auth 透传真实 account_id）— **闭环**

- 代码：`session_auth.py:297-305` 已把 `account_id` 透传，且 `"unknown"`/空值传 `None`（`:300-304`）；异常分支由 `pass` 改为 `log.debug`（`:308-309`），不再静默。
- external 语义：`probe_account_benefit_tier` → `_resolve_probe_proxy(account_id, ...)` → `resolve_credential_exit(account_id)`，真实 id 会走账号出口；`"unknown"` 仍按 `no_account` fail-closed（`resolver.py:138-165`）。
- 测试：`test_verify_account_identity_passes_account_id_to_tier_probe`（`tests/unit/test_credential_exit_routing.py:600-645`）真实断言 `calls == ["acc_1"]`，非形式断言。
- 残留 N-1：未覆盖 `"unknown"` 负例（断言不传 account_id）。

### 7.2 P1-2（import_cookies → cookie_refresher 的 proxy 透传）— **闭环**

- 代码：`Session.import_cookies(..., proxy=...)`（`session.py:604-621`）→ `load_cookies_from_string(..., proxy=...)`（`cookie_refresher.py:106-150`）→ `_refresh_session_cookies(proxy=...)`（`:47-100`）。路由层在 external 下解析 `account.id` 出口并透传，skip 时 `do_refresh=False` 但**继续导入**（`routes_accounts.py:1655-1680`）。
- off 模式：`proxy_arg = UNSET_PROXY`，`_refresh_session_cookies` 仍读 `settings.proxy_url`（`cookie_refresher.py:61-74`）。
- 测试：`test_import_prerefresh_uses_account_exit_in_external_mode`（`tests/unit/test_credential_exit_routing.py:682-710`，断言 `proxy == EXIT_ONE` 且 resolver 用真实 id）、`test_import_prerefresh_skips_when_external_has_no_exit`（`:713-739`，断言 `session.calls == []` 且账号仍落库）、`test_import_prerefresh_off_mode_keeps_global_proxy`（`:742-766`，断言 `proxy is UNSET_PROXY`）。
- 备注：因 P2-4 已让 external+browser 启动即失败，该分支在受支持配置（browserless）下不可达；保留的透传属防御性正确实现。

### 7.3 P1-3（生成主链 fail-closed / 不回退全局 socks）— **单元层闭环，端到端未闭环（P1-R1）**

- 代码：`_resolve_exit_proxy` 改为 `resolve_credential_exit`，无出口抛 `RoutingError(code="exit_unavailable", status=503)`（`engine_runtime.py:307-329`）；不再 `or settings.proxy_url`（`:278-290`）。
- browserless_session：`external_exits` 标志下 `_get_direct_http` 不再 `or settings.proxy_url`（`browserless_session.py:884-900`）。
- daemon：external 下不传 `-proxy`（`local_daemon.py:111-114`、`engine_runtime.py:211-219`），signer 默认 proxy 不再等于 `settings.proxy_url`。
- off 模式：`proxy = settings.proxy_url`、`external_exits=False`（`engine_runtime.py:281-296`），`test_off_mode_session_uses_global_proxy` 断言哨兵值（`tests/unit/test_credential_exit_routing.py:853-872`）。
- 测试：`test_external_no_exit_rejects_session_without_global_proxy`（`:786-809`）断言 503/`exit_unavailable` 且 signer 零调用；`test_external_direct_fallback_session_is_proxy_free`（`:812-850`）断言 `_proxy is None` 且 direct_http 传 `None`；`test_signer_daemon_omits_global_proxy_in_external_mode`（`:875-894`）断言命令无 `-proxy`。
- **端到端缺口见 P1-R1**：该 `RoutingError` 不会到达 API 客户端，且会把槽位打成 INVALID。

### 7.4 P2-4（external + browser 启动 fail-fast）— **闭环**

- `build_engine_runtime` 在 external + browser 时直接 `raise RuntimeError(...)`（`engine_runtime.py:401-420`），错误信息明确“browser 引擎使用进程级全局代理，已拒绝启动（fail-fast）”。
- 测试：`test_external_mode_browser_engine_fails_fast`（`tests/unit/test_credential_exit_routing.py:902-911`）断言 `RuntimeError` 且匹配 `external`。
- 备注 Q-1：该异常在应用 lifespan 启动阶段抛出（`app.py:447-450`），会以未捕获异常终止启动；信息清晰，但没有额外的启动摘要日志。

### 7.5 P2-1 / P2-3（合成键、真实 id 后置探测、半成品账号、rotator）— **多数闭环，pipeline 503 UX 见 P2-R2**

- `import_cookies`：external 且无 `req.account_id` 时**不再**预探测，先落库后用真实 `account.id` 做 `_post_save_identity_probe`（`routes_accounts.py:1597-1650`）；有 `req.account_id` 时预探测也用该真实 id（`:1600-1640`）。
- `import_bundle`：external 下跳过合成键预探测，落库后按真实 id 后置探测（`routes_accounts.py:1937-1985`）；off 模式保留旧全局代理预探测（`resolve_credential_exit("")` → off → global）。
- 派发键：external 下所有探测都用真实 id；`cookie_<hex>` 仅作为 `cookie_id` 落库，不再进管理器（`test_import_cookies_external_binds_real_account_id`、`..._uses_explicit_real_id_before_save`、`test_import_bundle_external_binds_real_account_id`，`tests/unit/test_credential_exit_routing.py:919-1011`）。
- 半成品账号：`probe_and_import` 在 external 直接 503，不落库（`routes_accounts.py:1738-1750`；测试 `test_probe_and_import_external_fails_without_identity`，`:497-521` 断言 `service.list_accounts() == []`）。rotator 注册/`clear_cooldown`/`_reconcile_verify_after_import` 在正常导入路径仍完整（`routes_accounts.py:1644-1656,1986-1992`）。
- 残留：external 下 `probe-and-import` 永久 503 的 UX 回归见 **P2-R2**。

### 7.6 P2-2（Free-fresh 邮箱 skip → ok=False，300s）— **闭环**

- `account_info_refresher.py:626-676`：Free-fresh 且邮箱探测无出口时返回 `ok=False, skipped=True, skip_reason`；keepalive 的 `failed>0` → `_retry_delay` 300s（`account_keepalive.py:455-483,295-305`）。
- 测试：`test_free_fresh_email_skip_is_not_ok_and_retries_300s`（`tests/unit/test_credential_exit_routing.py:1019-1071`）断言 `next_due == now + 300` 且 `tier_checked_at` 未变。

### 7.7 Q（skip 日志限频、注释、显式 None）— **部分闭环**

- **显式 None 不绕过 fail-closed：闭环**。`_resolve_probe_proxy` 仅把非空 `str` 视为已决议，`None`/空串一律重跑 resolver（`cookie_parser.py:36-53`）；测试 `test_explicit_none_proxy_does_not_bypass_fail_closed`（`tests/unit/test_credential_exit_routing.py:299-319`）断言客户端未构造。
- **注释更新：闭环**。`resolver.py:10-16`、`:380-386`、`:503-511` 已改为“fail-closed / 应急阀，绝不回退全局代理”。
- **skip 日志 60s 限频：部分闭环（P2-R3）**。`resolver._log_skip` 已按 `(account, reason)` 60s 限频（`resolver.py:53,101-121`），但 `AccountInfoRefresher._skipped_result` 每次仍 `logger.warning`（`account_info_refresher.py:510-533`），keepalive 每轮也打印 skipped 列表（`account_keepalive.py:455-470`）。且没有针对 `_log_skip` 限频的测试（现有测试只覆盖应急阀限频，`tests/unit/test_credential_exit_routing.py:322-339`）。

### 7.8 新风险评估

**P1-R1（残留，最高优先级）`exit_unavailable` 端到端不可达，且瞬时无出口会把槽位打成 INVALID**

- 位置：`engine_runtime.py:319-325`（抛 `RoutingError`）→ `instance_pool.py:636-655`（`except BaseException`，置 `slot.state=INVALID`，`raise`）→ `instance_manager.py:1349-1352`（`except Exception` → `InstanceNotReadyError`，`record.state=INVALID`）→ `routing_pipeline.py:824-833`（`InstanceNotReadyError` 不是 `RoutingError`，被 `except Exception` 吞掉并按“试下一个候选”处理）→ `:512-538` 最终 `SurfaceCapacityError`（503，code 非 `exit_unavailable`）。`eligible_instances` 只收 UNSTARTED/SLEEPING/STARTING/ACTIVE（`instance_manager.py:324-342`），`wake` 也拒绝 INVALID（`:606-618`），所以该槽位**不会自动恢复**，需手动 restart/rebind；`app.py:734-741` 的 `InstanceError` 处理器也只会回 409 `instance_not_ready`。
- 复现：external + browserless，账号无出口（管理器宕机/未分配）→ 冷启动或唤醒该槽位 → `create_session` 抛 503 `exit_unavailable` → 客户端实际收到 503 `surface_capacity`（或 409），槽位保持 INVALID；管理器恢复后同一请求仍 503，直到手动重启实例。
- 影响：安全不变量（不走全局 socks）成立；但 (a) 用户要求的 `exit_unavailable` 契约未实现；(b) 瞬时故障被放大成粘性 INVALID，违反“无出口默认 skip、下一轮重试”的期望。
- 建议：
  - 在 `InstanceManager._activate` 的通用 `except Exception` 之前加 `except RoutingError: raise`，让 `routing_pipeline.acquire` 的 `except (RoutingError, TierRequiredError): raise`（`:824-825`）真正生效；并在 `pool.activate` 对 `RoutingError` 不置 INVALID（保持 UNSTARTED/SLEEPING，允许下次重试）。
  - 或更彻底：在 routing 准入阶段先解析出口（`resolve_credential_exit`），无出口时在 `begin()` 直接抛 `RoutingError(exit_unavailable)`，根本不唤醒/启动该槽位。
  - 增加端到端测试：external 无出口 → 请求 503 `exit_unavailable` → 槽位仍可唤醒；管理器恢复后下一次请求成功。

**P2-R1（残留）signer daemon “省略 `-proxy`” ≠ 直连；应急阀在默认 signer 传输下不保证直连**

- 证据：`waa-signer/cmd/waa-signer/main.go:1157` 的 `-proxy` 默认值是 `discoverProxy()`；`discoverProxy` 依次读取 `AISTUDIO_PROXY/HTTPS_PROXY/https_proxy/HTTP_PROXY/http_proxy`，再探测 `127.0.0.1:7890/10808`（`:883-890`；`waa-signer/README.md:79`）。请求侧 `effectiveProxy := strings.TrimSpace(proxyOverride); if effectiveProxy == "" { effectiveProxy = p.proxy }`（`main.go:519-521`）。
- 后果：
  - external 下 Python 只是不传 `-proxy`（`local_daemon.py:111-114`），但 daemon 仍会用环境/自动探测代理作为默认；若宿主设置了 `HTTPS_PROXY`（本仓外部网络约定常指向 `127.0.0.1:10809`），**任何漏传 proxy 的 signer 请求都会走该代理，而不是直连**。
  - 应急阀（`proxy=None`）在 `signer_rpc`（默认传输）下也不直连：`signer_client.py:371-374` 仅在 `proxy` 真值时写入，daemon 回落到 `p.proxy`。`browserless_session.py:724` 的 `send_rpc(proxy=self._proxy)` 会因 `None` 省略该字段。
  - 测试 `test_signer_daemon_omits_global_proxy_in_external_mode`（`tests/unit/test_credential_exit_routing.py:875-894`）只断言命令中没有 `-proxy`，**没有**证明 daemon 最终无代理，属 false confidence。
- 建议：external 下显式传 `-proxy ""`（Go flag 显式空值覆盖 `discoverProxy` 默认，`main.go:519-521` 即得 `effectiveProxy=""`），或在子进程环境中剥离 `AISTUDIO_PROXY/HTTPS_PROXY/HTTP_PROXY/ALL_PROXY` 并阻止自动探测；同时增加一个能证明“无默认代理”的测试（例如断言命令含 `-proxy ""` 或子进程 env 已剥离，或加 Go 侧单测）。若短期不改，至少要在日志/文档中明确：应急阀只对 httpx 路径保证直连，signer 传输仍可能使用环境代理。

**P2-R2（残留）external 下 `probe-and-import` 永久 503 的 UX 回归**

- 位置：`routes_accounts.py:1738-1750`。多账号探活在 binding 前无真实 account id，选择整段 503。
- 评估：安全上正确（不产生合成绑定/半成品账号），但**批量导入在 external 下完全不可用**，只剩 `import-cookies`（单账号、需 email/tier 或后置探测）。不算阻断安全，但属明显 UX/功能回归，按 P2 跟踪。
- 建议“先落库再探测”可行方案：为整份 Cookie jar 先创建一个**临时/正式账号**（使用 `req.account_id` 或生成的真实 id 落库）→ 用该真实 id 解析一次出口 → 调 `probe_google_accounts_infinite(account_id=real_id)` → 按返回的 `auth_user/email/tier` 拆分为多个账号（同一出口可先复用，随后各自 `assign`）→ 删除/保留临时账号。这样既无合成键、无孤儿绑定，也不需要 503。另一条路是给管理器增加“瞬时/可释放”的导入出口。

**P2-R3（残留）skip 日志限频只覆盖 resolver 层**

- 见 7.7。建议在 `_skipped_result`/keepalive 复用同一个限频器，或把 per-account skip 日志降到 debug；补 `_log_skip` 限频测试。

**P2-R4（残留，文档）** 见第 9 节。

### 7.9 回归：off 模式全链不漂移 — **通过（测试级）**

- 生成：`test_off_mode_session_uses_global_proxy`（`tests/unit/test_credential_exit_routing.py:853-872`）。
- 元数据：`test_off_mode_uses_global_proxy_unchanged`（`:187-204`）。
- 导入：`test_import_prerefresh_off_mode_keeps_global_proxy`（`:742-766`）。
- Cookie 探测/RotateCookies：`test_resolve_credential_exit_off_mode_returns_global`（`:585-592`）与既有 `test_probe_headers` 等通过。
- 门禁 1918 passed（见第 10 节），ruff/pyright 全绿。

## 8. 二轮残留 P0/P1/P2

- **P0**：无。
- **P1-R1**：`exit_unavailable` 端到端不可达 + 瞬时无出口把槽位打成粘性 INVALID（见 7.8）。
- **P2-R1**：signer daemon `discoverProxy` 默认使省略 `-proxy` ≠ 直连；应急阀在 signer 传输下不保证直连；相关测试仅检查命令不含 `-proxy`。
- **P2-R2**：external 下 `probe-and-import` 永久 503，批量导入不可用（建议先落库再探测）。
- **P2-R3**：skip 日志 60s 限频只在 resolver 层；refresher/keepalive 仍每账号每轮打印；缺限频测试。
- **P2-R4**：`CONTRACT.md` 等文档未补新语义（见第 9 节）。

## 9. 文档缺口（本轮只标注，不修改）

建议在 `docs/dev/rebuild/domains/P1-account/research/exit-pool/CONTRACT.md` 补齐：

1. `AISTUDIO_EXIT_DIRECT_FALLBACK`：默认关、truthy 取值、只直连、绝不回退 `settings.proxy_url`、WARNING 限频。
2. external 模式**仅支持 browserless**，browser 引擎启动即 fail-fast（`build_engine_runtime`）。
3. 生成主链无出口语义：503 `exit_unavailable`（以及当前 P1-R1 的端到端传播限制，修复后删除该免责说明）。
4. `POST /accounts/probe-and-import` 在 external 下的 503 语义与“请改用单账号导入 / 先落库再探测”的迁移路径。
5. `refresh_accounts` API 新增 `skipped/skip_reason` 字段与 300s 重试口径。
6. import 预探测/后置探测约定：external 必须用真实 `account.id`，禁止合成 `cookie_<hex>` 进管理器。
7. signer daemon 的 `-proxy` / `discoverProxy` 默认与 external 期望（建议显式 `-proxy ""` 或剥离 env；否则文档需说明应急阀只对 httpx 保证直连）。
8. 轮换（RotateCookies）池会话 vs 裸路径的口径：有池会话走 signer/session；无会话用账号出口，`no_exit` 不扇出。

另可同步 `docs/dev/rebuild/domains/P1-account/research/exit-pool/EVALUATION.md` 的 F 章节（“故障时只能回退全局 socks”已过时）。

## 10. 二轮 Q/N

- **N-1**：P1-1 的 `"unknown"` 负例未测。
- **N-2**：`detect_account_tier` 无出口回 502（`routes_accounts.py`），与生成侧 503 不一致；属管理接口，可接受但建议统一。
- **N-3**：`_post_save_identity_probe` 调 `update_account_tier` 未传 `checked_at`（`account_store.py:1055-1075`），导入后新账号 `tier_checked_at` 仍为空，下一轮 keepalive 会再探测一次；功能无害，可顺手补时间戳。
- **N-4**：external + `req.account_id` 预探测使用请求 id；若 `save_account` 的 `_safe_account_dir` 清洗后 `id` 变化，仍可能出现一次错配（干净 id 无此问题，边界项）。
- **N-5**：`import_bundle` 的 0.3s pacing 仍无测试；`_log_skip` 限频无测试。
- **Q-1**：external+browser fail-fast 是 startup RuntimeError；是否需要在 UI/日志给出更友好的启动失败摘要，由主控决定。
- **Q-2**：旋转兄弟扇出传了 `account_id=sib.id`，但 `client` 非空时被忽略（`cookie_parser.py` 中 `if client is not None` 分支）；合并 jar 场景可接受，建议注释。
- **Q-3**：应急阀日志文案“改为直连（不走全局代理）”在 signer 传输下可能误导（见 P2-R1）。

## 11. 二轮实跑门禁输出

```text
$ uv run ruff check .
All checks passed!

$ uv run pyright src tests
0 errors, 0 warnings, 0 informations

$ uv run pytest -q
1918 passed in 57.08s
```

- 未执行 `git add/commit/checkout/merge/push`；未启动服务/浏览器/UI debug；未访问 VPS/外网/真实凭据；未跑 `bun run build`。
- 现有前端/协议错误渲染代码本身能正确渲染 `RoutingError`（`error_mapping.py:152-182`、`format_errors.py:216-258`）；P1-R1 是异常在 instance 层被转换/吞掉，不是渲染器缺失。

## 12. 二轮只读声明

本轮仍为 100% 只读：

- 未修改任何源码、测试、配置、迁移、静态产物；
- 未执行 `git add` / `git commit` / `git checkout` / `git merge` / `git push`；
- 未启动真实服务、真实浏览器或 UI debug 服务；
- 未访问 VPS / 外网 / 真实 Google 账号 / 真实凭据；
- 唯一写入：本文件追加的「二轮复审」章节。

二轮复审完成时间：2026-11-23 04:19 UTC。

---

# 三轮复审（P1-R1 + P2-R1..R4 返工复核，2026-11-23 04:48 UTC）

- 复审角色：独立只读 Reviewer
- 复审方式：逐文件核对返工 diff 与测试断言；实跑 Python 门禁、Go 纯逻辑包 test/vet/gofmt；未跑 build、未启动服务/浏览器
- 复审对象：`instance_pool.py`、`instance_manager.py`、`routing_pipeline.py`、`domain/errors.py`、`engine_runtime.py`、`local_daemon.py`、`waa-signer/cmd/waa-signer/main.go`、`waa-signer/internal/proxydefault/`、`routes_accounts.py`、`account_info_refresher.py`、`account_keepalive.py`、`CONTRACT.md` 及新增测试
- 复审结论：**CONDITIONAL**
- 摘要：**P1-R1 已端到端闭环**（三层全覆盖 + 真实 503 + 槽位 UNSTARTED + 恢复可重建）；P2-R1 代码/测试闭环；P2-R2 主路径闭环但有“重复锚点”残留（P2-A）；P2-R4 文档仍有过时/矛盾（P2-B）。因此不能写“可提交”。

## 13. 逐条复核（1–7）

### 13.1 P1-R1 端到端 — **闭环**

- 失败判定单一化：`domain/errors.py:140-155` 的 `is_exit_unavailable_error` 只匹配 `RoutingError` 且 `code == "exit_unavailable"`，不会误伤其它 code（满足“不能把所有 RoutingError 都当 exit_unavailable”）。
- 三层覆盖（不是单元层打补丁）：
  - 池层：`instance_pool.py:644-660` 在 `exit_unavailable` 时先 `_close_runtime`，把 `slot.state` 置 `UNSTARTED` 并原样 `raise`；`instance_pool.py:707-716` 对 `exit_unavailable` **立即 re-raise，不做 `_load_attempts` 重试**（避免同一次失败放大管理器调用）。
  - 管理器层：`instance_manager.py:294-308`（startup 保留 `UNSTARTED` 且不中断其它槽位）、`:1070-1082`（engine rebuild 同样保留可重试，且 `return` 只退出当前 `_rebuild` 协程）、`:1364-1376`（`_activate` 对 `exit_unavailable` 置 `UNSTARTED` 后 **re-raise 原始 503**，不再包装成 409 `InstanceNotReadyError`）。
  - 路由层：`routing_pipeline.py:778-790`（批量 wake 遇到 exit_unavailable 仅 debug，不刷 WARNING）、`:831-840`（`acquire` 对 `RoutingError` 直接终止候选循环，不再被 `except Exception` 吞成 surface_capacity）。
- 测试牙口：`tests/unit/test_credential_exit_routing.py:1312-1407` 用真实 `InstancePool + InstanceManager + RoutingPipeline`：
  - startup 后槽位为 `UNSTARTED`（`:1368`）；
  - `pipeline.begin/acquire` 抛 `RoutingError`，`status_code == 503`、`code == "exit_unavailable"`（`:1375-1376`）；
  - `signer.method_calls == []`，零上游调用（`:1377`）；
  - 协议渲染：OpenAI `code == "exit_unavailable"`、Gemini `type == "exit_unavailable"`（`:1380-1391`）；
  - 槽位仍为 `UNSTARTED`（`:1393-1395`）；
  - `resolver.fail=False` 后同一槽位重建为 `ACTIVE`（`:1397-1405`）。
- “不换候选掩盖”：`test_exit_unavailable_does_not_advance_to_next_candidate`（`:1410-1476`）断言 `wake` 抛 503 后 `begin_request` 从未被调用（`begin_calls == []`）。
- 半初始化 runtime：`engine_runtime.py:278-296` 在构造 `BrowserlessSession` **之前**解析出口，失败即抛；`socket/context/session` 只在 `create_session` 正常返回后才写入（`instance_pool.py:717-720`），失败路径 `_close_runtime` 后无残留句柄。
- off 不变：`engine_runtime.py:281-296` 在无 resolver 时仍取 `settings.proxy_url` 且 `external_exits=False`；二轮用例 `test_off_mode_session_uses_global_proxy`（`:853-872`）继续通过。

**结论：无 P1 残留。** 备注：混合池（部分账号有出口、部分没有）时是否 503 取决于候选排序（`routing_pipeline.py:641-650` 的 warm 只是末位 tie-break），若排到无出口账号则整请求 503，即使别的账号健康——这是“不换候选掩盖”的直接后果，属策略选择，列 Q-1 而非缺陷。

### 13.2 P2-R1 signer 代理发现 — **代码/测试闭环，构建仍有环境限制**

- Go 纯逻辑：`waa-signer/internal/proxydefault/proxydefault.go:11-24` 的 `Resolve` 语义与要求一致；`proxydefault_test.go` 覆盖 5 种组合并断言 `discover` 调用次数（默认 1 次，其余 0 次），是真实断言。
- 接线：`main.go:883-895` 引入 `proxydefault.Resolve`；`:1165-1185` 把 `-proxy` 默认改为 `""`，新增 `-no-proxy-discovery`，并在 `flag.Parse()` 后用 `flag.Visit` 判定 `proxyExplicit`。`flag.Visit` 只访问显式设置的 flag，`-proxy ""` 会被记录，因此“显式空串优先”可靠（Go 单测覆盖 `Resolve`，接线本身未单测，见 Q-3）。
- Python：`local_daemon.py:112-134` 在 external 下不传 `-proxy`、追加 `-no-proxy-discovery`；`:136-150` 的 `_child_env` 大小写不敏感地剥离 `AISTUDIO_PROXY/HTTP_PROXY/HTTPS_PROXY/ALL_PROXY/NO_PROXY`；off 模式命令与环境保持不变。
- Python 测试：`tests/unit/test_local_daemon.py:22-57` 真实断言 external 含开关且无 `-proxy`、子进程 env 剥离（含小写变体）且保留 `AISTUDIO_KEEP_ME`；off 模式保留 `-proxy` 与 `HTTPS_PROXY`。
- **构建限制如实记录**：本机 `go build ./cmd/waa-signer` 失败于 `internal/waa/mozjs: build constraints exclude all Go files`（cgo/SpiderMonkey），因此**不能宣称全量构建通过**；本次只验证了纯逻辑包。另：本机 `waa-signer/bin/waa-signer.exe` 是 2026-11-10 的旧产物（gitignored），实测对 `-no-proxy-discovery` 报 `flag provided but not defined`；external 部署前必须用 cgo 工具链重建该二进制（Q-4）。
- env 剥离副作用：只剥离代理相关变量，未触碰 `WAA_DNS_SERVERS/AISTUDIO_DNS_SERVERS` 与 TLS/时区/语言变量，DNS/TLS 无影响；剥离 `NO_PROXY` 对显式 per-request 代理无实质影响（Q-5）。

**结论：P2-R1 闭环（源码与纯逻辑测试）；全量构建不可验证，需部署侧重建二进制。**

### 13.3 P2-R2 批量 probe-import — **主路径闭环，重复锚点残留（P2-A）**

- 实现：`routes_accounts.py:1786-1879` 在 external 下先 `save_account_from_cookies` 落库真实 id 的锚点（`:1791`），用锚点 id 解析出口（`:1797`），再 `probe_google_accounts_infinite(..., account_id=anchor.id, proxy=...)`（`:1803`）；u/0 回写锚点，u/N 用真实 id 新建（`:1820-1845`）；缺 email/tier 的账号再用各自真实 id 探测。
- 无出口不整体 503：`metas=[anchor]`、`skipped_ids.append(anchor.id)`、原因写入 `last_refresh_error`，返回 200 + `skipped/skip_reason/probe_failed`（`:1814-1830`）。
- 部分失败保留并标记：`_post_save_identity_probe`（`:174-260`）把 `email probe failed/...` 写入 `last_refresh_error`，响应 `probe_failed` 携带原因。
- 合成键：external 下所有 `resolve_credential_exit` 都用真实 id；`cookie_<hex>` 只作 `cookie_id`。测试 `:501-543`（无出口保留真实锚点、零探测、`resolver.calls == [(real_id,"chat")]`）、`:545-588`（拆分 2 个真实 id、probe 1 次且 `account_id` 为真实 id）、`:591-637`（部分失败保留并标记）均真实断言。
- **P2-A（残留）重复锚点/孤儿锚点**：
  - `save_account_from_cookies` 的去重只在 `account_id is None and email` 时按 `email+auth_user` 命中（`account_store.py:928-939`）。external probe-import 的锚点固定 `email=None` 且不传 `account_id`（`routes_accounts.py:1791-1799`），因此**每次 probe-import 都生成新锚点 id**；探测成功后 u/0 回写新锚点，旧的 u/0 账号（同邮箱）仍然存在 → 重复账号。
  - 若 `probe_google_accounts_infinite` 返回结果里没有 `auth_user=="0"`，锚点不会被放入 `metas`（只有 u/N 被 append），但已落库 → 成为未注册 rotator 的孤儿半成品。
  - 建议：锚点使用稳定的确定性 id（如按 Cookie 指纹/邮箱派生），或探测前先按稳定指纹匹配已存在账号；拆分完成后删除/合并未被 u/0 认领的锚点。

### 13.4 P2-R3 日志限频 — **refresher/keepalive 共用已闭环，但存在跨 logger 双行（Q）**

- `account_info_refresher.py:100-121` 提供 `skip_log_due` 与 `reset_skip_log_throttle`，conftest 已重置（`tests/conftest.py:26-31`）；`account_keepalive.py:468-479` 直接复用 `skip_log_due`，共享同一字典。
- 测试：`test_refresher_skip_log_is_throttled_per_account_and_reason`（`:1484-1506`）两轮只 1 条（按 logger `aistudio.account_refresher` 过滤）；`test_keepalive_skip_log_is_throttled_across_rounds`（`:1509-1571`）两轮只 1 条（`metadata refresh skipped`）。
- **Q-2**：resolver 的 `_log_skip`（`resolver.py:101-121`）与 refresher 的 `skip_log_due` 是**两个独立限频字典**，首次 skip 时各有 1 条 WARNING（共 2 行）；测试按 logger 过滤，未证明“总计 1 条”。keepalive 测试用 stub refresher，未覆盖“真实 refresher 先记录、keepalive 应被抑制”的共享交互。

### 13.5 P2-R4 文档 — **未完全闭环（P2-B）**

`CONTRACT.md` 已新增 §6.1–§6.7，但存在与实际实现不一致/过时之处：

- **§6.6 过时**：仍写“signer 侧修复在另一路”“在该修复落地前，应急阀只对 httpx 路径保证直连，signer 传输仍可能使用环境代理”。本轮 Go 侧已落地 `proxydefault` + `-no-proxy-discovery` + env 剥离，应改为当前实现（未显式 `-proxy` 且 `-no-proxy-discovery` → 空默认；显式 `-proxy`（含 `""`）优先；Python external 传开关并剥离 env）。
- **§6.1 “单次 skip 只产出一条 WARNING” 不准确**：resolver 与 refresher 各一条（见 13.4）。
- **§6.4 “不建半成品” 与实现矛盾**：无出口/探活失败时锚点（`email=None`）仍保留落库（这是本轮明确要求“保留并标记”），应改为“保留一个标记了原因的锚点/半成品，供后续重试或清理”，或说明清理策略。
- 其余 §6.1（应急阀默认关/truthy/直连/fail-closed）、§6.2（external 仅 browserless + fail-fast）、§6.3（503 端到端/不 INVALID/不换候选）、§6.5（skip/300s）、§6.7（RotateCookies）与实现一致。

### 13.6 新风险逐项

- **槽位 UNSTARTED 后的半初始化 runtime/句柄**：无。出口在 `BrowserlessSession` 构造前解析（`engine_runtime.py:283-292`），`socket/session` 只在成功返回后赋值（`instance_pool.py:717-720`），失败先 `_close_runtime`。
- **RoutingError re-raise 误伤其它 code**：无。`is_exit_unavailable_error` 精确匹配 `code=="exit_unavailable"`（`errors.py:143-155`）；其它 RoutingError 仍按原逻辑包装。
- **显式 `-proxy ""` 的 `flag.Visit` 可靠性**：可靠（Visit 只遍历显式设置项），但该接线未单测（Q-3）；纯逻辑 `Resolve` 已测。
- **剥离子进程 env 影响 DNS/TLS**：无。仅剥离代理变量；`WAA_DNS_SERVERS/AISTUDIO_DNS_SERVERS`、TLS、locale/timezone 均保留（Q-5 仅 `NO_PROXY` 低风险）。
- **旧 signer 二进制**：本机 `waa-signer/bin/waa-signer.exe`（gitignored，2026-11-10）拒绝 `-no-proxy-discovery`；external 运行需先重建（Q-4）。

### 13.7 回归

- off 全链不漂移：生成 `test_off_mode_session_uses_global_proxy`、元数据 `test_off_mode_uses_global_proxy_unchanged`、导入 `test_import_prerefresh_off_mode_keeps_global_proxy`、Cookie 探测 `test_resolve_credential_exit_off_mode_returns_global` 全部通过；`1930 passed`。
- static 未变：`git status --short src/aistudio_api/static` 为空。

## 14. 三轮残留 P0/P1/P2

- **P0**：无。
- **P1**：无（P1-R1 端到端闭环）。
- **P2-A（probe-import 重复/孤儿锚点）**：`routes_accounts.py:1786-1845` + `account_store.py:928-939`；external 每次 probe-import 新建 `email=None` 锚点 → 重复 u/0；无 u/0 时锚点不进 `metas` → 孤儿半成品。建议确定性锚点 id 或探测后合并/清理。
- **P2-B（CONTRACT §6 不一致）**：`docs/dev/rebuild/domains/P1-account/research/exit-pool/CONTRACT.md` 的 §6.6 过时、§6.1 “单条 WARNING” 不准确、§6.4 “不建半成品” 与保留锚点矛盾。按 13.5 修订。

## 15. 三轮 Q/N

- **Q-1**：混合池候选排序无“出口可用性”权重（`routing_pipeline.py:641-650`），排到无出口账号即整请求 503；若希望“有健康账号就服务”，应在候选阶段排除/重排无出口账号，而不是依赖 `acquire` 终止。属策略选择。
- **Q-2**：resolver `_log_skip` 与 refresher `skip_log_due` 双字典，首次 skip 总计 2 条 WARNING；测试按 logger 过滤未证“总计 1 条”。
- **Q-3**：`-proxy ""` 的 `flag.Visit` 接线未单测（纯 `Resolve` 已测）。
- **Q-4**：本机旧 `waa-signer.exe` 拒绝新 flag；`go build ./cmd/waa-signer` 因 mozjs/cgo 构建约束无法完成，部署前必须重建 signer。
- **Q-5**：子进程 env 剥离了 `NO_PROXY`；对显式 per-request 代理无实质影响，DNS/TLS 变量未动。
- **Q-6**：`routing_pipeline._wake` 的 exit_unavailable 分支实际很难触达（`wake_eligible` 已逐账号处理），属防御性代码。
- **Q-7**：engine rebuild 遇 exit_unavailable 会把原因加入 `errors` 并最终抛 `InstanceRebindError`（槽位仍 UNSTARTED），是否希望“重建部分失败”中止引擎切换需产品确认。
- **Q-8**：keepalive 限频测试用 stub refresher，未覆盖真实共享限频交互。
- **N-1**：`probe-import` 外部锚点使用 `cookie_id=cid`、u/N 共享同一 `cookie_id`，不影响出口键，符合“同 jar”语义。
- **N-2**：`_bootstrap_runtime` 对 exit_unavailable 不重试（已加断言外的代码级保证），测试未显式断言“只解析一次”。

## 16. 三轮实跑门禁输出

Python：

```text
$ uv run ruff check .
All checks passed!

$ uv run pyright src tests
0 errors, 0 warnings, 0 informations

$ uv run pytest -q
1930 passed in 52.61s

$ git status --short src/aistudio_api/static
(empty)
```

Go（`waa-signer`，`GOTOOLCHAIN=auto` 使用本地缓存的 1.27.0，`GOPROXY=off`）：

```text
$ go test -count=1 -v ./internal/proxydefault
=== RUN   TestResolve
--- PASS: TestResolve (0.00s)
    --- PASS: TestResolve/default_discovers_proxy
    --- PASS: TestResolve/no-proxy-discovery_skips_discovery
    --- PASS: TestResolve/explicit_proxy_wins_over_discovery
    --- PASS: TestResolve/explicit_proxy_wins_over_no-proxy-discovery
    --- PASS: TestResolve/explicit_empty_proxy_disables_discovery
PASS
ok  	github.com/Mag1cFall/AIStudio2API/internal/proxydefault	0.056s

$ go vet ./internal/proxydefault
(no output)

$ gofmt -l internal/proxydefault cmd/waa-signer/main.go
(no output)

$ go build ./cmd/waa-signer
internal/waa/mozjs: build constraints exclude all Go files
```

- 全量 signer 构建**未通过/未验证**（cgo/SpiderMonkey 构建约束），符合“不得宣称已全量构建”。
- 未跑前端 build；未启动服务/浏览器/UI debug；未访问 VPS/外网/真实凭据。

## 17. 三轮只读声明

本轮仍为 100% 只读：

- 未修改任何源码、测试、配置、迁移、静态产物、Go 源码/二进制；
- 未执行 `git add` / `git commit` / `git checkout` / `git merge` / `git push`；
- 未启动真实服务、真实浏览器、真实 signer 服务或 UI debug 服务（仅执行 `waa-signer.exe -h` / `-no-proxy-discovery -h` 的本地 help，未启动监听）；
- 未访问 VPS / 外网 / 真实 Google 账号 / 真实凭据；
- 唯一写入：本文件追加的「三轮复审」章节。

三轮复审完成时间：2026-11-23 04:48 UTC。

---

# 四轮确认（P2-A/P2-B 收尾，2026-11-23 05:02 UTC）

- 复审角色：独立只读 Reviewer
- 复审方式：核对幂等锚点实现与测试、CONTRACT §6 一致性；实跑 Python 门禁、grep 管理器键路径；未跑 build、未启动服务/浏览器
- 复审对象：`routes_accounts.py`（P2-A 锚点）、`CONTRACT.md`（P2-B）、`tests/unit/test_credential_exit_routing.py`
- 复审结论：**PASS（可提交）**
- 说明：0 P0/P1/P2。仅剩测试缺口（N-1/N-2）与若干边界观察（Q），不阻塞提交。

## 18. P2-A 幂等锚点（逐条）

### 18.1 锚点派生只依赖稳定身份 Cookie — **PASS**

- `routes_accounts.py:262-300`：`_ANCHOR_IDENTITY_COOKIES = ("SAPISID","APISID","SID")`，按优先级取**第一个非空值**，`sha256("google:"+value)[:12]` → `acc_import_<12hex>`。
- 明确排除滚动令牌：注释与实现均不读取 `__Secure-1PSIDTS`/`SIDCC`（`:267-269,280-285`）。
- 无稳定身份 Cookie 时的 fallback 只对 **cookie 名称@domain** 排序哈希（`:286-300`），不哈希取值、不哈希整包 values；仅用于无法探测的畸形 jar，属低风险兜底（Q-2）。

### 18.2 1PSIDTS 轮换后仍落同一记录 — **实现 PASS，测试缺口 N-1**

- 代码层面：锚点只由 SAPISID/APISID/SID 决定，`__Secure-1PSIDTS`/`SIDCC` 变化不改变 `acc_import_<hash>`，因此再次导入会命中同一 `anchor_id`（`:1853-1865`）。
- **测试缺口**：现有 `test_probe_and_import_external_is_idempotent_for_same_cookie`（`tests/unit/test_credential_exit_routing.py:641-677`）两次使用**完全相同**的 cookie 串，未覆盖“仅 `__Secure-1PSIDTS`/`SIDCC` 变化、SAPISID 不变”的场景。建议补：两次导入之间替换 `__Secure-1PSIDTS`，断言 `first.accounts[0].id == second.accounts[0].id` 且账号数不变。代码按实现判断正确，故仅记 N。

### 18.3 u/0 认领/合并与锚点清理 — **PASS（claim 有测；merge 测试缺口 N-2）**

- u/0 无同身份账号：回写锚点 email/tier 并保留锚点（`:1893-1920`）；测试 `:680-709` 断言只剩 1 条、无 `acc_import_` 孤儿、resolver 仅以真实锚点 id 解析。
- u/0 已有同身份账号：`_find_identity_account(email+auth_user, exclude_id=anchor.id)`（`:303-318`）命中后 `save_account_from_cookies(account_id=existing.id)` 合并，再 `delete_account(anchor.id)`（`:1900-1922`）。因 `exclude_id` 与精确 id 删除，不会误删被合并账号。
- 无 u/0 的探测成功：`anchor_claimed=False` → 删除锚点（`:1950-1953`）；测试 `:712-741` 断言无 `acc_import_` 记录、resolver 无 `cookie_` 键。
- 失败/无出口：`metas=[anchor]` + 标记 `last_refresh_error`，同一 Cookie 反复导入仍是同一条（`:1886-1890`；测试 `:744-784` 断言两次 id 相同、账号数 1、`skip_reason=no_exit`）。
- **测试缺口 N-2**：`_find_identity_account` 的 merge 分支（已存在同 email+u/0 → 合并进真实账号并删除锚点）**没有测试**。按代码推演正确（claim 前排除锚点、merge 用 `existing.id`、只删锚点 id），但建议补一条“先建 `acc_real` 同 email/u0，再 probe-import → 账号数仍 1、`acc_real` 保留、无 `acc_import_`”的用例，同时覆盖“删除锚点不误删真实账号”。

### 18.4 管理器绑定键与可见性 — **PASS**

- 管理器键无 `cookie_<hex>`：9 处 `resolve_credential_exit(` 调用全部传真实 id 或 `""`（off/无 resolver 路径），无一传 `cid`/`cookie_*`（grep 见第 21 节）；测试 `:677,741` 显式断言 `not account_id.startswith("cookie_")`。
- `cookie_<hex>` 仍只作 `cookie_id`（`import_cookies`/`import_bundle`/`probe-import`），符合“合成值不得进管理器”。
- 可见性：锚点与最终 u/0 账号都使用 `acc_import_<hash>` 作为 `id`（测试 `:674`、`:706`），且 `list_accounts` 无过滤、前端无 `acc_import_` 特判。作为“恰好 1 条可重试占位”这是可接受的；但该 id 会长期出现在真实 u/0 账号上，占位时也以普通 Free 账号展示（name=前缀、email 为空、`last_refresh_error` 有原因）。建议（N-3）在 UI/字段上标注占位或说明 `acc_import_` 语义，不影响本轮提交。

## 19. P2-B CONTRACT §6 一致性 — **PASS**

- §6.1 已改为“两层各自独立限频：`resolver._log_skip` 一条 + refresher/keepalive 共用 `skip_log_due` 一条；首次 skip 最多 2 条 WARNING”，与 `resolver.py:101-121`、`account_info_refresher.py:100-121`、`account_keepalive.py:468-479` 一致；Q-2 表述准确。
- §6.4 与实现一致：稳定身份 Cookie 派生 `acc_import_<hash>`、排除 1PSIDTS/SIDCC、u/0 认领/合并并删锚点、无 u/0 删锚点、失败仅 1 条占位并写 `last_refresh_error`、0.3s pacing。
- §6.6 已更新为“代理发现修复已落地”：Python 不传 `-proxy` 并传 `-no-proxy-discovery`、剥离代理 env；Go `proxydefault.Resolve` 显式 `-proxy`（含 `""`）优先 / `-no-proxy-discovery` 不探测 / 默认才 `discoverProxy()`；并明确旧二进制需用 cgo/SpiderMonkey 重建。与 `local_daemon.py:112-150`、`main.go:883-895,1165-1185` 一致。
- §6.2/§6.3/§6.5/§6.7 与三轮回审结论一致，未发现新的文档矛盾。

## 20. 新风险复核

- **确定性锚点与多账号 u/0,u/1 冲突/错并**：无。锚点只由 jar 级稳定身份 Cookie 派生，u/1.. 用 `email+auth_user` 的真实生成 id；u/0 才认领/合并。同一邮件+u/0 即同一 Google 账号，合并语义正确。仅“探活返回多个 u/0”属异常输入（Q-4，低风险）。
- **显式 account_id 与 email 去重竞态**：`import_cookies` 显式 `account_id` 仍优先于 email 去重（`account_store.py:928-957`），与既有 off 行为一致；并发 probe-import 的 `_find_identity_account`→save→delete 无跨请求锁（Q-5，用户驱动、低概率）。本轮未改变该路径。
- **删除锚点误删真实账号**：锚点 id 形如 `acc_import_<12hex>`，真实生成 id 形如 `acc_<8hex>`（`account_store.py:315-319`），格式不重叠；`delete_account` 按精确 id 删除，且 merge 分支 `exclude_id=anchor.id`。正常路径无误删；仅用户主动指定同形 id 的极端情况可能碰撞（Q-6）。
- **用户可见列表/状态**：见 18.4；单条确定性占位可接受，建议加标记（N-3）。

## 21. 实跑门禁输出

```text
$ uv run ruff check .
All checks passed!

$ uv run pyright src tests
0 errors, 0 warnings, 0 informations

$ uv run pytest -q
1934 passed in 53.95s

$ git status --short src/aistudio_api/static
(empty)

$ git diff --stat src/aistudio_api/static
(empty)
```

管理器键 grep（`resolve_credential_exit(` 全部调用点）：

```text
cookie_parser.py:54            account_id or ""        (内部探测；调用方均为真实 id / off)
account_info_refresher.py:532  account_id
routes_accounts.py:196         account.id
routes_accounts.py:932         account_id
routes_accounts.py:1119        account_id
routes_accounts.py:1710        req.account_id or ""   (external 需 req.account_id；off 为全局)
routes_accounts.py:1766        account.id
routes_accounts.py:1868        anchor.id              (acc_import_<hash>)
routes_accounts.py:2172        ""                     (仅 off 分支，无 resolver)
engine_runtime.py:316          account_id or ""
```

未发现任何将 `cookie_<hex>` 传给 `resolve_credential_exit`/管理器的路径；符合断言。

## 22. 四轮残留 P0/P1/P2

- **P0**：无。
- **P1**：无。
- **P2**：无。

## 23. 四轮 Q/N

- **N-1（测试缺口）**：未覆盖“仅 1PSIDTS/SIDCC 变化、SAPISID 不变”的锚点幂等性；建议补一条。
- **N-2（测试缺口）**：未覆盖“已存在同 email+u/0 的真实账号 → probe-import 合并、删锚点、不误删真实账号”的 merge 分支；建议补一条。
- **N-3**：真实 u/0 账号长期使用 `acc_import_<hash>` 作为 id，占位期间以普通 Free/无 email 账号出现在列表；功能可接受，建议 UI/文档加占位标记或说明。
- **Q-1**：无稳定身份 Cookie 时 fallback 使用 name@domain 形状哈希，可能让不同畸形 jar 撞同一占位；此类 jar 无法探测，风险低。
- **Q-2**：同 18.1 fallback 说明；未哈希 values，符合“排除整包哈希”。
- **Q-3**：锚点曾为 active 且后续 merge 到既有账号被删除时，`delete_account` 会把 active 重指到“第一个 enabled 且未过期”的账号，未必是合并目标；属边界 UX（`account_store.py:1013-1021`）。
- **Q-4**：探活异常返回多个 u/0 时会重复 append 同一 meta（现实不应发生），低风险。
- **Q-5**：`_find_identity_account`→save→delete 无跨请求锁；并发 probe-import 同一 jar 存在理论竞态（用户驱动，低概率）。
- **Q-6**：锚点 id 与真实 id 命名空间不重叠（`acc_import_<12hex>` vs `acc_<8hex>`）；仅用户显式指定同形 id 才可能碰撞导致误删。
- **Q-7**：删除曾注册到 rotator 的锚点不会清理 rotator 中该 id 的统计（幽灵统计，不影响路由/调度）。

## 24. 四轮只读声明

本轮仍为 100% 只读：

- 未修改任何源码、测试、配置、迁移、静态产物、Go 源码/二进制；
- 未执行 `git add` / `git commit` / `git checkout` / `git merge` / `git push`；
- 未启动真实服务、真实浏览器或 UI debug 服务；未跑 build；
- 未访问 VPS / 外网 / 真实 Google 账号 / 真实凭据；
- 唯一写入：本文件追加的「四轮确认」章节。

四轮确认完成时间：2026-11-23 05:02 UTC。
