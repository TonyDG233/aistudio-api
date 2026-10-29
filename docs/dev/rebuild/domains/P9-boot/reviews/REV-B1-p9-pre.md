# REV-B1-p9-pre：B1（P9-pre）装配 / 配置 / 鉴权 只读审查

- 审查对象：目标工作树 `D:\LLM\self_programming\aistudio-api\.kilo\worktrees\rebuild-upstream`
- 基线：`HEAD = a3ffbe6`（B0 文档提交），本步改动全部在工作区（未 commit）
- 审查方式：只读代码审查 + 实际跑测；唯一写入是本报告
- 轮次：第 1 轮 2026-10-29（CONDITIONAL）；第 2 轮 2026-10-29 收尾复审（含局部返工复核，本版）

## 结论

**PASS**

第 1 轮提出的 2 个阻塞项（B1-01`logging` 非字典块静默回退并被接口 200 落盘、B1-02`security` 非字典块启动失败）已在局部返工中修复并有回归用例；B1-04（API Key CRUD 未捕获 `OSError`）与 B1-05（写后应用原始 logging 值）同样已修复；B1-03 已由用户裁决接受（不改代码）。第 2 轮实测 `2 failed, 264 passed`（两个失败仍为既有 Windows 平台差异）、本次改动文件 `ruff` 全通过、`git diff --check` 干净、`git ls-files config.yaml` 为空且本地文件存在并被忽略。未发现新的阻塞问题，故结论由 CONDITIONAL 升为 PASS。

---

## 0. 局部返工复核记录（2026-10-29，第 2 轮）

### 0.1 复核动作（全部实跑）

| 命令 | 结果 |
|---|---|
| `git status --short` / `git diff --stat` / `git diff --cached --stat` | 13 个 tracked 文件改动 + 1 个新增测试文件（`tests/unit/test_features_config.py`）；暂存区仍只有 `D config.yaml`；`HEAD` 仍为 `a3ffbe6`（无 commit） |
| `$env:PYTHONUTF8='1'; $env:PYTHONIOENCODING='utf-8'; uv run pytest -q` | `2 failed, 264 passed in 3.64s`；失败项仍为 `test_browser_engine.py::test_find_chromium_executable_strictly_requires_cloakbrowser` 与 `test_browser_scripts_syntax.py::test_evaluate_wrapper_expressions_parse_cleanly`（两文件本步无 diff，既有 Windows 环境差异） |
| `uv run pytest -q tests/unit/test_features_config.py tests/unit/test_routes_system.py tests/unit/test_api_auth.py` | `41 passed` |
| `uv run ruff check <12 个改动 py 文件>` | `All checks passed!` |
| `git diff --check` | 无空白错误；仅 `test_api_auth.py` 的 LF→CRLF 提示 |
| `git ls-files config.yaml` | 空（0 行） |
| `Test-Path config.yaml` | `True`（本地文件存在且为新 schema） |
| `.gitignore` | 第 15 行含锚定 `/config.yaml` |
| 只读探针（`uv run python -c`，系统临时目录） | 见 0.2 |

### 0.2 逐条复核

| 原编号 | 原问题 | 返工内容 | 复核证据 | 状态 |
|---|---|---|---|---|
| B1-01 | `logging: []`/`null` 静默回退无 warning；`/config/yaml` 会 200 落盘非法结构 | `_apply_noncritical_fallbacks` 对非 dict `logging` warning + 回退默认块；`write_config_text` 对非 dict `logging`/`security` 直接拒绝 | `model_defaults.py:62-76`（warning+回退）、`:724-740`（写前块类型检查）；探针：`validate_config_mapping({'logging': []})` 返回 `logging` 为 `dict`/`INFO` 且输出 `配置 logging 必须为字典（当前类型 list），已回退默认 logging 块`；`write_config_text('logging: []')` → `ConfigValidationError`，目标文件未创建；用例 `test_features_config.py:81-105`、`test_routes_system.py:94-105`（400 且文件内容不变） | **已修复** |
| B1-02 | `security: null/false` 直接启动失败，与“非关键块回退默认”冲突 | 非 dict `security` 改为 warning + 回退默认块；`validate_config_mapping` 不再把 `security` 当关键块 `_require_mapping` | `model_defaults.py:87-106`；探针：`validate_config_mapping({'security': None})` 返回 `{'api_allow_anonymous': False}` 并输出 `配置 security 必须为字典（当前类型 NoneType），已回退默认 security 块`；`write_config_text('security: null')` → 400 且不写文件；用例 `test_features_config.py:81-105` | **已修复** |
| B1-04 | API Key CRUD 未捕获 `OSError` | 三处 CRUD 增 `except OSError → HTTPException(500)` | `routes_system.py:460-463`、`508-511`、`562-565`；用例 `test_routes_system.py:137-150` 断言 500 + `写入配置文件失败` | **已修复** |
| B1-05 | 写后用原始 `logging.level` 应用，可能把非法值传给 `set_log_level` | 改为写后从 `get_configured_logging_settings()` 取归一化值 | `routes_system.py:310-313`；用例 `test_routes_system.py:108-133` 断言写入 `level: verbose` 后应用的是 `INFO` | **已修复** |
| B1-03 | 深合并范围扩大到 `paid_model_rules`/`tier_quotas` | 不改代码 | 用户已裁决接受“所有 dict 块深合并默认、list/标量整体覆盖” | **用户已裁决接受** |

