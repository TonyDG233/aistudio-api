# REV-2.1.5.4 · 批次 A · RotateCookies 引擎分路 + signer 冷启动双收获 + 扇出收敛

- 审查基线：`5f044e5`（REV-2.1.5.3 二轮复审 PASS）之后的**未提交工作树改动**
- 业务域：`P1-account`（账号 Cookie 轮换 / Tier / 同源兄弟 / 冷启动收获；跨触 P5 模型发现、P6 保活）
- 审查类型：100% 独立只读 Reviewer，与 Worker 零上下文共享
- 审查日期：2026-11-20
- 结论：**CONDITIONAL PASS**（执行门禁全绿、A1/A2 核心交付成立；残留两条「裸 RotateCookies」退路需用户/主控明确接受或排期，另有一条 signer 头部保真项需实机确认）

---

## 0. 只读声明

本次审查**除本报告文件外未修改任何文件**；未执行 `ssh`、未联网、未启动生产服务、未拉起真实浏览器、未访问真实 Google 账号。

---

## 1. 改动集合核对（清单 1）

`git status --short` 实测改动集合：

```
 M src/aistudio_api/application/account_keepalive.py
 M src/aistudio_api/application/account_service.py
 M src/aistudio_api/infrastructure/account/cookie_parser.py
 M src/aistudio_api/infrastructure/gateway/model_discovery.py
 M src/aistudio_api/infrastructure/waa/browserless_session.py
 M src/aistudio_api/infrastructure/waa/signer_client.py
 M tests/unit/test_account_keepalive.py
 M tests/unit/test_browserless_auth_rebind.py
 M tests/unit/test_browserless_session.py
 M tests/unit/test_cookie_rotation_and_bundle.py
 M tests/unit/test_model_discovery_browserless.py
 M tests/unit/test_signer_client.py
 M waa-signer/cmd/waa-signer/main.go
 M waa-signer/internal/aistudio/runtime_go.go
 M waa-signer/internal/aistudio/runtime_native.go
 M waa-signer/internal/signerapi/types.go
?? waa-signer/internal/aistudio/runtime_go_test.go
```

- 与 Worker 声称的 A1/A2 改动集合逐文件一致；`runtime_go_test.go` 确为唯一 untracked 新文件。
- **零触碰**核验：`git status` 中不含 `REV-*.md`、`docs/dev/rebuild/**`、`AGENT.md`、`defaults/**`、`web/**`；亦无 `engine_runtime.py`、`routes_accounts.py`。
- **「未改 engine_runtime/routes_accounts」声明为真**：两文件不在 diff 内。`routes_accounts.py:1666-1676` 的槽位→session 解析为基线已有代码（本批未动），本批只依赖其既有契约。
- 无调试残留：对 diff 新增行扫描 `print(`/`breakpoint`/`pdb`/`TODO`/`FIXME`/`sys.settrace`，0 命中。
- `git diff --check`：exit 0（无空白错误）。

> 结论：改动集合与声称一致，无越界改动。

---

## 2. 风控正确性（清单 2，逐项钉死）

### 2a. 会话-账号绑定安全 —— 系统级成立，但守卫本身不校验绑定

- `AccountService._session_can_rotate`（`src/aistudio_api/application/account_service.py:119-131`）只校验：`engine == "browserless"`、存在 `rotate_cookies_via_signer`/`is_alive` 且 `is_alive()` 为真。**它本身不比较 session 所属账号与 `account_id`**。
- 但两条调用链均把 session 与目标账号**同槽位绑定**：
  - 保活：`account_keepalive.py:697-702` 以 `slot.account_id` 为键收集 session，`:716` 按 `account.id` 取回，`:718` 传入；`engine_runtime.py:250-256` 构造 `BrowserlessSession(account_id=<slot 账号>)`。
  - 手动接口：`routes_accounts.py:1669-1676` 遍历 `pool_slots()`，仅取 `slot.account_id == account_id` 的 session。
- 且真正发出请求的是 `send_hooked_request` → `send_rpc(cookies=self._cookie_text(), account_key=self.stable_account_key, ...)`（`browserless_session.py:711-721`），即**同一 session 自己的 Cookie 文本 + 自己的 accountKey**；signer 侧 `AcquireWorker` 再以 `ResolveAccountKey(accountKeyOverride, ...)` 校验一致性（`main.go:513-516`）。
- 因此**当前无可达路径把 A 账号 Cookie 从 B 账号 Worker 发出**。但「传入的 session 必属目标账号」是调用方口头契约而非守卫内断言，见 P2-1。

