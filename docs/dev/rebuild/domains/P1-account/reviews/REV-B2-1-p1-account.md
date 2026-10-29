# REV-B2-1 — P1 Account / Auth / Paid 只读审查报告

- 步骤：B2-1（P1 / D02：Account / Auth / Paid）
- 工作树：`.kilo/worktrees/rebuild-upstream`，HEAD `a9e7dc3`（B1 提交之后），B2-1 改动**尚未提交**
- 权威依据：`06-decision-log.md` P1 章节（58-102）与 99c38ba 裁决 D02-032..034（460-462）、`08-09-decision-record.md` D-05 / D-08 / D-13、`06-feature-index.csv` D02-001..034、`06-test-disposition.csv` cookie/account/presets/model 行、`AGENT.md`
- 审查方式：只读；对照实际未提交 diff 与实跑结果
- 复审日期：2026-10-29（P-01 局部返工后）

## 1. 结论

**PASS**

- P-01（外部 storage-state 导入未过滤瞬态/遥测 Cookie）已在解析/落盘层修复，并由新增用例覆盖；内部注入/回写与 RPC 探测保持“保留 accounts/OSID”口径；跨域 twin 保真未被破坏。
- 实跑 `pytest`：**334 passed / 2 failed**，两个失败均为既有 Windows 平台差异；`ruff check src tests` 全绿；`git diff --check` 干净。
- 无新增阻塞。剩余 1 项口径边界（P-01b）与其余非阻塞项保留在第 4 节，不阻塞本步提交。

### 1.1 局部返工复核记录（2026-10-29）

- 返工范围核对：23 个已跟踪改动文件中，仅 `src/aistudio_api/infrastructure/account/cookie_parser.py` 与 `tests/unit/test_cookie_import.py` 的 mtime 为 21:08-21:09（返工时刻），其余文件仍为 21:00-21:01 的原 B2-1 改动；`git diff --numstat` 与上一轮审查逐文件一致，未发现夹带改动。未改动 `AGENT.md` / 06 / 08-09 / archive。
- P-01 修复内容：`parse_storage_state(raw, *, discard_transient=False)` 新增开关（`cookie_parser.py:342-345`）；为 True 时按 `DISCARDABLE_IMPORT_COOKIE_NAMES` 与 `_ga_`/`_gcl_` 前缀过滤（`:389-392`）；`parse_cookie_string` 透传该开关且外部导入默认 True（`:436-441`）；`cookies_for_google_rpc`（`:420-433`）与浏览器注入/回写（`cookie_refresher.py:85` 默认 False）保持保留。
- 新增/调整用例：`test_parse_storage_state_external_import_discards_transient_cookies`（`test_cookie_import.py:184-214`，断言仅保留 `LSID` 双域并保留 `origins`）；原跨域保真用例显式改为 `discard_transient=False`（`:162-182`）。

返工后三条口径同时成立（逐条验证）：

| 口径 | 结论 | 证据 |
|---|---|---|
| 外部导入过滤 | 成立 | 三个外部导入端点均走 `parse_cookie_string` 默认 True：`routes_accounts.py:773,870,979`；过滤实现 `cookie_parser.py:389-392`；用例 `test_cookie_import.py:184-214` |
| 内部注入/回写保留 accounts/OSID | 成立 | 注入路径 `cookie_refresher.py:85` 调 `parse_storage_state` 不传 discard（默认 False）；页面全量注入 `session_auth.py:121-149`；回写合并 `session_auth.py:396-423`；RPC 探测 `cookie_parser.py:482,545,592` 走 `cookies_for_google_rpc`（只取 google.com/.google.com，保留核心认证项） |
| 跨域 twin 保真 | 成立 | `parse_storage_state` 按 `(name, domain, path)` 去重（`cookie_parser.py:375-383`），不压平；`parse_raw_cookies` 按域名优先级取规范副本（`:126-149,152-205`）；用例 `test_cookie_import.py:162-182,216-224` |

## 2. 实际执行命令与结果