### 0.3 返工是否引入新问题

未发现新的阻塞问题。核对要点：

- 返工仅触及 `model_defaults.py`（`_apply_noncritical_fallbacks`、`write_config_text`）、`routes_system.py`（OSError 映射、归一化 logging 应用）与两个测试文件；`app.py`/`dependencies.py`/`state.py`/`account_rotator.py`/`defaults/config.yaml`/`conftest.py` 的 diff 行数与第 1 轮一致，未被连带改动。
- `security` 不再走 `_require_mapping`，但 `_apply_noncritical_fallbacks` 保证返回前必为 dict，`validate_config_mapping` 的返回值结构契约仍成立（探针已验）。
- `write_config_text` 拒绝非 dict `logging`/`security`，而直接改本地文件仍走“warning + 回退”（启动可用）；这是有意的“允许启动、禁止经接口持久化非法结构”的不对称，不构成回归。
- 用例增量 +6（第 1 轮 258 → 264），无用例被删除或弱化；新增用例均为精确断言（状态码、错误消息、文件未变、warning 文本、应用值）。

---

## 1. 审查范围与依据

### 1.1 依据（只读）

| 文件 | 取用内容 |
|---|---|
| `AGENT.md` | §6.3 API 鉴权（D-14，`276-281`）；§8 配置口径（`314-322`，含 D-07 于 `317`、B1 迁移口径于 `322`） |
| `docs/dev/rebuild/06-decision-log.md` | P9 总原则（`366-391`）、P9 上游裁决（`432`）、08-09 修正（`478`） |
| `docs/dev/rebuild/08-09-decision-record.md` | `D-07`（第 19 行）、`D-14`（第 26 行）、旧结论处置（`35`、`37`） |
| `docs/dev/rebuild/06-feature-index.csv` | `D01-029..033`、`D01-039/040`、`D01-045/046`、`D01-048/049`、`D01-053`、`D01-063` |
| `docs/dev/rebuild/06-test-disposition.csv` | `test_features_config`(rewrite)、`test_api_auth`(rewrite)、`test_runtime_persistence`、`test_routes_system`(keep)、`test_account_rotation_concurrency`、`test_tier_preemption` 行 |

### 1.2 审查代码范围（仅 B1）

```
.gitignore
defaults/config.yaml
src/aistudio_api/api/app.py
src/aistudio_api/api/dependencies.py
src/aistudio_api/api/routes_system.py
src/aistudio_api/api/state.py
src/aistudio_api/application/account_rotator.py
src/aistudio_api/infrastructure/gateway/model_defaults.py
tests/conftest.py
tests/unit/test_api_auth.py
tests/unit/test_features_config.py   (新增, untracked)
tests/unit/test_genai_sdk_compat.py
tests/unit/test_routes_models.py
tests/unit/test_routes_system.py
```

未扩展审查 P2/P3/P6/P4/P5 行为（仅核对本步留下且被引用的钩子）。

---

## 2. 实际运行命令与结果

均在目标工作树内执行。

