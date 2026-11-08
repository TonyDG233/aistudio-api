# REV-C2a — C2a UI 调试沙箱 / AccountsView v3 独立只读审计（final）

- Audit date: 2026-11-06/07 (UTC)，本轮进行只读源码/测试/差异审查 + 沙箱实跑，重点复核最新 P2-2 / P2-11 修复。
- Target worktree: `D:\LLM\self_programming\aistudio-api\.kilo\worktrees\rebuild-upstream`。
- Target branch / HEAD: `rebuild-upstream` / `b2b84bd9e3054482f1daef1172eebb871a3ed564`。
- Target 工作树：`git status --porcelain=v1` 共 **65** 个条目（43 个 tracked modified + 22 个 untracked 条目）。
- Main repo: `D:\LLM\self_programming\aistudio-api`，HEAD `49b0f96805e9e9505a37e08c57b09eb111ccfc2c`，分支 `rebuild`（ahead 44）。
- Method: 只读 diff/source/test 审查；离线执行 `pytest`、`ruff`、`pyright`、前端 `lint/type-check/build/bun test`、`git diff --check`、config 跟踪检查；在随机空闲 localhost 端口 `1839` 以 `AISTUDIO_UI_DEBUG=1`、`AISTUDIO_WEB_PASSWORD=''` 启动沙箱服务，用 headless Playwright 实跑 UI，结束后停止服务并确认端口关闭。
- 本轮**未修改源码/测试/配置**；仅更新本报告的两份副本。**未 stage、未 commit、未 push。**

## 0. Verdict

**PASS — 0 个 P0，0 个 P1，0 个 P2，0 个未关闭 Q。**

核心功能与全部门禁通过：P1-1 的模型路由穿透已封堵；ui_debug 启动使用临时配置/数据目录；debug 端点 false 时 404、true 时鉴权；静态产物 LF/可复现；SLEEPING/RECLAIMING 已播种；refresh/reset 能清除 expired/session；B1/B2/B3、批量管理、防重复、部分失败提示、debug 关闭不轮询等均已实测通过。
本轮重点复核的两项此前未收口问题均已修复并实测通过：**P2-2**（统计/筛选/行内单一状态分类器，鉴权冷却优先于实例、配额排除 disabled/bound/cooldown）与 **P2-11**（releasing 槽位不再阻塞占用，恢复账号可重新绑定，后端忽略 stale invalid）。按指令 PASS 仅当未解决 P0/P1/P2/Q 为 0，本轮满足。

## 1. 审查清单逐项结论