| 命令 | 结果 |
|---|---|
| `$env:PYTHONUTF8='1'; $env:PYTHONIOENCODING='utf-8'; uv run pytest -q` | **2 failed, 334 passed in 5.16s**。失败恰为既有 Windows 平台差异：`tests/unit/test_browser_engine.py::test_find_chromium_executable_strictly_requires_cloakbrowser`、`tests/unit/test_browser_scripts_syntax.py::test_evaluate_wrapper_expressions_parse_cleanly`；两个测试文件本步均未改动，属 `AGENT.md` §2.4 所列例外 |
| `uv run ruff check src tests` | `All checks passed!`（0 error） |
| `git diff --check` | 退出码 0；仅 `tests/unit/test_account_presets.py` 的 LF→CRLF 提示 |
| `git status --short` | 23 个已跟踪文件修改 + 10 个新增源/测试文件 + 本报告；**未改动** `05`/`06`/`08-09`/`archive`；**无 commit** |
| `git diff --stat` | `23 files changed, 1213 insertions(+), 366 deletions(-)`（10 个未跟踪新文件另计） |
| 前端 `cd web && bun run type-check / lint / build` | **未执行**：`web/node_modules` 不存在，按本步口径记为非阻塞 |

未运行 `uv run pyright src tests` 与 `ruff format --check`（不在本步审查指令内；提交门禁仍建议按 `AGENT.md` §3.4 补跑）。

## 3. 逐项核对表

### 3.1 Cookie / 身份（D02-003..013、019、029..031）

| 检查点 | 结论 | 证据 |
|---|---|---|
| 跨域 Cookie 不被压平 | 通过 | `cookie_parser.py:342-418` 新增 `parse_storage_state`，带 domain 的 storage-state 原样保留；`cookie_parser.py:126-149,152-205` 按 `_cookie_domain_rank` 选规范域；`tests/unit/test_cookie_import.py:162-182,216-224` |
| 回写按 name+domain+path 合并 | 通过 | `session_auth.py:360-367` 三键；`session_auth.py:374-393` 现有未过期项保留、页面值覆盖同键 |
| 保留未过期跨域 twin / 丢弃已过期 | 通过 | `session_auth.py:386-392`；`tests/unit/test_cookie_persistence.py:126-155`（LSID 双域并存） |
| 非 cookie 字段保留 | 通过 | `account_store.py:45-65` `write_storage_state` 先读旧文件再覆盖 cookies；`tests/unit/test_cookie_persistence.py:158-181` |
| 原子写 | 通过 | `account_store.py:24-39`（唯一临时文件 + `os.replace`）；回写统一走 `write_storage_state`（`session_auth.py:396-428`） |
| 注入全量含 accounts/OSID | 通过 | `session_auth.py:121-149` 已删除 OSID/SIDCC 过滤；`__Host-` 由 `cdp_client.py:641-648` 转 `url` 注入；`tests/unit/test_cookie_persistence.py:63-84` |
| 外部导入过滤瞬态/遥测 Cookie | **通过（P-01 已修复）** | `cookie_parser.py:342-345,389-392`；外部导入默认 True（`routes_accounts.py:773,870,979`）；内部注入默认 False（`cookie_refresher.py:85`）；`tests/unit/test_cookie_import.py:184-214` |
| `_ga_` / `_gcl_` 前缀过滤 | 通过 | `cookie_parser.py:389-392` |
| `verify_account_identity` 不匹配返回 False 且不删目录 | 通过 | `session_auth.py:235-357` 无删除逻辑；全仓 `rmtree` 仅 `account_store.py:605`（删账号路径） |
| 旧 auth 迁移 | 通过（沿用上游） | `account_store.py:328-356`；仍无迁移单测（与 D02-003 台账一致） |
| 单删 / Cookie 组删 / 批量删除 / 重复标记 | 通过 | `routes_accounts.py:474-540,543-584`；`account_store.py:406-442,595-619`；`tests/unit/test_account_duplicates.py`（4 项）、`test_cookie_import.py::test_delete_cookie_group_migrates_active_account` |
| 导入/probe 测试离线（monkeypatch） | 通过 | `tests/unit/test_cookie_import.py:247-279`（模块级 probe 打补丁）、`:514-563`（`cookie_parser.probe_google_accounts_infinite` 打补丁）；全量 5.16s，无真实网络 |

