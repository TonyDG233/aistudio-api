# REV-C1b — VPS Tier / 403 / Warmup 只读复核

- 步骤：C1b（VPS 四项发现复核：SOCKS + tier 安全、403 stale auth、模型预热节奏、测试门禁）
- 目标工作树：`D:\LLM\self_programming\aistudio-api\.kilo\worktrees\rebuild-upstream`，HEAD `c19e961`，审查开始时 26 个未提交状态项（23 modified tracked + 3 untracked tests）
- 主仓：`D:\LLM\self_programming\aistudio-api`，HEAD `32d91db`，审查开始时 `git status --porcelain` 为空
- 审查日期：2026-11-02
- 审查方式：只读。未修改 source/tests/config；未执行 stage/commit/push；未启动 server/browser；未访问网络。离线运行本仓 pytest/ruff/pyright 与本地临时目录的 uv 行为探针；仅写入本报告两份副本。
- 结论：**CONDITIONAL**

## 1. 结论摘要

四项 VPS 发现的**主路径修复均成立**：

1. SOCKS 依赖在 `pyproject.toml` 与 `requirements.txt` 均声明为 `httpx[socks]`，Docker 走 `pip install -r requirements.txt`；外部 httpx 客户端均传入 `settings.proxy_url`，仅 CDP/localhost 客户端直连。tier 探测失败返回 `None`，明确 Free 才写 Free；`detect-tier` 失败返回 400/502 且不落库；手工探测绕过 3 天 Free 缓存；成功后同步 slot/context tier 且不 rebuild。启动/导入主路径保留既有 Pro/Ultra。
2. 首次 403 同账号重试在清模板/重发之前先 `refresh_auth_state()`：清空 `page._cached_cookies`、强制 `Network.getCookies`、校验 SAPISID 后重新签名；刷新失败不重发旧凭据，改走切换/抛错。`clear_templates` 空缓存静默，403/rebind 单路径最多一条清空 INFO。
3. `live_page` 成功默认 86400s（可配置），其他来源/失败 600s；不存在 10 分钟 live 循环；刷新摘要 INFO 每轮最多一条，连续完全相同摘要被抑制。
4. 本地离线门禁：`pytest -q` **950 passed / 0 failed / 0 skipped**；`ruff check .` 0；`pyright`（src）0；`pyright src tests` 0；`git diff --check` 0；主仓 config 无改动。

但存在两项 **P1** 使本步不能直接判定 PASS：

- **P1-1**：默认启动 keepalive 的 `AccountInfoRefresher` 仍把“HTTP 200 但不可解析”的 BenefitTier 响应写成 `Free`，并把未知 code 写成非规范 `unknown-N`；这是启动主路径，能破坏“Existing Pro/Ultra preserved on startup/import”的验收声明（详见 §4）。
- **P1-2**：新增测试 `test_lockfile_declares_socks_dependency` 读取被 `.gitignore` 忽略且未被 git 跟踪的 `uv.lock`；干净 checkout/CI 中该文件不存在，CI 的 950/0 不可复现（详见 §4）。

因此判定 **CONDITIONAL**：运行时四项修复本身可接受；但上述两项需在提交/验收前处理或由用户明确接受。

**未提交 / 未 push**：目标树与主仓均保持未提交；本报告是两仓唯一新增文件，未 stage/commit/push。

## 2. 实跑门禁（精确命令与结果）

| 命令 | 结果 |
|---|---|
| `& .\.venv\Scripts\python.exe -m pytest -q`（目标树） | **950 passed in 12.05s**；0 failed，0 skipped，退出码 0 |
| `& .\.venv\Scripts\ruff.exe check .` | `All checks passed!`；退出码 0 |
| `& .\.venv\Scripts\ruff.exe format --check .` | 退出码 1：**90 files would be reformatted, 208 files already formatted**；其中本步改动的 10 个 Python 文件被点名。该格式门禁不在 CI（CI 只跑 `ruff check`），且是基线状态，见 N-2 |
| `& .\.venv\Scripts\pyright.exe`（pyproject `include=["src"]`，strict） | **0 errors, 0 warnings**；195 files；退出码 0 |
| `& .\.venv\Scripts\pyright.exe src tests` | **0 errors, 0 warnings**；177 files；退出码 0 |
| `git -C <target> diff --check` | 退出码 0；仅 LF→CRLF 提示，无空白错误 |
| `git -C <main> diff --check` | 退出码 0 |
| `git -C <target> status --porcelain=v1` | 26 项：23 个 tracked modified + 3 个 untracked tests；无 stage |
| `git -C <main> status --porcelain=v1` | 0 项（审查前） |
| 根 `config.yaml` | 目标树与主仓均未修改；`defaults/config.yaml` 与 `config/config.schema.json` 仅有预期的 `model_discovery` 新增 |
| `git ls-files uv.lock` | 空（未跟踪）；`.gitignore:23` 忽略 |

离线说明：pytest 全部为 mock/本地用例；未启动 VPS、未开浏览器、未访问网络。uv 行为探针只在本机临时空项目中执行，不触碰仓库。

## 3. 四项 VPS 发现逐条核对

### 3.1 SOCKS + tier 安全