| # | 审查项 | 结论 | 关键证据 |
|---|---|---|---|
| 1 | P1-1 模型路由不读真实 AccountStore、不外呼 httpx | PASS | `routes_models.py:28-36,99-107,112-130,142-146`；`routes_openai.py:227-230,258-261`；`model_discovery.py:37-43,421-424,608-611`；`test_ui_debug_sandbox.py:473-525`；实测沙箱 Key 下 `/api/models`、`/v1/models`、`/v1beta/models`、单模型均 200，不存在模型 404 |
| 2 | Q1/Q2 临时 config/data/log/dump；启动迁移不写真实 config | PASS | `app.py:327-372`；`ui_sandbox.py:159-245`；`test_ui_debug_sandbox.py:528-588`；实测真实 `config.yaml` mtime 仍为 `2026/11/1 2:11:23`、SHA256 `42AC8888...40626F` |
| 3 | Q3 debug 鉴权；false 时 404 | PASS | `routes_debug.py:15-23,49-53`；`test_ui_debug_sandbox.py:438-470` |
| 4 | P2-1 静态 `index.html` LF、构建可复现、`git diff --check` 0 | PASS | `.gitattributes:3`；`static/index.html` CRLF=0/bareLF=16；`web/index.html` CRLF=0/bareLF=13；构建前后 12 个静态资产 + `index.html` SHA256 全同；`git diff --check` 退出码 0 |
| 5 | P2-2 单一状态分类器；统计/筛选/行内一致 | PASS（本轮修复） | `utils/accountStatus.ts:25-50`；`StatsStrip.vue:27-47`；`AccountTable.vue:47-58`；`AccountRow.vue:69-76,313-362`；`utils/instances.ts:58-70,93-141`；`web/tests/accountStatus.test.ts:44-194`；实测见 §4.4 |
| 6 | P2-3 SLEEPING/RECLAIMING 播种；empty 场景 | PASS | `ui_sandbox.py:1519-1530`；all_states 9 实例含 SLEEPING/RECLAIMING；empty 场景 0 账号/0 实例 |
| 7 | P2-4 refresh/reset 清除 expired/session/cooldown | PASS | `routes_accounts.py:174-185,409-470`；`ui_sandbox.py:932-972,1289-1295`；`routes_system.py:254`；实测 refresh all 与恢复清除 flags |
| 8 | P2-5 B3 创建排除已绑定账号 | PASS | `CreateInstanceModal.vue:31-45`；`utils/instances.ts:104-106`；`test_instance_manager.py:741-773`；实测候选仅 4 个未占用且合格账号 |
| 9 | P2-6 未绑定槽位无唤醒；wake 409；sleeping 可唤醒 | PASS | `InstancePoolCard.vue:64-69,198-237`；`routes_instances.py:228-241`；`ui_sandbox.py:514-525`；实测 unbound wake=409、sleeping wake=200 |
| 10 | P2-7 批量刷新 loading/防重复 | PASS | `AccountTable.vue:163-169,486-516`；`stores/accounts.ts:31-32,321-347`；实测 0 选中 4 个批量按钮 disabled，选中 2 后 enabled |
| 11 | P2-8 批量部分失败/零成功不弹 success | PASS | `stores/accounts.ts:355-412`；代码分支 error/warning/success；本轮复核成功路径与按钮禁用 |
| 12 | P2-9 ui_debug=false 不轮询 debug、菜单隐藏 | PASS | `stores/debug.ts:41-64`；`AccountsView.vue:61,66`；`test_ui_debug_sandbox.py:438-470` |
| 13 | P2-10 清理未使用代码/接口 | PASS | `/api/instances/{id}/bind` 已移除；`refreshingIds` 已被行/批量 UI 消费；`Settings.ui_debug` 已移除；`/api/system/rotation` 别名未保留；`web/.gitignore:1`；`web/tsconfig.json:19` |
| 14 | Q5 顶部“切换下一账号”移除 | PASS | `AccountsView.vue` 无该按钮；`stores/system.ts`、`api/system.ts` 无 `forceNextAccount` |
| 15 | figure2 优先级/分组失效徽章 | PASS | `AccountRow.vue:313-362` 顺序 disabled → expired → cooldown → bound instance → active → quota → ready；`CookieGroupBlock.vue:33-40,105-110` 显示 `⚠ N 个失效` |
| 16 | figure3 三宽度无横向重叠、无邮箱显示 `ID: acc_...` | PASS | `AccountRow.vue:94-107,266-283`；1440/1024/768 `scrollWidth==innerWidth`、row/overflow=0；`acc_free_nomail` 显示 `ID: acc_free_nomail` 与“未记录邮箱” |
| 17 | B1 in-flight reassign 安全排空 | PASS | `instance_manager.py:424-470`；`instance_pool.py:626-657`；`ui_sandbox.py:472-512,675-687`；`test_instance_manager.py:605-674`；实测见 §4.5 |
| 18 | B2 unstarted/sleeping 绑定；bound-elsewhere 拒绝；恢复账号可绑定 | PASS | `ui_sandbox.py:472-525,689-704`；`instance_manager.py:1179-1201`；`BindSlotModal.vue:47-51,80-88,148-152`；`test_instance_manager.py:677-749`；实测见 §4.5/§4.6 |
| 19 | B3 create 容量/tier/账号排除 | PASS | `CreateInstanceModal.vue:31-45`；`routes_instances.py:145-171`；`instance_manager.py:291-334`；实测见 §4.5 |
| 20 | 批量管理：选择/刷新/启停/删除确认；刷新全部 | PASS | `AccountTable.vue:149-264,458-528`；`stores/accounts.ts:301-412`；实测见 §4.7 |
| 21 | 恢复 clears expired/session；重置锁定 clears expired | PASS | `stores/accounts.ts:416-447`；`ui_sandbox.py:1289-1295`；`routes_system.py:254`；实测见 §4.8 |
| 22 | debug 菜单/404、无 debug 轮询（false API 测试） | PASS | 菜单与四场景实测；404/401 由 `test_ui_debug_sandbox.py:438-470` 覆盖；未启动 false 服务 |
| 23 | 全量门禁 | PASS | pytest 1071 passed；ruff/pyright/lint/type-check/build/bun test/diff-check 均 0（见 §5） |

## 2. 工作树状态、范围与洁净

`git status --porcelain=v1` 共 **65** 个条目（43 tracked modified + 22 untracked）：

```text
 M .gitattributes
 M config/config.schema.json
 M defaults/config.yaml
 M src/aistudio_api/api/app.py
 M src/aistudio_api/api/response_models.py
 M src/aistudio_api/api/routes_accounts.py
 M src/aistudio_api/api/routes_instances.py
 M src/aistudio_api/api/routes_models.py
 M src/aistudio_api/api/routes_openai.py
 M src/aistudio_api/api/routes_system.py
 M src/aistudio_api/api/state.py
 M src/aistudio_api/application/instance_manager.py
 M src/aistudio_api/config.py
 M src/aistudio_api/domain/instances.py
 M src/aistudio_api/infrastructure/browser/fingerprint.py
 M src/aistudio_api/infrastructure/gateway/instance_pool.py
 M src/aistudio_api/infrastructure/gateway/model_defaults.py
 M src/aistudio_api/infrastructure/gateway/model_discovery.py
 M src/aistudio_api/static/assets/AccountsView.js
 M src/aistudio_api/static/assets/LogsView.js
 M src/aistudio_api/static/assets/SettingsView.js
 M src/aistudio_api/static/assets/index.css
 M src/aistudio_api/static/assets/index.js
 M src/aistudio_api/static/assets/vendor-icons.js
 M src/aistudio_api/static/assets/vendor-vue.js
 M src/aistudio_api/static/index.html
 M tests/unit/test_config_migration.py
 M tests/unit/test_instance_manager.py
 M tests/unit/test_routes_instances.py
 M web/index.html
 M web/src/api/accounts.ts
 M web/src/api/client.ts
 M web/src/api/system.ts
 M web/src/components/accounts/AccountRow.vue
 M web/src/components/accounts/AccountTable.vue
 M web/src/components/accounts/CookieGroupBlock.vue
 M web/src/stores/accounts.ts
 M web/src/stores/system.ts
 M web/src/types/accounts.ts
 M web/src/types/index.ts
 M web/src/types/system.ts
 M web/src/views/AccountsView.vue
 M web/tsconfig.json
?? docs/dev/rebuild/domains/P8-api-ui/
?? src/aistudio_api/api/routes_debug.py
?? src/aistudio_api/api/ui_sandbox.py
?? tests/unit/test_ui_debug_sandbox.py
?? web/.gitignore
?? web/src/api/debug.ts
?? web/src/api/instances.ts
?? web/src/api/keepalive.ts
?? web/src/components/accounts/InstancePoolCard.vue
?? web/src/components/accounts/KeepaliveBar.vue
?? web/src/components/accounts/StatsStrip.vue
?? web/src/components/debug/
?? web/src/components/instances/
?? web/src/stores/debug.ts
?? web/src/stores/instances.ts
?? web/src/stores/keepalive.ts
?? web/src/types/debug.ts
?? web/src/types/instances.ts
?? web/src/types/keepalive.ts
?? web/src/utils/accountStatus.ts
?? web/src/utils/instances.ts
?? web/tests/
```

