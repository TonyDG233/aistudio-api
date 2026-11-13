# REV-2.1.3.1-session-expired-sibling-sync — 无浏览器 502 登录态终态 / 同源兄弟整组隔离 / toolshape 日志降级 独立审查报告

## 0. 审查基线与只读声明

- 审查对象：当前工作区全部未提交改动（`src/` 5 个文件 + `tests/unit/` 5 个文件）。
- `HEAD`：`ba07c863cadefeb73d5ea122cb8ad176ad0e24d0`（`ba07c86 feat(routing,errors): 新增 verify-account 移动设备验证风控自动熔断隔离并跳过同号重试`）。
- 当前索引：`git diff --cached` 为空（无 staged 改动）；`git status --short` 全部为 ` M`（10 个已跟踪文件的未暂存修改）。
- 变更规模：10 files changed, 653 insertions(+), 18 deletions(-)。
- 只读声明：本次审查仅在 `docs/changelog.md` 增补修复条目，并将本报告写入 `docs/dev/rebuild/domains/P3-pool-instance/reviews/REV-2.1.3.1-session-expired-sibling-sync.md`（原命名 `REV-2.1.3.md`，已按四位审查标号规范收口重命名）；**未修改任何业务代码、配置或测试**；未执行 `git add/commit/push`；未启动真实服务、真实浏览器、真实账号或外网请求，全部为离线命令与离线测试。
- 审查结论：**PASS（0 个未关闭 P0/P1；4 项 P2 为非阻塞建议；Q/N 项仅作记录）**。

---

## 1. 变更文件清单（`git diff --name-only`）

| # | 文件 | 域 | 变更要点 |
|---|---|---|---|
| 1 | `src/aistudio_api/infrastructure/waa/browserless_session.py` | 基础设施/WAA | 502 登录态映射、`_reload_if_changed`、`_mark_root_session_expired`、兄弟文件同步、刷盘后清 pending |
| 2 | `src/aistudio_api/infrastructure/account/account_store.py` | 基础设施/账号 | `_apply_rotating_cookie_objects`、`sync_sibling_storage_state_files`、`get_sibling_account_ids`、`mark_session_expired_group` |
| 3 | `src/aistudio_api/application/account_rotator.py` | 应用层 | 暴露 `account_store`、`get_account_stats` 镜像 store 过期态、`record_auth_error` 过期提升为终态 |
| 4 | `src/aistudio_api/application/account_orchestrator.py` | 应用层 | `session_expired` 事件向同源兄弟账号传播 |
| 5 | `src/aistudio_api/application/chat_service.py` | 应用层 | `_emit_toolshape()` 由 `logger.info` → `logger.debug`（单行） |
| 6 | `tests/unit/test_browserless_session.py` | 测试 | 502 映射、兄弟文件同步 + mtime 热重载 |
| 7 | `tests/unit/test_browserless_auth_rebind.py` | 测试 | 502 终态、刷新语义、同锚点整组隔离 + 单次换绑 |
| 8 | `tests/unit/test_toolshape_and_signature.py` | 测试 | INFO 不再输出 `[toolshape]`、DEBUG 仍脱敏 |
| 9 | `tests/unit/test_gemini_request_normalization.py` | 测试 | 同上（PG-04 断言分级） |
| 10 | `tests/unit/test_pg_toolcall_harness.py` | 测试 | 同上（PG-04 断言分级） |

确认 `src/aistudio_api/application/generation_service.py` **未被触碰**（不在 `git diff --name-only` 中）。

---

## 2. 逐项核查

### 2.1 `_map_signer_auth_error` 502 登录态终态映射

