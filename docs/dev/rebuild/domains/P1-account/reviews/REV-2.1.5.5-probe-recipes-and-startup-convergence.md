# REV-2.1.5.5 · 批次 B · 裸探测配方头部修复 + 启动期串行探测收敛

- 审查基线：`5796ee2`（REV-2.1.5.4 二轮复审 PASS）之后的**未提交工作树改动**
- 业务域：`P1-account`（Cookie 探测配方 / 账号元数据刷新；跨触 P6 保活周期与 P9 启动装配）
- 审查类型：100% 独立只读 Reviewer，与 Worker 零上下文共享
- 审查日期：2026-11-20
- 结论：**CONDITIONAL PASS**（B1 三处配方、B3 启动零裸探测与保活承接均成立，门禁除格式检查外全绿；批次自身新增测试文件未格式化、另有两处可自裁的注释/命名残留未清零）

---

## 0. 只读声明

本次审查**除本报告文件外未修改任何文件**；未执行 `ssh`、未联网、未启动生产服务、未拉起真实浏览器、未访问真实 Google 账号。全部门禁为本机离线实跑（Windows / pwsh，Python 前设 `PYTHONUTF8=1`、`PYTHONIOENCODING=utf-8`）。

---

## 1. 改动集合核对（清单 1）

`git status --short` 实测：

```
 M src/aistudio_api/api/app.py
 M src/aistudio_api/application/account_keepalive.py
 M src/aistudio_api/infrastructure/account/account_info_refresher.py
 M src/aistudio_api/infrastructure/account/cookie_parser.py
 M tests/unit/test_account_keepalive.py
 M tests/unit/test_app_startup_warmup.py
 M tests/unit/test_tier_probe_safety.py
?? tests/unit/test_probe_headers.py
```

- 与 Worker 声称的 B1/B2/B3 改动集合逐文件一致；`tests/unit/test_probe_headers.py` 为唯一 untracked 新文件。
- **完整不折叠 diff 已逐行审计**（`git diff --stat` 与 7 个文件的全部 hunk）：改动仅落在上述 7 个 tracked 文件 + 1 个新测试文件。
- **零触碰核验**：`git status` 中不含 `REV-*.md`、`docs/dev/**`、`AGENT.md`、`defaults/**`、`web/**`、`waa-signer/**`，亦无 `routes_accounts.py`。本报告除外。
- 无调试残留：对 diff 新增行与新增测试文件扫描 `print(` / `breakpoint(` / `pdb` / `TODO` / `FIXME` / `sys.settrace`，0 命中。
- `git diff --check`：exit 0（无空白错误）。

> 结论：改动集合与声称一致，无越界改动。

---

## 2. 风控正确性（清单 2）

### 2a. 三个探测函数头部已修复，且是纯头部修复

| 函数 | 修复点 | 证据 |
|---|---|---|
| `probe_account_emails_map`（OGS/邮箱映射） | 旧 Chrome 133 UA 换成全站 Firefox 152，并补 `accept-language` | `src/aistudio_api/infrastructure/account/cookie_parser.py:627-628` |
| `probe_account_benefit_tier`（GetAiStudioBenefitTier） | 新增 `User-Agent` + `accept-language`（此前完全依赖 httpx 默认 UA） | `cookie_parser.py:704-705` |
| `probe_google_accounts_infinite`（ListModels 链式探测） | 新增 `User-Agent` + `accept-language` | `cookie_parser.py:848-849` |

- 统一常量：`cookie_parser.py:733-736` `DEFAULT_BROWSER_USER_AGENT = "… rv:152.0 … Firefox/152.0"`、`DEFAULT_ACCEPT_LANGUAGE = "en-US,en;q=0.9"`。
- 全站一致性：`account_info_refresher.py:60-63` 的 `USER_AGENT` / `ACCEPT_LANGUAGE` 为同一字面量；`rotate_google_cookies`（`cookie_parser.py:778-783`）亦用同一常量。
- **纯头部修复**：`cookie_parser.py` 的 diff 只有 3 个 hunk、净增 7 行，全部是头部行；URL、`content="[]"` 请求体、`X-User-Agent`/`X-Goog-*` 等既有头、超时、`AsyncClient` 生命周期、探测时序与返回值均零变更；三个函数签名与返回契约未动。
- `probe_account_benefit_tier` 现显式下发 Firefox UA，**不再可能以 `python-httpx` 默认 UA 发出**；新用例断言 `"python-httpx" not in headers["User-Agent"]`（`tests/unit/test_probe_headers.py:121`）。