| 检查点 | 结论 | 证据 |
|---|---|---|
| pyproject 安装 SOCKS | 通过 | `pyproject.toml:14` `httpx[socks]>=0.28.0` |
| requirements 安装 SOCKS | 通过 | `requirements.txt:3` `httpx[socks]>=0.28.0` |
| Docker 使用 requirements | 通过 | `Dockerfile:87` `RUN pip install --no-cache-dir -r requirements.txt`；镜像不依赖 `uv.lock` |
| `uv.lock` 未跟踪是否可接受 | 条件通过 | `.gitignore:23` 忽略；Docker/requirements 路径已核实。但新增测试读取该忽略文件，见 P1-2 |
| 外部 httpx 客户端统一走代理 | 通过 | `account_info_refresher.py:175,186`（`self._proxy = settings.proxy_url`）；`format_converters/media.py:60-73`（无注入 transport 时 `proxy=settings.proxy_url`）；`cookie_parser.py:508,574,610`；`model_discovery.py:811` |
| 仅 CDP/localhost 直连 | 通过 | `cdp_client.py:818,826,834,876,904,1027` 均为浏览器 CDP 本地端点；`cdp_client.py:117` 为本地 websocket；属于允许例外 |
| 无 Cookie 返回 None | 通过 | `cookie_parser.py:542-549` |
| 网络/代理异常返回 None | 通过 | `cookie_parser.py:573-584`（`except Exception` 返回 None）；新测试 `test_tier_probe_safety.py:128-137` 用 `httpx.ProxyError` 断言 None |
| 非 200 返回 None | 通过 | `cookie_parser.py:576-580`；`test_tier_probe_safety.py:141-151` |
| 畸形 200 返回 None | 通过 | `cookie_parser.py:581` → `parse_benefit_tier_strict`（`:44-51`）→ `benefit_tier.py:37-46,69-105`；测试 `:141-164` |
| 明确 Free 才写 Free | 通过 | `[]`、`[null]`、`[0]` 为 valid Free；未知 code/布尔/非 list/坏 JSON 为 `valid=False` → `strict_tier()` None（`benefit_tier.py:26-46,88-105`）；测试 `test_tier_probe_safety.py:154-164` |
| `detect-tier` 缺 Cookie → 400 | 通过 | `routes_accounts.py:588-593`；测试 `:249-265` |
| `detect-tier` 探测失败 → 502 且不更新 | 通过 | `routes_accounts.py:594-600`；测试 `:168-190` 断言 `update_mock.assert_not_called()`、store tier 仍 Pro、`manager.synced == []` |
| 手工探测绕过 3 天 Free 缓存 | 通过 | `detect_account_tier` 全程无 `TIER_PROBE_FRESHNESS_SECONDS`/`is_verified_free_tier_fresh` 判断；测试 `:288-307` 使用刚校验的 Free 账号仍探测到 Pro |
| 有效更新同步池/实例，不 rebuild | 通过（有边界） | `routes_accounts.py:602-615` 先写账号再调 `instance_manager.sync_account_tier`；`instance_manager.py:685-697` 委托 `InstancePool.sync_account_tier`；`instance_pool.py:287-312` 更新 `slot.tier` 与 `session.active_context.tier`，不重启页面。边界见 P2-2/P2-3 |
| 启动/导入保留既有 Pro/Ultra | 部分通过 | app 侧 `api/app.py:263-278`：`probed_tier is None` 时 `continue`，不落库；导入 `account_store.py:534-557` 在 `resolved_tier == "Free"` 时保留既有非 Free。测试 `test_tier_probe_safety.py:311-358` 覆盖。**但默认启动 keepalive 仍走不安全 refresher，见 P1-1** |
| 测试非自证 | 通过 | `test_tier_probe_safety.py` 对 HTTP 层注入 fake client、对 route 注入 fake manager、对 store 读取真实落盘；`test_403_auth_refresh.py` 使用真实 `BrowserSession`/`PooledSession`/`InstanceRequestClient`/`RequestCaptureService`；`test_model_warmup_refresh.py` 直接跑 `_model_warmup_cycle` 并捕获 sleep。未发现只断言 mock 调用而不断言行为的核心用例 |

### 3.2 403 stale auth

