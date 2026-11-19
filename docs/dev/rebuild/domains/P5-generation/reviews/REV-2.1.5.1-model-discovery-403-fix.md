# REV-2.1.5.1-model-discovery-403-fix — Browserless 模型发现 403 根治、退避闸门与手动刷新端点独立只读审查报告

- **审查对象**：工作区未提交改动（`git diff`，尚未 commit）——browserless 引擎 `aistudio.model_discovery` 全天约 20 分钟一轮 4 候选全 `ListModels HTTP status 403` 刷屏问题的修复
- **审查角色**：100% 独立只读 Reviewer（与 Worker 无上下文共享，全部结论以亲眼核验为准）
- **审查日期**：2026-11-19
- **最终判定**：**PASS**
- **报告归属**：`docs/dev/rebuild/domains/P5-generation/reviews/`。归属理由：`docs/dev/rebuild/domains/INDEX.md` 中 `P5-generation` 的职责明确包含「模型发现与 `countTokens`」，本次改动主体 `model_discovery.py` 正属该域；同目录既有 `REV-B6-discovery-hardening.md` 亦为模型发现主题。

> **只读声明**：本审查全程只读。除按授权写入本报告文件外，未修改任何业务源码、配置、测试、`web/`、`defaults/`、`docs/**` 既有文件或任何 `REV-*.md`；未启动真实服务、未发起真实网络请求、未使用真实 Cookie、未执行 `git add/commit/push`。所有门禁均在离线 `uv` 工具链下实跑。

---

## 1. 变更范围全量盘点

`git status --porcelain=v1` 仅 5 个已修改文件，无未跟踪文件：

| 文件 | 增/删 | 归属 |
|---|---|---|
| `src/aistudio_api/infrastructure/gateway/model_discovery.py` | +202 / -25 | signer 通道、退避闸门、403 分类、单轮即停 |
| `src/aistudio_api/api/routes_system.py` | +42 / -0 | `POST /api/models/refresh` |
| `src/aistudio_api/api/app.py` | +9 / -1 | 预热循环异常兜底 |
| `tests/unit/test_model_discovery_browserless.py` | +228 / -26 | 新增/改写用例 |
| `tests/unit/test_model_discovery_live_wiring.py` | +19 / -3 | 隔离夹具补退避字段、signer 断言 |

`git diff --stat`：`5 files changed, 500 insertions(+), 55 deletions(-)`。

**范围红线核验结论（全部通过）**：
- 零 `web/` 改动、零 `defaults/` 改动、零 `docs/**` 现有文件改动、零既有 `REV-*.md` 被触碰（`git status` 只有上述 5 个 `M`）。
- 未改 `browserless_session.py` / `engine_runtime.py` / `waa-signer/**`，与 Worker 声称一致；signer 通道确为复用既有能力（见 §2.1）。
- 全量 diff 逐行审计：无 `print(`、`console.`、`breakpoint(`、`pdb.`、`TODO/FIXME/XXX` 残留；新增 import 仅 `asyncio`（`routes_system.py:5`）、`cast`（`model_discovery.py:13`）、测试侧 `Any, cast`。无调试代码、无意外破坏。

---

## 2. 逐项核验（对应审查清单 1–10）

### 2.1 架构红线与 signer 通道真实性

- **无反向 import**：`model_discovery.py`（infrastructure）新增代码只引入标准库 `cast`（`model_discovery.py:13`），未新增 `application`/`api` 顶层 import。`_active_rotator()` 仍走既有 `sys.modules` 间接解析（`model_discovery.py:291-300`），保持既有边界。
- **未引入 shim/deprecated 存根**：新增 `ListModelsHttpError`、`_classify_list_models_error`、`_record_failure`、`reset_backoff`、`model_count` 均为实义实现，无兼容别名包装。
- **确为复用 `send_hooked_request`**：
  - 定义：`src/aistudio_api/infrastructure/waa/browserless_session.py:713-747`，签名 `(*, body: str, url: str | None = None, headers: dict[str, str] | None = None) -> tuple[int, bytes]`。
  - 调用：`model_discovery.py:1304-1308`，先 `getattr(session, "send_hooked_request", None)` 判可调用，再 `sender(body="[]", url=LIST_MODELS_URL)`。签名与返回类型完全匹配。
  - browserless 池会话确为 `BrowserlessSession` 本体（`infrastructure/waa/engine_runtime.py:238-267` 直接构造 `BrowserlessSession` 并放进 `SessionBuild.session`，无包装层），`engine = "browserless"`（`browserless_session.py:242`），`_session_is_browserless` 能正确识别（`model_discovery.py:238-243`）。
  - 池会话候选来源：`instance_manager.py:783-806` 返回 `slot.session`；`state.py:85-98` 透传；`app.py:137-138` 传给 `get_models`。