- `git diff --cached --name-only` 为空；**没有 staged 内容**。
- `git status -sb`: `## rebuild-upstream...origin/rebuild-upstream`，无 ahead/behind 标记；HEAD 仍为 `b2b84bd`。
- `git ls-files config.yaml` 在 worktree 与主仓均为空；root `config.yaml` 未跟踪。
- `git diff --check` 退出码 **0**（仅有 CRLF→LF 的 Git 规范化 warning，不是 trailing whitespace 错误）。
- 报告写入目标树后，`docs/dev/rebuild/domains/P8-api-ui/` 仍为 untracked；未 stage、未 commit、未 push。

## 3. 关键实现证据

### 3.1 P1-1 模型路由离线化

- `routes_models.py:28-36`：`_ui_debug_models()` 在 `runtime_state.ui_debug=True` 时直接返回 `sandbox_model_catalog()`。
- `routes_models.py:99-107`：`/v1beta/models` 在 ui_debug 下不再调用 `discovery_sessions_for()` / `model_discovery.get_models()`。
- `routes_models.py:112-130`：`/api/models` 管理别名仅 ui_debug 下返回本地目录，否则 404。
- `routes_models.py:142-146`：单模型查询在 ui_debug 下走 `sandbox_model_by_id()`。
- `routes_openai.py:227-230,258-261`：`/v1/models` 与 `/v1/models/{id}` 同样直接使用沙箱目录。
- `model_discovery.py:37-43,421-424,608-611`：防御性 guard，即使其他调用方绕过路由，也不会读取真实 `AccountStore()`。
- 实测（沙箱内 `POST /api-keys` 创建 key）：`/api/models`、`/v1/models`、`/v1beta/models`、`/v1beta/models/gemini-3.7-flash`、`/v1/models/gemini-3.7-flash` 均 200；`/v1beta/models/does-not-exist` 为 404；`/v1/models` 返回 10 个沙箱模型（含 `gemini-3.7-flash`）。

### 3.2 Q1/Q2 临时路径隔离

- `app.py:327-372`：在 `validate_config()` 之前进入 `UiDebugIsolation`，之后才构造 `UISandbox`；`finally` 中关闭 sandbox、恢复 runtime、关闭 isolation。
- `ui_sandbox.py:159-245`：将 `AISTUDIO_CONFIG_PATH`、`AISTUDIO_DATA_DIR`、`AISTUDIO_ACCOUNTS_DIR`、stats/rotator/model cache/log/dump 路径全部重定向到 `tempfile.mkdtemp()`；关闭时恢复 env/settings 并 `rmtree`。
- `test_ui_debug_sandbox.py:528-588`：验证 `/config`、`/api-keys`、`/api/logs/export` 使用临时路径，且真实仓库 `config.yaml` 的 mtime/bytes 前后一致。
- 实测：真实 `config.yaml` mtime 仍为 `2026/11/1 2:11:23`，SHA256 `42AC88882808E71B7D455DC4CA572228D6FE94803098B278A416B0990D40626F`，未因启动/创建 API Key 改变。

### 3.3 Q3 debug 鉴权/404

- `routes_debug.py:15-23`：router 级依赖先检查 `ui_debug` 与 `UISandbox`，不满足直接 404；满足后才 `require_web_auth()`。
- `routes_debug.py:49-53`：handler 内再次检查。
- `test_ui_debug_sandbox.py:438-470`：false 时 GET/POST 均 404；auth 开启时无 token 401，带 token 200。

### 3.4 P2-1 静态构建与 LF

- `.gitattributes:3`：`* text=auto eol=lf`；二进制 fixture 仍 `-text`。
- `static/index.html` 实测 CRLF=0、bare LF=16；`web/index.html` CRLF=0、bare LF=13；`git ls-files --eol` 为 `w/lf`。
- 构建前静态资产 SHA256 见 `%TEMP%\kilo-c2a-review\static-pre.txt`；`bun run build` 后 12 个 `static/assets/*` 与 `static/index.html` 哈希**全部未变**（`ASSETS_IDENTICAL`，index SHA256 `EB4ACAC8...D5BE09`）。
- `git diff --check` 退出码 0。

### 3.5 P2-2 单一状态分类器（本轮修复，PASS）