| 命令 | 结果 |
|---|---|
| 第 1 轮 `uv run pytest -q` | `2 failed, 258 passed`（失败为两个既有 Windows 平台差异） |
| 第 2 轮（返工后）`$env:PYTHONUTF8='1'; $env:PYTHONIOENCODING='utf-8'; uv run pytest -q` | `2 failed, 264 passed in 3.64s`。失败项：`test_browser_engine.py::test_find_chromium_executable_strictly_requires_cloakbrowser`、`test_browser_scripts_syntax.py::test_evaluate_wrapper_expressions_parse_cleanly`（`bun.CMD` 在 `cmd.exe` 下 `\n was unexpected at this time`）。与给定基线一致，且这两个测试文件本步无 diff，判定为既有 Windows 平台差异，非本步引入。 |
| 第 2 轮定向 `uv run pytest -q tests/unit/test_features_config.py tests/unit/test_routes_system.py tests/unit/test_api_auth.py` | `41 passed` |
| `uv run ruff check <本步改动 12 个 py 文件>` | `All checks passed!`（两轮一致） |
| `uv run ruff check`（全仓，参考） | `Found 16 errors`，全部位于 `docs/dev/evidence/wp5*/harness/*.py`（W292 等），为既有证据脚本，本步未触碰；`src/`、`tests/` 零告警 |
| `git diff --check` | 无空白错误；仅 Git 提示 `tests/unit/test_api_auth.py` 为 LF、下次 checkout 将转 CRLF（不属 diff 错误） |
| `git status --short` | 见 §7；仅 B1 相关文件 + 1 个新增测试文件 + 本报告，`config.yaml` 删除已在暂存区 |
| `git diff --cached --name-status` | `D config.yaml` |
| `git ls-files config.yaml` | 空（0 行） |

补充只读探针（`uv run python -c`，仅系统临时目录）：验证非关键块回退与 `write_config_text` 拒绝行为，见 §0.2。

---

## 3. 逐项核对表

### 3.1 D-07 配置深合并 + 启动校验

| 核对点 | 结论 | 证据 |
|---|---|---|
| `_deep_merge` 递归合并 dict、列表/标量整体覆盖 | 通过 | `model_defaults.py:45-56` |
| `_load_yaml_config` 不再浅替换 `rotator` | 通过 | `model_defaults.py:765`（整份配置 `_deep_merge(_default_config(), loaded)`），旧的 `merged.update(...)` 已删除 |
| 缺键不再到请求期 `KeyError` | 通过 | `rotator` 走合并后必然含 `cooldowns` 四键（`model_defaults.py:588-596`）；`account_rotator.py:269-287`、`313-317` 的 `cooldowns[...]` 由启动校验兜底；`test_features_config.py:35-54` 用部分 rotator 实际驱动 `record_rate_limited` |
| `validate_config_mapping` 校验类型/必填 | 通过 | `model_defaults.py:177-285`：rotator/max_retries/cooldowns、paid_model_rules、tier_quotas、account_presets、model_defaults.profiles/models、instances、api_keys；`logging`/`security` 由非关键回退归一化 |
| `validate_config` 启动入口 | 通过 | `model_defaults.py:288-294`；`app.py:85` 为 lifespan 第一条语句 |
| `instances` 启动校验阶段归一化 | 通过（仅校验，结果未回写，见 B1-07） | `model_defaults.py:276` 调 `_normalize_instance_specs` |
| 全坏 instances fail-fast | 通过 | `model_defaults.py:168-169`；`test_features_config.py:141-157`、`166-183` |
| `account_presets` 的 `id/action` 必填 | 通过 | `model_defaults.py:242-260`；`test_features_config.py:191-201` |
| 非关键项（logging/security）warning + 默认回退 | 通过（第 2 轮已修复） | 非法值与非法块类型均回退并 warning：`model_defaults.py:62-106`；用例 `test_features_config.py:81-96`（`caplog` 断言两条 warning 文本）；`write_config_text` 额外拒绝持久化非法结构（`:724-740`） |

### 3.2 D-14 API 鉴权 / 管理页鉴权分离