- **请求确实带齐真凭据而非"假指纹"**：signer 侧 `/v1/rpc/send` 在 `waa-signer/cmd/waa-signer/main.go:1326-1337` 以 `worker.ProtocolHeaders()` 克隆真实协议头（含 `X-AIStudio-Visit-Id`、`X-Goog-Ext-519733851-Bin`、UA，见 `waa-signer/README.md:11`、`runtime_go.go:775-780`），再叠加 `Content-Type`/`Authorization`/tier，并由 `SendProtected` 补 `Origin`/`Referer`/`Sec-Fetch-*`（`runtime_go.go:783-801`）。客户端 `_BASE_HEADERS`（`browserless_session.py:66-72`）缺失的 ext-bin 由 signer 侧补齐，不构成功能缺口。
- **403 会以 403 状态返回**：signer 直接 `w.WriteHeader(resp.StatusCode)` 并写 `X-Upstream-Status`（`main.go:1370-1371`），`SignerClient.send_rpc` 以 `status=resp.status_code` 返回（`signer_client.py:412-417`），`_fetch_via_signer` 才能正确构造 `ListModelsHttpError(403, ...)`（`model_discovery.py:1314-1320`）。**这是"根治"能生效的关键链路，已闭环验证。**

### 2.2 退避闸门正确性

- **位置正确**：TTL 快路径（`model_discovery.py:800-806`）之后、取锁之前设闸门（`:808-817`），锁内二次检查（`:827-828`），可防"排队请求穿透"。
- **指数与封顶数学正确**：`_record_failure`（`:1063-1072`）`exponent = min(streak-1, 20)`，`delay = min(ttl * 2**exponent, 86400)`，`ttl=600` 时 600→1200→…→76800→封顶 86400；先钳制指数再算幂，无溢出。封顶常量 `:48`。用例 `tests/unit/test_model_discovery_browserless.py:468-481` 实测 streak=30 时恰为 `100 + 86400`。
- **`force_refresh=True` 确实被挡**：闸门未读取 `force_refresh`（`:811`），仅手动端点先 `reset_backoff()`（`routes_system.py:369`）再强制刷新。用例 `:485-512` 用"读账号即断言失败"的 `_BombStore` 与"被调用即断言失败"的 sender 证明零网络、且 DEBUG 日志含"退避期"（`:492-512`）。
- **成功清零**：`_accept_live` 同时置 `_failure_streak = 0`、`_next_retry_at = None`（`:1057-1058`），用例 `:598-621`。
- **旧语义共存**：失败仍不推进 `_last_fetched_at`（`:998-1025` 只改缓存/来源/日志），TTL 快路径不会掩盖闸门；闸门在 TTL 之后，正常 TTL 命中仍零开销。
- **退避期日志级别**：`logger.debug`（`:812`），不会制造新刷屏。失败轮单轮告警上限为"403 分类 + 首个 403 即停 + 保持缓存"3 条（`:1074-1081`、`:983-987`、`:1010-1015`），远低于原先每 20 分钟 4 候选刷屏。

### 2.3 403 分类与脱敏