- `utils/accountStatus.ts:25-50`：新增唯一分类器 `accountStatus()`，优先级固定为 disabled → expired/login → auth cooldown → bound instance → active → quota → ready；`StatsStrip`、`AccountTable` 过滤器、`AccountRow` 徽章全部调用它。
- `StatsStrip.vue:27-47`：`statuses` 由 `accountStatus` 生成，`expired/cooldown/quota/ready` 计数直接按 `status.key` 统计。
- `AccountTable.vue:47-58`：`matchesStatusFilter` 使用同一 `accountStatus`，`limited` 仅匹配 `quota`、`cooldown` 仅匹配 `cooldown`，不再直接读 `is_available` 或 `enabled`。
- `AccountRow.vue:69-76,313-362`：行内状态徽章使用 `status.key`；`instance` 分支显示 `实例 #N {{ instanceStateLabel(instance.state) }}`，`cooldown` 分支显示“鉴权冷却中”。
- `utils/instances.ts:58-70`：`instanceStateLabel` 精确映射 `starting→启动中`、`active→运行中`、`sleeping→休眠`、`unstarted→未启动`、`reclaiming→回收中`、`retiring→排空`、`cooldown→冷却`、`invalid→失效`。
- `web/tests/accountStatus.test.ts:44-194`：覆盖 disabled>expired>cooldown>instance>active>quota>ready、冷却优先于实例、quota 仅未绑定/非冷却/启用账号、矩阵计数与筛选同口径、invalid stale 不算绑定、releasing 槽位不占用、精确标签。
- 实测 all_states（12 账号）：顶部 `启用 11/12 / 就绪 3 / 登录态失效 2 / 配额耗尽 1 / 鉴权冷却 1 / 实例占用 9/10`；筛选 `配额耗尽=1`、`鉴权冷却=1`、`登录态失效=2`、`就绪=3`，与统计完全一致。
- 行内实测：`acc_free_ready→实例 #6 排空`、`acc_plus_prod→实例 #5 启动中`、`acc_pro_prod→实例 #1 运行中`、`acc_ultra_cooldown→实例 #3 鉴权冷却中`（不再显示“实例运行中”）、`acc_free_quota→实例 #7 休眠`、`acc_free_quota_idle→配额耗尽`、`acc_ultra20x_disabled→已停用`、`acc_pro_expired/acc_pro_session→登录态失效`、其余 3 个→就绪。
- 证据文件：`C:\Users\ADMIN\AppData\Local\Temp\kilo-playwright-mcp\c2a-row-status.txt`（本轮行内快照以 Run 输出为准）。

### 3.6 P2-11 恢复后绑定口径（本轮修复，PASS）

- `utils/instances.ts:93-141`：新增 `isReleasingState`（invalid/retiring/reclaiming）、`isSlotOccupied`、`bindingInstanceFor`；`boundInstanceFor` 只排除 reclaiming/invalid（retiring 仍按“排空”展示）。
- `BindSlotModal.vue:47-51,60,80-88,148-152`：`accountBoundInstance` 改用 `bindingInstanceFor`，`boundElsewhere` 改用 `isSlotOccupied`；releasing 槽位不再被当作“已绑定”阻塞，也不再禁用全部槽位。
- `instance_manager.py:1179-1201`：后端 `_record_for_account` 排除 `RECLAIMING/RETIRING/INVALID`；`_has_record_for_account` 复用该判定，因此 stale invalid 绑定不再阻止同一账号 `create`/`reassign`。
- `ui_sandbox.py:689-704`：沙箱 `_record_for_account` 同样的 releasing 排除。
- `test_instance_manager.py:723-749`：`test_reassign_recovers_account_from_invalid_stale_slot` 直接覆盖“invalid stale 槽位不阻塞重新绑定”；`:703-720` 覆盖 bound-elsewhere 拒绝；`:677-700` 覆盖 unstarted 槽位绑定；`:605-674` 覆盖 in-flight 排空。
- 实测：`acc_pro_expired`（expired，绑定 invalid inst-4）点击“恢复”后 `expired=false/session_expired=false/auth_cooldown=0/is_available=true`，行内显示“就绪 + 激活”；打开 BindSlotModal **不再出现“该账号已绑定实例 #4”**，`#9 未启动 推荐` 可选且“确认绑定”可点；提交后 `inst-9` 变为 `account_id=acc_pro_expired, state=active`，`inst-4` 仍为 `invalid` 且保留 stale account_id——说明后端忽略了 stale invalid。
- 恢复后行内变为 `实例 #9 运行中`。

### 3.7 P2-3 SLEEPING/RECLAIMING

- `ui_sandbox.py:1519-1530`：all_states 播种 `inst-7 SLEEPING`、`inst-8 RECLAIMING`；all_states 共 12 账号/9 实例。
- `ui_sandbox.py:514-525`：wake 仅接受 UNSTARTED/SLEEPING；unbound 时 `_require_bound_account` 抛错。
- 实测 all_states UI 显示 `#7 休眠`、`#8 回收中`；empty 场景账号/实例均 0。

### 3.8 P2-4/P2-11 恢复与重置

- `routes_accounts.py:174-185`：成功检测/恢复后把 `expired=False` 并清 rotator cooldown。
- `routes_accounts.py:409-470`：refresh 路由调用 refresher；`ui_sandbox.py:932-972` 中沙箱 refresher 调 `_clear_recovery_state()`。
- `ui_sandbox.py:1289-1295`：清除 `_cooldown_until`、`auth_cooldown`、`session_expired`、持久化 `expired=False`。
- `routes_system.py:254`：单账号 clear-cooldown 同步写回 `expired=False`。
- 实测：
  - `acc_pro_expired` 恢复后 `expired=false`；`acc_plus_prod` 恢复后 `expired=false, session_expired=false, auth_cooldown=0, is_available=true`。
  - `account_expired` 动作后 `acc_plus_prod.expired=true`，恢复后回 false。
  - 刷新全部 `POST /api/accounts/refresh {"all":true}` 返回 `refreshed_count=12`、`expired_count=0`，并清除 expired/session。