### 3.2 AccountInfoRefresher（D02-014..017）

| 检查点 | 结论 | 证据 |
|---|---|---|
| 200 → tier/email 刷新；401 → expired；5xx/非 key 403 → 不改状态 | 通过 | `account_info_refresher.py:222-301,340-382`；`tests/unit/test_account_info_refresh.py:191-285,310-359` |
| 403 为 key 问题时回收 key 重试一次 | 通过 | `account_info_refresher.py:461-502`（仅 key 问题触发，只重试一次）；`test_account_info_refresh.py:250-285` 断言 POST 恰好 2 次 |
| 并发 `Semaphore` 限流、结果顺序稳定 | 通过 | `account_info_refresher.py:303-338`；`test_account_info_refresh.py:365-387`（`max_active<=3`） |
| 409 进程内互斥 | 通过 | `account_info_refresher.py:314-317`；`routes_accounts.py:273-281`（HTTP 409）；`test_account_info_refresh.py:389-411,600-630`（含真实 ASGI 栈） |
| tier 映射 0/1/2/3/其他 | 通过 | `account_info_refresher.py:52,560-562`；参数化用例 `:111-138` |
| 路由 refresh / detect-tier / detect-email 接线 | 通过 | `routes_accounts.py:248-310,335-471` |
| `runtime_state.account_refresher` + lifespan 装配/清理 | 通过 | `api/state.py:47`；`api/app.py:110,80` |
| 24h 调度不在本步实现 | 通过 | 本步未新增任何定时任务；`api/app.py` 中 `_auto_refresh_tiers` 为上游既有代码、未在 diff 内（P6/D08 归属） |
| 传输异常按账号隔离 | **偏差** | 见 P-05 |

### 3.3 D-08 预设持久化（D02-034）

| 检查点 | 结论 | 证据 |
|---|---|---|
| 无非重入嵌套锁 | 通过 | `account_store.py:318` 使用 `threading.RLock`；`_load_presets`（`788-813`）在锁内调用 `_save_presets`（`815-820`）为重入 |
| 写失败显式抛出、不留半写文件 | 通过 | `account_store.py:815-820`（先原子写成功再更新缓存）；`_atomic_write_json` 失败清临时文件（`24-39`）；`tests/unit/test_account_presets.py:170-182` |
| 只读目录 / 删文件不死锁 | 通过 | `test_account_presets.py:155-168`（删 presets.json 后跨线程重载）、`:185-206`（写失败跨线程） |
| 保留 `data/accounts/presets.json` 位置 | 通过 | `account_store.py:315,788-820` |
| 写失败后内存缓存一致性 | **偏差** | 见 P-04 |

### 3.4 D-13 删除 notes / 批量分组（D02-033）

| 检查点 | 结论 | 证据 |
|---|---|---|
| `notes` 字段 / API / 前端类型全部删除 | 通过 | 全仓 `notes` 命中仅 docs/archive、`AGENT.md`、`static` 产物与测试断言；`AccountMeta`（`account_store.py:126-186`）无 notes；`account_service.py` 无 notes 方法 |
| `updateNotes` / `batchToggleGroup` 及后端端点删除 | 通过 | `routes_accounts.py` 无 `/notes`、无 `groups/{g}/toggle`；`web/src/api/accounts.ts` diff 删除两方法 |
| 分组 CRUD 与预设 CRUD/UI 过滤保留 | 通过 | `routes_accounts.py:645-763`；`web/src/types/accounts.ts` 仅增 rebind/expired 字段 |
| 测试断言 notes/batch 不存在 | 通过 | `tests/unit/test_routes_account_presets.py:49-61`（404/405）、`tests/unit/test_routes_accounts.py:73` |
| 交付产物（static）无死客户端 | **偏差** | 见 P-06 |

