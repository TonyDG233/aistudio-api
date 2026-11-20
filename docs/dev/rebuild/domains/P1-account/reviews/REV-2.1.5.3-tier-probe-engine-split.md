# REV-2.1.5.3 — Tier 探测引擎分流 + 失败指数退避 + 手动刷新 UI 同步

- 审查类型：独立只读复审（100% 独立子代理，与 Worker 零上下文共享）
- 审查基线：`b1253ff`（REV-2.1.5.2 二轮复审 PASS）之后的**未提交改动**
- 审查日期：2026-11-20
- 审查环境：Windows / pwsh 7 / `uv` 工具链 / 主工作树（仓库根目录）
- 归属域：**`P1-account`**
  - 归属理由：本次改动的实质面是 `BenefitTier` 探测传输安全（`infrastructure/account/account_info_refresher.py` + `api/routes_accounts.py` 的 `detect-tier`），而 `P1-account/index.md` 明确将二者列入「核心代码模块」并承担「BenefitTier 安全探测（D02-014..017）」，同域已有 `REV-C1b-vps-tier-and-403-fix.md` 同族先例。`application/account_keepalive.py` 在本包中只是一行调用点（`respect_backoff=True`）与 provider 注入，不构成独立域变更。
- 结论：**CONDITIONAL**

---

## 1. 结论摘要

核心修复**真实有效且实现正确**：三条主张（引擎分流、指数退避、UI 消费逐账号结果）均在代码与测试中逐条核验成立，1727 项单测全绿，ruff/pyright/web 三门禁全过，无 P0/P1 缺陷，无越界改动、无调试残留。

判 **CONDITIONAL** 的唯一原因是三处**可自行裁决、成本极低却未修**的 P2：新增了一个零调用方的死代码公共 API、`detect-tier` 最关键的「引擎失败后绝不再裸打 + 502 摘要」分支无任何测试覆盖、UI 全失败分支丢失了既有的「登录态失效」提示语义。三者都不改变架构与契约，属于本包内即可闭环的收口项（与项目既定「review 中可自行裁决的 P 项必须当轮修复」纪律一致）。

| 维度 | 结论 |
|---|---|
| 风控正确性（本次核心） | 通过（browserless 路径零裸 headers/Cookie；退避窗口内零上游请求；手动永不被拦；`engine_attempted` 语义正确；三处降级 WARNING 俱在） |
| 架构红线 | 通过（infrastructure 无反向依赖；provider 注入方向 api/app→infra；页面脚本为局部闭包，无 window 全局） |
| 变更集合 | 通过（8 改 + 1 新增测试，零触碰 docs/AGENT/defaults/data/REV-*） |
| 测试真实性 | 有牙齿，但存在 1 个关键分支的覆盖缺口（P2-2） |
| 门禁 | 全过（见 §3） |
| 无池会话残余风险 | 现实存在但已定级 P2（见 §6） |

---

## 2. 变更集合全量审计（清单 1）

`git status --short`（动工前后一致，全程 8 改 + 1 新增）：

```
 M src/aistudio_api/api/routes_accounts.py
 M src/aistudio_api/application/account_keepalive.py
 M src/aistudio_api/infrastructure/account/account_info_refresher.py
 M src/aistudio_api/static/assets/index.js
 M src/aistudio_api/static/index.html
 M tests/unit/test_account_info_refresh.py
 M tests/unit/test_account_keepalive.py
 M web/src/stores/accounts.ts
?? tests/unit/test_tier_probe_engine_split.py
```

`git diff --stat b1253ff`：8 files changed, 435 insertions(+), 23 deletions(-)。

- 与 Worker 声称一致：3 个生产源文件（routes / keepalive / refresher）+ 1 个前端 store + 2 个 static 产物 + 2 个既有测试 + 1 个新测试文件。
- **零越界**：未触碰 `docs/**`、`AGENT.md`、`defaults/**`、`data/**`、`REV-*.md`、`pyproject.toml`、`web/package.json`、`config.yaml`。
- **零调试残留**：对全部新增行做 `print(|console.log|TODO|FIXME|XXX|breakpoint(|pdb` 扫描，无命中。
- `git diff --check b1253ff` → 退出码 0（无空白错误）。
- static 产物与源码一致：旧 bundle 含旧 toast（`已刷新 ${r.refreshed_count}`）、不含 `刷新失败`；新 bundle 反之；diff 仅 1±1 行（minified 单行）。

---

## 3. 门禁独立实跑（清单 7）

全部在本机以 UTF-8 环境实跑，真实输出如下（非复述 Worker 数据）：