- 说明：P2-11 的绑定口径已同步修复，见 §3.6。

### 3.9 P2-5/P2-6/B1/B2/B3

- B3：`CreateInstanceModal.vue:31-45` 用 `isSlotOccupied` + `isAccountEligible` 排除“占用中已绑定 + disabled/expired/session”账号；实测候选为 `acc_free_ready(retiring 释放中)`、`acc_free_quota`、`acc_free_quota_idle`、`acc_cookie_shared_b`，已绑定 active/starting/cooldown/sleeping 的账号均被排除；用 `acc_cookie_shared_b` 创建 `inst-10` 成功（`state=active, only_tier=Pro`，容量 10/10）。
- B2：`ui_sandbox.py:472-525` reassign 校验 lane/tier/bound-elsewhere 并支持 in-flight drain；实测 inst-2（unstarted）绑定 `acc_free_nomail` 成功、inst-7（sleeping）换绑 `acc_cookie_shared_a` 成功；对已绑定 inst-1 的 `acc_pro_prod` 执行 `POST /api/instances/inst-9/reassign` 返回 409 `instance_already_exists`（“账号 acc_pro_prod 已绑定实例 #1...”）。
- B1：inst-1 有 `in_flight=1`，UI 显示“该实例有 1 个在途请求；确认换绑后将先安全排空（最长 12 秒）...”并只列出未绑定兼容账号；提交后 `acc_pro_prod -> acc_cookie_shared_b`，`state=active, in_flight=0`（沙箱 drain 常量 `_SANDBOX_DRAIN_TIMEOUT_S=0.05`，实测请求 382ms 内完成）。
- P2-6：未绑定 inst-2 的 UI 只有“绑定账号/关闭”，无“唤醒”；API `POST /api/instances/inst-2/wake` 返回 409 `instance_account_invalid`；sleeping inst-7 wake 返回 200 并变 active。
- 真实 manager：`instance_manager.py:424-470` 在 reassign 有在途请求时 `_retire_record`；`instance_pool.py:626-657` 在超时后清零 `active_requests` 并 `active_zero.set()`，避免旧槽位永久 busy。

### 3.10 P2-7/P2-8 批量

- `AccountTable.vue:163-169`：`batchWorking = batchBusy || batchRefreshing`；`:480-518` 所有批量按钮 `:disabled="selectedCount === 0 || batchWorking"`。
- `stores/accounts.ts:31-32`：`refreshingIds` / `batchBusy`；`:321-347` 防止重复刷新并设置/清理 `refreshingIds`。
- 实测：批量模式 0 选中时“批量刷新/启用/停用/删除”4 按钮均 disabled；勾选 2 行后显示 `已选 2 / 12`，4 按钮均 enabled；`批量停用` 后 `acc_free_ready`、`acc_free_nomail` 均变为“已停用”。
- 删除确认文案：`AccountTable.vue:256` 为 `确定要删除选中的 N 个账号吗？此操作不可逆。`。
- `stores/accounts.ts:355-412`：`batchDelete` 在 `deleted===0` 时 `toast.error`，部分失败 `toast.warning`；`batchToggle` 按 `toggled_count/error_count` 分流（P2-8 既有浏览器 Toast 证据保留，本轮复核代码分支未回退）。
- 刷新全部：实测 `POST /api/accounts/refresh {"all":true}` 返回 200，`refreshed_count=12, expired_count=0`。

### 3.11 P2-9/P2-10/Q5

- `stores/debug.ts:41-64`：先请求 `/api/system/status`，`ui_debug !== true` 时清空 state 并直接 return，不请求 `/api/debug/*`。
- `AccountsView.vue:61` 拉取 debugState，`:66` 8 秒轮询整体 `loadData`；false 时轮询只到 system status。
- P2-10：`routes_instances.py` 已无 `/bind`，只有 reassign/wake；`refreshingIds` 已被行/批量 UI 使用；`config.py` 已无 `Settings.ui_debug`；`web/.gitignore:1` 忽略 `tsconfig.tsbuildinfo`；`web/tsconfig.json:19` 把 tsbuildinfo 移到 `node_modules/.cache`。
- Q5：`AccountsView.vue` 无“切换下一账号”；`stores/system.ts`、`api/system.ts` 无 `forceNextAccount`；顶部按钮已移除。

## 4. 浏览器实跑证据

### 4.1 沙箱服务

启动命令（随机空闲端口；`--browser-port` 只作为未使用参数）：

```powershell
$env:AISTUDIO_UI_DEBUG='1'; $env:AISTUDIO_WEB_PASSWORD=''
uv run aistudio-api server --port 1839 --browser-port 1840
```