判定：**满足**（系统级），但建议加固（P2-1）。

### 2b. signer 路径 method/body 与裸函数一致；差量回写走既有 `_apply_cookie_payload`

- 裸函数：`cookie_parser.py:785` `body = '[null,"-1234567890",0]'`，`:788-792` `http_client.post(...)`。
- signer 路径：`browserless_session.py:76-82` 常量 `_ROTATE_COOKIES_URL`/`_ROTATE_COOKIES_BODY`/`_ROTATE_COOKIES_HEADERS`；`_rotate_cookies`（`:649-681`）以 `send_hooked_request(body=_ROTATE_COOKIES_BODY, url=_ROTATE_COOKIES_URL, ...)` 发出。
- method：`send_hooked_request`（`:699-733`）→ `send_rpc`；Go 侧 `SendProtected` 固定 `http.MethodPost`（`runtime_go.go:850-865`），`POST /v1/rpc/send` 契约（`main.go:1318`）。
- body：逐字一致（`'[null,"-1234567890",0]'`），且测试断言精确字符串（见 §4）。
- 差量回写：`send_hooked_request` 取 `X-WAA-Set-Cookies` 后调 `_apply_rpc_metadata`（`:836-857`）→ `_apply_cookie_payload`（`:469-482`）；随后 `_rotate_cookies` 在 `_cookie_dirty` 时 `save_current_cookies()`。**复用既有路径，无另起写回逻辑**。
- 成功判定：signer 把上游状态码直接作为 HTTP 状态返回（`main.go:1425-1426`），`RpcResult.status` 即上游状态，`ok = status == 200`（`browserless_session.py:680`）成立。

判定：**满足**。注意 method/body 一致，但**请求头并非逐字一致**——见 Q-1。

### 2c. 扇出收敛：missing_seed 零扇出、兄弟至多 1 次、全失败单条 WARNING、无死循环遍历

- 扇出门槛：`account_service.py:179` `if not ok and skipped != "missing_seed":` —— missing_seed 直接跳过整段扇出；结合 `browserless_session.py:661-662` 在缺种子时**不发任何上游请求即返回** `skipped="missing_seed"`。
- 至多 1 兄弟：`account_service.py:188-213` 循环内成功/失败均 `break`；失败分支仅打一条 WARNING（`:208-212`），无「逐兄弟继续撞」。
- 测试坐实：`tests/unit/test_cookie_rotation_and_bundle.py` 的 `test_missing_seed_skips_sibling_fanout`（`calls == 1`）与 `test_sibling_fanout_attempts_at_most_one_sibling`（3 个同 cid 兄弟但 `calls == 2` 且命中 `至多 1 个兄弟` 日志）。
- 残留说明：扇出回退仍调用**裸** `rotate_google_cookies`（`account_service.py:204`），这是 P2-2（范围决策，见 §7）。

判定：**满足**（收敛要求）；裸回退另列 P2-2。

### 2d. 裸配方 UA/accept-language 对齐 Firefox 152、无 Chrome 残留（限 RotateCookies 配方）

- `cookie_parser.py:730-733` `DEFAULT_BROWSER_USER_AGENT` 改为 `... Firefox/152.0`，新增 `DEFAULT_ACCEPT_LANGUAGE="en-US,en;q=0.9"`；`:780` 注入 `accept-language`。
- 全站对照：`account_info_refresher.py:61`、`model_discovery.py:41` 均为同一 Firefox 152 UA；`model_discovery.py:1421`、`account_info_refresher.py:286/832/934` 均用 `en-US` 系 accept-language，**口径一致**。
- RotateCookies 配方内已无 Chrome：`rotate_google_cookies`（`:777-785`）只含 Firefox UA。
- 全局扫描仍有一处 Chrome UA：`cookie_parser.py:627`（`probe_account_emails_map` 的 OGS 邮箱探测），**属「邮箱回填」风险点、非本批 RotateCookies 配方，且本批未改动该行**。见 N-1。