判定：**满足**。

### 2b. 全站 UA 一致性断言真实存在

`tests/unit/test_probe_headers.py:89-93` 同时断言 `DEFAULT_BROWSER_USER_AGENT`、`DEFAULT_ACCEPT_LANGUAGE`、`USER_AGENT`、`ACCEPT_LANGUAGE` 四者等于同一 Firefox 152 字面量，可捕获两处常量漂移；`:104-107`、`:139-140` 对每次实际请求的头部逐一复核。

---

## 3. 启动收敛正确性（清单 3）

### 3a. 启动路径零裸探测

- `src/aistudio_api/api/app.py:204-230` 新 `_startup_account_metadata_snapshot()` 只读 `account_store.list_accounts()`，统计 email/tier/tier_checked_at 并打一条 INFO，函数体内无 `httpx`、无 `probe_*`、无 `await`。
- `app.py:512-523` `_startup_metadata_and_tiers()` 仅调用 snapshot + `keepalive.run_startup_metadata_refresh()`，无第二段应用侧探测。
- 全文件扫描 `app.py`：`probe_account*` / `probe_google*` / `import httpx` / `_refresh_startup` **0 命中**；旧函数 `_refresh_startup_account_metadata`、`TIER_PROBE_FRESHNESS_SECONDS`、`_checked_at_ts` 及 `from datetime import UTC, datetime` 均已删除且全仓无残留引用（`TIER_PROBE_FRESHNESS_SECONDS` 仅存在于其正确归属 `account_info_refresher.py:134/356`，供 `is_verified_free_tier_fresh` 使用）。

判定：**满足**。

### 3b. 保活首轮刷新走 AccountInfoRefresher 传输层（signer 感知）

调用链实测：`account_keepalive.py:392-398` `run_startup_metadata_refresh` → `run_now("metadata_refresh")` → `_metadata_refresh`（`:409-443`）→ `self._refresher.refresh_accounts(...)` → `account_info_refresher.py:853-878` `_post_tier_request`：命中池内 session 时走 `probe_tier_over_session`（signer/page XHR），仅在无 session 时才回退 `_post_tier_via_http` 并打 `region-restricted` WARNING。启动路径自身不再构造任何裸请求。

判定：**满足**（运行时是否已拿到池 session 见 Q-2）。

### 3c. 「补邮箱」调度补齐语义正确，且不引入重复 tier 探测（Free 分支成立）

- `account_keepalive.py:420-429` due 过滤新增 `not getattr(account, "email", "")` 首条件：缺邮箱账号即使 tier 刚校验过也进入批次；`checked` 仅在第二分支赋值，短路安全，无未定义引用。
- 进入 `refresh_accounts` 时仍为 `refresh_email=False, skip_fresh_free_tier=True, respect_backoff=True`（`:438-443`）；`refresh_account` 的 `wants_email = refresh_email or not account.email`（`account_info_refresher.py:581-582`）保证缺邮箱仍会回填。
- 已验证 Free 新鲜账号走 `:522-546` 分支：只 `_fetch_account_email`，**不发 tier POST**——新用例以 `transport.posts == 0`、`transport.gets == 1` 钉死（`tests/unit/test_account_keepalive.py:820-824`）。
- 增补副作用：对「缺邮箱但 tier 为付费且仍在新鲜窗口」的账号，`skip_fresh_free_tier` 不覆盖，会附带一次 tier 探测（见 N-1，非本批回归，频次仍受 24h 周期约束）。

判定：**满足**（Free 分支），付费分支说明见 N-1。

---

## 4. 功能不刀核对（用户红线，清单 4）