| 检查点 | 结论 | 证据 |
|---|---|---|
| 首次 403 同账号重试先刷新再清模板/重发 | 通过 | `routing_pipeline.py:705-712`：`_refresh_auth_state(...)` 成功后 `same_retries += 1`、`record_retry("403")`、`clear_templates()`、返回 `retry_same`；测试 `test_403_auth_refresh.py:79-100` 断言事件顺序 `["refresh", "clear"]` |
| 刷新使 `_cached_cookies` 失效并强制 CDP 取 Cookie | 通过 | `transport.py:157-183`：`page._cached_cookies = None` → `await page.get_cookies()`；`cdp_client.py:636-647` 每次 `Network.getCookies` 都重建 `_cached_cookies`；测试 `:205-240` 断言 fetch 时缓存为 `None`、随后为新值 |
| 新 SAPISIDHASH | 通过 | `transport.py:217-230` 在发送时重新计算；测试 `:241-246` 断言新 hash 等于 fresh、且不等于 stale |
| 刷新失败不重发旧凭据 | 通过 | `routing_pipeline.py:723-734` 刷新失败直接 `_switch_or_raise`；测试 `:103-127` 断言只发生 `refresh`、`same_retries == 0`、`failed_accounts == {"acc-1"}` 并抛 `AuthError`；有候选时测试 `:130-155` 断言 `outcome == "switch"` |
| 刷新异常不重发 | 通过 | `_refresh_auth_state` 捕获异常返回 False（`routing_pipeline.py:844-855`）；测试 `:158-182` 断言 `outcome == "switch"`、只一条异常日志 |
| `clear_templates` 空缓存静默且幂等 | 通过 | `capture.py:42-46` 空缓存直接 `False`，非空才 `clear()` + 一条 INFO；测试 `:321-337` 断言 `[False, False, False]` 无日志、一次 True 一条日志、再次 False |
| 403/rebind 单路径最多一条清空 INFO | 通过 | 删除了原 `_try_rebind` 前的无条件 `clear_templates()`（diff `routing_pipeline.py:922` 旧行）；当前 403 成功路径仅 `:712` 清一次，rebind 路径仅 `:985` 清一次；测试 `:339-380` 使用真实 `InstanceRequestClient` 断言 `_log_count(...) == 1` |
| 403 重试预算 | 通过 | 仅 `attempt.same_retries == 0` 时同账号重试，且 `context.total_attempts < context.max_retries`（`routing_pipeline.py:705`）；失败走 switch 并记录 `failed_accounts`；未无限重试 |
| `refresh_auth_state` 协议可选实现 | 通过（有 Q） | `GenerationClient` 新增 `async def refresh_auth_state(self) -> bool`（`routing_pipeline.py:153`）；`RequestSession` 同步新增（`session_protocol.py:24`）；`BrowserSession`/`PooledSession`/`InstanceRequestClient`/`AIStudioClient` 均有实现。路由侧用 `getattr(..., None)` 兼容旧 mock（`routing_pipeline.py:834`）；`tests/unit/routing_fakes.py:41-49` 给 FakeSession 增加实现。`AIStudioClient.clear_templates` 返回 None，见 Q-2 |

### 3.3 Warmup

| 检查点 | 结论 | 证据 |
|---|---|---|
| live_page 成功 86400 可配置 | 通过 | `api/app.py:135-149` 按 `source == "live_page"` 取 `live_refresh_interval_sec`（默认 86400）；`model_defaults.py:1545-1580` 读取 `features.model_discovery`；`defaults/config.yaml` 新增 86400/600；`config/config.schema.json:205-221` 定义 |
| fallback/其他来源 600 | 通过 | `api/app.py:149` 返回 `fallback_refresh_interval_sec`（默认 600）；测试 `test_model_warmup_refresh.py:31-44` 覆盖 fallback/snapshot/live_http |
| 失败刷新保持短间隔 | 通过 | `api/app.py:169-172`：异常或 `last_error is not None` 强制 source=fallback；测试 `:96-125` 覆盖 live_page 带错误与抛异常均 sleep 600 |
| 无 10 分钟 live 循环 | 通过 | `api/app.py:470-479` 循环调用 `_model_warmup_cycle`；`_model_warmup_cycle` 在 `:173` sleep 当前 source 间隔，不再使用 `model_discovery.cache_ttl`；测试 `:128-150` 两次 live 成功均 1234（配置覆盖），`600 not in sleeps` |
| 每轮仅一条摘要 INFO | 通过 | `api/app.py:126-132` 删除了旧 `logger.info("模型发现预热完成...")`；`model_discovery.py:553-568` 仅 `_log_refresh_summary` 输出；`_accept_live` 与 fallback 各调用一次；测试 `test_model_warmup_refresh.py:153-180` 断言两条连续相同刷新只产生一条 INFO |
| 连续完全相同摘要抑制 | 通过 | `_last_refresh_info` 字符串完全相等即 return（`model_discovery.py:561-567`）；`changed` 由 `_model_identity` 的 `(id, displayName)` 计算（`:172-181,574-585`） |
| 取消/关闭 | 通过 | `_model_warmup_cycle` 捕获 `CancelledError` 后 re-raise（`api/app.py:162-165`）；`_shutdown_runtime` 先 cancel 并 gather 全部任务（`:303-309`），lifespan 传入 `model_warmup_task`（`:485-487`） |
| 跨账号日志去重 | 通过 | `_last_refresh_info` 为进程级；摘要只含 source/count/changed，不泄露账号；相同摘要跨账号也抑制，符合“identical consecutive suppressed”目标 |

### 3.4 Test / gates / API contracts

- 本地全量 pytest 950/0/0，新增 3 个测试文件均通过。
- `ruff check .` 0；`pyright` 0（src 与 src+tests 均 0）。
- `git diff --check` 0；根 `config.yaml` 未改；`defaults/config.yaml` 与 schema 为预期新增。
- API contract：`AccountResponse`/`ImportCookiesResponse` 字段未改；`detect-tier` 的失败语义由“静默不回写”改为 400/502（这是本步目标行为）；`probe_account_benefit_tier` 返回 `str | None`、`clear_templates` 返回 `bool`、`RequestSession` 新增方法均为内部 Python 契约，未改变对外 JSON schema。未发现既有 API 响应字段回归。
- 前端 `web/` 未改；无 Bun 门禁可跑（不影响本步）。