判定：**满足**（RotateCookies 配方无 Chrome）。

---

## 3. 冷启动收获安全（清单 3）

- **不新增请求/goroutine/阻塞**：`sendPageRPCs`（`runtime_go.go:548-568`）把原 `drain(do(...))` 拆为 `do()` → 判断捕获方法 → `captureStartupResponse` 或 `drain(response, nil)`。仍在**既有页面启动 RPC 的同一 goroutine** 内完成；`drain` 本就整体读空并关闭（`:609-615`），捕获读 `LimitReader(64KB)` 后继续 `io.Copy(io.Discard)` 关闭（`:572-595`），I/O 总量与原路径等价，无额外阻塞。
- **有界**：`startupCaptureLimit = 64 << 10`（`:49`）；单测强制断言超长响应被截到恰好 65536 字节。
- **GET /v1/worker/state 只读 + 127.0.0.1 默认**：`WorkerPool.WorkerState`（`main.go:397-427`）仅读 `entry.worker.StartupCapture()`，无写入；路由 `main.go:1199-1215` 校验 `accountKey`（`validateExplicitAccountKey`）。signer 默认监听 `127.0.0.1:8081`（`main.go:1137/1154`），本端点不改变监听面。
- **未污染 MintResponse 契约**：新增独立 `WorkerStateResponse`（`signerapi/types.go:79-92`），仅 `accountKey/visitId/capturedAt/startupTierRaw/startupModelsRaw`，无 Cookie/头/授权。
- **capturedAt 去重**：Python `_harvest_worker_startup`（`account_keepalive.py:533-581`）以 `capturedAt`（或原始体摘要）为 marker，`:562-564` 同快照只消费一次；测试用同一状态跑两轮断言 `fetch_calls==2` 但 `accepted` 仅 1 次。
- **解析失败不落脏快照**：`accept_startup_models`（`model_discovery.py:1074-1106`）对非 str/bytes、空串、JSON 解析失败、`_parse_raw_models` 空结果一律 `return False`，不触碰缓存、不推进失败计数；`_accept_live` 仅在有效模型时提交并清零退避。
- **层级正确**：收获编排在 application 层（`account_keepalive.py`）→ 调用 infrastructure 的 `model_discovery`/`benefit_tier`，无 infrastructure→application 反向 import；`model_discovery._active_rotator` 的 `sys.modules` 写法（`model_discovery.py:297-306`）本批未动（已核对完整）。
- 观察：收获入口仅在保活 `auth_update` 循环内触发（见 N-2）。

判定：**满足**。

---

## 4. 测试真实性（清单 4）

逐条阅读新增/迁移用例：

| 用例 | 断言是否有牙齿 | 判定 |
|---|---|---|
| `test_browserless_session.py::test_refresh_auth_state_rotates_via_signer_and_persists` | 断言 signer RPC 命中 `accounts.google.com/RotateCookies`、body 精确 `'[null,"-1234567890",0]'`、Cookie 落盘、`release→mint` 顺序 | 有牙，强 |
| `test_browserless_session.py::test_rotate_cookies_via_signer_shape_and_delta` | 断言 URL/body/`updated`/落盘值 | 有牙 |
| `test_browserless_session.py::test_rotate_cookies_via_signer_missing_seed_skips` | 断言 `skipped` 且 `rpc_calls == []`（零上游） | 有牙 |
| `test_cookie_rotation_and_bundle.py`（UA/accept-language、signer 路由、missing_seed、至多 1 兄弟） | 裸函数被 patch 成 `AssertionError` 以证明「live 会话不走裸函数」；扇出计数与 WARNING 文本均断言 | 有牙 |
| `test_account_keepalive.py`（收获去重 + 不可解析 tier） | 断言 fetch 2 次/accept 1 次、tier=Ultra 与 `checked_at`；不可解析保持 Free | 有牙 |
| `test_model_discovery_browserless.py`（accept_startup_models 成功/拒绝） | 断言模型落地、`last_source`、退避清零；非法体保持缓存不变 | 有牙 |
| `test_signer_client.py::test_fetch_worker_state_round_trip_and_404` | 断言路径、`accountKey` 参数、200 解析、404→None、空 key→None 且不发请求 | 有牙 |
| Go `runtime_go_test.go`（3 例） | 断言 tier+models 捕获、VisitID、CapturedAt 非零；超长截到恰好 64KB；403/非目标 RPC 不捕获且 CapturedAt 保持零 | 有牙，强 |