- 服务端口：`127.0.0.1:1839`；页面：`http://127.0.0.1:1839/accounts`。
- `GET /health` → `{"status":"ok","busy":false,"ui_debug":true,"ui_debug_scenario":"all_states"}`。
- `GET /api/system/status` → `ui_debug=true, scenario=all_states, accounts=12, instances=9`。
- 服务停止后 `Test-NetConnection 127.0.0.1:1839` → `False`；`Get-NetTCPConnection` 仅剩 `TimeWait`，无监听进程。停止方式为 `background_process.stop`（强制终止），因此本轮未观察到 lifespan 的优雅清理；优雅关闭路径由 `test_ui_debug_sandbox.py:89-115` 覆盖。

### 4.2 UI Debug 菜单与场景

- 页面顶部可见 `UI Debug 当前场景: all_states`，场景按钮 `全状态all_states / 海量账号mass_accounts / 窄屏边界narrow_edge / 空数据empty` 与“重置”均可见。
- 场景切换实测：
  - `all_states`：12 账号 / 9 实例（`全部分组 (12)`）。
  - `mass_accounts`：48 账号（`全部分组 (48)`，48 行）/ 6 实例。
  - `narrow_edge`：3 账号 / 2 实例（`全部分组 (3)`）。
  - `empty`：0 账号 / 0 实例，显示“未导入账号”；出现预期的 `/api/accounts/active` 404（无 active 账号）。
- 快捷动作实测：
  - `instance_inflight`：inst-1 `in_flight` 1→2。
  - `account_expired`：active 账号 `expired=true`（本轮实际作用于当时的 active 账号，验证 flags 生效）。
  - `instance_cooldown`：inst-1 `cooldown`，`acc_pro_prod` `auth_cooldown=296`。
  - `keepalive_partial`：`POST /api/keepalive/run {"task":"metadata_refresh"}` 返回 `ok:false, reason:sandbox_partial`。

### 4.3 截图（1440/1024/768 + narrow_edge）

本轮实跑生成（同目录）：

- `C:\Users\ADMIN\AppData\Local\Temp\kilo-playwright-mcp\c2a-all_states-1440x900.png`
- `C:\Users\ADMIN\AppData\Local\Temp\kilo-playwright-mcp\c2a-all_states-1024x900.png`
- `C:\Users\ADMIN\AppData\Local\Temp\kilo-playwright-mcp\c2a-all_states-768x900.png`
- `C:\Users\ADMIN\AppData\Local\Temp\kilo-playwright-mcp\c2a-narrow_edge-768x900.png`

补充证据：`%TEMP%\kilo-c2a-review\static-pre.txt`、`static-post.txt`、`index-pre.txt`、`index-post.txt`；Playwright console 日志 `C:\Users\ADMIN\AppData\Local\Temp\kilo-playwright-mcp\console-2026-11-06T19-54-48-646Z.log`。

### 4.4 figure2 / figure3

- figure2 顶部统计（all_states）：启用 `11 / 12`、就绪 `3`、登录态失效 `2`、配额耗尽 `1`、鉴权冷却 `1`、实例占用 `9 / 10`、保活距今“暂无记录”。
- 筛选一致性实测：`配额耗尽=1`、`鉴权冷却=1`、`登录态失效=2`、`就绪=3`，均与统计一致；`配额耗尽` 仅返回 `acc_free_quota_idle`（不含 disabled、不含绑定/冷却账号）；`鉴权冷却` 仅返回 `acc_ultra_cooldown` 且行内为“鉴权冷却中”。
- 实例池标签实测：`#1 运行中`、`#2 未启动`、`#3 冷却`、`#4 失效`、`#5 启动中`、`#6 排空`、`#7 休眠`、`#8 回收中`、`#9 未启动`，覆盖要求的精确文案。
- figure3（768x900）：`document.documentElement.scrollWidth=768 == innerWidth`，12 个账号行 `scrollWidth<=clientWidth`，全页 overflow 元素 0；无邮箱账号显示 `ID: acc_free_nomail` 与“未记录邮箱”。
- 1024x900、1440x900 同样 `scrollWidth==innerWidth`、rowProblems=0、overflowing=0。
- 窄屏 long-email 场景：`narrow_edge` 768x900 同样无横向溢出/重叠。

### 4.5 B1/B2/B3

| 场景 | 操作 | 结果 |
|---|---|---|
| B1 | inst-1（in_flight=1）换绑 `acc_pro_prod -> acc_cookie_shared_b` | PASS：模态显示“安全排空（最长 12 秒）”且候选仅未绑定兼容账号；提交后 `state=active, in_flight=0`，请求 382ms 完成 |
| B2 | inst-2（unstarted）绑定 `acc_free_nomail` | PASS：`state=active`，账号已绑定 |
| B2 | inst-7（sleeping）换绑 `acc_cookie_shared_a` | PASS：`state=active`，账号已绑定 |
| B2 | `POST /api/instances/inst-9/reassign {acc_pro_prod}`（已绑定 inst-1） | PASS：409 `instance_already_exists`，提示“已绑定实例 #1...” |
| B2 | 恢复 `acc_pro_expired` 后绑定到 inst-9 | PASS：等价于 `bindingInstanceFor` 忽略 invalid；inst-9 active，inst-4 仍 invalid（stale 被忽略） |
| P2-6 | unbound inst-2 `POST /wake` | PASS：409 `instance_account_invalid` |
| P2-6 | sleeping inst-7 `POST /wake` | PASS：200，变 active |
| B3 | CreateInstanceModal 候选 | PASS：仅 `acc_free_ready`、`acc_free_quota`、`acc_free_quota_idle`、`acc_cookie_shared_b`；已占用/失效/停用账号被排除；创建 inst-10 `only_tier=Pro` 成功（10/10） |