- **手动刷新（tier/邮箱、可传 `refresh_email`）**：`routes_accounts.py:489-515` 未改动，`RefreshAccountsRequest.refresh_email: bool = True`（`:304`）语义不变。
- **手动 detect-email**：`routes_accounts.py:610-621` 与 `:670`、`:1326`、`:1606` 的 `probe_account_email` 调用点全部保留（文件零改动）。
- **导入/登录校验探测**：`routes_accounts.py:1284` `import_cookies`、`:1326` 邮箱探测、`:1373-1411` `probe_and_import`（`probe_google_accounts_infinite`）、`:1490` `import_bundle` 均保留；这些路径拿到的正是 B1 修复后的 Firefox 152 头部，能力增强而非削减。
- **删除的仅是启动期探测时机**：`app.py` 侧不再在 lifespan 里串行裸探测；探测能力由保活 `metadata_refresh` 周期承接（`:392-398` + `:512-523`），默认配置 `metadata_refresh: true` / `metadata_refresh_on_startup: true`（`defaults/config.yaml:141-143`）即启动后自动补齐。运行期 `run_account_keepalive_job` 等保活编排在 `app.py:504` 前后保持不变。

判定：**满足**；配置关闭 metadata_refresh 的边界见 Q-1。

---

## 5. 测试真实性（清单 5）

| 用例 | 断言是否有牙齿 | 判定 |
|---|---|---|
| `test_probe_headers.py:89-93` 常量一致性 | 四常量同值断言，可捕获任何一处漂移 | 有牙 |
| `test_probe_headers.py:96-107` emails_map 头部 | 遍历**所有**记录请求，断言 Firefox UA + accept-language 且 `"Chrome/" not in UA` | 有牙（旧 Chrome 133 必红） |
| `test_probe_headers.py:110-121` benefit_tier 头部 | 仅 1 次 POST，断言 UA/AL 并显式排除 `python-httpx` | 有牙（旧默认 UA 必红） |
| `test_probe_headers.py:124-140` accounts_infinite 头部 | 3 次上游调用（ListModels/BenefitTier/下一账号 401）逐一复核头部 | 有牙 |
| `test_probe_headers.py:159-172` `_fetch_app_html` | 断言实际下发的 `user-agent`/`accept-language`（B2 现状特征化测试） | 有牙（守护回归，不新增生产改动） |
| `test_account_keepalive.py:777-825` 缺邮箱补齐 | 1h 新鲜 Free + 空邮箱，断言 `accounts == [id]`、`email` 已回填、`posts == 0`、`gets == 1` | 有牙，**红绿必要性成立**：去掉 `not account.email` 判据后该账号不 due，`reason=no_due_accounts`、`accounts` 为空，断言必红 |
| `test_app_startup_warmup.py:158-253`（迁移） | 断言 keepalive 仍探测 1 次（`posts == 1`）、snapshot 零 `probe_*` 调用、五项统计精确相等、INFO 文案命中 | 新语义强于旧语义（旧：covered 跳过/uncovered 探测；新：启动零裸探测 + 缓存快照 + 明确承接） |
| `test_tier_probe_safety.py:398-425`（迁移） | 断言两个 probe 的 `await_count == 0`、账号数 1、原 tier 保持 `Pro` | 语义对齐（从「探测失败不改 tier」变为「不再探测，tier 不可能被改写」），作为防回退护栏有效；见 N-2 |

- 新增用例数核对：`test_probe_headers.py` 恰 5 例；keepalive 新增恰 1 例；迁移恰 2 例，与 Worker 声称一致。
- 显式重跑：`uv run pytest -q tests/unit/test_probe_headers.py` → `5 passed in 0.54s`；`...::test_metadata_refresh_backfills_missing_email_when_tier_is_fresh` → `1 passed in 0.58s`。

判定：**测试真实、无伪造、无弱化到失效**；唯一弱化点是旧「探测失败保留 tier」用例的失败分支随功能删除而消失（N-2）。

---

## 6. 门禁独立实跑（清单 6，真实输出）

| 门禁 | 命令 | 实测结果 |
|---|---|---|
| 依赖同步 | `uv sync --extra dev` | exit 0（Audited 47 packages） |
| Ruff Lint | `uv run ruff check .` | `All checks passed!`（exit 0） |
| Pyright | `uv run pyright src tests` | `0 errors, 0 warnings, 0 informations`（exit 0） |
| 全量 pytest（UTF-8） | `uv run pytest -q` | **`1747 passed in 49.52s`**（exit 0，与声称完全一致） |
| 受影响测试集 | `uv run pytest -q tests/unit/test_probe_headers.py tests/unit/test_app_startup_warmup.py tests/unit/test_tier_probe_safety.py tests/unit/test_account_keepalive.py` | `62 passed in 2.03s` |
| diff 空白 | `git diff --check` | exit 0 |
| **格式检查** | `uv run ruff format --check .` | **未通过**：`63 files would be reformatted`（其中 62 个为基线既有，见 N-3；批次新增 `tests/unit/test_probe_headers.py` 属本批引入，详见 P1-1） |
| 改动文件格式定位 | `uv run ruff format --check <8 个改动/新增文件>` | 仅 `tests/unit/test_probe_headers.py` 与 `tests/unit/test_app_startup_warmup.py` 未格式化；后者用 `git show HEAD:<file>` 以 stdin 复核**基线即未格式化**（exit 1），非本批引入 |