- **读体上限 512**：`_fetch_via_signer` 取 `text[:512]`（`:1315`），裸 HTTP 兜底同样 `resp.text[:512]`（`:1373`）。
- **四分类齐全**：`_classify_list_models_error`（`:72-80`）覆盖 `region not supported` / `available-regions` → `REGION_RESTRICTED`，`verify-account` / `must be verified` → `VERIFICATION_REQUIRED`，`servicerestricted` / `myaccount.google.com/restrictions` → `SERVICE_RESTRICTED`，其余 `UNKNOWN_403`；全小写化匹配大小写不敏感。`tests/unit/...:550-563` 参数化 7 例全覆盖。
- **摘要 ≤120 且只预警一次**：`_error_snippet` 先折叠空白再截 120（`:68-69`），`ListModelsHttpError.summary[:120]` 双保险（`:64`）；`_log_fetch_error` 仅在 403 时 `warning` 输出 `分类/标签/摘要`（`:1074-1081`），非 403 降为 DEBUG（`:1082-1083`）。单轮内首个 403 即 `break`，后续候选不再触发第二条 403 warning。
- **脱敏**：新增日志字段仅为 status、category、label（`"browserless signer"`/`candidate.label`）与响应体摘要；不打印 Cookie、Authorization 或完整 headers。`label` 为账号 id 或固定串（`:361`、`:943`、`:971`），不含密钥。`_session_cookies`/`calculate_sapisid_hash` 结果从不进入日志。符合 AGENT.md §7.2。

### 2.4 单轮即停控制流

- signer 段：`except ListModelsHttpError`；`if exc.status == 403: abort_round = True; break`（`:941-946`）；`except Exception` 仅 DEBUG 并继续后续 signer 候选（`:947-953`），`except asyncio.CancelledError: raise`（`:939-940`）。
- 磁盘兜底段：`if not abort_round:` 才进入（`:956`），内部同样首个 403 `break`（`:969-974`）。
- 非 403（如 401/5xx/网络错误）不中止，用例 `:192-234`（signer 401 → 继续到健康账号）与 `:419-465`（signer 401 + 兜底 401 → 两路各一次）证明。
- 首个 403 即停用例 `:516-547`：两个 signer 会话仅第一个被调用（`calls == ["first"]`），`last_error` 含 `region_restricted`，`_failure_streak == 1`，第二个候选零调用。

### 2.5 手动刷新端点

- **路由与鉴权面**：`@protected_router.post("/api/models/refresh")`（`routes_system.py:356`），`protected_router` 即 `routes_system.protected_router`，在 `app.py:53-56` 以别名导入并在 `app.py:639` 以 `dependencies=[Depends(require_web_auth)]` 挂载；同文件 `/api/system/status` 也在同一 `protected_router`（`routes_system.py:236`），鉴权面一致。`require_web_auth` 语义见 `api/dependencies.py:199-212`（`auth_enabled` 关闭时免密，否则仅接受 Bearer 管理密码）。
- **30s 超时**：`_MODEL_REFRESH_TIMEOUT_SECONDS = 30.0`（`:48`），`asyncio.wait_for(..., timeout=...)`（`:371-375`）。
- **失败如实返回、不吞异常**：`except TimeoutError` 与 `except Exception` 分别构造 `error` 并 `logger.warning`，仍返回结构（`:378-392`）；响应恒为 `{source, model_count}`，仅有错误时附 `error`（`:386-392`）。
- **端点顺序无遮蔽**：`models_router` 仅注册 `GET /api/models`、`GET /v1beta/models`、`GET /v1beta/models/{model_id:path}`；且 `system_protected_router` 在 `app.py:639` 先于 `models_router`（`:653`）注册，`POST /api/models/refresh` 无冲突。
- **零前端改动**：`git status` 无 `web/`；`git grep "models/refresh"` 仅命中 `routes_system.py:356`，无前端调用方。

### 2.6 预热循环兜底

`app.py:615-624`：`while True` 内 `try: await _model_warmup_cycle(...)`，`except asyncio.CancelledError: raise`，`except Exception` 记 `warning` 后 `await asyncio.sleep(_model_warmup_interval_seconds("fallback"))`。只捕获非 `CancelledError`，不绕过闸门、不产生强刷路径（`_refresh_model_catalog_once` 仍传 `force_refresh=True`，由闸门统一拦截，`app.py:133-139`）。注意 `_model_warmup_cycle` 自身已含 `sleep`（`app.py:159-181`），外层仅在其抛出未预期异常时才额外等待一次，属双保险，不会加速重试。