| 门禁 | 命令 | 实测结果 |
|---|---|---|
| Python Lint | `uv run ruff check .` | `All checks passed!`（exit 0） |
| Python 格式 | `uv run ruff format --check <6 个改动 py 文件>` | `6 files already formatted`（exit 0） |
| Python 格式（全仓参考） | `uv run ruff format --check .` | `62 files would be reformatted`（**基线既存**：改动集 6 文件全部已格式化，62 个文件均非本 diff 触及） |
| 类型 | `uv run pyright src tests` | `0 errors, 0 warnings, 0 informations`（exit 0） |
| 单测（UTF-8 全量） | `uv run pytest -q` | `1727 passed in 46.21s`（exit 0） |
| 单测（聚焦三文件） | `uv run pytest tests/unit/test_tier_probe_engine_split.py tests/unit/test_account_keepalive.py tests/unit/test_account_info_refresh.py -q` | `82 passed in 2.35s` |
| 前端 Lint | `cd web && bun run lint` | exit 0 |
| 前端类型 | `cd web && bun run type-check` | exit 0 |
| 前端构建 | `bunx vite build --outDir <系统 Temp> --emptyOutDir`（**为遵守「除报告外不得改文件」，构建到临时目录而非覆盖 `static/`**） | `✓ built in 2.11s`（exit 0） |
| 空白 | `git diff --check b1253ff` | exit 0 |

构建产物与仓库 `static/` 逐文件哈希比对（只在两处不一致，均为非功能性）：

1. `assets/index.js`：Temp 33502B / 仓库 33498B，首个差异位于偏移 31886，唯一差别是 Vite 运行时 helper 的 `new Error(...)` 与 `Error(...)`（Vite 版本内部差异）。仓库 bundle **确已包含**本次新逻辑（`R.ok!==!0` / `刷新失败` / `个失败：` / `未知错误` 全部命中），因此不属于「产物漏构建」。
2. `index.html`：整文件 CRLF/LF 差异 + 仓库版尾部多一空行（对应 Worker 偏离⑤），无功能影响。

---

## 4. 逐项清单核验（清单 2–6）

### 4.1 架构红线（清单 2）— 通过

- `account_info_refresher.py:29-50` 的全部 import 只涉及 `config`、`infrastructure.account.*`、`infrastructure.gateway.public_web_constants`、`infrastructure.utils.*`；**无 `aistudio_api.application` / `aistudio_api.api` 反向依赖**（`SlotProvider` 为 `Callable[[], Sequence[object]]` 鸭子类型，`:145`）。
- 注入方向正确：`api/routes_accounts.py:55-73` 从 `runtime_state.instance_manager.pool_slots` 取 provider 注入（api→infra）；`application/account_keepalive.py:149-155` 以 `self._pool_slots`（`:226-237`）注入（app→infra）。
- 复用而非平行抽象：browserless 分支直接调用既有 `session.send_hooked_request`（`account_info_refresher.py:279-292`），与 `model_discovery.py:1330-1345` 同款先例；`signer_client.send_rpc` 接受任意 `url`（`signer_client.py:380-401`）。
- 页面脚本合规：`TIER_PAGE_XHR_SCRIPT`（`:233-256`）是裸箭头函数 + 局部 `Promise`/`XMLHttpRequest` 闭包，**未写 `window`**，符合 AGENT §4.7。
- 协议面方法签名核对：`CDPPage.evaluate(expression, args=None, ..., timeout_s)`（`cdp_client.py:401-408`）与调用点 `:326-337`（`script` 位置参数 + `args`/`timeout_s` 关键字）匹配；`EngineSession.send_hooked_request(*, body, url, headers)`（`session_protocol.py:47-52`）与调用点匹配。
- 见 Q3：browser 分支未复用协议方法，属于「部分复用」而非「再造平行传输」，不破红线但值得收口。

### 4.2 风控正确性（清单 3）— 通过

① **browserless 下不再组装裸 headers/Cookie**：`:278-310` 仅发送 `{"accept": "*/*", "accept-language": ACCEPT_LANGUAGE}`，Cookie/Fingerprint/visit-id 全部由 signer 侧补齐（`browserless_session.py:713-746` + `_merged_headers:830-848`）；裸配方 `_rpc_headers`（含静态 `EXT_BIN`、随机 `x-ai-studio-visit-id`、写死 `sec-fetch-site: cross-site`、Python TLS）只被 `_post_tier_via_http:895-914` 使用，而该函数唯一入口是 `_post_tier_request:893` 的降级分支。测试 `test_tier_probe_engine_split.py:179` 以「headers 全等断言」直接焊死该不变量，`:172` 断言 `transport.requests == []`。