### 4.6 B2 恢复账号绑定专项（P2-11）

- 初始：`acc_pro_expired` 绑定 `inst-4`（INVALID），`expired=true`，行内“登录态失效”。
- 点击“恢复”后：`/api/accounts` 返回 `expired=false, session_expired=false, auth_cooldown=0, is_available=true`；行内变为“就绪 + 激活”。
- 点击“激活”：BindSlotModal 无“该账号已绑定实例 #4”提示；`#9 未启动 推荐` 与“确认绑定”均可用；`#4 失效 当前绑定` 仅作为展示项禁用。
- 确认绑定后：`inst-9` → `acc_pro_expired, active`；`inst-4` 仍 `invalid` 且保留 stale `account_id`；行内显示“实例 #9 运行中”。
- 证据：`instance_manager.py:1179-1201`、`ui_sandbox.py:689-704`、`BindSlotModal.vue:47-51`、`test_instance_manager.py:723-749`。

### 4.7 批量管理

- 行级 checkbox、组级 checkbox、“全选当前筛选”均可见；选中 2 行显示 `已选 2 / 12`。
- 批量刷新：`POST /api/accounts/refresh` 200；0 选中时 4 个批量按钮 disabled，选中后 enabled。
- 批量停用：`acc_free_ready`、`acc_free_nomail` 均变为“已停用”。
- 批量删除确认：源码文案为 `确定要删除选中的 N 个账号吗？此操作不可逆。`（`AccountTable.vue:256`）。
- 刷新全部：`POST /api/accounts/refresh {"all":true}` 返回 `refreshed_count=12, expired_count=0`。
- 部分/零成功：`stores/accounts.ts:355-412` 按 `deleted/toggled` 与 `failed` 分流 error/warning/success，不再零成功弹 success。

### 4.8 恢复与重置

- `account_expired` 动作后 `acc_plus_prod.expired=true`；production 分组头显示 `⚠ 1 个失效`；其行显示“实例 #5 登录态失效 恢复”。
- 点击“恢复”后：`expired=false, session_expired=false, auth_cooldown=0, is_available=true`；行内回到“实例 #5 运行中”，分组 `⚠ N 个失效` 徽章消失。
- `acc_pro_expired` 恢复后 `expired=false`（`session_expired=false`）；`acc_pro_session` refresh 后 `session_expired=false`。

## 5. 门禁与实跑

| 命令 | 结果 |
|---|---|
| `uv run pytest -q` | **1071 passed**（0 failed；输出无 skip） |
| `uv run ruff check .` | **All checks passed!**（exit 0） |
| `uv run pyright src tests` | **0 errors, 0 warnings, 0 informations** |
| `git diff --check` | **退出码 0**（仅 CRLF→LF 规范化 warning） |
| `git ls-files config.yaml` | 空（worktree 与主仓均空） |
| `git diff --cached --name-only` | 空（无 staged） |
| `cd web && bun run lint` | 退出码 0（eslint `--max-warnings 0`） |
| `cd web && bun run type-check` | 退出码 0（vue-tsc） |
| `cd web && bun run build` | 退出码 0；12 个静态资产 + `index.html` SHA256 构建前后一致 |
| `cd web && bun test` | **13 pass / 0 fail**，41 expect（1 file，`web/tests/accountStatus.test.ts`） |
| `git diff --check`（build 后复跑） | 退出码 0 |
| 服务停止 | 端口 1839 已关闭（`Test-NetConnection` False，仅 TimeWait） |

## 6. 问题清单（P0/P1/P2/Q/N 全覆盖）

### 6.1 P0（阻塞）

- **P0：无。**

### 6.2 P1（提交前必须修复）

- **P1-1 — 已关闭（PASS）**：ui_debug 下 `/api/models`、`/v1/models`、`/v1/models/{id}`、`/v1beta/models` 均返回本地沙箱目录，不再读取真实 `AccountStore()`，也不向 Google 发起 httpx 请求。证据：`routes_models.py:28-36,99-107,112-130,142-146`；`routes_openai.py:227-230,258-261`；`model_discovery.py:37-43,421-424,608-611`；实测沙箱 Key 下 `/api/models`、`/v1/models`、`/v1beta/models/gemini-3.7-flash` 均 200。

### 6.3 P2（应修复，非阻塞）