> 除格式检查外门禁全绿，且全量 1747 与 Worker 声称一致。

---

## 7. 隐私扫描（清单 7）

- 对完整 `git diff` 与新增测试文件，按任务指定模式（本机盘符绝对路径前缀、两个私网网段字面量、真实邮箱域名）逐项扫描：**0 命中**（为避免二次落盘敏感样本，本报告不逐字复写模式）。
- 本报告全部使用仓库相对路径，未写入本机绝对路径；新增测试样例邮箱使用保留示例域，不含真实邮箱域名。

---

## 8. 问题清单

### P0

无。

### P1

- **P1-1 批次新增测试文件未通过格式门禁**
  `tests/unit/test_probe_headers.py` 被 `uv run ruff format --check` 判定需重排（`:31-33` 字符串折行、`:67-69`、`:96-98`、`:119-123` 等）。这是 AGENT.md §3.4 明确要求的门禁项，且属本批新引入（基线无此文件）；功能零影响，但按「门禁全绿才提交」口径应在提交前执行 `uv run ruff format tests/unit/test_probe_headers.py` 并复核。仓库整体 `ruff format --check .` 在基线即为红（62 个既有文件含 docs 代码块），因此本项只针对批次自身新文件判定。

### P2

- **P2-1 启动装配处注释已失真**
  `app.py:502-503` 仍写「App owns the first metadata refresh below so the two startup probes can coordinate: keepalive reports covered accounts and app-side skips them」，但该协调机制已被整段删除：现在 app 侧只读缓存快照，不存在第二个探测、也不消费 `covered_ids`。建议替换为「启动期只读快照，探测全部由 keepalive 承接」，避免后续误读。
- **P2-2 `tier_task` 命名残留**
  `app.py:510/523/546` 的任务与函数已不含任何 tier 探测，仍名 `tier_task` / `_startup_metadata_and_tiers`。功能无影响，但语义误导；建议一并改为 `metadata_task` / `_startup_metadata_refresh`（Worker 已自报，属可自裁小改，宜当轮清掉）。

### Q（需决策/实机确认）

- **Q-1 `metadata_refresh=false` 时不再有启动期自动邮箱回填**
  旧 `_refresh_startup_account_metadata` 无条件执行；新实现仅在 `settings.metadata_refresh and settings.metadata_refresh_on_startup`（`app.py:515`）时触发，且 `keepalive.start(startup_metadata_done=True)` 把下轮周期排到 +24h（`account_keepalive.py:251-256`）。默认配置两项均为 true，故默认行为不回退；但显式关闭 metadata_refresh 的用户将失去任何自动回填（手动 detect-email / 批量刷新仍在）。请确认这是期望语义（「关闭保活探测即完全不打探测」自洽），还是需要在关闭时保留一次性启动回填。
- **Q-2 启动首轮保活刷新可能尚未拿到池 session**
  保活 metadata 刷新与 `_startup_instances` 并行启动（`app.py:506-523`）；若刷新先于池 READY 命中 `_resolve_account_session` 为空，会落到 `_post_tier_via_http` 裸 httpx 并打 `region-restricted` WARNING（`account_info_refresher.py:871-878`）。该竞态为基线既有，但意味着「启动首轮一定走 signer」不成立；如需硬保证，应让启动 metadata 刷新串行等待池 READY（或显式跳过本轮、留待下个周期）。
- **Q-3 `_fetch_app_html` TLS 指纹仍为 Python httpx**
  B2 仅留档未改（`account_info_refresher.py:930-935` docstring 已如实声明）。属已知残留，需在后续批次以 signer GET 页面通道替换；本批不作为缺陷。

### N（说明/非阻塞）