## 4. Findings（逐条，含严重级别）

### P0

- **无 P0**。未发现可直接导致数据损坏、凭据泄露或不可恢复启动失败的缺陷。

### P1

- **P1-1（启动主路径仍可把既有 Pro/Ultra 降级）**：默认启动 keepalive 的 `AccountInfoRefresher` 仍用 `display_tier(include_unknown_code=True)`，在 HTTP 200 但响应体不可解析时返回 `"Free"`（`account_info_refresher.py:278-283`、`:417-422`；`benefit_tier.py:48-58` 对 `valid=False` 返回 `"Free"`），随后 `update_account_refresh` 直接写入账号（`account_info_refresher.py:295-309`、`:421-425`）。默认 `metadata_refresh_on_startup=True`（`account_keepalive.py:62,77-78,170`），启动时对到期账号调用 `refresh_accounts(..., skip_fresh_free_tier=True)`（`:326-330`）；`api/app.py:444-460` 会把 keepalive 覆盖的账号从 app 侧安全探测中排除，因此 Pro/Ultra 的启动主路径正是该 refresher。未知 code `[9]` 还会写成非规范 `unknown-9`（既有测试 `tests/unit/test_account_info_refresh.py:119`），下游 `canonical_tier` 视为未知/Free，可能丢失 paid 路由。**这不是本 diff 新引入的代码，但它使验收声明“Existing Pro/Ultra preserved on startup/import”在主启动路径上不成立，建议在提交前把 refresher 改为 strict/保留原 tier，或由用户明确接受该边界。**
- **P1-2（新增测试依赖被忽略的 `uv.lock`，干净 CI 会失败）**：`test_lockfile_declares_socks_dependency` 读取 `root / "uv.lock"`（`tests/unit/test_tier_probe_safety.py:361-371`）；`uv.lock` 被 `.gitignore:23` 忽略且 `git ls-files uv.lock` 为空。CI（`.github/workflows/ci.yml:76-86`）只执行 `uv venv`、`uv pip install -e ".[dev]"`、`uv run --no-sync pytest`；本地用 uv 0.9.5 在临时空项目验证：`uv pip install -e .` 与 `uv run --no-sync` 均不生成 `uv.lock`。因此在干净 checkout/CI 中该测试会 `FileNotFoundError`，本机 950 passed 不可复现。**建议测试只断言 `pyproject.toml`/`requirements.txt`，或在 `uv.lock` 不存在时 skip；不要把忽略文件作为门禁依赖。**

### P2

- **P2-1（真实 pool/instance 同步缺少单测）**：`test_tier_probe_safety.py:96-107` 的 `_FakeInstanceManager.sync_account_tier` 只记录调用，未覆盖 `InstancePool.sync_account_tier` 对 `slot.tier`、`session.active_context.tier` 的真实更新（`instance_pool.py:287-312`），也未覆盖 `InstanceManager.sync_account_tier` 的委托（`instance_manager.py:685-697`）。当前实现正确，但回归保护不足。
- **P2-2（tier 跨 lane 时不改 lane）**：`InstancePool.sync_account_tier` 只更新 `slot.tier`/`context.tier`，不更新 `slot.lane`（`instance_pool.py:145,287-312`）；Free 账号手工探测为 Pro 后，原 Free-lane 槽仍受 lane 校验限制（`instance_pool.py:366-369,417-419`），无法立即服务 paid 路由，需后续重启/改绑。这是“不 rebuild 同步 tier”的已知边界，应在运维/UI 文案中说明。
- **P2-3（同步失败被静默吞掉）**：`detect_account_tier` 在账号写库成功后，对 `sync_account_tier` 的任何异常用 `contextlib.suppress(Exception)` 吞掉并继续返回 200（`routes_accounts.py:610-615`），可能出现“账号 tier 已更新、池/实例视图仍旧”的短时不一致。
- **P2-4（null-hole 带 message 仍判 Free）**：`parse_benefit_tier_envelope` 对任何首元素为 `None` 的 list 都返回 valid Free（`benefit_tier.py:92-95`），包括 `[null, "error message"]`；若上游以 200 + null-hole 错误体返回，`strict_tier()` 仍会返回 `"Free"`。新增测试只覆盖 `[null]`（`test_tier_probe_safety.py:157`），未覆盖带第二元素 message 的 null-hole。

### Q

- **Q-1（502 错误体语义）**：`detect-tier` 返回 502（`routes_accounts.py:594-600`），但通用 `HTTPException` 处理器把 `>=500` 的 Gemini `status` 标为 `"INTERNAL"`（`api/app.py:565-574`），而 `format_errors.py:56` 有 `502 -> UNAVAILABLE`。请确认 `/api/accounts/{id}/detect-tier` 的错误体应使用 `INTERNAL` 还是 `UNAVAILABLE`；HTTP 状态码 502 本身正确。
- **Q-2（clear_templates 返回类型不一致）**：`GenerationClient.clear_templates` 协议改为 `-> bool`（`routing_pipeline.py:151`），`InstanceRequestClient`（`instance_client.py:78-79`）与 `RequestCaptureService`（`capture.py:35-46`）返回 bool；但 `AIStudioClient.clear_templates` 仍返回 `None`（`client.py:111-114`）。当前 `AIStudioClient` 未作为 `GenerationClient` 使用，运行时无影响；请确认是否统一，以免未来误用。