| 核对点 | 结论 | 证据 |
|---|---|---|
| 空 `api_keys` + `api_allow_anonymous=false` → 401 | 通过 | `dependencies.py:79-101`；`test_api_auth.py:32-43` |
| 开关 `true` 才放行 | 通过 | `dependencies.py:76-77`（在 Key 校验前返回）；`test_api_auth.py:46-58` |
| 默认值 false（代码默认 + defaults + 根本地） | 通过 | `model_defaults.py:643-645`、`defaults/config.yaml:4-6`、根本地 `config.yaml:6-7`、`tests/conftest.py:9-10` |
| 删除「Web 密码作为超级 API Key」分支 | 通过 | `dependencies.py` 对应 `compare_digest(token, settings.web_password)` 已删；`test_api_auth.py:103-113` 断言管理密码不能当 API Key |
| 管理页空 `AISTUDIO_WEB_PASSWORD` 仍免密 | 通过 | `dependencies.py:47-50`（`settings.auth_enabled = bool(web_password)`，`config.py:276-278`）；`test_api_auth.py:129-132` |
| 管理页不被 API Key 锁死 | 通过 | `require_web_auth` 只比对 `web_password`；`test_api_auth.py:116-126` |
| API Key 与 Web 密码互不复用 | 通过 | 双向测试：`test_api_auth.py:103-126` |
| 不引入 IP/CIDR/白名单；CF Tunnel 不变 | 通过 | 仅新增 `client_ip` 用于日志；diff 无绑定逻辑改动 |
| 错误体 / WWW-Authenticate | 通过（细节差异见 B1-06） | `dependencies.py:89-101`；`test_api_auth.py:32-43` |

### 3.3 实例配置（D01-048/049）

| 核对点 | 结论 | 证据 |
|---|---|---|
| `defaults/config.yaml` 默认 Free/Pro 各 1（`count/surface/min_tier`） | 通过 | `defaults/config.yaml:8-14`；根本地 `config.yaml:10-15` 一致 |
| `count` 正整数、`surface` 仅 chat/build、`min_tier` 白名单（大小写归一） | 通过 | `model_defaults.py:120-166`；`test_features_config.py:108-139` |
| 坏项丢弃 + 同 `(surface, min_tier)` 合并 count、保序 | 通过 | `model_defaults.py:161-166`；用例断言 `count: 3` 的合并结果 |
| 全坏 fail-fast | 通过 | `model_defaults.py:168-169`，且被 `validate_config_mapping:276` 调用 |
| 消费方接线 | 待 B2-3（见 B1-07） | `get_configured_instance_specs`（`model_defaults.py:1105-1111`）目前仅测试调用 |

### 3.4 配置防泄露

| 核对点 | 结论 | 证据 |
|---|---|---|
| `defaults/config.yaml` 为唯一 tracked 默认 | 通过 | `git ls-files defaults/config.yaml` 命中；9 个顶层键与本地一致 |
| 根 `config.yaml` 已移出 index | 通过 | `git ls-files config.yaml` 为空；`git diff --cached` = `D config.yaml` |
| 本地根 `config.yaml` 仍存在且为新 schema | 通过（第 2 轮复验仍未变） | 文件存在；顶层键 `api_keys/security/instances/logging/rotator/paid_model_rules/model_defaults/tier_quotas/account_presets`；rotator 无打分键；presets 均含 `id/action` |
| `.gitignore` 有 `/config.yaml` | 通过 | `.gitignore:15`（锚定根路径，不影响 `defaults/config.yaml`） |
| 与 `AGENT.md:322` B1 迁移口径一致 | 通过 | 只提交 `defaults/config.yaml`，根配置不入库 |

### 3.5 原子写回（D01-045/046）

| 核对点 | 结论 | 证据 |
|---|---|---|
| `_atomic_write_text`：同目录临时文件 + fsync + `os.replace`，异常清理 | 通过 | `model_defaults.py:706-721` |
| `write_config_text` 先解析/校验、后写、再失效缓存 | 通过 | `model_defaults.py:724-745`（新增非 dict `logging`/`security` 拒绝于 `735-740`） |
| `/config/yaml` 走 `write_config_text`，校验失败 400 且不改文件 | 通过 | `routes_system.py:298-306`；`test_routes_system.py:79-105`（断言文件内容不变） |
| API Key CRUD 全部改走 `write_config_text` | 通过（OSError 已补 500） | `routes_system.py:456-465`、`504-513`、`558-567` |
| `update_configured_logging_settings` 改走 `write_config_text` | 通过（读原始文件、不物化默认值，正确） | `model_defaults.py:915-947` |
| 是否还有配置写 `write_text` 漏网 | 无 | 全仓 `write_text` 仅剩 dump/json/account_store/logger 等非配置目标（`logger.py:419/422`、`common.py:19`、`account_store.py:24`、`client.py:123`、`streaming.py:57`） |
| 临时文件清理 | 通过 | `model_defaults.py:718-721`；`test_features_config.py:203-224` 断言无 `*.tmp` 残留 |

### 3.6 lifespan（P9-Q1/Q2）