| 核查点 | 结论 | 证据 |
|---|---|---|
| 401 仍为终态 | **符合** | `browserless_session.py:102-106`：`exc.status_code == 401 or any(marker in lowered ...)` → `SessionExpiredError`。 |
| 502 + `隔离登录态失效` 为终态 | **符合** | 标记表 `browserless_session.py:77-84`；判断 `:101-106`；测试 `test_browserless_session.py:670-681`（断言 `isinstance(mapped, SessionExpiredError)` 且消息含 `隔离登录态失效`）。 |
| 502 + `登录态失效` / `servicelogin` / `url=https://accounts.google.com` | **符合** | `:78-83`；大小写归一 `:101`，覆盖 Go `camoufoxnative.ErrAuthenticationRequired`（`waa-signer/internal/camoufoxnative/types.go:12`）与 `runtime_go.go:444` 拼接的 `url=https://accounts.google.com/...`。 |
| 403/WAA 拒绝保留同号重签 | **基本符合（见 P2-02）** | `:102-107` 仅在 reason 命中标记时才升为 `SessionExpiredError`；Go 签名器自身仅返回 400/502（`waa-signer/cmd/waa-signer/main.go:1042,1065,1072-1082`），实际 403 只会来自被代理上游且走 `RpcResult`，故运行期风险低，但标记匹配未按状态收敛，与 docstring “403 keeps the same-account re-sign path” 存在边界不一致。 |
| 路由语义闭环 | **符合** | `SessionExpiredError` → `routing_pipeline.py:761-768` 直接 `_switch_or_raise`，不进入 `_refresh_auth_state` 的 403 重签（`:799-819`）；测试 `test_browserless_auth_rebind.py:529-560` 已由“刷新 2 次 mint”改为“失败即换绑 1 次、mint 1 次”。 |

### 2.2 `refresh_auth_state()` 重新 mint 命中 SessionExpiredError

| 核查点 | 结论 | 证据 |
|---|---|---|
| 置 `_session_expired=True` | **符合** | `_raise_signer_auth_error`（`:443-447`）在映射为 `SessionExpiredError` 时调用 `_mark_root_session_expired`（`:449-451`）；`refresh_auth_state` 的 `except SignerAuthError` 分支（`:639-651`）记录 error 日志并 `return False`。 |
| 调用 `mark_session_expired_group` 标记同源兄弟 | **符合** | `:449-467`：`_session_expired=True` → 依据 `auth_file.resolve().parent.parent` 定位 accounts 目录 → 校验 `registry.json` → `AccountStore(accounts_dir).mark_session_expired_group(self._account_id)`；`account_store.py:759-766` 展开 `[account_id, *get_sibling_account_ids()]` 并逐个 `update_account_refresh(expired=True)`。 |
| 成功路径清除失效标记 | **符合** | 正常 mint `:581` 与刷新成功 `:657` 均 `_session_expired = False`。 |
| 测试覆盖 | **符合** | `test_browserless_auth_rebind.py:286-296` 断言 `refresh_auth_state() is False` 且 `session.session_expired is True`。 |

### 2.3 `save_current_cookies()` 兄弟同步与 `_reload_if_changed()`

| 核查点 | 结论 | 证据 |
|---|---|---|
| 落盘后同步同锚点兄弟 `auth.json` | **符合** | `browserless_session.py:797-798` 在 `write_storage_state` 成功后调用 `sync_sibling_storage_state_files(self._auth_file, self._cookies)`；实现 `account_store.py:176-234`：由 `<accounts_dir>/<id>/auth.json` 反推目录、`compute_session_anchor` 比对锚点、仅同步 `ROTATING_SESSION_COOKIE_NAMES`（`cookie_parser.py:143-149`）的整条 Cookie 记录（值 + 过期元数据），`_apply_rotating_cookie_objects`（`:129-173`）在 `_AUTH_FILE_WRITE_LOCK` 内原子替换；非滚动身份 Cookie 保持各账号独立。 |
| 文件 `st_mtime_ns` 变更自动重载 | **符合** | `_auth_file_mtime_ns`（`:409-415`）、`_load_storage_state` 记录基线（`:379`）、`_reload_if_changed`（`:417-441`）比较 mtime 后重载并把本会话 `_mint_updates` / `_rpc_set_cookies` 叠加到新快照之上；调用点 `ensure_ready:535`、`generate_snapshot:552`（每条请求路径均先重载，包含 `send_hooked_request`/`send_streaming_request` 经 `ensure_ready`）。 |
| 刷盘后基线更新与 pending 清空 | **符合** | `:799-802`：更新 `_last_loaded_mtime_ns` 并清空 `_mint_updates` / `_rpc_set_cookies` / `_cookie_dirty`，避免重复写入。 |
| 测试覆盖 | **符合** | `test_browserless_session.py:716-763`：u0 收到 `__Secure-1PSIDTS=ts-new` / `SIDCC=sidcc-new` 后落盘，断言 u1 的 `auth.json` 已同步且 `SID` 保持 `sid-u1`；随后 bump u1 `st_mtime_ns`，断言 u1 下一次 mint 收到 `ts-new` / `sidcc-new`。 |