- **N-1 缺邮箱 + 付费新鲜 tier 会附带一次 tier 探测**
  due 过滤按「缺邮箱」纳入后，`skip_fresh_free_tier` 只覆盖已验证 Free（`account_info_refresher.py:522`），付费账号即使 1 小时前刚校验过仍会 POST benefit-tier（`:548`）。频次仍受 `metadata_refresh_seconds`（24h）约束，且基线启动探测同样只跳过 Free，故非回归；但「补齐邮箱不引入重复 tier 探测」的表述仅对 Free 成立，建议域看板如实注明。
- **N-2 旧失败分支用例的语义随功能删除而消失**
  `test_tier_probe_safety` 迁移后的用例无法再覆盖「探测失败不改 tier」的运行时分支（该分支已不存在）；「不伪造等级」不变量现由 `BenefitTierEnvelope.strict_tier` 与保活失败分支的既有用例各自覆盖，回归面未实质缩小。
- **N-3 仓库整体格式门禁基线即红**
  `uv run ruff format --check .` 在 62 个基线既有文件上失败（含 `docs/dev/**` 内 Python 代码块与多处源码/测试），本次改动前即如此；批次只新增 1 个（P1-1）。若坚持「门禁全绿」口径，需另立仓库级格式化批次。
- **N-4 `_startup_account_metadata_snapshot` 返回值被丢弃**
  `app.py:513` 忽略返回值；若仅作日志可返回 `None`，若干脆留作可测契约则应在 docstring 注明用途与调用方。
- **N-5 快照日志反映刷新前状态**
  snapshot 在 `run_startup_metadata_refresh` 之前执行（`app.py:513-516`），INFO 的「待补齐邮箱」可能在同一秒后被保活补齐，属快照语义而非错误，日志阅读时需知悉。
- **N-6 缺邮箱账号在 tier 失败退避期间不尝试回填邮箱**
  `account_info_refresher.py:508-521` 的 `respect_backoff` 早退发生在邮箱分支之前，退避窗口（300s 起、封顶 6h）内既不探测 tier 也不回填邮箱；仅延迟、不丢数据。

---

## 9. Worker 四条偏离逐条裁决

1. **B2 未改代码（UA 已达标，仅 docstring 留档）** —— **接受**。实测 `account_info_refresher.py:60-63` 已是同一 Firefox 152 UA/accept-language，`_fetch_app_html`（`:936-946`）未含 Chrome/默认 UA 残留；docstring（`:930-935`）如实标注 TLS 指纹残留并划出范围。新增 `test_probe_headers.py:159-172` 作为特征化护栏合理。
2. **保留保活首轮刷新（不删除 `run_startup_metadata_refresh`）** —— **接受**。它是「探测能力由保活周期承接」的唯一落点；`keepalive.start(startup_metadata_done=True)`（`app.py:504`）已把常规首轮排到 +24h，启动首轮必须显式触发，否则缺邮箱账号要等满 24h。
3. **调度最小补齐（due 增加 `not account.email`）** —— **接受**。最小必要：不改 refresher 的 `wants_email` 契约、不新增探测函数，仅让缺邮箱账号进入既有批次；红绿必要性已独立复核成立（§5）。
4. **`tier_task` 命名残留未改名** —— **有条件接受**。无功能风险、关闭顺序正确（`app.py:546` 仍在 tasks 元组内）；但属可自裁的语义清理，按「可裁决当轮清零」惯例建议提交前顺带改为 `metadata_task` 并同步函数名（P2-2）。

---

## 10. 可裁决清零判定

- **未清零**：存在可自行裁决却未处理的项——**P1-1（新文件格式）**、**P2-1（失真注释）**、**P2-2（命名残留）**。
- **非单方可裁**：Q-1（关闭 metadata_refresh 的语义取舍）需用户/主控确认；Q-2（启动首轮与池就绪竞态）需决定是否加串行等待并实机验证；Q-3 已明确划出本批范围。
- 建议：提交前至少修掉 P1-1 与 P2-1/P2-2（均为分钟级、零行为变更），即可达成「可裁决清零」；Q-1 给出结论后可再降为文档口径。

---

## 11. 结论

**CONDITIONAL PASS**