② **退避数列 / 封顶 / 清零 / 窗口内零上游**：`TIER_PROBE_FAILURE_BASE_SECONDS=300`、`TIER_PROBE_BACKOFF_CAP_SECONDS=21600`（`:141-142`）；`_record_tier_probe(:446-463)` 生成 300→600→1200→2400→4800→…→21600，成功即双清（`:451-453`）；`respect_backoff` 命中即 **在 `_post_tier_request` 之前返回**（`:508-521` vs `:548`）。测试 `:249-265` 逐一断言数列与封顶；`:284-289` 断言跳过期请求数仍为 1；`:446-503` 断言两次调度轮仅 1 次 signer 调用、`transport.requests == []`。

③ **手动刷新永不被退避拦截**：`api/routes_accounts.py:514-516` 不传 `respect_backoff`（默认 `False`，`account_info_refresher.py:489`）；测试 `:291-295` 证明退避期间手动刷新仍打上游并清零计数。

④ **`engine_attempted` 防双打语义正确**：`routes_accounts.py:813-830` 仅在 `probe_tier_over_session` 返回非 None 时置位；`:832` 的裸 httpx 兜底被 `not engine_attempted` 严格门控。即「引擎已给出失败结论 ⇒ 绝不裸打第二次」。返回 None 的三种情形（`session is None` / browserless 无 sender `:280-285` / browser 无活动 page `:313-320`）都属于「引擎不可用」而非「引擎失败」，仍允许兜底，语义划分正确。

⑤ **降级 WARNING 路径存在且不打断功能**：三处 WARNING 齐备——无 signer（`:281-285`）、无 page（`:314-320`）、无池会话（`:886-892`）；返回 None 后落到 `_post_tier_via_http`，功能继续。测试 `:205-222`、`:225-242` 分别断言两条 WARNING 文本含 `no live page` / `raw httpx` / `no pooled engine session`。

### 4.3 无池会话降级（清单 4）— 见 §6 定级

### 4.4 UI 同步（清单 5）— 通过（有一处语义小退）

- `web/src/stores/accounts.ts:302-324` 三分支：全成功 `:311-314`、部分失败 `:320-323`（warning）、全失败 `:315-318`（error）；`refreshAll:331` 与 `refreshAccounts:355` 均已改消费 `res.results`，单账号 `refreshAccount` 路径未动。
- 契约一致：后端 `RefreshAccountResult` 逐字段 `id/ok/tier/expired/email/error/checked_at`（`routes_accounts.py:307-317`），TS 类型完全对齐（`web/src/types/accounts.ts:80-94`）；新代码只读 `results[].ok/error`、`refreshed_count`、`expired_count`，**未展示响应中不存在的字段**。
- `expired_count` 语义保留：后端口径仍为 `sum(r.expired is True)`（`routes_accounts.py:533`），成功/部分失败分支继续拼「N 个登录态失效」（`accounts.ts:310`）。
- 小退：全失败分支（`:315-318`）只输出 `刷新失败 N 个账号：<首个错误>`，丢弃 `expiredNote`——旧实现下「全部 401 过期」会提示「N 个登录态失效」，新实现退化为「HTTP 401: …」。判定为 P2-3（文案级，3 行可修）。

### 4.5 测试真实性（清单 6）— 大体有牙齿

10 个新用例逐条判读（`tests/unit/test_tier_probe_engine_split.py`）：

| # | 用例 | 断言是否有牙齿 |
|---|---|---|
| 1 | `:162` browserless 走 signer | 强：`transport.requests == []` + headers 全等 + 落库 tier/tier_checked_at |
| 2 | `:186` browser 走页面 XHR | 强：transport 零调用 + `args` 字段逐一断言 + `SAPISIDHASH ` 前缀 |
| 3 | `:205` 无 page 降级 | 强：确证走了一次裸请求 + WARNING 文本两段匹配 |
| 4 | `:225` 无池会话降级 | 强：`[]` 体解析为 Free + WARNING 文本 |
| 5 | `:245` 退避数列/封顶/清零 | 强：逐个数值断言，非区间 |
| 6 | `:268` 调度遵守退避 / 手动不遵守 | 强：以「请求计数不增」证明零上游，再以成功清零证明手动穿透 |
| 7 | `:298` 批量逐账号 ok/error | 强：顺序 + 首条成功 + 次条 403 摘要 |
| 8 | `:345` detect-tier 走 signer | 强：把 `probe_account_benefit_tier` 换成「被调用即 AssertionError」，物理封死裸兜底 |
| 9 | `:383` 保活 metadata 走 signer | 强：signer 1 次 + transport 0 次 + 落库 + 计数 0 |
| 10 | `:446` 失败后下一轮不再打上游 | 强：signer 403 → 计数 1 → 第二轮 signer 仍 1 次、transport 0 次 |