### 2.4 账号层与编排层整组隔离闭环

| 核查点 | 结论 | 证据 |
|---|---|---|
| 兄弟识别 | **符合** | `account_store.py:727-757`：registry 校验 + `_safe_account_dir` 防路径逃逸 + `compute_session_anchor`（SAPISID / PAPISID / SID 优先，`cookie_parser.py:162-173`）一致即视为兄弟，锚点为空不误配。 |
| 整组标记 | **符合** | `account_store.py:759-766`；`update_account_refresh`（`:1033-1078`）仅在有变化时原子写 `meta.json` + registry，`expired` 与 `enabled` 分离，不误改启用位。 |
| 编排层传播 | **符合** | `account_orchestrator.py:72-85`：`session_expired` 先记本体，再经 `_sibling_account_ids`（`:21-34`，容错 `getattr` + 异常告警 + 类型收窄）逐个 `record_session_expired(reason="sibling_session_expired(<id>)")`。 |
| 旋转态与统计一致 | **符合** | `account_rotator.py:616-618` 暴露 `account_store`；`:630-634` 将 store `expired` 镜像进 `AccountStats.session_expired`；`:968-976` 在 store 已过期时把 403 提升为终态 `record_session_expired`；准入侧 `:646,687,792,831` 均排除 `expired`。 |
| 恢复闭环 | **符合** | 重新导入走 `save_account` 重建 `AccountMeta`（`account_store.py:928-942`，`expired` 缺省 False）并 `clear_cooldown`（`routes_accounts.py:1302-1304`）；tier/邮箱刷新走 `_clear_account_failure_state`（`routes_accounts.py:196-205`，含 `update_account_refresh(expired=False)` 与 `rotator.clear_cooldown`），`clear_cooldown` 复位 `session_expired`（`account_rotator.py:402-409`）。 |
| 测试覆盖 | **符合** | `test_browserless_auth_rebind.py:748-777`：u0 失效后 1 次换绑切到独立账号，u1 未被 mint 尝试，u0/u1 rotator 统计均 `session_expired=True`，独立账号保持 False。 |

### 2.5 锁/线程安全与异常泄露

| 核查点 | 结论 | 证据 |
|---|---|---|
| 死锁风险 | **未发现** | 锁序恒为 store `_lock`（`RLock`，`account_store.py:491`）→ `_AUTH_FILE_WRITE_LOCK`（`:42`，由 `_update_auth_json_cookies:77`、`_apply_rotating_cookie_objects:136`、`write_storage_state:53` 获取）；`_apply_rotating_cookie_objects` 只持文件锁、不再申请 store 锁，`_atomic_write_json`（`:24-39`）不自持锁，无反向嵌套。 |
| 兄弟同步失败隔离 | **符合（见 P2-04）** | `sync_sibling_storage_state_files` 内每个兄弟文件 `try/except` 跳过损坏文件（`:226-229`），`_apply_rotating_cookie_objects` 异常降级为 `logger.warning`（`:171-173`）；调用侧 `with suppress(Exception)`（`browserless_session.py:797`）保证 cookie 落盘成功不被同步失败回滚。 |
| 标记失效异常隔离 | **符合（见 P2-04）** | `_mark_root_session_expired` 整体 `try/except`（`:454-467`），失败仅记录 `log.debug`，不影响抛出 `SessionExpiredError` 的主链路。 |
| 无明文敏感信息 | **符合** | 新增日志仅输出 realm / account_id / 异常文本；`auth.json` 同步不打印 Cookie 值。 |