- **被移除的 `rotate_google_cookies` monkeypatch autouse fixture（`test_browserless_auth_rebind.py:47-58`、`test_browserless_session.py:26-37`）属职责迁移而非弱化**：`browserless_session.py` 已不再 import/调用 `rotate_google_cookies`（diff 删除该 import），保留 fixture 反而会 `AttributeError`；新路径由 `FakeSigner` 注入 `rpc_set_cookies` 离线覆盖，回归面未缩小。
- 覆盖缺口（非缺陷）：Go 侧只单测 `captureStartupResponse`/`StartupCapture`，未覆盖 `WorkerPool.WorkerState` 与 HTTP 路由；去重测试只用同一 capturedAt，未覆盖「新一次冷启动新 stamp 再次消费」。见 N-3/N-4。

判定：**测试真实、无弱化**。

---

## 5. 门禁独立实跑（清单 5，真实输出）

环境：Windows / pwsh；Python 前设 `PYTHONUTF8=1`、`PYTHONIOENCODING=utf-8`。Go 侧按 `waa-signer/build.ps1` 配方：Zig 0.13.0（`.deps/zig/zig-windows-x86_64-0.13.0`）、`CGO_ENABLED=1`、`CC/CXX=zig cc/c++ -target x86_64-windows-gnu`、`CGO_CXXFLAGS=-I<deps>/include`、`CGO_LDFLAGS=-L<deps>/lib`。

| 门禁 | 命令 | 实测结果 |
|---|---|---|
| 依赖同步 | `uv sync --extra dev` | exit 0 |
| Ruff | `uv run ruff check .` | `All checks passed!`（exit 0） |
| Pyright | `uv run pyright src tests` | `0 errors, 0 warnings, 0 informations`（exit 0） |
| 全量 pytest | `uv run pytest -q` | **`1739 passed in 45.73s`**（exit 0，与声称一致） |
| diff 空白 | `git diff --check` | exit 0 |
| gofmt（改动文件） | `gofmt -l` 5 个改动/新增 .go | 无输出（干净） |
| Go 构建 | `go build ./...` | exit 0 |
| Go 测试 | `go test ./...` | 全包 `ok`/`no test files`，exit 0 |
| Go 新用例强制重跑 | `go test -count=1 -run TestCaptureStartup -v ./internal/aistudio/` | 3 例全 `PASS`，exit 0 |

> 门禁全部通过，与 Worker 声称的「1739 passed / Go 全绿 / gofmt 干净」一致。

---

## 6. 隐私扫描（清单 6）

- 对 `git diff` 按任务指定敏感模式（本机绝对路径前缀、私网网段字面量、真实邮箱域名、设备标识符常量）逐项扫描：**0 命中**（为避免二次落盘敏感样本，本报告不逐字复写这些模式）。
- 本报告全部使用仓库相对路径，未写入本机绝对路径；测试样例邮箱统一为保留示例域名，不含真实邮箱域名。

---

## 7. 问题清单

### P0
无。

### P1
无（无静态可证、必现的功能/安全缺陷；门禁全绿）。

### P2（建议修，含范围决策）

- **P2-1 会话-账号绑定守卫需内建校验**
  `account_service.py:119-131` 的 `_session_can_rotate` 不比较 session 所属账号与 `account_id`，绑定完全依赖两个调用方（`account_keepalive.py:697-718`、`routes_accounts.py:1669-1676`）。当前无可达越界路径，但这是「最严重风险」的唯一防线却不在守卫内。建议给 `BrowserlessSession` 暴露只读 `account_id` 并在守卫内断言 `session_account == account_id`，使不变量不可破坏。

- **P2-2 signer 失败后的兄弟扇出仍走裸 `rotate_google_cookies`（残留最大的裸 RotateCookies 面）**
  `account_service.py:204` 在 signer 轮换返回非 200/非 missing_seed 时，用合并后的兄弟 Cookie **裸 httpx** 打 `accounts.google.com/RotateCookies`。本批目标是「RotateCookies 引擎分路 + 裸请求收敛」；扇出已收敛到 1 次，但仍是裸打。属**范围决策**：请在「本批接受为 signer 失败兜底」与「须并入 signer 路由（同一登录的合并 jar 需可获取 worker）」之间明确取舍。