被改的既有用例无弱化：`test_account_info_refresh.py:605` 与 `test_account_keepalive.py:180/208/230` 仅补 `respect_backoff` 形参，并新增一条正向断言 `test_account_keepalive.py:606 assert refresher.backoff_flags == [True]`（更严，非更松）。

唯一关键缺口：**清单④「引擎失败 ⇒ 不再裸打 + 502 带摘要」这条最核心的风控分支没有直接用例**（见 P2-2）。

---

## 5. 问题清单

### P0 —— 无

### P1 —— 无

### P2（本包内可自行裁决，建议当轮收口）

**P2-1 新增公共 API `fetch_tier_response` 零调用方（死代码 / 与 `refresh_tier` 语义重复）**

- 证据：`account_info_refresher.py:787-800` 新增 `async def fetch_tier_response(...)` 并列入 `__all__:1001`；全仓检索 `.fetch_tier_response(` 命中 0 处。其行为与既有 `refresh_tier:702-768` 的传输段完全重复（后者在 `b1253ff` 基线亦无调用方，属既存死代码，不在本包追责范围）。docstring 声称「manual endpoint」使用它，但 `detect-tier` 实际直接调用 `probe_tier_over_session`（`routes_accounts.py:816`）。
- 影响：公共面污染 + 后续维护者误判两条并行入口；违反 AGENT §3.3「精炼克制」「零 Shim」。
- 建议：删除该函数与 `__all__` 条目（或让 `detect-tier` 改用并顺带消除重复），二选一即可。

**P2-2 `detect-tier` 引擎失败 → 502 摘要 / 防双打分支无测试**

- 证据：被测代码 `routes_accounts.py:813-855`；现有唯一 endpoint 用例 `test_tier_probe_engine_split.py:345` 只覆盖「signer 200 成功」路径。无任何用例断言：(a) signer 返回 403 时 `probe_account_benefit_tier` **不被调用**；(b) 最终 `HTTPException.status_code == 502` 且 `detail` 含 `HTTP 403: Region not supported.`。
- 影响：这正是本次修复的风控核心语义，却只有代码阅读保障；后续任何一次改动都可静默回退为「引擎失败后再裸打一次」，且 1727 项测试无法报警。
- 建议：补 1 个用例，复用 `:364` 的 `_forbidden_probe` 手法 + `pytest.raises(HTTPException)`，断言 `exc.status_code == 502` 与 `"HTTP 403" in exc.detail`。

**P2-3 UI 全失败分支丢失「登录态失效」提示**

- 证据：`web/src/stores/accounts.ts:310` 计算了 `expiredNote`，但 `:315-318` 的全失败分支未使用；对照成功分支 `:311-314` 与部分失败分支 `:320-323` 均使用。后端对 401 账号返回 `expired=true, error="HTTP 401: …"`（`routes_accounts.py:530-537`），因此「全部账号登录态失效」的场景文案由「已刷新 0 个，N 个登录态失效」退化为「刷新失败 N 个账号：HTTP 401…」。
- 影响：语义提示精度下降，属回归（旧行为包含该信息）。
- 建议：全失败分支拼接 `expiredNote` 后再追加首条错误；改后需 `cd web && bun run build` 重新产出 static。

---

## 6. 无池会话降级风险评估（清单 4 定级）

**结论：现实存在、已缓解、定级 P2（可接受但有残余），不构成 P1。**

引擎通道可用条件（实测）：

1. 账号被绑定到某个池槽位（`InstanceSlot.account_id`，`instance_pool.py:162-176`）；
2. `slot.session` 非 None（`find_account_session:198-216` 会跳过空/死会话）；
3. browserless 需 `is_alive()` 为真（`browserless_session.py:369-372`，判据是持有 SAPISID）；browser 需 page 未关闭（`_page_is_open:188-195`）。

覆盖面证据：`InstanceManager.startup()`（`instance_manager.py:239-298`）对每个 spec 槽位**立即**挑选账号并 `_activate → pool.activate → _bootstrap_runtime`（`instance_manager.py:1303-1348`、`instance_pool.py:565-640`），即两种引擎在启动后都是**急切建立 session**（非懒加载）。因此「已绑定槽位的账号」在稳态下恒有引擎通道——这正是原 403 病灶的账号集合。

残余窗口（三条，均可定位）：