### 2.6 `chat_service._emit_toolshape()` 日志降级

| 核查点 | 结论 | 证据 |
|---|---|---|
| 降级为 DEBUG | **符合** | `chat_service.py:782-785` 为 `logger.debug("[toolshape] %s", ...)`，diff 仅此一行（`- logger.info` / `+ logger.debug`）。 |
| 脱敏与摘要逻辑未变 | **符合** | `_toolshape_summary`（`:687-...`）与调用点 `:1104-1105` 未改动；测试 `test_toolshape_and_signature.py:48-52` 断言 INFO 级别无 `[toolshape]`，`:55-...` 在 DEBUG 下仍校验值脱敏。 |
| `generation_service.py` 未触碰 | **符合** | `git diff --name-only` 无该文件。 |

---

## 3. 门禁实跑结果（本机 Windows / PowerShell，离线）

| 门禁 | 命令 | 结果 |
|---|---|---|
| Ruff Lint | `uv run ruff check .` | **PASS**：`All checks passed!`（exit 0） |
| Ruff Format | `uv run ruff format --check <10 个指定文件>` | **PASS**：`10 files already formatted`（exit 0） |
| Pyright | `uv run pyright src tests` | **PASS**：`0 errors, 0 warnings, 0 informations`（exit 0） |
| 全量单测 | `$env:PYTHONUTF8='1'; uv run pytest -q` | **PASS**：`1475 passed in 53.70s` |
| 定向单测 | `uv run pytest -q tests/unit/test_browserless_session.py tests/unit/test_browserless_auth_rebind.py tests/unit/test_toolshape_and_signature.py tests/unit/test_gemini_request_normalization.py tests/unit/test_pg_toolcall_harness.py` | **PASS**：`70 passed in 1.18s` |
| Diff 空白检查 | `git diff --check` | **PASS**：无输出（exit 0） |
| 配置跟踪 | `git ls-files config.yaml` | **PASS**：空输出（根 `config.yaml` 仍为 untracked + gitignored） |

未启动真实服务 / 真实浏览器 / 外网请求；无平台差异失败。

---

## 4. 发现明细

### P0（阻断）
- 无。

### P1（阻断）
- 无。

### P2（非阻塞建议，均已确认不改变本次结论）
- **P2-01 兄弟同步无“新旧”比较（last-writer-wins）**（R2 已修复关闭）：`sync_sibling_storage_state_files`（`account_store.py:176-234`）只要值不同就覆盖兄弟文件，未比较 `__Secure-1PSIDTS` 的 `expires` 或 `last_rotated_at`；理论上较旧槽位保存时可能把较旧滚动 Cookie 回灌给较新槽位。缓解事实：每条请求路径入口都先 `_reload_if_changed()`（`browserless_session.py:535,552`），保存时内存罐已是最新磁盘快照叠加本次 Set-Cookie，实际回灌概率低。建议后续比较过期时间或仅在来源更新时覆盖。
- **P2-02 过期标记匹配未按状态收敛**（R2 已修复关闭）：`_EXPIRED_REASON_MARKERS` 匹配（`browserless_session.py:101-104`）对任意状态码生效，若 403 的 reason 文本含 `authentication required` / `session expired` 等标记，会跳过同号重签直接永久隔离，与 `_map_signer_auth_error` docstring“403 保留同号重签”不一致。实际签名器自身只发 400/502（`waa-signer/cmd/waa-signer/main.go:1042,1065,1072-1082`），风险低；建议将标记匹配限定在非 401/403，或同步修正 docstring。
- **P2-03 标记集合与签名器判定集合不完全对齐**（R2 已修复关闭）：`signer_client._AUTH_FAILURE_MARKERS`（`signer_client.py:178-190`）含 `缺少登录 cookie` / `需要重新登录` / `请重新登录`，而 `_EXPIRED_REASON_MARKERS`（`browserless_session.py:77-84`）未包含，502 冷 Worker 缺 SAPISID 仍会多走一次同号 `refresh_auth_state`（内部 `_has_sapisid` 为 False 后返回 False 再切号）。行为正确但有一次无谓 worker release；建议后续对齐两处标记表。
- **P2-04 事件循环内同步磁盘 IO 与过期标记日志级别**（R2 已修复关闭，日志级别部分）：`_mark_root_session_expired`（`browserless_session.py:449-467`）在请求协程内同步读取/写入 registry 与 meta，`sync_sibling_storage_state_files` 每次成功落盘会遍历 accounts 目录并逐个读取 `auth.json`（O(N)），大批账号时会短暂阻塞事件循环（无死锁）；且标记失败降级为 `log.debug`，与“非关键回退须显式记录”口径相比偏静默，建议提升为 `warning` 并评估 `asyncio.to_thread`。