### N

- **N-1（uv.lock 运维口径）**：`uv.lock` 被 `.gitignore:23` 忽略且未跟踪；若部署/开发使用 `uv sync`，需在流程中生成/校验，不能依赖仓库内文件。Docker 走 `requirements.txt`，SOCKS 已覆盖。此项与 P1-2 相关但独立记录。
- **N-2（格式门禁基线）**：`ruff format --check .` 当前 90 个文件未格式化（本步改动文件中有 10 个被点名），但 `ruff check .` 为 0，CI 仅跑 `ruff check`（`.github/workflows/ci.yml:79-80`）。该格式失败是基线状态，不作为本步阻塞；提交前建议按 `AGENT.md` §3.4 处理或明确记录。

## 5. 边界与一致性问题清单

| 边界 | 结论 | 说明 / 证据 |
|---|---|---|
| `str | None` 传播：startup | 安全 | `api/app.py:263-278` 在 None 时 `continue`，不写库；测试 `:311-329` |
| `str | None` 传播：detect-tier | 安全 | None 且无 Cookie → 400；None 且有 Cookie → 502；不写库（`routes_accounts.py:583-600`） |
| `str | None` 传播：账号刷新 | 安全 | `routes_accounts.py:462-467` 仅非空且非 Free 才覆盖；None 不写 |
| `str | None` 传播：单条/批量导入 | 安全（新账号回退 Free，既有非 Free 保留） | `routes_accounts.py:991-998,1182-1189`；`account_store.py:534-557`；测试 `:331-358` |
| `str | None` 传播：session_auth | 安全 | `session_auth.py:278-311` 仅在 `discovered_tier` 非空时写 |
| `str | None` 传播：probe_google_accounts_infinite | 安全 | `cookie_parser.py:637-665` 返回 `tier=None`；`batch_import_accounts` 对新账号 `or "Free"`，既有保留 |
| `str | None` 传播：AccountInfoRefresher | **不安全（P1-1）** | `display_tier` 把 malformed/unknown 转成 Free/unknown-N 并写库 |
| `detect-tier` 502 映射 | 状态码正确，body 语义待确认 | `routes_accounts.py:594-600`；`api/app.py:565-574`；`format_errors.py:56`（Q-1） |
| pool 同步一致性 | 账号/slot/context 同步；lane 不变；异常吞掉 | `instance_pool.py:287-312`；`routes_accounts.py:610-615`（P2-2/P2-3） |
| `refresh_auth_state` 可选 mock | 兼容 | `routing_pipeline.py:834` 用 `getattr`；`routing_fakes.py:41-49`；旧 fake 无方法时返回 False 并切换 |
| 403 重试预算 | 一次同账号刷新重试 | `routing_pipeline.py:705-712`；失败/第二次 403 走 switch；`context.budget.record_retry("403")` |
| warmup 取消/关闭 | 安全 | `api/app.py:162-165` re-raise CancelledError；`:303-309` cancel+gather；`:485-487` 传入 task |
| 日志去重跨账号 | 安全 | `_last_refresh_info` 进程级，摘要不含账号；连续相同字符串抑制（`model_discovery.py:553-568`） |
| 403/rebind clear 次数 | 单路径一条 INFO | `capture.py:42-46`；测试 `test_403_auth_refresh.py:339-380` |
| `clear_templates` 返回类型 | 内部不一致 | `AIStudioClient` 返回 None；见 Q-2 |

## 6. 残余风险

1. `refresh_page_auth_state` 只校验新 Cookie 含 `SAPISID`，未校验其属于同一 `auth_user`/账号（`transport.py:177-181`）。若页面已被切到另一账号，仍可能用错误账号的新 Cookie 重签一次；第二次 403 会进入切换，但本轮请求可能打到错误账号。
2. `[null, "message"]` 的 null-hole 被当作 explicit Free（P2-4），若上游用该形状承载 200 错误体，仍可能误写 Free。
3. `AccountInfoRefresher` 对未知 code 写 `unknown-N`（既有测试 `test_account_info_refresh.py:119`），该值不是 canonical tier；下游 `canonical_tier` 可能视为未知/Free，造成 paid 路由降级（P1-1 同路径）。
4. 启动探测把 code 2 归一为 `Ultra`；既有 `Ultra_20X` 账号可能被收窄为 `Ultra`（`domain/model_access.py:66-71,107-117`），丢失 20X 标签；无对应测试。
5. Free→Pro 手工探测后原 Free-lane slot 不立即具备 paid 能力（P2-2），需重启/改绑；与本步“不 rebuild”口径一致，但运维需知情。
6. `sync_account_tier` 异常被吞（P2-3），极端情况下账号 tier 与实例视图短时不一致。
7. 本次仅离线静态/单测复核；未在真实 VPS、真实 SOCKS 代理、真实 Google 上游上验证 403 刷新能否实际换到新 SAPISID/`__Secure-1PSIDTS`，也未验证代理链路的实际 DNS/超时行为。
8. `uv.lock` 未跟踪：uv 用户若直接 `uv sync` 会重新解析依赖；`uv.lock` 内容可能与本步 pyproject 不同步（当前本地文件已含 socksio，但 CI 不保证存在）。见 P1-2/N-1。