- (a) 账号数 > 槽位数的差额账号（`startup` 的 `_pick_account` 用尽后槽位 `UNSTARTED`，过剩账号无槽位）；
- (b) 启动 `_activate` 失败 → 槽位 `INVALID`（`instance_manager.py:284-292`）；
- (c) browserless 会话丢失 SAPISID（`is_alive()` 假）→ `find_account_session` 跳过。

影响评估：这些账号仍会走裸 httpx，但**已被两重缓解**——① `account_info_refresher.py:886-892` 打出显式 WARNING（可观测）；② 指数退避把重复窗口从 300s 拉到 6h 封顶，且窗口内零上游请求（实测用例 `:446-503`）。同时，未绑定槽位的账号本身不承载任何生成流量，tier 陈旧不影响路由准入。

因此**不构成 P1**；但它确实意味着「无槽位账号的 tier 刷新依旧可能被 Google 判 region not supported」，必须在此明确记录，不得宣称「已全量根治」。若未来要求覆盖该类账号，正确做法不是为它们临时开页/建会话（违反 D-05 单页边界与防风控铁律），而应在文档层面声明「tier 刷新覆盖范围 = 已绑定槽位账号」。

---

## 7. Worker 五条偏离逐条裁决

| # | 偏离内容 | 裁决 | 理由与保留意见 |
|---|---|---|---|
| ① | `cookie_parser.py` 未改，原裸探测仅作无池会话最后兜底 | **接受** | 裸配方只在 `:893` 降级可达，且已被 WARNING + 退避双重约束；改 `cookie_parser` 会波及其它 5 处既有裸调用点（`app.py:291`、`session_auth.py:294`、`routes_accounts.py:674/1331/1611`），超出本包边界。保留意见见 Q4/Q5。 |
| ② | signer 通道依赖账号存在池槽位，无槽位仍走裸 httpx | **接受** | 见 §6：这是 D-05「不新建页/不多开页」约束下的必然取舍；改法应是把「覆盖范围」写清楚，而不是打破单页边界。残余窗口已定级 P2 而非 P1。 |
| ③ | 手动失败也会给周期退避「上膛」 | **接受** | 「手动永不被拦截」已被代码（`routes_accounts.py:514`）与用例（`:268`）双双保证；手动失败上膛的实际效果是「手动连点也不能加速撞 Google」，与防风控初衷一致，且成功即清零（`:451-453`）。保留意见：cookie 重导入不会复位退避（Q1）。 |
| ④ | `app.py` 未动 | **接受（带保留）** | 启动期一次性 tier 探测（`app.py:280-300`）仍是裸 httpx，属同一病灶类别但为「每次启动一次」的非循环路径，且已有 `is_verified_free_tier_fresh` 跳过。列入 Q5，不阻塞本包。 |
| ⑤ | 构建产物 `index.html` 尾部多一空行 | **接受** | 纯样式产物差异，无功能影响（构建到临时目录比对已确证：除该空行与 CRLF 外与源码构建一致）。列入 Q7，建议由 `bun run build` 重新生成以消除手工痕迹。 |

---

## 8. Q（质量与非阻塞观察）