| 核对点 | 结论 | 证据 |
|---|---|---|
| 启动最先 `validate_config()` | 通过 | `app.py:85`，位于 lifespan 第 1 条（早于全部组件导入与装配） |
| `runtime_state.instance_manager/keepalive` 钩子 | 通过 | `state.py:44-45`；`app.py:178-179` 先置 None 占位，待 B2-3/B3 装配 |
| `try/finally` 保证关闭 | 通过 | `app.py:181-185` |
| 顺序：取消后台任务 → keepalive.stop → instance_manager.close → client.close → 清空状态 | 通过 | `app.py:63-81`（任务 cancel 在 `67-69`，keepalive `71`，manager `72`，client `73`，清态 `75-81`） |
| 同步/异步生命周期方法兼容 | 通过 | `app.py:46-59` `inspect.isawaitable`；`test_features_config.py:264-284` 混用同步/异步组件断言顺序 |
| 关闭异常可观测性 | 偏差（非阻塞） | `app.py:53-59` 吞异常仅 `debug`，见 B1-09 |

### 3.7 rotator（D01-006 / P9-Q3）

| 核对点 | 结论 | 证据 |
|---|---|---|
| 打分键从默认配置/`defaults`/根本地/config conftest 删除 | 通过 | 全仓 `grep`（`*.py`）`strategy/stickiness_bonus/stickiness_threshold/drip_penalty/tier_weights` 零命中；`defaults/config.yaml`、根本地 `config.yaml`、`tests/conftest.py` 的 rotator 仅剩 `max_retries` + `cooldowns` |
| `_score_candidate` 已删除 | 通过 | 零引用；`account_rotator.py:688-699` 为新 `_least_used_key` |
| 选择算法：当前健康账号 sticky → 最少总请求 → 该模型今日请求 → last_used（LRU/FIFO） | 通过 | `account_rotator.py:745-755`；tie-break 元组 `(total_requests, model_daily.requests, last_used)` |
| sticky 仅在候选集合内生效（已过 429/403 冷却、准入过滤） | 通过 | sticky 循环遍历 `valid_candidates`（`731-749`），不在集合则落到最少使用 |
| 账号状态 / 429/403 冷却保留 | 通过 | `account_rotator.py:184-217`（`is_available`/`get_cooldown_remaining`）、`252-287`（429 三档）、`298-317`（403 auth_error） |
| 太平洋午夜跨天重置保留 | 通过 | `account_rotator.py:26-52`、`176-193`、`257` |
| `required_tiers` 过滤保留 | 通过 | `account_rotator.py:653-654` |
| 失败账号过滤/单账号自愈 | 通过 | `account_rotator.py:729-743` |
| 请求期 `cooldowns` 直接下标的安全性 | 通过（绕过写入口的热改场景见 B1-08） | 启动校验 + 深合并保证四键存在；`isinstance(cooldowns, dict)` 守卫仍在（`269-271`、`313-315`） |

### 3.8 测试质量

| 核对点 | 结论 | 证据 |
|---|---|---|
| `test_features_config.py` 覆盖深合并、单坏块 fail-fast、security 默认/非法值/非 dict 块、instances 归一/坏项/全坏、presets 必填、原子写校验顺序/拒绝非 dict/ tmp 清理、shutdown 顺序与任务取消 | 通过（返工后补强） | `test_features_config.py:35-304`；新增 `:81-96`（caplog 断言 warning）、`:98-105`（`write_config_text` 拒绝且不落盘，2 个参数化） |
| `test_api_auth.py` 重写覆盖 D-14 四要素 | 通过 | 空 Key 默认 401、开关放行、管理页空密码免密、互不复用、管理页拒伪造 Accept |
| 重写是否削弱既有契约 | 未发现 | 原 `test_auth_is_disabled_when_no_api_key_is_configured`（空 Key=200）按 D-14 语义翻转为 401，属预期；原 timing-safe 组合用例被 4 个更精确的新用例覆盖（含删除的“Web 密码当 API Key”反向断言） |
| `test_genai_sdk_compat.py` 改动是否只是合理适配 | 是 | 新增 autouse fixture 配置 `settings.api_keys={"test-key"}`（`test_genai_sdk_compat.py:16-23`），与各用例 `api_key="test-key"` 匹配；无助验/放宽断言 |
| `test_routes_models.py` 改动是否只是合理适配 | 是 | 空 Key=开放 → 显式 `x-goog-api-key`（`test_routes_models.py:6`、`17-78`）；`?key=` 用例仍断言未授权 401（`57-67`） |
| 返工是否补足路由级用例 | 是 | `test_routes_system.py:94-105`（非 dict logging → 400 且文件不变）、`:108-133`（写后应用归一化 INFO）、`:137-150`（CRUD OSError → 500） |
| 是否把业务回归伪装成平台差异 | 未发现 | 失败项文件本步无 diff；失败原因为 `bun.CMD`/浏览器可执行文件探测的 Windows 环境差异 |
| 覆盖缺口 | 存在（非阻塞，见 B1-06） | 缺 lifespan 首条 `validate_config()` 顺序用例、原子写失败路径（tmp 清理）用例、普通缺 Key 分支的 `WWW-Authenticate` 断言 |