### Q（待确认）
- **Q-01**：`[toolshape]` 降级为 DEBUG 后，线上排障需显式开启 `aistudio.application.chat_service` 的 DEBUG 才能看到 PG-04 摘要；是否需要在诊断文档/日志页说明该开关（当前仅靠代码与测试约定）。
- **Q-02**：同源兄弟被整组标记 `expired` 后，恢复依赖重新导入（`save_account` 重建 meta）或 tier/邮箱刷新；是否需要在管理台以“同组失效”提示引导用户一次性批量重新导入，避免逐个恢复。

### N（备注）
- **N-01**（R2 已修复关闭）：`get_sibling_account_ids`（`account_store.py:740-757`）在 `_load_registry` 返回后于锁外迭代 `registry.accounts`，与并发增删账号（线程池端点）理论上存在 `dictionary changed size during iteration` 竞态；建议改为 `for acc_id in list(registry.accounts)`。本次测试与现有调用路径未触发。
- **N-02**：`_reload_if_changed` 在“删除类 tombstone 尚未落盘”的窗口内重载会从磁盘读回被删 Cookie，但 `_load_storage_state` 会过滤已过期条目（`:404-407`），Google 的删除型 Set-Cookie 通常带过期时间，实际不会复活，记录备查。
- **N-03**：`save_current_cookies` 现同时清空 `_mint_updates`/`_rpc_set_cookies`（`:800-801`），与 `refresh_auth_state`（`:614-615`）的既有清理语义一致，未引入重复写盘。
- **N-04**：`_mark_root_session_expired` 使用函数内延迟 import `AccountStore`（`:458`）避免模块循环依赖，与仓库既有局部 import 风格一致。

---

## 5. 结论

**PASS**：本次未提交改动满足任务要求——502「隔离登录态失效」类 `SignerAuthError` 已映射为终态 `SessionExpiredError`；`refresh_auth_state()` 命中终态会置 `_session_expired` 并整组标记同源兄弟；`save_current_cookies()` 已同步兄弟滚动凭据且 `_reload_if_changed()` 按 `st_mtime_ns` 自动重载；`_emit_toolshape()` 已降级为 DEBUG 且 `generation_service.py` 未被触碰。Ruff / Ruff Format / Pyright / 全量 1475 项 pytest / 定向 70 项 pytest / `git diff --check` / `git ls-files config.yaml` 全部通过；P0/P1 为 0，P2-01..04 为后续迭代建议，Q-01..02 与 N-01..04 仅作记录，不阻塞提交。

---

## 6. R2 复审（P2-01..04 / N-01 收口 + `generation_service.py` 思维链截断告警）

### 6.1 R2 基线与只读声明

- 复审对象：在 R1 审查后新增的收口改动，`HEAD` 仍为 `ba07c863cadefeb73d5ea122cb8ad176ad0e24d0`。
- 变更规模：`git diff --stat` = **13 files changed, 888 insertions(+), 26 deletions(-)**；较 R1 新增 `src/aistudio_api/application/generation_service.py`（71 行变更）与 `tests/unit/test_generation_routing.py`（+77），`docs/changelog.md` 增补 8 条收口说明。
- 索引状态：`git diff --cached` 为空；`docs/dev/rebuild/domains/P3-pool-instance/reviews/REV-2.1.3.1-session-expired-sibling-sync.md`（原名 `REV-2.1.3.md`）在 R2 时为 untracked（`??`）。
- 只读声明：R2 仅更新本报告，**未修改任何业务代码、配置或测试**；未执行 `git add/commit/push`；未启动真实服务/真实浏览器/真实账号/外网请求。