## 7. 交付状态与 SHA256

- 目标树：`c19e961` + 26 个既有未提交项 + 本报告；**未 stage / 未 commit / 未 push**。
- 主仓：`32d91db` + 本报告副本；**未 stage / 未 commit / 未 push**。
- 本报告两份副本按字节复制；SHA256 见最终交付回执（对最终文件计算，避免自引用）。
- 审查只读声明：未修改 `src/`、`tests/`、`config/`、`defaults/`、`config.yaml`、`Dockerfile`、`pyproject.toml`、`requirements.txt`；未运行 server/browser；未访问网络。

---

## 8. C1b 收口复核（Worker 修复后 · 只读终审）

- 复核时间：2026-11-02（工作树 HEAD `c19e961`，未提交；主仓 HEAD `32d91db`）
- 复核方式：只读。未改 source/tests/config；未 stage/commit/push；未启动 server/browser；未访问网络。仅追加本两份报告副本。
- **结论：PASS**（无未关闭 P1；P2/Q/N 保留为非阻塞项，逐条列于 §8.4）

### 8.1 原 P1/P2 关闭情况

| 编号 | 状态 | 证据（file:line） |
|---|---|---|
| P1-1 | **CLOSED / RESOLVED** | `refresh_account` 仅 `envelope.strict_tier()`（`src/aistudio_api/infrastructure/account/account_info_refresher.py:283`）；`tier is None` 时在写库前提前返回、`tier=None`、不改账号（`:284-302`）。`refresh_tier` 同构（`:441-459`）。`strict_tier()` 仅 `valid=True` 才返回，未知 code→None（`benefit_tier.py:38-46,103-109`），`[null,"error message"]`→invalid（`:94-100`），空/裸 `[null]`→Free（`:89-100`），空 body/坏 JSON→`json.loads` 异常→invalid（`:77-80`）。批量 `refresh_accounts` 用 `asyncio.gather` 逐账号返回，不因单账号失败中断（`:404-422`）。测试真实：`tests/unit/test_account_info_refresh.py:138-178`（Pro/Ultra × `[9]`/`not-json`/`null`/`[null,"error message"]`，断言 `update_account_refresh` 未被调用且 tier/checked_at 保持），`:181-210`（批量两条均 ok=False 且 Pro/Ultra 保留）。既有 `unknown-9`/`not-json→Free` 参数已删除。 |
| P1-2 | **CLOSED / RESOLVED** | `test_manifests_declare_socks_dependency_without_requiring_lockfile` 只断言 `pyproject.toml` 与 `requirements.txt` 含 `httpx[socks]`（`tests/unit/test_tier_probe_safety.py:400-401`）；`uv.lock` 仅 `lock.exists()` 时才做可选校验（`:403-407`），干净 checkout 不再 `FileNotFoundError`。 |
| P2-3 | **CLOSED / RESOLVED** | 账号写库成功后同步池/实例，异常改为 `log.warning`（`src/aistudio_api/api/routes_accounts.py:602,610-620`），账号更新仍先成功并返回 200。测试 `tests/unit/test_tier_probe_safety.py:272-294` 用 `_FailingSyncInstanceManager` 断言响应/落库 tier 均为 `Ultra`。 |
| P2-4 | **CLOSED / RESOLVED** | `[null, "error message"]` 因 `message` 非空→invalid→None（`benefit_tier.py:94-100`）；裸 `[null]`/`[]` 仍 valid Free（`:89-100`）。测试 `tests/unit/test_tier_probe_safety.py:159-171` 明确区分 `[null]`/`[null, ""]`→Free 与 `[null,"error message"]`→None。 |

### 8.2 实跑门禁（精确命令与结果）

| 命令 | 结果 |
|---|---|
| `& .\.venv\Scripts\python.exe -m pytest -q`（目标树） | **958 passed in 12.04s**；0 failed，0 skipped，退出码 0 |
| `& .\.venv\Scripts\ruff.exe check .` | `All checks passed!`；退出码 0 |
| `& .\.venv\Scripts\pyright.exe`（`include=["src"]`，strict） | **0 errors, 0 warnings, 0 informations**；退出码 0 |
| `& .\.venv\Scripts\pyright.exe src tests` | **0 errors, 0 warnings, 0 informations**；退出码 0 |
| `& .\.venv\Scripts\ruff.exe format --check .` | 退出码 1：**90 files would be reformatted, 209 files already formatted**（基线状态，CI 不跑；见 N-2） |
| `git -C <target> diff --check` | 退出码 0（仅 LF→CRLF 提示，无空白错误） |
| `git -C <main> diff --check` | 退出码 0 |
| 根 `config.yaml` | 目标树与主仓均未修改 |
| `defaults/config.yaml` + `config/config.schema.json` | 目标树仅预期的 `model_discovery` 改动；主仓无改动 |
| `git status` | 目标树：25 tracked modified + 3 untracked tests + 本报告；主仓：仅本报告副本；均**未 stage** |