- **B1**：三处裸探测配方头部确已统一为 Firefox 152 + `accept-language`，且为纯头部修复（URL/请求体/时序/返回值零变更）；`probe_account_benefit_tier` 不再可能以 `python-httpx` UA 发出；全站一致性断言真实存在。
- **B2**：`_fetch_app_html` UA 已达标，未改代码的核查结论为真；TLS 指纹残留已如实留档并划出范围。
- **B3**：启动路径零裸探测（只读快照 + INFO），旧常量/函数/datetime 导入零残留；保活首轮经 AccountInfoRefresher 的 signer 感知传输层发出；缺邮箱调度补齐语义正确（Free 分支不重复打 tier）。
- **功能不刀**：手动 detect-email、导入/登录校验探测、手动 `refresh_email` 路径全部保留，删除的仅为启动期探测时机。
- **门禁**：ruff / pyright / 全量 pytest **1747 passed** / `git diff --check` 全绿；`ruff format --check` 未通过，其中批次新文件 1 个（P1-1）需提交前修复，其余为基线既有（N-3）。
- **放行条件**：
  1. 提交前格式化 `tests/unit/test_probe_headers.py`（P1-1）。
  2. 清理 `app.py:502-503` 失真注释与 `tier_task` 命名（P2-1/P2-2），以满足可裁决当轮清零口径。
- **需确认（非阻塞）**：Q-1（关闭 metadata_refresh 是否接受「无自动回填」）、Q-2（启动首轮是否必须等待池 READY 再刷新）。

---

### 附：只读再次声明

本 Reviewer 全程只读；唯一写入为本报告文件。未 ssh、未联网、未启动真实服务/浏览器、未访问真实 Google 账号。

---

## 12. 二轮复审（返工核验）

- 复审对象：一轮三项可自裁项（P1-1 新文件格式、P2-1 失真注释、P2-2 命名残留）的 Worker 返工。
- 复审基线：同一未提交工作树（`5796ee2` 之后）；`git status` 仍为 7 个 tracked 改动 + 2 个 untracked（本报告与 `tests/unit/test_probe_headers.py`），未新增第三处改动面。
- 改动差分证据：`git diff --stat` 与一轮逐文件比对——`account_keepalive.py`(6) / `account_info_refresher.py`(6) / `cookie_parser.py`(7) / `test_account_keepalive.py`(49+) / `test_app_startup_warmup.py`(23) / `test_tier_probe_safety.py`(22) 的增删行数**完全未变**；仅 `app.py` 由 159 行变更面变为 179 行（注释改写 + 原子改名 + shutdown 元组换行），说明格式化**只作用于新增测试文件**。

### 12.1 三项闭环状态（逐项 file:line）

| 项 | 状态 | 核验证据 |
|---|---|---|
| **P1-1 新测试文件格式化** | **已闭环** | `uv run ruff format --check tests/unit/test_probe_headers.py` → `1 file already formatted`（exit 0）；文件由 172 行重排为 169 行，全部为折行/拼接调整 |
| **P2-1 失真注释** | **已闭环** | `app.py:502-503` 改写为「启动期只读缓存快照；邮箱/等级探测全部由下方的保活 metadata_refresh 周期承接（首轮显式触发，之后按 24h 周期补齐）」；与 `app.py:504`（`startup_metadata_done=True`，常规首轮排 +24h）、`app.py:513-516`（仅当 `metadata_refresh && metadata_refresh_on_startup` 时显式触发首轮）、`_startup_account_metadata_snapshot` 只读实现一致 |
| **P2-2 命名残留** | **已闭环** | `tier_task`→`startup_metadata_task`（`app.py:510/523/550`）、`_startup_metadata_and_tiers`→`_startup_metadata_refresh`（`app.py:512/523`）；`src` + `tests` 内旧名 **0 命中**（`Select-String` 无输出），无 `tier_task = startup_metadata_task` 之类别名/Shim；shutdown 元组 `app.py:543-552` 已同步为 `(warmup_task, startup_task, startup_metadata_task, model_warmup_task)` |

### 12.2 格式化后断言语义逐例比对（5 例零弱化）