### 2.7 测试真实性（逐条核对删除的旧断言）

旧断言被删除/改写的仅有以下几处，逐一判定**均非"为通过而弱化"，而是契约变更后的等价或更强覆盖**：

| 旧断言（已删） | 新断言 | 判定 |
|---|---|---|
| `test_browserless_skips_...`：`_fetch_via_http` 收到 `cookies["SAPISID"] == "sapisid-auth-a"` | signer 被调用、`body == "[]"`、url 以 `ListModels` 结尾，且 page/httpx 两条路径均设为 `AssertionError`（`:154-188`） | 更强（旧契约正是本次要废除的裸 httpx 路径） |
| `test_http_candidates_skip_401...`：`calls == ["sapisid-auth-bad", "good"]` | `signer_calls == [1]` 且 `calls == ["good"]`（`:192-234`） | 等价语义（401 跳过→下一健康账号），首候选改走 signer |
| `test_live_http_success...`：mock `_fetch_via_http` | 改用 signer sender，断言快照原子写入且无 `.tmp.` 残留（`:237-259`） | 等价 + 保留原子写断言 |
| `test_all_candidates_fail...`：`len(attempts) == 2` | `signer_calls == [1]` 且 `len(attempts) == 1`（合计仍 2 次网络尝试），新增 streak/next_retry/缓存字节不变断言（`:419-465`） | 等价 + 增强 |
| `test_browserless_discovery_never_touches_a_page`：`http_fetch.await_count >= 1` | `page_fetch.await_count == 0 and http_fetch.await_count == 0`（`test_model_discovery_live_wiring.py:469-476`） | 更强 |

新增用例含"零网络"牙齿：`_BombStore`（读账号即失败）、sender 即失败、page/httpx `AssertionError`、`_failure_streak/_next_retry_at` 数值断言、快照字节不变断言。测试隔离已覆盖新字段：`test_model_discovery_live_wiring.py:129-158` 保存/恢复 6 元组（含 `_failure_streak`、`_next_retry_at`）。

---

## 3. 门禁实跑（真实输出，UTF-8 环境）

环境：Windows / pwsh；`$env:PYTHONUTF8='1'; $env:PYTHONIOENCODING='utf-8'`。

| 命令 | 真实输出 | 退出码 |
|---|---|---|
| `uv sync --extra dev` | `Resolved 47 packages in 1ms` / `Audited 47 packages in 2ms` | 0 |
| `uv run ruff check .` | `All checks passed!` | 0 |
| `uv run ruff format --check .` | `62 files would be reformatted, 328 files already formatted` | 1（基线存量） |
| `uv run ruff format --check <本次 5 个文件>` | `5 files already formatted` | 0 |
| `uv run pyright src tests` | `0 errors, 0 warnings, 0 informations` | 0 |
| `uv run pytest -q`（全量） | `1704 passed in 49.12s` | 0 |
| `uv run pytest tests/unit/test_model_discovery_browserless.py tests/unit/test_model_discovery_live_wiring.py -q` | `37 passed in 0.57s` | 0 |
| `git diff --check` | （无输出） | 0 |

**Worker「62 个 ruff format 未格式化文件为历史存量、本次 5 个改动文件均已格式化」核验：属实。**
- `git status` 只有 5 个 `M`，62 个未格式化文件全部不在改动集合内，属仓库历史存量；
- 单独对 5 个改动文件执行 `ruff format --check` 返回 `5 files already formatted`。
- 佐证：仓库级 `ruff format --check .` 的 exit 1 在本次改动前即存在（此类存量文件包含 `docs/dev/evidence/**` 脚本、`src/.../public_web_constants.py`、`tests/unit/test_ui_debug_sandbox.py` 等与本次无关文件），非本改动引入。

---

## 4. 遗留风险与评级

### 4.1 `source` 仍标 `live_http`（诊断口径混淆）