### 6.2 P2 / N 收口逐项核查

| 项 | 修复要点 | 证据 | 结论 |
|---|---|---|---|
| **P2-01** 兄弟同步 last-writer-wins | 新增数字过期时间比较，来源更旧则不覆盖 | `account_store.py:129-135` `_numeric_cookie_expiry`（bool/非数字/≤0 返回 `None`）；`:138-151` `_is_stale_rotating_update`（双方均有 `>0` 数字 `expires` 且 `source < existing` 时判旧）；`:177-178` 在整条 Cookie 覆盖前 `continue` 跳过；无过期元数据（`-1`）仍保持 last-writer-wins；测试 `test_browserless_session.py:788-818`（u1 持有 `expires=2000` 的 `ts-newer`，u0 以 `expires=1000` 的 `ts-older` 落盘后 u1 仍为 `ts-newer`） | **已修复关闭** |
| **P2-02** 标记匹配未按状态收敛 | 采纳“同步修正 docstring”方案，行为与文档对齐 | `browserless_session.py:90-100`：明确“401 或 reason 命中明确登录态失效/登录页重定向标记 → 终态 `SessionExpiredError`；其余 403/502 鉴权拒绝保留同号重签 `AuthError`”；实现 `:105-111` 与文档一致。未做状态码收敛，属已文档化的接受态（见 N-05） | **已修复关闭（文档对齐）** |
| **P2-03** 标记集合不对齐 | 补齐签名器已有 3 个中文登录态标记 | `browserless_session.py:77-87` 现含 `缺少登录 cookie` / `需要重新登录` / `请重新登录`，与 `signer_client.py:178-190` 中同义标记对齐；`lowered` 归一后匹配 (`:105-107`)；`test_browserless_auth_rebind.py` 的 502 冷 Worker 用例释放次数由 `[_OLD, _OLD]` 收敛为 `[_OLD]`（不再多走一次同号 refresh） | **已修复关闭** |
| **P2-04** 过期标记日志级别偏静默 | `_mark_root_session_expired` 异常日志提升为 `warning` | `browserless_session.py:453-471`：`except Exception as exc` → `log.warning("browserless 标记同源会话失效失败 realm=%s: %s", ...)`，符合“非关键回退须显式记录”；同步磁盘 IO / `asyncio.to_thread` 性能项未实施，转入 N-05 | **已修复关闭（日志级别部分；性能项转 N-05）** |
| **N-01** 锁外迭代字典 | 迭代前取快照 | `account_store.py:773` `for acc_id in list(registry.accounts):`，消除并发增删时的 `dictionary changed size during iteration` 竞态 | **已修复关闭** |

### 6.3 `generation_service.py` 思维链截断告警核查