| 用例 | 重排前 | 重排后 | 语义 |
|---|---|---|---|
| `test_shared_identity_constants_are_firefox_152` | 4 条常量断言 | 同 4 条，逐字一致 | 零变更 |
| `test_probe_account_emails_map_uses_firefox_identity` | 遍历全部 calls + UA/AL + `"Chrome/" not in UA` | 同断言，仅调用折为单行 | 零变更 |
| `test_probe_account_benefit_tier_sends_firefox_identity` | 1 次 POST + UA/AL + 排除 `python-httpx` | 逐字一致 | 零变更 |
| `test_probe_google_accounts_infinite_sends_firefox_identity` | 3 状态序列 + 遍历头部 | 仅函数签名折行，断言逐字一致 | 零变更 |
| `test_fetch_app_html_uses_firefox_identity` | 断言 `user-agent`/`accept-language` 与次数 | 逐字一致 | 零变更 |

- 辅助件 `_FIREFOX_152` 由两段字符串拼接改为单行字面量，**字符串值完全相同**；`_RecordingClient` 仅 `calls.append` 折行，行为不变。
- 5 例显式重跑：`uv run pytest -q tests/unit/test_probe_headers.py` → `5 passed in 0.14s`。

判定：**格式化仅限该文件，断言零弱化**。

### 12.3 门禁独立实跑（真实输出）

| 门禁 | 命令 | 实测结果 |
|---|---|---|
| Ruff Lint | `uv run ruff check .` | `All checks passed!`（exit 0） |
| 新文件格式 | `uv run ruff format --check tests/unit/test_probe_headers.py` | `1 file already formatted`（exit 0） |
| Pyright | `uv run pyright src tests` | `0 errors, 0 warnings, 0 informations`（exit 0） |
| 全量 pytest（UTF-8） | `uv run pytest -q` | **`1747 passed in 48.89s`**（exit 0，与声称一致） |
| 新文件定向重跑 | `uv run pytest -q tests/unit/test_probe_headers.py` | `5 passed in 0.14s` |
| diff 空白 | `git diff --check` | exit 0 |
| 旧名残留 | `src` + `tests` 扫描 `tier_task` / `_startup_metadata_and_tiers` | 0 命中；别名/Shim 0 命中 |

> 门禁全部通过，且全量 1747 与 Worker 声称一致。

### 12.4 可裁决项清零判定

- **P1-1 / P2-1 / P2-2 三项均已真实闭环**，无半改、无别名兼容层、无行为漂移；shutdown 取消顺序与任务集合保持等价。
- 其余 Q-1 / Q-2 / Q-3 与 N-1..N-6 归类为**用户或实机决策/说明项**，不计入可裁决遗留（与一轮归类一致）。
- **可裁决遗留：无**。

### 12.5 二轮新增观察（非阻塞，不影响结论）

- **N-7** 注释「首轮显式触发」未点明其受 `metadata_refresh && metadata_refresh_on_startup` 双开关约束（`app.py:515`）；默认配置下语义准确，关闭开关时的行为已由 Q-1 覆盖。
- **N-8** 历史文档仍引用旧名/旧行号：`docs/dev/rebuild/domains/P6-keepalive/reviews/REV-B3-p6-keepalive.md:45`、`docs/dev/rebuild/domains/docs-and-audit/reviews/REV-C5a.md:81`、`docs/KEEPALIVE_AND_LOGGING_SPEC.md:149`。属历史留档/规格文档的既有时序漂移，本批按禁令不得触碰 `docs/dev/**`，故延后归类，不构成本批残留。
- **N-9** 一轮本报告正文中仍保留 `tier_task` / `_startup_metadata_and_tiers` 的旧名叙述（§3a/§8/§9 历史记录），属审查过程留痕，不回改。

### 12.6 最终结论

**PASS**

- B1 / B2 / B3 核心交付成立且未被返工破坏；三项可自裁项已当轮闭环（格式化仅限新文件、断言零弱化、注释与真实行为一致、改名无残留且 shutdown 元组同步）。
- 全量门禁独立实跑全绿：`ruff check .` / 新文件 `ruff format --check` / `pyright` 0 errors / `pytest -q` **1747 passed** / `git diff --check` exit 0。
- 无新的 P0/P1/P2；剩余项仅 Q-1/Q-2/Q-3 与 N-1..N-9 的说明/延后台账。

### 12.7 只读声明

本轮复审除在本报告文末追加本章节外，**未修改任何文件**；未 ssh、未联网、未启动真实服务/浏览器、未访问真实 Google 账号。全部门禁为本机离线实跑。