- **P2-3 browserless `transport.mode=direct_http` 时 `rotate_cookies_via_signer` 实际裸打**
  `account_service.py:122` 只判 `engine=="browserless"`；而 `browserless_session.py:241-245/709-733` 在 `direct_http` 模式下 `send_hooked_request` 会落到 `_direct_http_request`（裸 httpx，`:900-912`）。即配置 `direct_http` 的用户，轮换虽名为「via_signer」实为裸 HTTP。建议守卫增加 `session.transport_state == "signer_rpc"` 条件，或在 direct_http 下显式声明该行为。

### Q（需实机/决策确认）

- **Q-1 signer RotateCookies 的 Origin/Referer 被 Go 改写为 aistudio.google.com**
  Python 传入的 `_ROTATE_COOKIES_HEADERS`（Origin/Referer=`accounts.google.com`，`browserless_session.py:78-82`）会被 `SendProtected` 无条件覆盖为 `https://aistudio.google.com`（`runtime_go.go:859-867`；`main.go:1381-1392` 之后进入 `SendProtected`）。method/body 与裸函数一致，但 Origin/Referer 不一致。鉴于 AI Studio 页面本身也可能以 `aistudio.google.com` 作为 Origin 调 `/RotateCookies`，这未必失败，但**静态/离线无法证实**。建议实机确认 200；否则 Python 传入的 accounts.google.com 头部实为无效死值。

- **Q-2 `WorkerState` 用 TryLock 忙时返回 404，会与「无该 worker」混淆**
  `main.go:408-411` 在 `entry.mu` 被占用（如冷构建进行中，`:546` 起长持锁）时返回 false → 路由 404 → Python `fetch_worker_state` 视为 None 跳过本轮收获。数据不会丢（下次 capturedAt 仍可消费），但可能延迟一个保活周期。建议区分 404（不存在）与 409/503（忙）。

### N（说明/非阻塞）

- **N-1** `cookie_parser.py:627`（`probe_account_emails_map`）仍为 Chrome 133 UA；属「邮箱回填」风险点、本批未改动，按既定批次另行处理，非本批回归。
- **N-2** 冷启动收获仅在保活 `auth_update` 循环内消费（`account_keepalive.py:740-741`）；常规流量期间发生的冷启动将在下一次保活周期被收获（仅保留最新一次快照）。与「任何冷启动即喂缓存」的决策措辞存在时序差，建议在域看板注明口径。
- **N-3** Go 仅单测捕获函数，`WorkerPool.WorkerState` 与 `GET /v1/worker/state` 路由无用例。
- **N-4** 去重测试仅覆盖「同 capturedAt 只消费一次」，未覆盖「新冷启动新 stamp 再次消费」。
- **N-5** signer 成功但 `save_current_cookies()` 落盘失败时，`_rotate_cookies` 仍返回 `ok=True`；随后 `load_cookies_from_auth_file` 可能读回旧值，存在「报成功但磁盘未更新」的轻微语义偏差（已有 OSError 告警）。

---

## 8. Worker 五条偏离逐条裁决

1. **Firefox UA 常量在 cookie_parser 本地定义（避免反向 import gateway）** —— **接受**。`cookie_parser` 属 infrastructure/account，反向 import gateway 会制造同层耦合；本地常量与全站值一致（§2d 对齐核验通过）。
2. **signer 路径头由 Go 契约决定、仅 method/body 与裸函数一致** —— **有条件接受**。method（POST）与 body 逐字一致已核验；但 Origin/Referer 被 Go 覆盖为 aistudio.google.com，须按 Q-1 实机确认，否则 `_ROTATE_COOKIES_HEADERS` 中的 accounts.google.com 头为无效死值。
3. **WorkerState TryLock 忙时返回 404 防阻塞** —— **接受（附建议）**。不阻塞、不新增请求的取舍合理；建议后续区分 404/忙（Q-2）。
4. **未改 engine_runtime/routes_accounts（接线已足）** —— **接受**。实测两文件未改动；`engine_runtime.py:250-256` 已传入 `account_id`、`routes_accounts.py:1669-1676` 已按账号取 session，接线确已足够。
5. **新增 `waa-signer/internal/aistudio/runtime_go_test.go` 为 untracked** —— **接受**。三例覆盖捕获成功/有界/忽略错误，`gofmt` 干净，`go test -count=1` 全 PASS，应随本批入库。