### 3.5 D-05 toggle / apply_preset 时序（D02-032）

| 检查点 | 结论 | 证据 |
|---|---|---|
| 改写 enabled 前捕获旧 active | 通过 | `routes_accounts.py:612-613`；`account_store.py:885-886` |
| 旧 active 被禁用时返回 `rebind_required` / previous_active / target_account | 通过 | `routes_accounts.py:618-635,750-762`；`account_store.py:918-934` |
| 不提前改写 active、不直接切浏览器 | 通过 | toggle/apply 路由无 `runtime_state` 依赖，不触碰浏览器；测试断言 registry 仍指向旧 active：`test_account_presets.py:88-97`、`test_routes_account_presets.py:100-104`、`:152` |
| `get_active_account` 仅在 active 缺失时兜底 | 通过 | `account_store.py:470-494`（active 存在即原样返回，含 disabled；缺失才在有 enabled 未过期账号时补写） |
| 给 B2-3 留干净切口 | 通过（附 P-07） | 全仓无 rebind 执行体（`grep rebind` 仅返回信号定义/日志/测试）；候选选择 `select_active_candidate`（`account_store.py:444-455`）无副作用 |
| 应用预设不吞错误 | 通过 | `routes_accounts.py:738-741`（ValueError→404） |

### 3.6 Paid（D02-021..028）

| 检查点 | 结论 | 证据 |
|---|---|---|
| 单一 paid 种子/集合 | 通过 | `application/model_access.py:16-31,43-60`；`_FALLBACK_MODELS` 已无 `paid` 字段（`model_discovery.py:96-155`）；fallback 只回填 `paid=_is_paid_model(...)`（`337-346`） |
| ListModels `item[77]` → field 78，`paid=access_tier==2`，保留 `access_tier` | 通过 | `model_discovery.py:617-625,677-681`；`tests/unit/test_model_discovery_paid.py:26-44`；`test_routes_models.py:171-176` |
| discovery / snapshot / fallback 同步 | 通过 | `model_discovery.py:174-249`（快照加载即 `set_paid_models`）、`261-266,325-328`（发现即同步+落快照）、`337-346`（fallback） |
| 缺失模型保守 | 通过 | `model_access.py:43-60` 只更新快照内出现的模型；`test_model_access.py:58-72` |
| `model_catalog.py` 无 channel_registry / surface_orchestrator / groups / lease | 通过 | `application/model_catalog.py` 全文仅前缀/去重/chat-only 投影 |
| `routes_models` 只消费裸 ID，不外泄 paid 元数据 | 通过 | `routes_models.py:76-80` + `test_routes_models.py:29-33`、`test_model_discovery_paid.py:47-73` |
| `/stats` paid 来自 model_access | 通过 | `routes_system.py:50,130`；`response_models.py:98`；`test_routes_system.py:168-183` |
| 不重复 paid 源 | 通过（附 P-08） | 旧 `_paid_map` 与规则驱动的 `is_paid_model` 已移除；`paid_model_rules` 仅剩 tier 准入用途（`model_discovery.py:434-464`） |

### 3.7 测试质量

| 检查点 | 结论 | 证据 |
|---|---|---|
| cookie persistence | 通过 | 新增 `tests/unit/test_cookie_persistence.py`（5 项：注入保全、合并、双域 twin、非 cookie 字段、无 auth_file） |
| 导入/解析过滤 | 通过 | `test_cookie_import.py:130-150`（平铺输入过滤）、`:184-214`（storage-state 外部导入过滤 + origins 保留）、`:162-182`（显式保留口径） |
| 导入/probe 离线 | 通过 | `test_cookie_import.py:247-279,514-563`；`test_load_cookies_from_string_preserves_storage_state_without_refresh`（`:226-245`）断言不得走 curl_cffi 刷新 |
| account duplicates | 通过 | `tests/unit/test_account_duplicates.py`（4 项，含重载持久化） |
| info refresh | 通过 | `tests/unit/test_account_info_refresh.py`（tier 映射 8 参数、authuser、401/403/5xx、邮箱 3 项、并发、409、路由 6 项） |
| presets store + 路由级 | 通过 | `test_account_presets.py`（含锁重入/写失败/只读目录）、新增 `test_routes_account_presets.py`（notes/batch 404、分组保留、toggle/apply rebind 时序、snapshot 不 rebind） |
| model access / catalog / discovery paid | 通过 | `test_model_access.py`、`test_model_catalog.py`、`test_model_discovery_paid.py` |
| 未删除有效断言 | 通过 | `test_routes_accounts.py` diff 仅新增断言 + 格式化；`test_account_presets.py` 由旧 notes 断言迁移为 D-05/D-08 断言并加强；返工仅把原跨域用例显式标注 `discard_transient=False`，断言数量未减 |
| 缺口 | 附注 | 无用例覆盖 `/import-cookies` 浏览器回写后的 auth.json 内容（P-01b）；detect-email/detect-tier 无路由级用例（台账已标 partial）；写失败用例未断言内存缓存（P-04）；P-09/P-10 |