### 8.3 交付状态

- 目标树：`c19e961` + 未提交改动 + 本报告；**未提交 / 未 push**。
- 主仓：`32d91db` + 本报告副本；**未提交 / 未 push**。
- 两份副本追加相同内容；最终 SHA256 见交付回执（对最终文件计算）。

### 8.4 仍保留的非阻塞项（逐条原文）

> 注：本节 P2-1 / P2-2 / Q-1 / Q-2 已由 §9 关闭；N-1 / N-2 保留，N-2 的格式基线数字在 §9 更新为 92。

- **P2-1（真实 pool/instance 同步缺少单测）**：`test_tier_probe_safety.py:96-107` 的 `_FakeInstanceManager.sync_account_tier` 只记录调用，未覆盖 `InstancePool.sync_account_tier` 对 `slot.tier`、`session.active_context.tier` 的真实更新（`instance_pool.py:287-312`），也未覆盖 `InstanceManager.sync_account_tier` 的委托（`instance_manager.py:685-697`）。当前实现正确，但回归保护不足。
- **P2-2（tier 跨 lane 时不改 lane）**：`InstancePool.sync_account_tier` 只更新 `slot.tier`/`context.tier`，不更新 `slot.lane`（`instance_pool.py:145,287-312`）；Free 账号手工探测为 Pro 后，原 Free-lane 槽仍受 lane 校验限制（`instance_pool.py:366-369,417-419`），无法立即服务 paid 路由，需后续重启/改绑。这是“不 rebuild 同步 tier”的已知边界，应在运维/UI 文案中说明。
- **Q-1（502 错误体语义）**：`detect-tier` 返回 502（`routes_accounts.py:594-600`），但通用 `HTTPException` 处理器把 `>=500` 的 Gemini `status` 标为 `"INTERNAL"`（`api/app.py:565-574`），而 `format_errors.py:56` 有 `502 -> UNAVAILABLE`。请确认 `/api/accounts/{id}/detect-tier` 的错误体应使用 `INTERNAL` 还是 `UNAVAILABLE`；HTTP 状态码 502 本身正确。
- **Q-2（clear_templates 返回类型不一致）**：`GenerationClient.clear_templates` 协议改为 `-> bool`（`routing_pipeline.py:151`），`InstanceRequestClient`（`instance_client.py:78-79`）与 `RequestCaptureService`（`capture.py:35-46`）返回 bool；但 `AIStudioClient.clear_templates` 仍返回 `None`（`client.py:111-114`）。当前 `AIStudioClient` 未作为 `GenerationClient` 使用，运行时无影响；请确认是否统一，以免未来误用。
- **N-1（uv.lock 运维口径）**：`uv.lock` 被 `.gitignore:23` 忽略且未跟踪；若部署/开发使用 `uv sync`，需在流程中生成/校验，不能依赖仓库内文件。Docker 走 `requirements.txt`，SOCKS 已覆盖。此项与 P1-2 相关但独立记录。
- **N-2（格式门禁基线）**：`ruff format --check .` 当前 90 个文件未格式化（本步改动文件中有 10 个被点名），但 `ruff check .` 为 0，CI 仅跑 `ruff check`（`.github/workflows/ci.yml:79-80`）。该格式失败是基线状态，不作为本步阻塞；提交前建议按 `AGENT.md` §3.4 处理或明确记录。

---

审查者：独立只读 Reviewer（C1b · 收口终审）

---

## 9. C1b 二次收口复核（P2-1 / P2-2 / Q-1 / Q-2 · 只读终审）

- 复核时间：2026-11-02（工作树 HEAD `c19e961`，未提交；主仓 HEAD `32d91db`）
- 复核方式：只读。未改 source/tests/config；未 stage/commit/push；未启动 server/browser；未访问网络。仅追加两份报告副本。
- **结论：PASS**（无未关闭 P1/P2/Q；新增 N-3 为非阻塞测试覆盖说明）

### 9.1 逐条关闭情况