signer 通道成功与裸 httpx 兜底成功都写 `source="live_http"`（`model_discovery.py:937`、`:965`），因此日志与 `POST /api/models/refresh` 返回的 `source` 无法区分"经 signer 正路成功"与"磁盘裸 httpx 兜底成功"。这会让后续 403 复现时的取证口径变模糊。评级 **P2**（可跟踪改进，非阻塞）。

### 4.2 磁盘兜底裸 httpx 残余风险

`_fetch_via_http` 仍用 `httpx.AsyncClient` + 自造 `X-AIStudio-Visit-Id: v1_<uuid4>`（`model_discovery.py:1355`）与静态 `EXT_BIN`（`:1354`）。在 403 根因（IP/区域/账号风控）未实机验证前，若 signer 通道返回非 403（网络错误/5xx），随后仍会走这条裸 httpx 兜底并可能撞同一风控闸门。当前已有"首个 403 即停 + 指数退避"两层兜底，最坏情况是每轮最多 2 个磁盘候选各撞一次后进入退避，不再出现原先每 20 分钟 4 候选刷屏。评级 **P2**（残余风险如实标注，但已被退避与单轮即停显著收敛）。

### 4.3 signer 通道与在途生成的共享资源

`InstanceManager.discovery_sessions()` 只做排序偏好（paid 优先、idle 优先），**不排除 busy 槽位**（`instance_manager.py:793-806`）；`get_models` 也不占 `active_requests`（`model_discovery.py:930-938`）。单槽位（默认 `count=1`）且正在流式生成时，discovery 会通过同一池会话的 signer worker 发起独立 `/v1/rpc/send`（`main.go:1263-1387`），并调用 `_apply_rpc_metadata` 合并 Set-Cookie（`browserless_session.py:740-746`、`:850-871`）。旧实现走裸 httpx，不触碰 signer worker；这是本次引入的新共享面。若 signer 单 worker 对并发请求语义严格，可能出现 worker 争用/请求被拒（后果是 discovery 失败进入退避，不影响在途生成），cookie 合并为 last-writer-wins。评级 **P2**（建议：全槽位 busy 时跳过本轮，或按 `active_requests == 0` 过滤）。

---

## 5. P0 / P1 / P2 / Q / N 清单

### P0（必须返工的破坏性/风控安全问题）

- **无**。

### P1（提交前应修复的功能缺陷）

- **无**。

### P2（可跟踪改进）

- **P2-1** `model_discovery.py:930-938` / `instance_manager.py:793-806`：discovery 与在途生成共用同一 signer worker，仅靠排序偏好 idle，busy 槽位仍会被选中；无 `active_requests` 门控。建议全 busy 时跳过本轮。
- **P2-2** `model_discovery.py:937, 965`：signer 正路与磁盘裸 httpx 兜底共用 `source="live_http"`，削弱 403 诊断口径；建议新增 `live_signer` 来源并同步 `app.py:142-156` 的间隔判定与展示口径。
- **P2-3** `model_discovery.py:1350-1355`：磁盘兜底仍为裸 httpx + 伪造 visit-id + 静态 ext-bin；403 根因未实机验证前仍存在"退避到期后撞墙"的残余风险（已被单轮即停与退避收敛）。
- **P2-4** `model_discovery.py:1307-1308`：signer 通道未设自有超时，依赖 signer client 的 httpx 读超时（可能远高于 page 路径 12s / http 路径 10s），后台预热可能被单次调用长时间占用（手动端点有 30s 兜底）。
- **P2-5** 测试覆盖缺口：`tests/unit/test_model_discovery_browserless.py:587-589` 仅函数级直调 `refresh_models`，无 TestClient 级断言路由挂载/鉴权面/状态码；`app.py:615-624` 新增的循环兜底 except 无单测（`tests/unit/test_model_warmup_refresh.py` 只覆盖 `_model_warmup_cycle`）。
- **P2-6** `routes_system.py:369-375`：`reset_backoff()` 先于取锁；若在等待锁期间在途轮失败并写入新退避，强制刷新会被闸门再次挡回（返回 error 而非静默成功，行为诚实但按钮表现为"无效果"）。建议锁内 reset 或返回 `retry_in`。
- **P2-7** `model_discovery.py:931-934, 981-993`：全部 signer 会话 `is_alive()==False` 时 `attempted_any` 保持 False、不记退避，但仍每轮打印"所有模型发现候选均失败"warning，特殊状态下可能维持 600s 一次告警（非 403 路径，风险低）。
- **P2-8** `model_discovery.py:238-243`：`_session_is_browserless` docstring 仍写 "they must use the HTTP path"，与当前 signer 通道实现不一致（纯注释问题）。