- **P2-1 — 已关闭（PASS）**：静态产物统一 LF，`bun run build` 后 12 个静态资产与 `index.html` 哈希不变，`git diff --check` 0。
- **P2-2 — 已关闭（PASS）**：新增单一分类器 `accountStatus()`，统计/筛选/行内同口径；`配额耗尽` 仅统计未停用、未失效、无冷却、未绑定且 `is_available=false` 的账号；`鉴权冷却` 行显示“鉴权冷却中”而非实例状态；实例徽章使用精确状态文案。证据：`utils/accountStatus.ts:25-50`、`StatsStrip.vue:27-47`、`AccountTable.vue:47-58`、`AccountRow.vue:69-76,313-362`、`utils/instances.ts:58-70,93-141`、`web/tests/accountStatus.test.ts:44-194`；实测统计与四类筛选逐一相等。
- **P2-3 — 已关闭（PASS）**：all_states 已播种 SLEEPING/RECLAIMING（`ui_sandbox.py:1519-1530`），empty 场景为 0 账号/0 实例。
- **P2-4 — 已关闭（PASS）**：refresh/reset 清除 expired/session/cooldown（`routes_accounts.py:174-185,409-470`；`ui_sandbox.py:932-972,1289-1295`；`routes_system.py:254`）。
- **P2-5 — 已关闭（PASS）**：B3 候选排除已占用绑定账号与不合格账号（`CreateInstanceModal.vue:31-45`；`utils/instances.ts:104-106`）。
- **P2-6 — 已关闭（PASS）**：未绑定槽位无“唤醒”，API wake 返回 409；sleeping 可唤醒（`InstancePoolCard.vue:64-69,198-237`；`routes_instances.py:228-241`）。
- **P2-7 — 已关闭（PASS）**：批量刷新 loading/防重复已生效（`AccountTable.vue:163-169,486-516`；`stores/accounts.ts:321-347`）。
- **P2-8 — 已关闭（PASS）**：批量删除/启停零成功与部分失败使用 error/warning Toast（`stores/accounts.ts:355-412`）。
- **P2-9 — 已关闭（PASS）**：false 时不请求 debug 端点，菜单隐藏（`stores/debug.ts:41-64`；单元测试覆盖 404）。
- **P2-10 — 已关闭（PASS）**：`/bind` 移除、`refreshingIds` 已消费、`Settings.ui_debug` 移除、`system/rotation` 别名未保留、`tsconfig.tsbuildinfo` 已 ignore。
- **P2-11 — 已关闭（PASS）**：releasing（invalid/retiring/reclaiming）槽位不再阻塞占用；`boundInstanceFor` 展示保留 retiring、忽略 invalid/reclaiming；`bindingInstanceFor`/`isSlotOccupied` 与后端 `_record_for_account` 口径一致；恢复账号可重新绑定且后端忽略 stale invalid。证据：`utils/instances.ts:93-141`、`BindSlotModal.vue:47-51,80-88,148-152`、`instance_manager.py:1179-1201`、`ui_sandbox.py:689-704`、`test_instance_manager.py:723-749`；实测 `acc_pro_expired` 恢复后成功绑定 inst-9。

### 6.4 Q（需用户/维护者裁决）

- **Q-1 — 已关闭（PASS）**：ui_debug 下 config/data/logs/dumps/API key 路径已重定向到临时目录（`ui_sandbox.py:159-245`；`test_ui_debug_sandbox.py:528-588`）。
- **Q-2 — 已关闭（PASS）**：`validate_config()` 迁移写盘发生在 `UiDebugIsolation.enter()` 之后，真实 `config.yaml` 字节未变。
- **Q-3 — 已关闭（PASS）**：debug 端点 false 时先 404 后鉴权，true 时受 `require_web_auth`（`routes_debug.py:15-23,49-53`；404/401 测试通过）。
- **Q-4 — 已按既定决策接受（PASS）**：ui_debug=false 时 `/accounts`、`/instances`、`/rotation` 仍返回新增业务字段；与“production_api_keeps_ui_fields”决策一致。
- **Q-5 — 已关闭（PASS）**：顶部“切换下一账号”已移除，前端无 `forceNextAccount`。

### 6.5 N（非阻塞观察 / 残余风险）

- **N-1**：静态 JS/CSS 与当前源码构建一致；`static/index.html` 与 `web/index.html` 均为 LF，构建前后哈希不变。
- **N-2**：`web/tsconfig.tsbuildinfo` 已通过 `web/.gitignore:1` 忽略，且 `web/tsconfig.json:19` 将增量缓存移到 `node_modules/.cache`。
- **N-3**：keepalive busy 409 / uninitialized 503 分支仍主要由代码审查覆盖（`routes_keepalive.py:51-54`）；partial 200 已实测。
- **N-4**：root `config.yaml` 在两个仓库均未跟踪/被忽略；唯一 tracked 模板为 `defaults/config.yaml`。
- **N-5**：empty 场景浏览器控制台会出现预期 `/api/accounts/active` 404（无 active 账号），不影响页面状态。
- **N-6**：本轮用 `background_process.stop` 强制停止服务，端口 1839 已关闭；强制终止不触发 lifespan 优雅清理，临时目录清理由 `test_ui_debug_sandbox.py:89-115` 覆盖。

## 7. 结论与下一步

- **PASS**：P2-2 与 P2-11 均已修复并实测通过，核心 C2a 功能、P1-1、全部 P2/Q 关闭、全部门禁通过；未解决 P0/P1/P2/Q 为 0。
- 本轮为最终只读复核；未修改源码/测试/配置，未 stage/commit/push。
- 下一步：由用户审阅本报告后再决定提交；提交时建议保留当前 worktree 全部 65 个条目（43 tracked modified + 22 untracked）作为一个 C2a 提交。

## 8. 只读声明、提交状态与报告完整性

- 本轮未修改源码/测试/配置；仅更新目标树与主仓两份 `REV-C2a.md`。
- 未 stage、未 commit、未 push。目标 HEAD 仍为 `b2b84bd9e3054482f1daef1172eebb871a3ed564`，分支 `rebuild-upstream` 无 ahead/behind；主仓 HEAD 仍为 `49b0f96`。
- 沙箱服务仅在 `AISTUDIO_UI_DEBUG=1`、`AISTUDIO_WEB_PASSWORD=''`、随机 localhost 端口 `1839` 下运行；未启动 ui_debug=false 服务；服务已停止，端口已关闭。
- 两份报告内容完全一致；最终文件 SHA256 见 Reviewer 最终消息。