---

## 9. 可裁决项清零判定

- 本批**仍有可自行裁决却未修**的项：**P2-1（会话绑定内建校验）** 属可自裁的加固项，未做；**P2-3（direct_http 守卫）** 属可自裁的小改，未做。
- **P2-2（裸兄弟扇出）与 Q-1（Origin/Referer）** 需用户/主控做范围或实机决策，非 Reviewer 单方可裁。
- 因此「可裁决遗留」判定为：**存在（P2-1、P2-3）**；建议若坚持「可裁决清零」口径，应在下一轮补上这两处守卫再提交。

---

## 10. 结论

**CONDITIONAL PASS**

- A1（RotateCookies 引擎分路：signer 主路 + 差量回写 + 裸配方 Firefox 152 + 扇出收敛）与 A2（signer 冷启动 tier/models 有界捕获 + 只读 worker state + capturedAt 去重 + 零新增请求）**均已实现**。
- 门禁独立实跑全绿：ruff / pyright / pytest **1739 passed** / `git diff --check` / gofmt / `go build ./...` / `go test ./...`。
- 改动集合与声称一致，零触碰禁改路径，无调试残留，隐私扫描 0 命中。
- **放行条件**：
  1. 用户/主控**明确接受或排期** P2-2（signer 失败后的裸兄弟扇出）与 P2-3（`direct_http` 模式的裸轮换）；否则「裸 RotateCookies 清零」口径不成立。
  2. **实机确认** Q-1（signer RotateCookies 被改写为 aistudio.google.com Origin/Referer 仍返回 200）。
- **推荐（非阻塞）**：补 P2-1 会话绑定内建断言；区分 Q-2 的 404/忙语义。

---

### 附：只读再次声明

本 Reviewer 全程只读；唯一写入为本报告文件。未 ssh、未联网、未启动真实服务/浏览器、未访问真实 Google 账号。所有门禁均为本机离线实跑。

---

## 11. 二轮复审（返工核验）

- 复审对象：一轮三项可自裁项（P2-1 会话归属断言、P2-3 direct_http 守卫、Q-2 忙/无区分）的 Worker 返工。
- 复审基线：同上工作树（`5f044e5` 之后未提交改动），新增 `waa-signer/cmd/waa-signer/main_test.go` 改动与 2 个 Python 新用例。

### 11.1 三项闭环状态（逐项 file:line）

| 项 | 状态 | 核验证据 |
|---|---|---|
| **P2-1 会话归属内建断言** | **已闭环** | `account_service.py:120` 签名改为 `_session_can_rotate(session, account_id)`；`:124-131` 取 `session.account_id`，为空或不等于 `account_id` 时打 WARNING（含“归属不匹配”）并 `return False`（fail-closed，绝不复用他账号会话）；唯一调用点 `:174` 已传入 `account_id`。`browserless_session.py:325-328` 新增只读 `account_id` 属性返回 `self._account_id`。 |
| **P2-3 direct_http 守卫** | **已闭环** | `account_service.py:132-138` 要求 `session.transport_state == "signer_rpc"`，否则 debug 记录并 `return False` → 走显式裸路径，**不再以 via_signer 名义行 direct HTTP 之实**。`browserless_session.py:344-346` `transport_state` 属性已存在。 |
| **Q-2 忙/无区分** | **已闭环** | Go `main.go:398` `WorkerState` 返回 `(state, found, busy)`；`:409-411` TryLock 失败置 `busy=true`；`:1210-1218` 路由 busy→`503 {"busy":true}`、`!found`→404。`signer_client.py:321-342` 404→`None`、503 且 `busy:true`→`None`（debug 日志）、503 非 busy 及其他非 200→`SignerError`。消费侧 `BrowserlessSession.fetch_worker_state` 与 `_harvest_worker_startup` 对 404/503-busy 均等价跳过本轮，行为等价。 |

### 11.2 四个新用例断言有牙