### 3.9 范围 / 洁净

| 核对点 | 结论 | 证据 |
|---|---|---|
| 未改 `05/06/08-09`、`archive/` | 通过 | `git status --short docs/` 仅显示本报告（untracked） |
| 无真实账号/IP/Google | 通过 | diff 中唯一 IP 为测试 `127.0.0.1`（`test_api_auth.py:33`）；无邮箱/token/密码常量 |
| 未 commit | 通过 | `HEAD = a3ffbe6`（B0），工作区改动未提交；仅 `config.yaml` 删除在暂存区 |
| 无越界目录 | 通过 | 改动 13 个 tracked 文件 + 1 个新增测试文件 |

---

## 4. 发现的问题（含返工后状态）

### B1-01（已修复，原阻塞）`logging` 非字典块静默回退，无 warning；且可被接口 200 落盘

- 原位置：`model_defaults.py:62-88`（旧版仅处理 dict 块）、`:_load_yaml_config` 未校验、`routes_system.py:294-295`。
- 原证据：`validate_config_mapping({'logging': []})` 通过且返回 `logging` 为 list、无 warning；`write_config_text('logging: []')` 被接受并写入。
- 返工后证据：`model_defaults.py:66-76` 对非 dict `logging` 输出 `配置 logging 必须为字典（当前类型 list），已回退默认 logging 块` 并回退默认块；`model_defaults.py:735-740` 在写入前拒绝非 dict 块；探针与 `test_features_config.py:81-105`、`test_routes_system.py:94-105` 均验证 400 且文件不变。
- 状态：**已修复**（第 2 轮复核通过）。

### B1-02（已修复，原阻塞）`security` 非字典块直接启动失败

- 原位置：旧版 `model_defaults.py:256`（`_require_mapping(config, "security")`）。
- 原证据：`validate_config_mapping({'security': None})` 抛 `ConfigValidationError`，与读取期“warning + false”不一致。
- 返工后证据：`model_defaults.py:87-106` 对非 dict `security` warning + 回退默认块；`_require_mapping(config, "security")` 已移除（回退保证必为 dict）；探针返回 `{'api_allow_anonymous': False}` 并输出 warning 文本；`write_config_text('security: null')` → 400 不落盘。
- 状态：**已修复**（第 2 轮复核通过）。

### B1-03（用户已裁决接受，不改代码）深合并范围扩大到整份配置

- 位置：`model_defaults.py:765`（`_deep_merge(_default_config(), loaded)`）。
- 结论：用户已裁决“接受所有 dict 块深合并默认、list/标量整体覆盖”，`paid_model_rules`/`tier_quotas` 默认规则不可通过省略删除属预期行为，本步不要求改代码。
- 状态：**已裁决接受**。

### B1-04（已修复，原非阻塞）API Key CRUD 未捕获 `OSError`

- 原位置：`routes_system.py:461-470`、`507-515`、`559-567` 仅 `except ConfigValidationError`。
- 返工后证据：`routes_system.py:460-463`、`508-511`、`562-565` 均补 `except OSError → HTTPException(500, "写入配置文件失败: ...")`；用例 `test_routes_system.py:137-150` 断言 500 与错误消息。
- 状态：**已修复**。

### B1-05（已修复，原非阻塞）`/config/yaml` 写后应用原始 logging 值

- 原位置：`routes_system.py:312-313`（直接用 `parsed["logging"]["level"]`）。
- 返工后证据：`routes_system.py:310-313` 改为 `get_configured_logging_settings()` 取归一化结果后应用；用例 `test_routes_system.py:108-133` 断言写入 `level: verbose` 后 `applied_levels == ["INFO"]`。
- 状态：**已修复**。