### Q（疑问，需产品/架构确认）

- **Q-1** `model_discovery.py:709-710`：退避状态仅内存态，进程重启即清空；若发生崩溃重启循环，每次启动都会再撞一轮 403。是否需要持久化退避？
- **Q-2** `model_discovery.py:811`：闸门对 403 与普通失败一视同仁，且会同时挡住 browser 引擎的 `live_page` 发现；混合引擎部署下是否接受该耦合？
- **Q-3** `waa-signer/cmd/waa-signer/main.go:1263-1387`：signer 单 worker 对"ListModels 与 GenerateContent 并发"的语义（排队/拒绝/复用页面）未在本仓审计范围内，P2-1 的严重度取决于该结论。是否需要在 signer 侧核实？
- **Q-4** `app.py:154-156`：`live_page` 才用 86400s，其余来源（含 signer 通道）统一 600s；signer 通道若属于"类 live"是否应单列间隔？与 P2-2 一并决策。

### N（非问题备注）

- **N-1** `model_discovery.py:998-1015`：失败轮单轮最多 3 条 warning（403 分类 / 首个 403 即停 / 保持缓存），但退避后首轮 600s、其后指数增长，已消除原"每 20 分钟 4 候选"刷屏，符合修复目标。
- **N-2** 旧实现会把 browserless 会话的内存 cookie 作为裸 httpx 候选（本次 diff 已移除该路径）；这是有意的契约变更，不是能力回退——池会话改由 signer 正路承载完整 Cookie jar 与真指纹。
- **N-3** `last_error` 内含 ≤120 字符响应体摘要（`model_discovery.py:64, 68-69`），仅经内部日志与受 `require_web_auth` 保护的 `POST /api/models/refresh` 暴露（`routes_system.py:376-391`），不含 Cookie/Authorization/headers。
- **N-4** 未在 `browserless_session.py`/`engine_runtime.py`/`waa-signer/**` 产生改动，signer 侧行为按既有实现只读核对（`main.go:1326, 1370-1371`）。
- **N-5** 62 个 `ruff format` 存量未格式化文件与本次改动无关（见 §3）。
- **N-6** `git diff --check` 无空白错误；5 个改动文件均通过 `ruff check`/`pyright`/全量 pytest。

---

## 6. 结论

**PASS。**

用户拍板的 6 项目标全部落地且可被证据链闭环：①池会话 ListModels 改经 `BrowserlessSession.send_hooked_request` 的 signer 通道（带真实 cookie jar、Firefox 152 指纹、服务端签发的 visit-id/ext-bin、代理出口），磁盘 auth.json 保留裸 httpx 兜底；②`_failure_streak/_next_retry_at` 指数退避闸门（base=600s 翻倍、封顶 86400s、`force_refresh` 不可越过、成功清零、退避期 DEBUG）；③首个分类 403 即停本轮剩余候选、非 403 继续；④403 读体 ≤512、四分类、warning 一次且摘要 ≤120、无敏感信息落日志；⑤`POST /api/models/refresh` 挂在 `protected_router`（与 `/api/system/status` 同一 Web 鉴权面）、30s 超时、失败如实返回、零前端改动；⑥预热循环仅兜底非 `CancelledError`。

未发现 P0/P1 缺陷；全部残余问题为 P2（signer 与在途生成的共享面、`source` 口径、磁盘兜底残余撞墙风险、signer 通道无自有超时、路由级/循环级测试缺口）与 Q（退避持久化、跨引擎耦合、signer 并发语义、间隔口径），不阻塞本次提交，建议纳入后续维护项跟踪。