- **Q1 退避状态不会因凭据重导入而复位**：`_tier_failure_counts/_tier_retry_at` 仅在成功时清零（`account_info_refresher.py:451-453`），导入/cookie 回写路径（`routes_accounts.py:1331/1611`）无复位钩子。极端场景：账号 403 连续失败到 6h 退避后，用户修复代理/换区并重导入 cookies，周期刷新仍可能被「空等」最多 6h（手动刷新不受影响）。建议后续在导入成功处调一个 `reset_tier_backoff(account_id)`。
- **Q2 「跳过」被记成「失败」**：`respect_backoff` 命中返回 `ok=False`（`:516-521`），keepalive 计入 `failed`（`account_keepalive.py:441-446`）并以 `FAILURE_RETRY_SECONDS=300` 重排（`:298-299`），每轮还会 `mark_refresh_attempt` 写 `last_refresh_error="tier probe backoff active (Ns remaining)"`（`account_store.py:1159-1167`）。效果：退避期间每 300s 一次 registry 原子写 + 一条 INFO/WARNING 级日志，且保活状态持续显示「失败」。不误伤上游，但语义不精确、有轻微磁盘/日志噪音。建议后续为「跳过」引入独立 reason（如 `ok=True, skipped="backoff"`）或至少让 keepalive 不对该 reason 落错误。
- **Q3 browser 分支未复用协议方法**：`EngineSession.send_hooked_request`（`session_protocol.py:47-52` → `pooled_session.py:258-278` → `transport.py:303-345`）本就提供「页内 XHR replay + 页内 authorization 计算 + 网络证据」。当前实现为 browser 分支另写 `TIER_PAGE_XHR_SCRIPT` 并用 Python 侧 `SAPISIDHASH`（`account_info_refresher.py:321-345`），与 `session_auth.py:200-228`、`model_discovery.py:1250-1310` 形成三份同构脚本。功能与测试均正确（且可避开 net-trace 监听对生成流的干扰，属于 Worker 的合理取舍），但「复用而非平行抽象」只达成一半。建议下一轮统一走 `session.send_hooked_request` 并删除脚本常量。
- **Q4 邮箱回填仍是裸 httpx，且因本次修复变得更可达**：`_fetch_account_email → _fetch_app_html`（`:942-962`）用裸 httpx 抓 apps 页 HTML；修复后付费账号 tier 探测首次成功，会继续走该分支取邮箱。手动刷新的 `refresh_email` 默认 True（`routes_accounts.py:304`），故每次手动「刷新」对每个账号会产生 1 次裸文档 GET。频率由用户点击控制、非周期放大，但与「减少 Python TLS 直连 Google」的方向不完全一致，建议后续将邮箱回填并入引擎通道。
- **Q5 启动期一次性裸探测仍在**（`app.py:280-300`）：每次冷启动对「非 3 天内已验证 Free」的账号各发一次裸 `GetAiStudioBenefitTier`，失败仅 debug 日志且不入退避。与偏离④同源，建议与重启纪律一并文档化。
- **Q6 退避字典无回收**：`_tier_failure_counts/_tier_retry_at`（`:426-427`）以 account_id 为键，账号删除后条目常驻（进程级极小额泄漏）。可在账号删除/导入路径顺手 `pop`。
- **Q7 静态产物可复现性**：`static/index.html` 尾部空行非构建产物（比对构建输出无该空行）；`static/assets/index.js` 与当前 Vite 版本构建结果相差 Vite 运行时 helper（`new Error` vs `Error`）。均无功能影响，建议以 `bun run build` 重新生成以消除「手工编辑产物」痕迹。
- **Q8 UI 部分失败分支无法在 UI 沙盒内实测**：`SandboxAccountRefresher.refresh_accounts` 恒定 `ok=True`（`ui_sandbox.py:990-1030`），无法造出「部分失败」响应，故本次 Toast 新分支只能靠代码与 TS 类型核验。同时本审查**未做 Playwright 截图归档**：①本次无页面/布局变更（仅 toast 文案逻辑）；②本任务红线限定「除报告外不得改文件」，截图资产落盘会越过该边界；③沙盒无部分失败场景可观察。建议后续给沙盒补一个 `refresh_partial` 场景，使该分支可回归。

---

## 9. 逐项清单结论对照

| 清单项 | 结论 | 关键证据 |
|---|---|---|
| 1 diff 全量审计 / 白名单 / 残留 | 通过 | §2 |
| 2 架构红线 / 复用 / 页面脚本闭合 | 通过（Q3 为质量建议） | §4.1 |
| 3 风控正确性 ①②③④⑤ | 通过 | §4.2 |
| 4 无池会话降级风险 | P2 残余，接受 | §6 |
| 5 UI 同步 | 通过（P2-3 文案回归） | §4.4 |
| 6 测试真实性 | 通过（P2-2 覆盖缺口） | §4.5 |
| 7 门禁独立实跑 | 全过 | §3 |
| 8 遗留判定 / 偏离裁决 | 已给出 | §5、§7 |

**本轮之后仍存在「可自行裁决却未修」的 P 项**：P2-1、P2-2、P2-3（三项均属本包内数行到十余行可闭环的收口项）。修复 P2-3 后须重新 `bun run build` 并复跑 web 三门禁；修复 P2-1/P2-2 后须复跑 `uv run pytest`、`ruff check`、`pyright`。

---

## 10. 只读声明

- 本次审查**仅新增本报告文件** `docs/dev/rebuild/domains/P1-account/reviews/REV-2.1.5.3-tier-probe-engine-split.md`，未修改任何源码、测试、配置、产物或其它文档。
- 未执行 `git add/commit/push/stash` 等改变仓库状态的操作；`git stash create` 仅生成了一个悬空对象、不改变工作树与 stash 列表（如需可忽略）。
- 未启动任何真实服务（`main.py server` 生产模式）、未拉起真实浏览器、未发起任何真实 Google / 外部网络请求、未执行 ssh 或远程操作。
- 前端构建验证使用 `vite build --outDir <系统 Temp>/kilo/verify-build` 输出到工作区之外，未覆盖 `src/aistudio_api/static/`。
- 全部门禁命令均在本机以 `PYTHONUTF8=1 / PYTHONIOENCODING=utf-8` 实跑，输出见 §3，未引用 Worker 数据。