### 3.8 范围 / 洁净

| 检查点 | 结论 | 证据 |
|---|---|---|
| 改动文件集合在 B2-1 授权域内 | 通过 | `git status --short`：api（app/routes_accounts/routes_models/routes_system/state/response_models）、application（account_rotator/account_service + 新 model_access/model_catalog）、infrastructure（account_store/cookie_parser/cookie_refresher/session_auth/model_discovery + 新 account_info_refresher）、tests、web types/api |
| 返工未夹带其它文件 | 通过 | mtime：仅 `cookie_parser.py`（21:08:54）与 `test_cookie_import.py`（21:09:25）晚于原 B2-1 批次（21:00-21:01）；`git diff --numstat` 其余文件与上一轮一致 |
| 未改 `05` / `06` / `08-09` / `archive` / `AGENT.md` | 通过 | `git status --short` 无 docs 变更 |
| 无真实 Google / 账号 / IP 访问 | 通过（文本层附 P-09） | 测试全离线；无真实网络调用；新增测试中出现一个来自 evidence 的真实邮箱字符串（P-09） |
| 未 commit | 通过 | `git log --oneline -1` = `a9e7dc3`（B1）；工作区为未提交改动 |

## 4. 问题清单

| ID | 严重度 | 状态 | 位置 | 证据 | 影响 | 阻塞 |
|---|---|---|---|---|---|---|
| P-01 | 中 | **已修复（2026-10-29 返工）** | `cookie_parser.py:342-345,389-392,436-441`；`test_cookie_import.py:184-214` | `parse_storage_state` 新增 `discard_transient`；外部导入默认 True，内部注入/回写默认 False；新增用例断言 `OSID`/`__Secure-OSID`/`SIDCC`/`_ga` 被丢弃、`LSID` 双域与 `origins` 保留 | 已消除：外部导入不再携带旧设备/IP 绑定与遥测 Cookie，且注入/回写仍保留 accounts/OSID | 否（已关闭） |
| P-01b | 低 | 新增（口径边界） | `routes_accounts.py:836-839`；`session.py:453,466-469`；`session_auth.py:422-423` | `/import-cookies` 落盘过滤后的 storage_state 后，仍以**原始** `req.cookies` 调 `browser_session.import_cookies`；`load_cookies_from_string` 保持不过滤（`cookie_refresher.py:85`），bootstrap 后 `save_browser_cookies` 会把注入值合并回 auth.json | 若 bootstrap 未重新下发同键 Cookie，导入时的 `OSID`/`_ga`/`SIDCC` 可能被写回；按“浏览器注入/回写保留”的既定口径属设计内，但若要求导入端注入也过滤，应改为传入过滤后的 `storage_state` | 否 |
| P-02 | 低 | 保留 | `routes_accounts.py:799-803`、`:452-456`、`:990-994` | 路由把 storage-state 压平成单域 `dict` 再传探测函数；`cookies_for_google_rpc`（`cookie_parser.py:420-433`）仅在收到原始串时生效，dict 入参被原样使用 | 同名 Cookie 的域选择仍依赖输入顺序，可能导致导入时 tier/email 探测取值偏差（不影响落盘 Cookie） | 否 |
| P-03 | 低 | 保留 | `session_auth.py:121-149`；`transport.py:193-198` | `load_cookies_to_page` 删除了 `page.account_auth_file = auth_file`（对比 `08-08-upstream-99c38ba-full-diff-audit.md` 第 34 项） | `_ensure_authorization_header` 的磁盘 Cookie 回退成为死路径；CDP 取 Cookie 异常时少一道 SAPISID 兜底 | 否（建议 B2-2/P3 会话重建时恢复绑定） |
| P-04 | 低 | 保留 | `account_store.py:832-841`、`:843-852` | 先修改 `_load_presets()` 返回的缓存对象，再调 `_save_presets`；写失败异常已抛出但缓存已改动 | 落盘失败后 `list_presets()` 与磁盘不一致 | 否 |
| P-05 | 低 | 保留 | `account_info_refresher.py:218,328-331` | `refresh_account` 未包裹传输异常；`asyncio.gather` 直接抛出 | 单账号网络失败会中断整批刷新并返回 500，其余账号结果丢失；状态未被改 | 否 |
| P-06 | 低 | 保留 | `src/aistudio_api/static/assets/index.js` | 构建产物仍含 `updateNotes`、`batchToggleGroup`、`/notes`（源码已按 D-13 清理）；`web/node_modules` 不存在 | 交付的管理页 bundle 仍带已删除端点的死客户端代码（无 UI 调用者，调用会 404） | 否（建议 P8/B4 统一重建） |
| P-07 | 说明 | 保留 | `transport.py:127`、`model_discovery.py:291`、`api_service_gemini.py:168` | 这些调用方仍以 `get_active_account()` 取当前账号；在 toggle/apply 返回 `rebind_required` 到 B2-3 落地之间，registry 会短暂保持「active=已禁用」 | D-05 明确设计的切口：B2-3 必须完成「停新请求→有界等待→关旧页→切号→改写 active」 | 否（B2-3 交付项） |
| P-08 | 说明 | 保留 | `model_discovery.py:425-470` | `get_required_tiers` 仍读 `paid_model_rules` 的 `required_tiers` | 与「单一 paid 源」不冲突（不参与 paid 分类），但 tier 准入仍有第二套规则入口，需 B2-4/P2（D05-046）收敛 | 否 |
| P-09 | 低 | 保留 | `tests/unit/test_account_info_refresh.py:292,322,325` | 测试使用 `tonygemini38@gmail.com`，该地址出现在 `docs/dev/evidence/` 真实账号证据中 | 真实个人邮箱进入代码库（无凭据、无请求） | 否（建议改 `example.com`） |
| P-10 | 低 | 保留 | `tests/unit/test_model_catalog.py:38-58`；`tests/unit/test_model_discovery_paid.py:27` | 测试直接改写全局 paid 集合不复位；`ModelDiscoveryService()` 构造会读 `settings.model_cache_file` | 本机若存在真实 `data/models_cache.json` 或测试顺序变化，paid 全局状态可能串扰 | 否 |
| P-11 | 低 | 保留 | `account_info_refresher.py:186-191` | `getattr(settings, "account_refresh_concurrency", 0)`，但 `config.py` 与 YAML 均无该键 | 并发度恒为 3 且静默回落，无法配置 | 否 |

## 5. 用户裁决项

无新增裁决项。P-01 已按「外部导入过滤、内部注入/回写保留」口径关闭；P-01b 为同一口径的边界说明，如后续要求导入端注入也过滤，只需把 `/import-cookies` 传给 `browser_session.import_cookies` 的入参改为过滤后的 `storage_state`（局部改动，不需改决策文档）。

## 6. 只读声明

- 本报告为只读审查产物：**未修改任何代码、配置或测试**，**未 commit**，**未访问真实 Google/账号/IP**。
- 除本报告外未写任何文件；两条报告路径内容一致。
- **B2-1 尚未 commit**，当前工作区改动等待用户审计确认。