- `tests/unit/test_cookie_rotation_and_bundle.py::test_cross_account_session_falls_back_to_naked_rotation`（`:556`）：传入 `acc_other` 的会话处理 `acc_target`，断言 `foreign.calls == 0`、裸函数调用 1 次、命中“归属不匹配”告警。**有牙**（证明跨账号会话被拒用）。
- `...::test_direct_http_session_falls_back_to_naked_rotation`（`:590`）：同账号但 `transport="direct_http"`，断言 `direct.calls == 0`、裸函数调用 1 次。**有牙**（证明不误标 via_signer）。
- `tests/unit/test_signer_client.py::test_fetch_worker_state_round_trip_404_and_busy`（`:302`）：断言 200 解析、`missing`→None、`busy(503)`→None、空 key→None 且不请求，并精确比对三次请求 URL。**有牙**（状态机 200/404/503-busy 全覆盖）。
- `waa-signer/cmd/waa-signer/main_test.go::TestWorkerStateDistinguishesBusyFromMissing`（`:248`）：断言 missing→`found=false,busy=false`、持锁 entry→`found=false,busy=true`、解锁且无 worker→`false,false`。**有牙**（busy 与 missing 真正可区分）。
- 既有 `test_account_service_routes_live_browserless_session_to_signer`（`:525`）仍用同账号 + `signer_rpc` 断言 signer 被调用 1 次、裸函数被 patch 为 `AssertionError`，作为正例基线保留。

### 11.3 门禁独立实跑（真实输出）

| 门禁 | 命令 | 实测结果 |
|---|---|---|
| 依赖同步 | `uv sync --extra dev` | exit 0 |
| Ruff | `uv run ruff check .` | `All checks passed!`（exit 0） |
| Pyright | `uv run pyright src tests` | `0 errors, 0 warnings, 0 informations`（exit 0） |
| 全量 pytest（UTF-8） | `uv run pytest -q` | **`1741 passed in 49.00s`**（exit 0，与声称 1741 一致） |
| 新 Python 用例（显式 node） | 4 个 node id | `4 passed in 0.98s`（exit 0） |
| diff 空白 | `git diff --check` | exit 0 |
| gofmt（改动 6 个 .go） | `gofmt -l ...` | 无输出（干净） |
| Go 构建（CGO/Zig 0.13 配方） | `go build ./...` | exit 0 |
| Go 测试 | `go test -count=1 ./...` | 全包 `ok`/`no test files`，exit 0 |
| Go 定向重跑 | `TestWorkerStateDistinguishesBusyFromMissing` + 3 个 `TestCaptureStartup*` | 4 例全 `PASS`，exit 0 |
| 隐私扫描 | diff 按任务指定敏感模式 | 0 命中 |
| 禁改路径 | `git status` 过滤 | 无源码/文档禁改命中（仅本 Reviewer 自身报告为 untracked） |

### 11.4 最终结论

**PASS**

- P2-1、P2-3、Q-2 三项自裁返工**真实存在、语义正确、用例有牙、门禁全绿**。
- 未发现返工引入的新缺陷；跨账号 fail-closed、direct_http 不误标、busy/missing 可区分且消费侧等价均成立。

### 11.5 剩余延后项台账（按用户口径归类，不计为可裁决遗留）

- **P2-2（设计内保留）**：signer 失败后兄弟扇出仍走裸 `rotate_google_cookies`（`account_service.py:204`），已收敛至至多 1 次；作为 signer 失败的裸兜底保留，属范围决策。
- **Q-1（实机清单）**：signer RotateCookies 的 Origin/Referer 被 Go `SendProtected` 改写为 aistudio.google.com（`runtime_go.go:859-867`）；需实机确认上游返回 200，未确认前 Python 侧 accounts.google.com 头视为潜在死值。
- **N 项（延后归类）**：N-1 邮箱探测 Chrome UA（范围外、未改）；N-2 收获仅在保活窗口消费；N-3 Go 路由层无 HTTP 级用例；N-4 未测“新冷启动新 stamp 再消费”；N-5 落盘失败仍报 ok。

### 11.6 只读声明

本轮复审除在本报告文末追加本章节外，**未修改任何文件**；未 ssh、未联网、未启动真实服务/浏览器、未访问真实 Google 账号。全部门禁为本机离线实跑。