---

## 11. 二轮复审（返工核验）

- 复审类型：独立只读复审（复核 Worker 对一轮三项 P2 的返工）
- 复审基线：`b1253ff` 之上**仍未提交**的工作树（同一未提交变更集）
- 复审日期：2026-11-20
- 复审环境：Windows / pwsh 7 / `uv` 工具链 / 主工作树（仓库根目录）
- 本轮结论：**PASS**

### 11.1 三项 P2 逐项闭环状态

| P 项 | 状态 | 独立核验证据（file:line） |
|---|---|---|
| **P2-1** 死代码 `fetch_tier_response` | **已闭环** | 全库检索（src/tests/web）`fetch_tier_response` 命中 **0 处**，仅本报告历史证据保留该名称；`account_info_refresher.py:973-988` 的 `__all__` 已无该条目，仅列常量 + `SlotProvider/find_account_session/probe_tier_over_session`；删除后全量 1729 项单测通过，无悬挂引用。既存 `refresh_tier:702` 属基线遗留、非本包新增，按原口径保留不追责。 |
| **P2-2** 引擎失败 → 502 摘要 / 防双打无测试 | **已闭环** | 新增 `tests/unit/test_tier_probe_engine_split.py:384-423` `test_detect_tier_engine_failure_blocks_raw_probe_and_returns_502`：signer 返回 403 时 `probe_account_benefit_tier` 被替换为「被调用即 `AssertionError`」(`:405-408`)，断言 `exc.value.status_code == 502`(`:415`)、`detail` 同时含 `HTTP 403` 与 `Region not supported.`(`:417-418`)、`len(session.calls) == 1`(`:419`)、`manager.synced == []`(`:420`)、落库 `tier` 仍为 `Pro`(`:423`)。配套 `:426-462` `test_detect_tier_without_pool_session_falls_back_to_raw_with_warning`：无池会话时 `engine_probe.assert_not_awaited()`(`:459`)、`raw_probe.assert_awaited_once()`(`:460`)、且命中 `回退裸 httpx` WARNING(`:462`)。两例均有牙齿（状态码、摘要、禁裸打、WARNING 四要素齐备）。 |
| **P2-3** 全失败分支丢 `expiredNote` | **已闭环** | `web/src/stores/accounts.ts:302-323` 三分支全部使用 `expiredNote`：全成功 `:312`、全失败 `:317`（`刷新失败 ${failures.length} 个账号${expiredNote}：${firstError}`）、部分失败 `:321`；语义一致，无分支遗漏。static 产物已同步含该分支（见 §11.3）。 |

### 11.2 变更集合增量核验（对比一轮记录）

一轮记录：`8 files changed, 435 insertions(+), 23 deletions(-)` + 1 新增测试文件。
本轮实测 `git diff --stat`：`8 files changed, 420 insertions(+), 23 deletions(-)`，删除行数不变、仅插入行 **−15**。

- 该 −15 行**完全由删除 `fetch_tier_response` 解释**：函数体约 14 行 + `__all__` 条目 1 行 = 15 行。
- 新增测试两例位于**未跟踪文件** `tests/unit/test_tier_probe_engine_split.py`（不计入 `git diff --stat`），行数由一轮的 10 例扩到 12 例（`--collect-only` 实见三支 `detect_tier` 用例）。
- store 为**同行改写**（`:317` 由 `账号：` 改为 `账号${expiredNote}：`），不改变行数，故不动插入/删除统计。
- `git status --porcelain` 与一轮集合逐字一致（8 改 + 同一未跟踪测试文件），另仅多出本报告文件本身；**无新增越界文件**、无 `docs/**`/`AGENT.md`/`defaults/**`/`data/**`/`pyproject.toml`/`config.yaml` 改动。
- 调试残留扫描（对全部新增行匹配 `console.log|TODO|FIXME|XXX|breakpoint|pdb|print(`）**零命中**。

### 11.3 静态产物核验 + Q7 更正