### B1-06（保留，非阻塞，后续项）

- 部分缓解：返工已补“非 dict `logging`/`security` 回退与 warning”“`write_config_text` 拒绝非 dict 块”“CRUD `OSError`”用例。
- 仍缺：
  - “lifespan 首条语句是 `validate_config()`”的顺序性用例（现只测 `_shutdown_runtime`：`test_features_config.py:264-304`）；
  - 原子写失败路径（写入抛错后 tmp 清理、目标文件不变）用例；
  - 普通缺 Key 分支的 `WWW-Authenticate` 断言（仅空 Key 分支有）。
- 状态：非阻塞，留待 P10 收尾或相关步骤补测。

### B1-07（保留，非阻塞，范围说明）instances 归一化结果未进入启动装配

- 位置：`model_defaults.py:276`（只做校验，返回值被丢弃）；`model_defaults.py:1105-1111` 的 `get_configured_instance_specs` 在 `src/` 无调用方。
- 影响：B1 只保证“启动阶段拒绝非法/全坏实例配置”，真正按 `count/surface/min_tier` 建池在 B2-3（InstancePool/Manager）。与批准顺序一致，但需在 B2-3 明确消费点。
- 状态：非阻塞（待 B2-3）。

### B1-08（保留，非阻塞）绕过写入口热改配置时的请求期非 KeyError 失败

- 位置：`account_rotator.py:269-274`、`313-316`。
- 证据：`isinstance(cooldowns, dict)` 守卫仍在，非 dict 会 `RuntimeError("rotator 配置缺少 cooldowns 配置块")`；缺键由深合并补齐，不会 `KeyError`。
- 影响：直接编辑文件把 `cooldowns` 改成非字典、且未经过 `/config/yaml` 校验时，错误推迟到请求期（不是 KeyError）。
- 状态：非阻塞。

### B1-09（保留，非阻塞）关闭阶段异常被吞为 debug

- 位置：`app.py:53-59`。
- 证据：`keepalive.stop` / `instance_manager.close` / `client.close` 失败仅 `logger.debug`；无异常路径用例。
- 影响：真实停机时组件关闭失败不易被发现。建议 P6 接线时至少 `warning`。
- 状态：非阻塞。

---

## 5. 给用户的裁决项（更新）

1. ~~**B1-01/B1-02 是否本步修复**~~ → **已闭环**：本步局部返工已修复，第 2 轮复核通过。
2. ~~**`security` 的级别口径（二选一）**~~ → **已闭环**：选择“非关键块 warning + 回退默认”，写入口拒绝持久化非法结构，与 AGENT/06 的“非关键回退默认”口径一致。
3. ~~**B1-03 深合并范围**~~ → **用户已裁决接受**：所有 dict 块深合并默认、list/标量整体覆盖，无需改代码。
4. ~~**B1-04/B1-05 是否顺手补齐**~~ → **已闭环**：两项均已在本步修复并有用例。
5. 仍待后续处理（非本步阻塞）：B1-06 的三项补测、B1-07 的 B2-3 接线、B1-08/B1-09 的观测性改善。

---

## 6. 只读声明

- 未修改任何代码、配置、测试、文档（本报告除外）；未执行 `git add/commit/stash/checkout/reset` 等写操作。
- 未访问真实 Google、真实账号或真实 IP；所有验证均为离线单测/mock/临时目录。
- 除本报告外，仅在系统临时目录写入过探针用的临时配置文件（`tempfile.mkdtemp()`），仓库内无新增/变更文件。

## 7. `config.yaml` 状态确认（第 2 轮复验）

| 项 | 状态 | 验证 |
|---|---|---|
| 版本控制（index） | 已移除：`git ls-files config.yaml` 为空；`git diff --cached` 显示 `D config.yaml`（230 行删除，暂存未提交） | 实际命令 |
| 本地文件 | 仍存在（工作区，已被忽略）；内容为新 schema：`api_keys/security/instances/logging/rotator/paid_model_rules/model_defaults/tier_quotas/account_presets`，rotator 无打分键，presets 均含 `id/action` | 实际读取 |
| 忽略规则 | `.gitignore:15` 含锚定 `/config.yaml`，不影响 `defaults/config.yaml` | 实际读取 |
| `defaults/config.yaml` | tracked，作为唯一分发默认；与本地根配置顶层键一致，instances/security/rotator 值一致 | 实际对比 |