| 编号 | 状态 | 证据（file:line） |
|---|---|---|
| P2-2 | **CLOSED / RESOLVED** | 跨 lane 不再“只改 tier 不改 lane”：`InstancePool.sync_account_tier` 在池锁内置 `RETIRING` 并移入 evict（`src/aistudio_api/infrastructure/gateway/instance_pool.py:288-330`），锁外有界 `retire_runtime`（`:332-336`），清理异常 `log.warning`（`:337-343`），`finally` 清空 `account_id/auth_file/auth_user` 并置 `UNSTARTED`（`:344-355`），随后记录 unbound INFO（`:357-366`）。`InstanceManager.sync_account_tier` 在生命周期锁内先同步池（`src/aistudio_api/application/instance_manager.py:687-719`），把被解绑记录标空并收集 evicted（`:721-736`），将恢复账号绑定到新 lane 的空闲槽（`:738-763`，`_find_bindable_record` 接受 `UNSTARTED`/`SLEEPING`，`:1046-1062`），对 evicted 槽用同 lane 备用账号回填；无备用则保持 `UNSTARTED` 并记录 INFO（`:765-798`）。真实测试覆盖：池级跨 lane 解绑+同 lane 原地更新（`tests/unit/test_instance_pool.py:311-341`）；提升账号移入 paid 且 Free 备用回填（`tests/unit/test_instance_manager.py:565-622`）；无备用时 Free 槽 `UNSTARTED`（`:626-665`）；恢复 Pro 绑定 `UNSTARTED` paid 槽（`:669-696`）；同 lane 更新 slot/active_context 且不重建页面（`:700-726`）；重复同步不产生重复绑定（`:615-622`）。 |
| P2-1 | **CLOSED / RESOLVED** | 不再是 no-op fake：新增用例直接构造真实 `InstancePool`/`InstanceManager`（`tests/unit/test_instance_manager.py:565-726`、`tests/unit/test_instance_pool.py:311-341`），断言 slot lane/state/account/context、`active_context.tier`、context 复用、`cdp.disposed` 与重复绑定唯一性；route 层仍用记录型 fake 仅作隔离。 |
| Q-1 | **CLOSED / RESOLVED** | 通用 `HTTPException` 处理器显式映射 `502: "UNAVAILABLE"`（`src/aistudio_api/api/app.py:568-580`），与 `format_errors` 语义一致；`detect-tier` 返回 502 时走该处理器（`src/aistudio_api/api/routes_accounts.py:594-600`）。ASGI 级测试断言 `resp.status_code == 502` 且 `resp.json()["error"]["status"] == "UNAVAILABLE"`，并确认账号 tier 保持 Pro（`tests/unit/test_tier_probe_safety.py:202-231`）。 |
| Q-2 | **CLOSED / RESOLVED** | 协议为 `def clear_templates(self) -> bool`（`src/aistudio_api/application/routing_pipeline.py:151`）；`AIStudioClient.clear_templates` 返回 bool，无 capture service 时返回 `False`（`src/aistudio_api/infrastructure/gateway/client.py:111-116`）；`InstanceRequestClient`（`.../instance_client.py:78-79`）与 `RequestCaptureService`（`.../capture.py:35-46`）均返回 bool。测试覆盖无 service/空缓存返回 `False`、有模板返回 `True`、再次清除返回 `False`（`tests/unit/test_403_auth_refresh.py:342-356`）。 |
| P1-1 / P1-2 / P2-3 / P2-4 | **保持 CLOSED** | §8.1 的关闭证据未回退；本轮全量测试与严格类型检查仍通过。 |

### 9.2 实跑门禁（精确命令与结果）

| 命令 | 结果 |
|---|---|
| `& .\.venv\Scripts\python.exe -m pytest -q`（目标树） | **965 passed in 11.47s**；0 failed，0 skipped，退出码 0 |
| `& .\.venv\Scripts\ruff.exe check .` | `All checks passed!`；退出码 0 |
| `& .\.venv\Scripts\pyright.exe`（`include=["src"]`，strict） | **0 errors, 0 warnings, 0 informations**；退出码 0 |
| `& .\.venv\Scripts\pyright.exe src tests` | **0 errors, 0 warnings, 0 informations**；退出码 0 |
| `& .\.venv\Scripts\ruff.exe format --check .` | 退出码 1：**92 files would be reformatted, 207 files already formatted**（基线状态，CI 不跑；见 N-2） |
| `git -C <target> diff --check` | 退出码 0（仅 LF→CRLF 提示，无空白错误） |
| `git -C <main> diff --check` | 退出码 0 |
| 根 `config.yaml` | 目标树与主仓均未修改 |
| `defaults/config.yaml` + `config/config.schema.json` | 目标树仅预期的 `model_discovery` 改动；主仓无改动 |
| `git status` | 目标树：29 tracked modified + 3 untracked tests + 本报告 = 33 项；主仓：仅本报告副本；两仓 `git diff --cached` 均为空（未 stage） |

### 9.3 交付状态与 N 项

- 目标树：`c19e961` + 33 个未提交项；**未提交 / 未 push**。
- 主仓：`32d91db` + 本报告副本；**未提交 / 未 push**。
- **N 项共 3 条**：
  - **N-1（uv.lock 运维口径）**：保留原文；`uv.lock` 仍被忽略且未跟踪，Docker 走 `requirements.txt`。
  - **N-2（格式门禁基线）**：`ruff format --check .` 当前 **92** 个文件未格式化（较上轮 90 增加 2 个），`ruff check .` 为 0，CI 仅跑 `ruff check`；不作为本步阻塞。
  - **N-3（测试覆盖广度）**：`SLEEPING` paid 槽绑定分支（`instance_pool.py:1046-1062` 已接受该状态）与跨 lane 清理失败告警（`instance_pool.py:337-343`）仅经代码核对，未由专门用例直接断言；当前实现行为正确，属非阻塞覆盖补充项。

审查者：独立只读 Reviewer（C1b · 二次收口终审）