| 核查点 | 结论 | 证据 |
|---|---|---|
| 非流式：仅 `thinking` 无正文/工具/图片 | **符合** | `_is_thinking_only`（`generation_service.py:83-95`）：`bool(candidate.thinking)` 且无 `text/function_calls/images/reasoning_images`；调用点 `:341-352` 输出 `logger.warning("[generation] 思维链截断告警: 模型仅返回 thinking 未产出正文或工具调用 (已跳过重试以保护配额): format=… model=… instance=… finish_reason=… thinking_len=…")`。 |
| 非流式：绝不触发空响应重试 | **符合** | 空响应判据 `_output_is_empty`（`:69-80`）包含 `candidate.thinking`，thinking-only 不算空 → `:310-311` 直接 `break`，不进入 `:312-322` 的同账号空响应重试；告警位于 `break` 之后、`record_success` 之前，**每次成功尝试恰好 1 次**。 |
| 流式：事件分类计数 | **符合** | `:474-476` 初始化 `produced_answer=False` / `thinking_chars=0`；`:522-534` 仅在 `text` 非空时累计：`thinking` 累加字符数，`body/tool_calls/images/reasoning_images` 置 `produced_answer=True`；`produced_content` 已在 `:523` 对 thinking 置真。 |
| 流式：绝不触发空流重试 | **符合** | 空流重试条件 `not produced_content`（`:573`）在 thinking-only 时为 False，不重试、不切号；告警 `:596-606` 位于重试与 `SyntheticRequiredError` 判定之后、`unwrapper.finalize()` 之前，**每次干净完成的尝试恰好 1 次**。 |
| 调用次数严格为 1 的测试坐实 | **符合** | `test_generation_routing.py:268-297`：`generate_content.await_count == 1`、`pipeline.empty_retries == 0`、`caplog` 中 `思维链截断告警` 仅 1 条且 `levelno == WARNING`、含 `thinking_len=14`；`:299-336` 流式：`calls == 1`、`empty_retries == 0`、事件中无 `body/tool_calls/images`、告警仅 1 条、末尾仍产出 `usage` 事件。 |
| 字段与模型输出一致性 | **符合** | 非流式用 `attempt.instance_id`（`routing_pipeline.py:244-245` 属性），流式用 `attempt.candidate.instance_id`（`routing_pipeline.py:238`），两者同源且 `RouteAttempt` 非空；`finish_reason` 分别取 `_finish_reason(output, normalized)` 与 `final_finish_reason`。 |

### 6.4 R2 门禁实跑结果（本机 Windows / PowerShell，离线）

| 门禁 | 命令 | 结果 |
|---|---|---|
| Ruff Lint | `uv run ruff check .` | **PASS**：`All checks passed!`（exit 0） |
| Pyright | `uv run pyright src tests` | **PASS**：`0 errors, 0 warnings, 0 informations`（exit 0） |
| 全量单测 | `$env:PYTHONUTF8='1'; uv run pytest -q` | **PASS**：`1482 passed in 54.64s`（R1 为 1475，新增 7 项） |
| Diff 空白检查 | `git diff --check` | **PASS**：无输出（exit 0） |
| 配置跟踪 | `git ls-files config.yaml` | **PASS**：空输出 |

未启动真实服务 / 真实浏览器 / 外网请求；无平台差异失败。

### 6.5 R2 残余记录（不阻塞，仅备查）

- **N-05**：P2-04 的 `_mark_root_session_expired` / `sync_sibling_storage_state_files` 仍在请求协程内做同步磁盘 IO（批量账号 O(N) 读 `auth.json`），`asyncio.to_thread` 未实施；P2-02 采用 docstring 对齐而非状态码收敛，403 若携带登录态失效文本仍会直接终态隔离（与文档一致，风险已接受）。
- **N-06**：`_apply_rotating_cookie_objects` 的 dict 形态 `cookies` 旧分支（`account_store.py:189-196`）仍为纯值覆盖、不做过期比较；真实 storage state 为 list 形态，dict 分支仅兼容遗留文件，实际不触发。
- **N-07**：流式告警位于 `unwrapper.finalize()` 之前；若 ATG 开启且 `finalize()` 事后补出正文，thinking-only 判定可能产生一次“事后看为空”的告警。当前仅 ATG+thinking-only 极端组合可触发，不影响重试/配额语义。

### 6.6 R2 结论与统计

**PASS**。R1 提出的 **P2-01 / P2-02 / P2-03 / P2-04 / N-01 已全部收口关闭**；`generation_service.py` 非流式与流式均在“仅 thinking、无正文/工具调用/图片”时输出 1 条 `WARNING` 级 `[generation] 思维链截断告警`，且严格不触发空响应重试或切号。

- 统计：**P0 = 0；P1 = 0；P2 新开 = 0（原 P2-01..04 全部关闭）；Q 待确认 = 2（Q-01..02，沿用 R1）；N 备注 = 3（N-02..04 保留，N-05..07 为 R2 新记录）；N-01 关闭**。
- 门禁：Ruff / Pyright / 全量 1482 项 pytest / `git diff --check` / `git ls-files config.yaml` 全部通过。
- 范围声明：本次复审未发现需要改动业务代码的问题，故未产生任何代码变更。