- `static/assets/index.js`（34781B）含本轮新文案：失败分支为 `刷新失败 ${t.length} 个账号${v}：${A}`（`v` 即 `expiredNote`），证明**确已重建**（一轮旧 bundle 不含 `刷新失败`）。
- 与当前源码 `vite build`（输出到系统 Temp，未覆盖 `static/`）逐字节比对：长度 34785 vs 34781，**唯一差异**位于偏移 33137，为 Vite 运行时 preamble 的 `new Error(` vs `Error(`（4 字节），属 Vite 版本内部 helper 差异，**功能等价**。
- **Q7 更正**：我现以同一 `vite build` 复核，构建输出 `index.html` 在 `<div id="app"></div>` 与 `</body>` 之间**确实自带一空行**（生成 `<CR><CR><LF>`）。因此仓库 `static/index.html` 的尾部空行是**真实构建产物**而非手工编辑；一轮 Q7「比对构建输出无该空行」的判断有误，特此更正。差异仅剩行尾风格（仓库 LF vs 本机构建 CRLF），无功能影响。

### 11.4 门禁独立实跑（本轮真实输出）

| 门禁 | 命令 | 实测输出 |
|---|---|---|
| Python Lint | `uv run ruff check .` | `All checks passed!`（exit 0） |
| 类型 | `uv run pyright src tests` | `0 errors, 0 warnings, 0 informations`（exit 0） |
| 单测（UTF-8 全量） | `uv run pytest -q` | `1729 passed in 50.83s`（exit 0） |
| 单测（本包聚焦） | `uv run pytest tests/unit/test_tier_probe_engine_split.py -q` | `12 passed in 0.66s`（exit 0） |
| 前端 Lint | `cd web && bun run lint` | exit 0（无输出，0 warning） |
| 前端类型 | `cd web && bun run type-check` | exit 0 |
| 前端构建（旁路核对） | `bunx vite build --outDir <系统 Temp>/verify-build-2 --emptyOutDir` | `✓ built in 2.65s`（exit 0，未覆盖 `static/`） |
| 空白 | `git diff --check` | exit 0 |

单测数由一轮 1727 增至 1729，与新增两例一一对应，无既有用例被跳过或弱化。

### 11.5 剩余 Q / N 台账（均非本轮可裁决 P）

- **Q1** 退避状态不因凭据重导入复位（`account_info_refresher.py:451-453` 仅成功清零）——延后，需独立钩子。
- **Q2**「退避跳过」被 keepalive 记成失败并有 300s 重排/落盘噪音——延后，需引入独立 reason 语义。
- **Q3** browser 分支未复用 `send_hooked_request`（另写 `TIER_PAGE_XHR_SCRIPT`）——延后，属收敛建议。
- **Q4** 邮箱回填仍走裸 httpx（`_fetch_app_html`）——延后，与减少 Python TLS 直连方向相关。
- **Q5** 启动期一次性裸探测仍在（`app.py`）——延后，与重启纪律一并文档化。
- **Q6** 退避字典无回收（账号删除后条目常驻）——延后，进程级小额泄漏。
- **Q7** 静态产物可复现性：**已更正**——尾部空行为真实构建产物（非手改）；仅余 LF/CRLF 与 Vite helper 版本差异，无功能影响。建议后续统一锁 Vite 版本以消除 4 字节漂移。
- **Q8** UI 部分失败分支无法在沙盒实测（`SandboxAccountRefresher.refresh_accounts` 恒 `ok=True`），且本次无页面/布局变更、红线限定除报告外不改文件，故未做 Playwright 截图；建议沙盒补 `refresh_partial` 场景。
- **N-0**：本轮返工**未引入任何新的可裁决阻断项**（无新增 P0/P1/P2，无越界改动，无死代码回流）。

### 11.6 最终结论

**PASS**：三项 P2（P2-1 死代码删除、P2-2 关键风控分支补测、P2-3 UI 全失败文案恢复）均真实闭环、证据可复验；变更集合仅比一轮多出「删除函数 + 两例新测试 + store 一行 + static 重建」四项，无越界；六项门禁（ruff / pyright / 全量 pytest / web lint / web type-check / `git diff --check`，另附旁路构建）全部实跑通过。本轮之后**不存在「可自行裁决却未修」的 P 项**，仅余上述 Q 类延后/依赖外部项。

### 11.7 本轮只读声明

- 本次复审**仅向本报告文件追加本章节**，未修改任何源码、测试、配置、产物或其它文档；`static/` 未被覆盖（构建输出写入系统 Temp）。
- 未执行 `git add/commit/push/stash/reset/checkout` 等改变仓库状态的操作。
- 未启动任何真实服务（`main.py server` 生产模式）、未拉起真实浏览器、未发起真实 Google / 外部网络请求。
- 门禁命令均以 `PYTHONUTF8=1 / PYTHONIOENCODING=utf-8` 实跑，输出见 §11.4，未引用 Worker 数据。
